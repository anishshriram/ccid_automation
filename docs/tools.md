# Tools

**Source files:** `tools/simulate.py` (444), `tools/replay_waveform.py` (344), `tools/calibrate_camera.py` (397), `tools/gpio_selftest.py` (272), `tools/scope_bench.py` (286)
**Tests:** `tests/test_tools_simulate.py`, `tests/test_tools_replay_waveform.py`, `tests/test_tools_calibrate_camera.py`, `tests/test_tools_gpio_selftest.py`, `tests/test_tools_scope_bench.py`

Five standalone CLIs for commissioning, calibration, and offline analysis. **None of these reimplement any logic that already exists in `ccid/`** — every one of them is a thin driver around the same production code paths (`Sequencer`, `RunRecorder`, `ccid.classify`, the HAL interfaces, `ccid.analysis`) that the real system uses, specifically so results from these tools reflect what the sequencer will actually do, not a parallel approximation of it. Two shared conventions worth knowing before reading any of them individually: every tool prints a single JSON object to stdout per invocation (scriptable, greppable, no ad hoc text parsing needed), and every one defaults to a simulated/safe backend, requiring explicit opt-in flags to touch real hardware.

---

## 1. `tools/simulate.py` — accelerated campaign runner

Always uses simulated HALs, never real hardware, regardless of `config.yaml`'s mode settings — this is stated as a hard property in the module docstring, not just a default. Exists to exercise the full `Sequencer` + `RunRecorder` stack at accelerated (non-real) time.

### `ManualClock`
The whole reason this tool can run a 6000-cycle campaign in seconds instead of days: `now()` returns an internal counter, `sleep(seconds)` just advances that counter by `seconds` instead of actually waiting. Passed as both `monotonic_now` and `sleep` to `Sequencer` — every timing-dependent branch in the sequencer still executes with correct relative timing, it just doesn't cost wall-clock time.

### `CrashInjector` — the mechanism behind the crash-safety proofs
```python
def __call__(self, step_name):
    if step_name == "after_artifacts": self._commits_started += 1   # counts which cycle we're on
    if not triggered and commits_started == target_cycle and step_name == target_checkpoint:
        triggered = True
        raise SimulatedCrash(...)
```
Passed as `RunRecorder`'s `crash_injector` (persistence doc §4.4's `_checkpoint` hook). This is what turns the recorder's commit-order *comment* into something actually tested end to end: raise a real Python exception at a chosen commit step of a chosen cycle, let it propagate up and abort the campaign, then start a **fresh** `RunRecorder` (no injector) against the same run directory and confirm resume recovers correctly — no skipped cycle, no duplicated cycle, opening order still safe.

### `default_camera_fixtures` — a documented gotcha
Worth reading the docstring on this one carefully: `CameraSim`'s own built-in default fixtures use raw near-black bytes that classify as LED "off" once actually run through HSV classification — the fixture's `led_state` label field is never consulted by `classify.await_charging_gate` (vision doc §9), only the pixel data is. A campaign run with plain `CameraSim()` defaults would therefore *always* time out on the vision gate, never observing a real charging classification. This function builds a real blinking-green sequence (`make_blinking_sequence(GREEN, ...)` from `classify.py`'s fixture helpers) long enough to satisfy the ~3s agreement window, specifically so `build_sim_bundle` produces a campaign that can actually proceed past the gate.

### `build_sim_bundle` / `run_campaign`
Constructs sim contactors/scope/camera (with optional `gpio_fail_operations` → `contactors.inject_failure(...)`, `camera_fail_after` → forces `CAMERA_UNAVAILABLE` after N samples, `scope_scenario` overrides), builds a `Sequencer` wired to the `ManualClock`, brackets `sequencer.run()` with `camera.start()`/`camera.stop()`. This is the shared engine all three subcommands below call into.

### Verification helpers
- **`opening_order_is_safe(contactors)`** — reads the sim's last 3 open events (`recent_open_order`, HAL doc §2) and checks K3 opened before K2/K1 among whichever of those actually appear — an independent, code-level check of the same K3-first safe-off ordering `safety.py` is supposed to enforce, run against real recorded events rather than trusted by inspection.
- **`no_skipped_cycles(run_dir)`** — reads `cycles.csv` and confirms the committed cycle indices are exactly the contiguous range `1..N`, no gaps, no duplicates — the other half of the crash-resume proof.

### Subcommands
- **`campaign`** — run N cycles (`--cycles`, `--scope-fault`, `--camera-fail-after`, `--gpio-fail OPERATION:COUNT`), report terminal/halt reason/pass-fail counts/opening-order safety as JSON.
- **`crash-resume`** — run with a `CrashInjector` targeting `--crash-cycle`/`--crash-checkpoint` (one of the four commit checkpoints), catch the resulting `SimulatedCrash`, confirm it actually fired (`injector.triggered`, else report failure rather than a false pass), then reconcile orphans and resume with a *clean* recorder, reporting `no_skipped_cycles` and `opening_order_safe` on the combined pre/post-crash result.
- **`sticky-halt-check`** — force a genuine rig-fault halt (`ScopeSimScenario(never_triggered=True)`), then attempt `load_run_state(..., allow_halted_resume=False)` and confirm it raises `ResumeBlockedError` — proving the sticky-halt property (persistence doc §4.2) actually blocks a naive resume, not just that the code path exists.

---

## 2. `tools/replay_waveform.py` — offline re-analysis

Full context on the algorithm side of this already lives in the analysis doc (§6, §9) and the persistence doc doesn't repeat it — this section covers the tool mechanics specifically.

**Hard rules, enforced by construction** (module docstring): original artifacts are only ever opened for reading; every output goes under a fresh `reanalysis/<replay_id>/` directory (`replay_<UTC-timestamp>`, via `make_replay_id`) that can never collide with or overwrite raw data; every replayed result is explicitly tagged `source: "replay"` plus the replay id and timestamp, so it's structurally distinguishable from the original inline `TripResult`; a `change_report.csv` is produced alongside so a verdict change is auditable at a glance rather than requiring a hand-diff of JSON files.

`build_analysis_config(config_path, algorithm_version)` loads the run's frozen config, then — if `--algorithm-version` overrides it — swaps in both the version *and* the matching endpoint-definition text via `_ENDPOINT_DEFINITION_BY_VERSION` (the exact mapping this session added a fix for — see the analysis doc §9 — so a `v3` override can never end up mislabeled with `v2`'s description text).

