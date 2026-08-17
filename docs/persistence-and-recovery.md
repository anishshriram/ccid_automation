# Persistence & Recovery

**Source files:** `ccid/recorder.py` (517 lines), `ccid/config.py` (456 lines)
**Tests:** `tests/test_recorder.py`, `tests/test_resume.py` (crash-injection proofs), `tests/test_config.py`

These two files are grouped together because they share one concept end to end: `AppConfig.canonical_hash()` (from `config.py`) is stamped into every `RunState` (from `recorder.py`) at the moment a run starts, and every resume attempt re-validates against it. Config validation and crash-safe persistence are two halves of the same guarantee — "this run's data can always be trusted to correspond to a known, valid configuration."

---

## 1. `ccid/recorder.py` — the commit-order contract

The module docstring states the entire safety property up front, and it's worth reproducing in full because everything else in the file exists to implement exactly this:

> Write and fsync every per-cycle artifact first, then append and fsync the CSV row, then atomically replace `runstate.json`, then send the external heartbeat. A crash at any point along that order leaves the run in a state `reconcile_orphans` can clean up without ever losing or double-counting a cycle: artifacts written but `runstate.json` not yet advanced are orphans (deleted on resume, since `last_completed_cycle` never claimed them); `runstate.json` is never advanced until everything it describes already exists on disk.

Three consequences worth internalizing:

- **`runstate.json`'s `last_completed_cycle` is the single source of truth for "what actually happened."** Everything else (waveform files, images, the per-cycle JSON sidecar, `cycles.csv` rows) is only trustworthy up to whatever `last_completed_cycle` says, no further.
- **A crash can produce extra files, never missing ones for a cycle `runstate.json` claims complete.** The commit order guarantees artifacts exist *before* the state file is advanced to claim them — never the reverse.
- **The heartbeat is sent last, on purpose.** An external liveness ping (Cronitor) must never certify a cycle that isn't durably on disk yet — if the process dies between writing `runstate.json` and sending the heartbeat, the *next* heartbeat will simply reflect the now-correct `last_completed_cycle`, and nothing was falsely reported as alive-and-progressing before it actually was.

---

## 2. Data model

```python
CycleArtifacts:  waveform_samples, waveform_preamble, scope_png, gate_jpg,
                  fault_jpg_burst: tuple[bytes, ...] = (),   # currently always empty in practice — see §7
                  cycle_sidecar: Mapping[str, object] = {}

CycleCsvRow:     cycle_index, run_id, utc_timestamp, monotonic_start, trip_time_s,
                  verdict, analysis_version, led_state_at_gate, degraded_flags, notes
                  # .from_values(...) stamps utc_timestamp = now() automatically

RunState:        run_id, last_completed_cycle, target_cycles, config_hash,
                  pass_count, fail_count, halt_reason: str | None
```

`RunState.halt_reason` is the field that makes a run "sticky" — `None` means healthy/in-progress; any string means the run is halted and stays halted until explicitly overridden on resume (§2.2).

---

## 3. On-disk layout

```
<run_root>/<run_id>/
    config.yaml              # frozen copy of the config that started this run (written once, at initialize_run)
    runstate.json            # the durability boundary — see §1
    cycles.csv                # append-only, one row per committed cycle
    cycles/<n>.json           # full sidecar per cycle: analysis result, state transitions, scope readback/preamble, config_hash, software_version
    waveforms/<n>.npz          # samples.bin + preamble.json, zip-packed (same format analysis.py loads — see that doc §3)
    images/<n>_scope.png       # scope screen capture
    images/<n>_green.jpg       # camera frame at gate grant
    images/<n>_fault_<i>.jpg   # (reserved — see §7)
    diagnostics/<n>/           # best-effort, outside the crash-safe contract — see §5
        scope_timeout.png, scope_state.json, scope_errors.txt
        forced_diagnostic_waveform.npz, forced_diagnostic_scope.png, forced_diagnostic_state.json
```

`_ensure_layout` creates `cycles/`, `waveforms/`, `images/` up front (idempotent, `exist_ok=True`); `diagnostics/<n>/` is created lazily by whichever diagnostic-write method needs it, and only under a per-cycle subdirectory the normal-path cleanup logic never looks at (§5).

---

## 4. `RunRecorder` — the lifecycle methods

### 4.1 `initialize_run(run_id, target_cycles, config_hash, frozen_config_yaml)`
Creates the directory tree, writes `config.yaml` (the frozen text, fsynced), writes the `cycles.csv` header if the file doesn't already exist (idempotent — safe to call again on a run directory that already has data, which matters for `resume`), and writes an initial `RunState` (`last_completed_cycle=0`, `halt_reason=None`) atomically. Returns the run directory path.