`replay_cycle` loads the original inline `TripResult` if one exists (`load_original_trip_result`, tolerant of a missing sidecar — returns `None` rather than failing, since a replay should still be possible even without a comparison baseline), re-analyzes the stored `.npz`, writes the replayed result JSON, and returns a `_diff_row` (`source_*`/`replay_*` trip time and verdict, `verdict_changed` boolean, `trip_time_delta_s`). `replay_run` drives this over a resolved cycle range (`resolve_cycle_range` — explicit `--cycle`, an explicit `--from`/`--to` pair, or if neither is given, every cycle from 1 to the run's current `last_completed_cycle`), skips (logs a warning, doesn't abort) any cycle whose waveform file is missing, and writes both the per-row `change_report.csv` and a `manifest.json` summarizing which cycles changed verdict.

### Subcommands
- **`waveform`** — replay exactly one `.npz` file directly, print (and optionally save) the resulting `TripResult` JSON. No run directory involved at all — works on any stored waveform, including ones pulled out of a run for standalone inspection.
- **`run`** — replay a cycle/range/whole run against a run directory, write the full `reanalysis/` output tree, print a summary (`cycles_replayed`, `cycles_with_verdict_change`).

---

## 3. `tools/calibrate_camera.py` — LED calibration

**Never drives hardware.** Only reads image files from disk (via a lazily-imported OpenCV — the tool works without it installed, right up until you actually call the function that needs it) and writes JSON/replay artifacts. The module docstring is explicit that all classification logic is delegated to `ccid.classify` "so calibration numbers are produced by the same code path the sequencer uses, not a parallel implementation" — this tool contains zero HSV math of its own beyond one geometry trick (below).

- **ROI**: `resolve_roi` prefers an explicit `RegionOfInterest` (parsed from `"x,y,width,height"` or loaded from a saved `roi.json`) over `center_roi`'s centered-fraction fallback.
- **`propose_hue_range`** — collects hue values from every "lit" pixel (`value >= min_value & saturation >= min_saturation`, the exact same test `classify.py` uses) across a directory of captured frames, then computes a percentile band via `_circular_hue_range`. The one piece of real logic unique to this tool: hue is circular (0°=360°), so a plain percentile on raw degree values would badly mis-measure a color like red that straddles the wrap point — the fix is to compute the circular mean angle, rotate every sample so that mean lands at 180° (as far as possible from the wrap point), take the percentile window in rotated space, then rotate the result back. This is what makes the proposed `HueRange` for red come out sensible instead of spuriously split across 0°/360°.
- **`verify_temporal_classification`** — feeds each captured, labeled sequence through a real `LedClassifier` (the same class `await_charging_gate` uses) and reports whether the declared `stable_color` actually matches the expected label — "a 'matched' result here means the real vision-gate path would also declare the expected state from this footage." This is a genuine verification against production logic, not a separate heuristic.
- **`build_replay_footage` / `write_replay_file`** — packages captured frames into exactly the JSON schema `CameraSim(replay_file=...)` reads back (`led_state` by enum name, base64-encoded BGR bytes, width/height) — this is how real captured footage becomes a `CameraSim` fixture set for later use in tests or `tools/simulate.py`.

The module docstring is careful to note that proposed hue ranges and the "verified" flag are calibration **aids for the operator to review** — nothing here writes back into `config.yaml` or `LedOpticalConfig`'s defaults automatically.

### Subcommands
`show-roi`, `propose-hsv` (per-color labeled frame directories → proposed `HueRange`s), `verify` (labeled sequences → match/mismatch report, non-zero exit if anything mismatches), `build-replay` (labeled frame directories → a `CameraSim` replay JSON file).

---

## 4. `tools/gpio_selftest.py` — guarded contactor commissioning

Defaults to `GpioSimContactorController`; touching real GPIO requires **both** `--real` and `--i-understand-this-energizes-hardware` (`_require_hardware_ack` — missing the second flag with `--real` raises `SystemExit` with an explicit message about what will physically energize). Critically, per the module docstring: **this tool cannot bypass the interlocks** — it only drives contactors through the same public `ContactorInterface` methods everything else uses, so `close_k3` still requires K1/K2 already closed and a fresh gate token, exactly as enforced in `gpio_real.py`/`gpio_sim.py` (HAL doc §2). A commissioning tool that could sidestep the interlock would defeat the entire point of testing against it.

`BCM_TO_PHYSICAL_PIN` is a hardcoded lookup table for the standard Raspberry Pi 40-pin header — used only for human-readable reporting (`pin_info`), not for anything functional.

`exercise_contactor(contactor, pulses, hold_s, cooldown_s)` — closes/opens the target contactor a bounded number of times, always starting and ending with `safe_off` (the ending one in a `finally`, so a mid-exercise exception still leaves hardware de-energized). If the target is K3, it closes K1 and K2 first and leaves them closed for the whole exercise — "so the exercise engages the same interlock a real cycle would rather than bypassing it," per the docstring — you cannot use this tool to pulse K3 in isolation; it has to go through the same prerequisite state a real cycle requires.

`mismatch_probe(stagger_ms)` — closes K1 only (deliberately not K2, to create a real mismatch condition) and checks `detect_mains_command_mismatch` reports correctly both immediately (`True` only if `stagger_ms == 0`) and after the stagger window elapses (always `True` once past it) — an independent, hardware-facing verification of the same debouncer algorithm documented in the HAL doc §2, runnable against either the sim or (with acknowledgement) real GPIO.