### 4.2 `load_run_state(run_dir, expected_config_hash, allow_halted_resume=False)`
Reads the current `RunState` and enforces two gates before returning it:
- **Config hash mismatch → `ConfigHashMismatchError`.** A run cannot silently resume against a different configuration than it started with — this is the whole reason `canonical_hash()` exists (§8).
- **`halt_reason is not None` and `not allow_halted_resume` → `ResumeBlockedError`.** This is the "sticky halt" property: a halted run stays halted on every subsequent resume attempt unless the caller explicitly passes an override. `read_run_state_unchecked` is the escape hatch that skips both checks entirely (used by `main.py`'s `--allow-config-hash-override` path).

### 4.3 `reconcile_orphans(run_dir, state)`
Called once, right after a resume's `RunState` is loaded, before the campaign loop starts again. Two cleanup passes:
- **`_delete_orphans`** — scans `cycles/`, `waveforms/`, and `images/` with regexes matching each artifact's numeric-cycle-index filename pattern, and deletes any file whose cycle index is *greater* than `last_completed_cycle`. These are artifacts a crash left behind from a cycle that was in progress but never got its `runstate.json` advance.
- **`_truncate_cycles_csv`** — rewrites `cycles.csv` keeping only rows with `cycle_index <= last_completed_cycle`. A crash between the CSV append and the `runstate.json` write would otherwise leave one extra CSV row that `runstate.json` doesn't actually claim happened.

Neither of these can ever *lose* a legitimately committed cycle — they only remove things whose index exceeds what `runstate.json` (the durability boundary) actually claims.

### 4.4 `record_cycle` — the main commit path

Validates first (`csv_row.run_id` must match, `csv_row.cycle_index` must be exactly `last_completed_cycle + 1` — both raise `PersistenceError` rather than silently accepting an out-of-sequence or cross-run write), then executes the commit order from §1 exactly, with a `self._checkpoint(step_name)` call after each stage:

```
write waveform .npz, scope PNG, gate JPG, any fault JPGs, cycle JSON sidecar   → _checkpoint("after_artifacts")
append CSV row                                                                  → _checkpoint("after_csv")
build next RunState (pass_count/fail_count incremented, halt_reason set if given), write atomically  → _checkpoint("after_runstate")
call heartbeat_sender(run_id, last_completed_cycle), if one was configured      → _checkpoint("after_heartbeat")
return next_state
```

`_checkpoint` is a no-op in production (`crash_injector` defaults to `None`). `tools/simulate.py`'s crash-resume tooling supplies a real injector that raises at a chosen step name — this is what lets `test_resume.py` *prove* (not just assert by inspection) that a crash at each specific point recovers correctly, rather than trusting the commit-order comment on faith.

The cycle's JSON sidecar is built by merging the caller-supplied `cycle_sidecar` (from `Sequencer._build_record_payload` — analysis result, state transitions, scope readback/preamble) with a few fields stamped here specifically so **an individually-inspected cycle JSON is self-describing**: `cycle_index`, `run_id`, `utc_timestamp`, `trip_time_s`, `verdict`, `analysis_version`, `led_state_at_gate`, `config_hash`, `software_version`. The comment is explicit about why `config_hash`/`software_version` are repeated here even though they're already in `runstate.json` at the run level: so you can pick up a single `cycles/47.json` file in isolation and know exactly what produced it.

---

## 5. Diagnostic writes — deliberately outside the crash-safe contract

`write_timeout_diagnostics` and `write_forced_diagnostic_capture` both share three properties, stated explicitly in their docstrings:

- They take no `RunState` and never touch `runstate.json` — nothing here can advance or affect `last_completed_cycle`.
- They write only under `diagnostics/<cycle_index>/`, a subtree `_ensure_layout` and `_delete_orphans` never look at — so a crash mid-diagnostic-write can never confuse the orphan-cleanup logic that governs the *real* commit contract.
- They are best-effort by nature (called from `Sequencer`'s already-best-effort diagnostic capture paths — see the sequencer doc §8) — a failure writing diagnostics is a logged warning upstream, never a reason to treat the halt itself differently.

**`write_timeout_diagnostics`** captures the scope's timeout snapshot (PNG, JSON state including `operation_condition`/`hal_status`/settings/K3 timing, an errors text file) for a halt where the scope never completed acquisition.

**`write_forced_diagnostic_capture`** is the Entry 11/13 diagnostic-only forced-trigger capture. Its docstring is worth reading verbatim in the source because it documents a real bug fix: the method used to take a single `forced_at_monotonic_s` field that was incorrectly assumed to correspond to the scope waveform's own t=0 — nothing in the system actually guarantees that. It now takes the full set of Pi-side timestamps plus `diagnostic_timeline` plus `waveform_analysis` (computed entirely from the waveform's own samples/preamble by `ccid.forced_diagnostic_analysis` — see the analysis doc §7), and the written JSON's own `note` field repeats the warning inline: *"do not map them onto the waveform's own time axis."* The written `capture_type` is literally `"forced_diagnostic_non_measurement"` — unambiguous at a glance to anyone browsing the diagnostics tree later.

---

## 6. Low-level write primitives

- **`_write_bytes_and_fsync`** — the base primitive everything else builds on: create parent dirs, write, `flush()`, `os.fsync(fileno())`. This is what makes "written" actually mean "on disk," not just "handed to the OS's page cache" — the distinction that matters for a crash-safety guarantee.
- **`_write_json_and_fsync`** — canonical JSON (`sort_keys=True`, compact separators) through the same fsync primitive.
- **`_write_waveform_npz`** — builds the same zip container format `analysis.py`'s `load_waveform` reads (`samples.bin` + `preamble.json`, `ZIP_DEFLATED`), in memory, then writes it through the fsync primitive.
- **`_write_runstate_atomic`** — the one write that isn't a plain fsync-and-done, because `runstate.json` is read on every resume and can never be allowed to exist in a torn state: write to a `NamedTemporaryFile` in the *same directory* (so the eventual rename is same-filesystem, hence atomic), fsync it, then `os.replace(tmp, path)` — atomic on POSIX, so any reader always sees either the fully-old or fully-new file, never a partial write caught mid-flight.
- **CSV helpers** (`_write_cycles_csv_header`, `_append_cycles_csv`, `_truncate_cycles_csv`) — all go through `csv.DictWriter` against the fixed `_CYCLES_CSV_COLUMNS` list (the "locked column schema" referenced elsewhere in the codebase — `analysis_version` living in this schema is exactly what lets a reader know which algorithm produced each row without opening the per-cycle JSON).

---

## 7. Things worth knowing precisely

- **`CycleArtifacts.fault_jpg_burst` and the `images/<n>_fault_<i>.jpg` naming pattern exist in the write path and the orphan-cleanup regex, but nothing in `Sequencer` currently populates `fault_jpg_burst` with anything** — it's always the empty default tuple in the current production path. The recorder is ready for a future "burst of images around a fault" feature; it isn't wired up yet.
- **`PathsConfig.output_root` is validated, hashed into the config hash, and never read anywhere else in the codebase.** `RunRecorder` is constructed with `run_root` only (`ccid/main.py`'s `build_hal_bundle`/lifecycle wiring). If you're looking for where output artifacts other than the run directory itself get written, there currently isn't one — `output_root` is reserved, not active.

---

## 8. `ccid/config.py` — strict validation and hash-freezing

Two-part contract per the module docstring: every section is validated against an **explicit key set** (`_reject_unknown_keys` — an unrecognized key anywhere in `config.yaml` fails loudly at load time, never silently ignored), and the whole thing collapses to a **canonical hash** used to detect a resume against a silently-changed configuration.

### 8.1 Structure
`AppConfig` is a frozen dataclass tree: `GpioConfig`, `VisionConfig`, `CameraHardwareConfig`, `TimingConfig`, `ModesConfig`, `PathsConfig`, `MonitoringConfig`, plus `analysis: AnalysisConfig` (imported directly from `ccid.analysis` — see that doc §2, not redefined here). `_validate_and_build` walks the raw YAML dict section by section; every scalar goes through one of `_require_int`/`_require_float`/`_require_str`/`_require_mapping`/`_require_non_empty_path`, each raising a specific `ConfigValidationError` message rather than letting a `TypeError`/`KeyError` leak out with a confusing traceback. Note `_require_int`/`_require_float` both explicitly reject `bool` (`isinstance(value, bool)` is checked *before* `isinstance(value, int)`, since `bool` is a subclass of `int` in Python and `True`/`False` would otherwise silently pass as `1`/`0`).

### 8.2 Cross-field validations worth knowing about
Beyond simple range checks, two invariants are enforced *across* fields:
- `pass_limit_s < no_trip_limit_s` — checked both here (on the raw `timing` values) and again inside `AnalysisConfig.__post_init__` (the analysis doc §2) — belt and suspenders, since `AnalysisConfig` can also be constructed directly without going through this loader (e.g. in tests).
- **`k3_backstop_s > no_trip_limit_s`** — less obvious, worth spelling out: if the K3 backstop could fire *at or before* the no-trip limit, a genuinely non-tripping device would have its injection forcibly cut off before the scope could ever observe a long enough window to legitimately conclude NO_TRIP — the hardware safety cutoff would make the software measurement impossible to trust in exactly the case it matters most (a real DUT failure). Backstop must always outlast the measurement window it's supposed to be a safety net *around*, not a competitor with.

### 8.3 `analysis:` section handling — the whole section is optional
`_build_analysis` accepts a missing `analysis:` key entirely (defaults to `{}`), builds `algorithm_version`/`endpoint_definition` with their own explicit validation (an unrecognized version string raises with the exact supported list, matching the analysis doc's versioning discipline), and passes every other present numeric key straight through to `AnalysisConfig`'s own constructor — meaning `AnalysisConfig.__post_init__`'s validation (analysis doc §2) is the ultimate authority on numeric ranges; this function only handles type coercion and the two special string fields. `pass_limit_s`/`no_trip_limit_s` are **not** independently settable here — they're always threaded through from `timing.*`, so there is exactly one place those two numbers live, never two copies that could drift apart.

### 8.4 `canonical_hash()` / `_canonical_for_hash`
`asdict(config)` (recursive dataclass→dict), with two deliberate adjustments before hashing: `algorithm_version` is converted from the enum to its `.value` string (enums aren't natively JSON-stable across representations), and `paths.*` are converted from `Path` to `str`. The `monitoring` section contributes **only the env var *name*** (`cronitor_url_env`) to the hash — never any secret/URL value, since none is ever loaded into `AppConfig` in the first place (`resolve_cronitor_url()` reads the actual URL from the OS environment fresh, at call time, and it's never part of the loaded config object at all). The hash is `sha256` over `json.dumps(..., sort_keys=True, separators=(",",":"))` — key-order-independent by construction, which is exactly what `test_hash_stable_across_key_order` checks (two YAML files with identical content in different key orders must hash identically).

---

## 9. Test coverage map

| Behavior | Test(s) |
|---|---|
| Full commit path writes artifacts/CSV/runstate correctly | `test_recorder.py::test_record_cycle_writes_artifacts_csv_and_runstate` |
| `trip_time_s` (raw) stays independent of `verdict` in storage | `test_cycles_csv_keeps_raw_trip_float_separate_from_verdict` |
| Halt reason persists/stays sticky in `runstate.json` | `test_halt_reason_is_sticky_in_runstate` |
| Timeout-diagnostics writes, and non-interference with normal artifacts/runstate | `test_write_timeout_diagnostics_writes_expected_files`, `test_write_timeout_diagnostics_does_not_touch_normal_artifacts_or_runstate`, `test_write_timeout_diagnostics_no_errors_marker` |
| Forced-diagnostic writes, and non-interference | `test_write_forced_diagnostic_capture_writes_expected_files`, `test_write_forced_diagnostic_capture_does_not_touch_normal_artifacts_or_runstate` |
| Resume blocked on sticky halt / config hash mismatch, both requiring explicit override | `test_resume.py::test_resume_blocks_on_halt_without_override`, `test_resume_blocks_on_config_hash_mismatch` |
| **Real crash-injection proofs** — crash after CSV write (orphan waveform/JSON get cleaned up, CSV truncated), crash after runstate write (committed cycle survives, nothing rolled back) | `test_crash_after_csv_keeps_last_completed_and_reconcile_removes_orphans`, `test_crash_after_runstate_keeps_committed_cycle` |
| Config loading happy path, `cronitor_url_env` resolution + validation | `test_config.py::test_loads_example_config`, `test_resolve_cronitor_url_reads_the_configured_env_var`, `test_rejects_empty_cronitor_url_env` |
| Rejected: duplicate GPIO pins, invalid pass/no-trip/backstop relationships, unknown keys (top-level, vision, camera), non-positive vision timing, required-frames below 1, `min_free_disk_gb` below 1, unsupported modes | The full `test_rejects_*` block in `test_config.py` |
| Hash stability across key order | `test_hash_stable_across_key_order` |

---

## 10. Things to know if you're about to change this file

- **Never write two artifacts in a different order than artifacts → CSV → runstate → heartbeat.** If you add a new per-cycle artifact, it belongs in the "artifacts" phase (before the CSV append), or it breaks the recoverability guarantee the whole module exists for.
- **Any new field added to `AppConfig` needs a decision about `_canonical_for_hash`**: does a change to this field mean a resumed run is now running under meaningfully different behavior (→ include it in the hash) or not (→ deliberately exclude it, and say why, like `monitoring`'s secret-exclusion above)?
- New `config.yaml` sections/keys need an entry in the relevant `_*_KEYS` frozenset or they'll be silently... actually loudly rejected — `_reject_unknown_keys` fails fast specifically so a typo'd config key surfaces immediately at load time rather than being silently ignored and defaulting to something unexpected.
- If you add a new diagnostic-write method (alongside `write_timeout_diagnostics`/`write_forced_diagnostic_capture`), keep it outside the crash-safe contract on purpose — don't give it a `RunState` parameter or let it touch `runstate.json`, or you've quietly expanded what "committed" means.