### Subcommands
`show-pins` (BCM/physical mapping, always safe, no hardware acknowledgement needed since it only reads config), `exercise` (`--contactor`, `--pulses`, `--hold-s`, `--cooldown-s`), `mismatch-test` (`--stagger-ms`, defaults to `config.yaml`'s `timing.mains_stagger_ms`).

---

## 5. `tools/scope_bench.py` — oscilloscope commissioning/bench tool

Defaults to `ScopeSim`; `--real` requires a VISA resource string (`--resource` or `CCID_SCOPE_RESOURCE`, matching `ccid.main`'s own env var). Every operation goes through the public `ScopeInterface` contract — "the same ones the sequencer uses, so bench results reflect what the sequencer will actually see," never a raw SCPI command issued directly by this tool.

One documented limitation worth knowing: `capture_after_acquire()` bundles the waveform, preamble, and PNG transfer into a single call *by contract* (HAL doc §1) — this tool times that call as one unit and reports total elapsed time plus each artifact's byte size, but cannot separately report how much of that time was BYTE-transfer vs. PNG-transfer, since splitting them would mean extending `ScopeInterface` itself, which this tool deliberately doesn't do.

`query_memory_depth` reads back `waveform_points_mode`/`waveform_points` through `readback_settings()` rather than issuing a separate `:ACQuire:POINts?`-style query — same reasoning: the interface exposes domain settings, not raw SCPI passthrough, and this tool stays inside that boundary.

`save_and_validate_capture` writes a bench capture in exactly the same `.npz` container format (`samples.bin` + `preamble.json`, zip) that `RunRecorder`/`analysis.py` use, then round-trips it through the real `load_waveform` to confirm it's actually readable (`sample_count > 0`, `sample_interval_s > 0`) — a genuine end-to-end validation of the capture pipeline, not just "the bytes were written."

### Subcommands
`identify` (`*IDN?`), `configure` (apply default `ScopeSettings`, report applied-vs-readback), `arm-check` (issue `:SINGle`, poll until armed or timeout), `memory-depth`, `capture-bench` (full arm→acquire→capture→save→validate pipeline, timed).

---

## 6. Common patterns worth naming once

- **Default-to-safe, explicit opt-in for hardware.** Every tool that could touch real hardware requires an explicit flag (`gpio_selftest`'s double-flag requirement is the strictest version of this; `scope_bench`/`simulate` at minimum require passing `--real` or, for `simulate`, is simply incapable of it at all).
- **JSON-on-stdout as the interface.** Every subcommand's `cmd_*` function ends in `print(json.dumps(report, sort_keys=True))` — this is what makes these tools scriptable/diffable rather than requiring a human to read prose output, and it's exactly the pattern the earlier code review's tool-output excerpts (e.g. `{"cycles_completed": 3, "opening_order_safe": true, ...}`) came from.
- **Argparse subcommand dispatch**: every tool's `main()` is the same three lines — parse args, configure logging, call `args.func(args)` — with each subparser's `set_defaults(func=cmd_whatever)` doing the routing. Consistent enough across all five files that knowing the pattern in one tells you how to read any of the others.
- **No tool reimplements domain logic.** Worth restating as the unifying principle: `simulate.py` uses the real `Sequencer`; `replay_waveform.py` uses the real `analyze_waveform_file`; `calibrate_camera.py` uses the real `LedClassifier`/`rgb_to_hsv`; `gpio_selftest.py` uses the real `ContactorInterface` implementations; `scope_bench.py` uses the real `ScopeInterface` implementations. If a tool's report ever disagrees with what the real system does, that's a bug in the tool's wiring, not an expected divergence.

---

## 7. Test coverage map

| Tool | Test(s) |
|---|---|
| `simulate.py`: campaign completion, fault-halt reporting, opening-order-safe check, crash-resume (both real recovery and injection-didn't-fire failure reporting), sticky-halt-check, `CrashInjector` firing exactly once at the right cycle/checkpoint, `--gpio-fail` spec parsing | `test_tools_simulate.py::SimulateToolTests` (10 tests) |
| `replay_waveform.py`: single-waveform replay matches the original inline result, full-run replay without touching originals, single-cycle replay, algorithm-version override recorded correctly, unsupported version rejected, conflicting range options rejected, both CLI subcommands end-to-end, missing-waveform-for-cycle skipped not fatal | `test_tools_replay_waveform.py::ReplayWaveformToolTests` (9 tests, plus the version-override test covered earlier in the analysis doc's versioning discussion) |
| `calibrate_camera.py`: ROI parsing/round-trip/fallback preference, hue-range recovery for a known default band and for all colors, empty-input rejection, temporal-classification match and mismatch reporting, replay-footage round-trip through real `CameraSim`, missing-dependency/path handling | `test_tools_calibrate_camera.py::CalibrateCameraToolTests` (12 tests) |
| `gpio_selftest.py`: pin-table correctness, sim-vs-real contactor construction, K1 exercise ends de-energized, K3 exercise auto-closes K1/K2 prerequisites first, invalid pulse/duration rejection, mismatch probe at zero and positive stagger, all three CLI subcommands, real-without-acknowledgement refusal | `test_tools_gpio_selftest.py::GpioSelftestToolTests` (12 tests) |
| `scope_bench.py`: sim-default construction, real-without-resource refusal, identify/configure/arm-polling/memory-depth reporting, capture-and-validate round trip, all CLI subcommands including real-without-resource refusal | `test_tools_scope_bench.py::ScopeBenchToolTests` (12 tests) |

---

## 8. Things to know if you're about to change one of these

- **Don't let a tool grow its own copy of logic that belongs in `ccid/`.** If you find yourself writing HSV math, a trip-time calculation, or a contactor interlock check inside a `tools/*.py` file, that logic almost certainly belongs in `ccid/classify.py`, `ccid/analysis.py`, or the HAL layer instead, with the tool just calling it — this is the property that keeps bench/calibration results trustworthy as a preview of real sequencer behavior.
- If you add a new commissioning operation that can touch real hardware, follow `gpio_selftest.py`'s pattern (explicit, named acknowledgement flag) rather than a bare `--real` switch alone — the double-flag design is deliberate friction against an accidental real-hardware run.
- `replay_waveform.py`'s "never overwrite original data, always a fresh `reanalysis/<id>/` directory" rule (§2) is safety-relevant in the same way `recorder.py`'s crash-safe contract is — any change to its output-path logic should preserve that property explicitly, not just incidentally.
