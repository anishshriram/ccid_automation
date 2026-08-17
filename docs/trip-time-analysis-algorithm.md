# Trip-Time Analysis Algorithm

**Source files:** `ccid/analysis.py` (1278 lines), `ccid/forced_diagnostic_analysis.py` (199 lines)
**Tests:** `tests/test_analysis.py` (756 lines, 12 test classes), `tests/test_forced_diagnostic_analysis.py`

This is where a raw voltage waveform becomes a trip-time number and a PASS/FAIL/NO_TRIP verdict. It is the most numerically dense file in the codebase, and the one this session's real bug hunting (cycles 1 and 17 of the 25-cycle campaign) lived in. Everything here is a pure function of its inputs — no hardware calls, no sleeps, nothing time-dependent beyond the waveform's own sample clock.

---

## 1. The central design decision: a *versioned boundary*, not a final algorithm

Straight from the module docstring: "the trip-time *algorithm* is deliberately deferred ('capture first, compute later'). This module is therefore a **versioned boundary**... `AnalysisVersion` tags every result so a later algorithm can be swapped in and replayed offline (`tools/replay_waveform.py`) without any change to the stored data format or to the `cycles.csv` schema."

Concretely, this means: the raw waveform (`.npz`) is the permanent record. The *number* computed from it (`trip_time_s`, `verdict`) is allowed to change if the algorithm that computes it changes — as long as that change is tagged with a new `AnalysisVersion` member and the old version's behavior is left completely alone. This project has already exercised this exactly as designed: `AnalysisVersion.V1 → V2 → V3` are three real, working algorithm versions, all still present in the code, all still independently replayable, and this document explains all three because you'll see all three in real data.

**The rule, stated explicitly in `V1_ENDPOINT_DEFINITION`'s own text:** *"Supersede only by re-versioning `AnalysisVersion`; never by editing in place."* When the v2→v3 onset-refinement bug was fixed this session, the fix did **not** touch what `V2` computes — it added a new `V3` that computes something different, while `V2`'s exact original (bug included) behavior was deliberately preserved and is locked in by a regression test (`test_historical_v2_remains_replayable_with_its_known_onset_defect`).

---

## 2. Core data types

### `Waveform` (frozen dataclass)
```python
samples_v: np.ndarray        # scaled volts, one per sample
sample_interval_s: float     # dt
first_sample_time_s: float   # time of sample[0], usually negative (pre-trigger)
preamble: Mapping[str, Any]  # the raw scope preamble, kept for reference
```
Two convenience methods: `time_of_index(i) = first_sample_time_s + i * sample_interval_s`, and its inverse `index_of_time(t)` (rounds to nearest sample, clamped into `[0, size-1]`). Properties `sample_rate_hz` (`1/dt`) and `duration_s` (`size * dt`).

### `TripResult` (frozen dataclass)
```python
trip_time_s: float | None       # None means "no collapse detected" (NO_TRIP or record too short)
verdict: Verdict                # PASS / FAIL / NO_TRIP
sanity_checks: Mapping[str, bool]
notes: str                      # semicolon-joined "key=value" diagnostic string
algorithm_version: AnalysisVersion
```
`trip_time_s` and `verdict` are stored **separately**, deliberately — the module docstring notes this is so "verdicts stay re-derivable from `cycles.csv` alone if the pass limit or the endpoint definition changes" (you can recompute PASS/FAIL from a stored `trip_time_s` without re-touching the waveform, as long as you know which limits were in force). `failed_sanity_checks` and `sanity_ok` are derived properties. `to_dict()`/`from_dict()` round-trip through JSON (used for both `cycles.csv` and the per-cycle sidecar) and `from_dict` raises `WaveformFormatError` on anything malformed — missing keys, bad enum values, non-mapping `sanity_checks` — rather than silently accepting corrupt data.

### `AnalysisConfig` (frozen dataclass) — every threshold, in one place

| Field | Default | Meaning |
|---|---|---|
| `pass_limit_s` | 0.02497 | ≤ this → PASS |
| `no_trip_limit_s` | 0.100 | ≥ this (minus tolerance) → NO_TRIP |
| `line_frequency_hz` | 60.0 | Drives `mains_period_s` |
| `endpoint_definition` | `V3_ENDPOINT_DEFINITION` | Human-readable text, frozen into the config hash |
| `algorithm_version` | `AnalysisVersion.V3` | Which code path runs |
| `envelope_window_cycles` | 0.5 | Envelope window, as a fraction of one mains cycle |
| `envelope_on_fraction` | 0.25 | Burst is "on" above `on_fraction × reference_amplitude` |
| `envelope_off_fraction` | 0.10 | Burst is "collapsed" below `off_fraction × reference_amplitude` |
| `noise_floor_v` | 0.5 | Absolute floor — an all-zero/noise-only record can't synthesize a burst from nothing |
| `collapse_persistence_cycles` | 1.0 | Envelope must stay collapsed this long before a collapse is accepted |
| `signal_present_rms_v` | 1.0 | Below this RMS post-t0 → "no signal captured" |
| `pretrigger_leakage_rms_v` | 1.0 | Absolute floor for the pre-trigger leakage check |
| `burst_start_tolerance_s` | 0.020 | How far the detected onset may sit from t0 before `burst_starts_near_t0` fails |
| `pretrigger_leakage_guard_cycles` | 1.0 | How far before t0 the leakage check looks |
| `residual_floor_noise_multiple` | 5.0 | Residual floor = `noise_sigma × this` (for endpoint refinement) |
| `noise_collapse_multiple` | 6.0 | Collapse threshold is also held this many σ above estimated noise |
| `endpoint_uncertainty_s` | 0.0005 | Fail-safe margin applied only at the no-trip boundary |

`__post_init__` validates every field: all the "positive" fields must be finite and `> 0`; `burst_start_tolerance_s`/`endpoint_uncertainty_s` must be finite and `>= 0`; the two envelope fractions must be in `(0, 1)` with `off < on`; `pass_limit_s < no_trip_limit_s`; `endpoint_definition` non-empty. Two derived properties: `mains_period_s = 1/line_frequency_hz`, `envelope_window_s = envelope_window_cycles × mains_period_s`, `collapse_persistence_s = collapse_persistence_cycles × mains_period_s`.

`resolve_analysis_config(config)` is deliberately duck-typed (accepts `None`, an `AnalysisConfig`, an `AppConfig`-like object with `.analysis`/`.timing`, or a plain `Mapping`) specifically to avoid an import cycle with `ccid.config` — that module builds `AnalysisConfig` from `config.yaml`'s `analysis:` section, and this module can't import it back.

---

## 3. Loading a waveform — `load_waveform`

Two accepted container layouts, both zip-based (`_read_container`):

1. **The recorder's bundle**: `samples.bin` (raw scope BYTE codes, `uint8`) + `preamble.json`. This is what `Sequencer._pack_waveform_blob` actually produces and what real cycles are analyzed from.
2. **A numpy `.npz`** with a `samples` array plus a `preamble` (or `preamble_json`) entry — JSON text by default, or a pickled mapping only if the caller explicitly passes `allow_pickle=True` (refused otherwise, with a clear error pointing at the fix — this is a deliberate security/correctness boundary, not an oversight).

**Scaling** (`_scale_samples`): floating-point samples are assumed to already be volts. Integer samples are converted via the scope's own preamble formula: `(raw - y_reference) * y_increment + y_origin`. Missing any of those three preamble fields on integer data raises `WaveformFormatError` — "without them the samples are meaningless numbers" is not an exaggeration, there is no fallback.

**Time base** (`_resolve_sample_interval` / `_resolve_first_sample_time`): `sample_interval_s` comes from `x_increment`, or `1/sample_rate_hz` if that's what's present instead — one of the two is required, or loading fails. `first_sample_time_s` comes from `x_origin` if present, else `-pretrigger_s`, else `-pretrigger_samples * dt`, else `0.0` (in that precedence order) — this is what lets both real scope preambles (`x_origin`) and synthetic test waveforms (`pretrigger_s`) work through the same loader.

---

## 4. The algorithm itself — `analyze_samples`, step by step

This is the function everything else calls into (`analyze_waveform` and `analyze_waveform_file` are thin wrappers that resolve config/load the waveform and then call this). Version gate first: only `V1`, `V2`, `V3` are implemented; anything else raises `NotImplementedError` immediately (this is what makes "editing in place" structurally awkward to do by accident — a config asking for a version with no matching branch just fails loudly).

### 4.1 Envelopes and thresholds

```python
magnitude = |samples|
window = envelope_window_samples(dt, config)          # round(envelope_window_s / dt)
envelope_end   = sliding_max(magnitude, window, align="leading")   # forward-looking
envelope_start = sliding_max(magnitude, window, align="trailing")  # backward-looking
ref_amplitude = reference_amplitude(magnitude, window)
noise_sigma   = _noise_sigma(magnitude, window)
```

**`sliding_max`** (§5.1) computes a windowed max in O(n) regardless of window size (van Herk/Gil-Werman). `align="leading"` at index `i` looks at `[i, i+window)` — a real ongoing burst keeps this high right up until one sample after it truly ends, which is exactly why it's used for collapse detection. `align="trailing"` at index `i` looks at `(i-window, i]` — it rises on the *first* sample of a burst, which is why it's used for onset detection. These two envelopes are not interchangeable; using the wrong one for the wrong purpose is precisely the class of bug this file has already had (§6).

**`reference_amplitude`** — the median of the top ~5% of `|v|` across the whole record. Not a plain max (one noise spike would inflate it) and not a percentile over the *whole* record (which would collapse toward zero on a record that's mostly post-trip silence). Sized from the half-cycle window specifically so the estimate doesn't depend on record length or burst length.

**`_noise_sigma`** — 10th percentile of half-cycle-block RMS. The idea: a silent block reads the noise; a conducting block reads roughly `0.7×` burst amplitude. Taking a low percentile recovers the noise level without needing to already know where the record is quiet.

Three thresholds derive from those two numbers:
```python
off_threshold = max(noise_floor_v, min(0.5*ref_amplitude, max(off_fraction*ref_amplitude, noise_collapse_multiple*noise_sigma)))
on_threshold  = max(on_fraction*ref_amplitude, 1.25*off_threshold)
residual_floor = min(off_threshold, max(noise_floor_v, residual_floor_noise_multiple*noise_sigma))
```
The `noise_collapse_multiple*noise_sigma` term in `off_threshold` exists specifically so a noisy record still collapses cleanly instead of never quite dropping below a fixed fraction of amplitude.

### 4.2 Onset detection — `_find_burst_start` then `_refine_start_index`

Two-stage, and this is the part that changed between V1/V2 and V3.

**Stage 1, `_find_burst_start`** (identical across all versions): finds the first sample where the *trailing* envelope crosses `on_threshold` **and** a leading rolling mean (`_rolling_mean_leading`, a plain forward moving average, not a sliding max) is at least `0.25 × on_threshold` — the second condition exists to reject an isolated noise spike, which "cannot carry a half cycle worth of energy." This is the coarse, high-confidence onset — call it `burst_index`.

**Stage 2, `_refine_start_index`**: tries to recover genuine sub-threshold conduction that started *before* `burst_index` (real current ramping up near a zero-crossing, below the confident `on_threshold` but still real). Uses the *leading* envelope at the much lower `residual_floor`:

```python
below = indices where envelope_lead[:burst_index+1] < residual_floor
candidate = min(burst_index, below[-1] + window)
```

Why `below[-1] + window` finds a real crossing: `envelope_lead[i] < residual_floor` means *every* sample in `[i, i+window)` is quiet. The moment that stops being true (`envelope_lead[i+1] >= residual_floor`), the *only* new sample entering the window is `i+window` itself — so that index is guaranteed to be the first raw sample at or above `residual_floor` after the last confirmed-quiet stretch. This is exact arithmetic, not a heuristic guess.

**V1** returns `candidate` directly if any quiet window was found; if none was found at all (`below.size == 0`), V1 assumes conduction began at sample 0.

**V2** returns `candidate` directly too (same formula as V1 in the "found a quiet window" case), but if no quiet window was found, V2 does *not* assume sample 0 — a forward-looking envelope with no confirmed quiet window can't prove conduction existed at the very first sample, so it returns `burst_index` unchanged instead. **This is the version with the real bug** (§6).

**V3** takes `candidate` and then *confirms* it against raw samples before trusting it:
```python
confirmation = round(2e-6 / dt)   # ~2 microseconds, hardcoded module constant, not config
confirmed = _first_sustained_low(magnitude >= residual_floor, confirmation, start=candidate, limit=burst_index)
return burst_index if confirmed is None else confirmed
```
It reuses `_first_sustained_low` (§5.2, normally used for collapse detection) to demand that the raw magnitude actually stay above `residual_floor` for a short sustained run starting at `candidate`, not just cross it once. If nothing satisfies that within `[candidate, burst_index]`, it falls back to the already-trustworthy `burst_index` rather than trusting an unconfirmed candidate.

### 4.3 Resolving t0 — `_resolve_t0`

Strict precedence, same across all versions:
1. `injection_time_s` passed explicitly to `analyze_waveform` (from a per-cycle sidecar) — **not currently wired up**; `Sequencer` never supplies this, so every real cycle analyzed so far falls through past this.
2. `k3_close_time_s` or `injection_time_s` found in the waveform's own preamble (`_injection_time_from_preamble`) — also not currently populated by the real capture pipeline.
3. The detected onset from §4.2 (`t0_source = "detected_onset"`) — **this is the path every real cycle actually uses today.**
4. If no onset was even found: `t0 = 0.0`, `t0_source = "trigger"`.

The trigger instant is deliberately *never* assumed to be t=0 on its own — the comment is explicit: "the +20V trigger level can fire up to a half mains cycle after injection."

### 4.4 Sanity checks computed before the collapse search

- **`signal_present`** — `rms(magnitude[i0:]) > signal_present_rms_v`. False means nothing was captured at all.
- **`no_pretrigger_leakage`** (`_pretrigger_leakage_ok`) — two-part check for K3 stuck closed. Part one: RMS of everything before `t0 - guard_cycles*mains_period` must be below a threshold that scales with burst amplitude (`max(pretrigger_leakage_rms_v, 0.10*ref_amplitude)`) — probe noise on a 120V signal shouldn't false-positive, but genuine leakage at burst-comparable amplitude must be caught. Part two (V2/V3 only): if the record has real pre-trigger depth, inspect the raw samples right at the record boundary directly (never the leading envelope, which could make a *later* burst look like it was present at sample 0) for a quarter-cycle window. **This is the one sanity check that can actually halt a cycle** (`Sequencer` checks it explicitly and raises `k3_pretrigger_current_detected` — see the sequencer doc §6) — every other sanity check here is logged-only.
- **`record_spans_no_trip_limit`** — `(last_sample_time - t0) >= no_trip_limit_s` (with epsilon tolerance). Also directly halt-worthy in the sequencer (`scope_record_too_short_for_no_trip_window`) — if the record isn't even long enough to conclusively rule out a no-trip, the rig can't trust its own answer.

### 4.5 Collapse and endpoint — the trip-time itself

If an onset was found: `burst_start_s = time_of_index(onset_index)`, and `burst_starts_near_t0` checks `delay = burst_start_s - t0` is within `[-ε, burst_start_tolerance_s + ε]` (0 to 20ms). Then `_first_sustained_low(envelope_end < off_threshold, persistence, start=burst_start_index, limit=valid_end)` looks for the first point the *leading* envelope stays below `off_threshold` for a full `collapse_persistence_s` (one mains cycle, 16.67ms at 60Hz) — this persistence requirement is exactly what prevents an ordinary AC zero-crossing mid-burst from being misread as the trip (the "8.33ms half-cycle trap," covered by `HalfCycleTrapTests`).

If a collapse index was found, `_refine_end_index` walks it back to the last raw sample `>= off_threshold` (the envelope crossing under-reports by up to `asin(off_fraction)/(2π·f_line)` near a current zero — a few hundred µs at typical noise levels, per the module docstring's known-bias note), then forward again up to a quarter mains cycle to catch the sub-threshold residual tail down to `residual_floor`. `trip_time_s = max(0.0, t_end - t0)`.

`collapse_is_clean` is then checked: does the envelope rise back above `on_threshold` anywhere between the collapse point and the end of the valid record? If so, the reported collapse wasn't really the end of conduction — this is exactly the check that caught cycle 1's bogus 0ms result (the real burst hadn't happened yet) even though it couldn't veto the number itself.

If **no** collapse index was found (burst never ends within the record): `trip_time_s` stays `None`, and instead `check_no_trip_persistent` verifies the envelope was above `on_threshold` for at least 95% of the window between onset and the record's valid end — a genuine no-trip should look like continuous conduction, not something intermittent.

### 4.6 Verdict — `_decide`

```
if trip_time_s is None: NO_TRIP ("no signal captured" or "no envelope collapse within the record")
elif trip_time_s >= no_trip_limit_s - endpoint_uncertainty_s - dt/2: NO_TRIP
elif trip_time_s <= pass_limit_s + dt/2: PASS
else: FAIL
```
`endpoint_uncertainty_s` (0.5ms) only applies at the no-trip boundary, and only in the fail-safe direction — a trip measured within that margin of 100ms is treated as NO_TRIP rather than risk under-reporting a real no-trip as a slow FAIL. It is deliberately **not** applied at the pass limit, where the strict 24.97ms table value stands unadjusted. The comment in `_decide` is blunt about this: **"Sanity results are never consulted here."** Every sanity check computed above is recorded, logged as a warning if any failed, and attached to `notes["sanity_failed"]` — but none of it changes `verdict` or `trip_time_s`. This is a deliberate, explicit design choice restated in three separate places in the file (module docstring, `_decide`'s docstring, and inline before the `TripResult` is returned): *"Recording both the number and the doubts about it is the entire point."*

---

## 5. Reusable numeric primitives

### 5.1 `sliding_max(values, window, align)`

O(n) sliding-window maximum via the van Herk/Gil-Werman block-max algorithm: split into blocks of `window`, compute a forward-running max (`prefix`) and backward-running max (`suffix`) within each block, then the max over any window is `max(suffix[i], prefix[i+window-1])`. `align="trailing"` is implemented by reversing the array, running the leading algorithm, and reversing the result back. Handles `window <= 1` (identity), `window > array.size` (clamped), and empty arrays as edge cases. Verified against a brute-force `O(n·window)` reference in `test_matches_a_brute_force_sliding_maximum`.

### 5.2 `_first_sustained_low(below, persistence, start, limit)`

Generic, reusable helper (despite the "low" name, it works on any boolean array — it's what `_refine_start_index`'s V3 path reuses for "sustained high" by simply not inverting its input): finds the first index in `[start, limit]` where `below` is `True` for `persistence` consecutive samples, using a cumulative-sum trick (`cumsum` of the *inverted* array; a zero-count window means every sample in it was `True`) rather than a Python loop — this is what makes it fast enough to run on a 1,000,000-sample record.

### 5.3 `rms`, `check_signal_present`, `check_no_pretrigger_leakage`, `check_no_trip_persistent`

Small, directly-testable primitives, each with its own dedicated tests (`EnvelopeTests.test_rms_helper`, `SanityCheckTests.test_signal_present_helper_thresholds_on_rms`, `test_leakage_helper_scales_with_the_burst_amplitude`) independent of the full `analyze_samples` pipeline.

---

## 6. The V1 → V2 → V3 story, precisely

This is worth having in one place since it's scattered across code comments, `SCOPE_TRIGGER_DEBUG_LOG.md`, and this session's conversation.

- **V1**: original algorithm. On a record with no confirmed-quiet leading-envelope window at all, assumes conduction began at sample 0 — a historical behavior kept exactly as-is for replay.
- **V2**: refined the "no quiet window found" case (can't prove conduction at sample 0 just because the forward-looking envelope never dropped quiet), and added the raw-sample pre-trigger-leakage check. **Defect**: `_refine_start_index`'s `below[-1] + window` formula is exact arithmetic for "first raw sample crossing `residual_floor`" — but nothing confirms that sample represents *real, sustained* conduction rather than a single noisy or ADC-quantization-limited sample. On real hardware (8-bit BYTE captures, ~2V per count), an isolated sample crossing the low `residual_floor` by pure quantization noise was enough to drag the refined onset back to an unrelated point in the pre-trigger buffer.
  - **Cycle 1** of the real 25-cycle campaign: the refined onset landed ~123ms before the true burst (at ~-4.24ms), because low-level noise crossed `residual_floor` almost continuously across most of the 250ms pre-trigger window. The resulting collapse search found essentially zero duration → `trip_time_s = 0.0`, `verdict = PASS`, with `collapse_is_clean = False` correctly flagging it as suspect (but not vetoing it — §4.6).
  - **Cycle 17**: a chain of a few isolated near-floor samples dragged the onset back ~13ms (to -16.85ms vs a true onset around -3.68ms), inflating `trip_time_s` from a real ~20.2ms (PASS) to a reported 33.36ms (**FAIL** — this one flipped a verdict, not just a number).
- **V3**: adds the raw-sample confirmation step described in §4.2. Verified against both real cycles (replaying the actual campaign `.npz` files under v3 recovers ~20.7ms/PASS for cycle 1 and ~20.2ms/PASS for cycle 17) and a synthetic regression test built specifically to reproduce the mechanism (`OnsetNoiseRobustnessTests.test_scattered_pretrigger_noise_blips_do_not_drag_the_onset_backward` — scattered single-sample blips spaced closer than one envelope window, chaining their forward-shadows together exactly like the real data did).

`CURRENT_ANALYSIS_VERSION = V3` and `config.yaml`'s `analysis.algorithm_version: v3` is what makes this the default for every cycle going forward. Cycles already recorded under `v2` are untouched and will keep reproducing their original (sometimes wrong) numbers unless explicitly replayed under `v3` via `tools/replay_waveform.py --algorithm-version v3`.

---

## 7. `forced_diagnostic_analysis.py` — a deliberately separate module

This is **not** part of the versioned trip-time algorithm and does not participate in `AnalysisVersion` at all. It exists to describe a *forced* capture (`:TRIGger:FORCe` — see the sequencer doc §5.7.2) — a scope acquisition that did not come from a real trigger, so it can never represent a real measurement.

Why it's a separate module rather than a mode of `analyze_waveform`: the module docstring is explicit — "mirroring `ccid.analysis`'s low-level numeric primitives (`load_waveform`, `rms`) but never its verdict-producing functions. Callers must never feed this module's output into `analyze_waveform`, a `Verdict`, `cycles.csv`, or any PASS/FAIL decision." Keeping it in a different module with a different return type (`ForcedDiagnosticWaveformSummary`, not `TripResult` — no `verdict` field exists on it at all) makes it structurally impossible to accidentally pipe a forced capture into a verdict decision.

It also deliberately never touches any Pi-side monotonic timestamp — a real defect this session found and fixed (`SCOPE_TRIGGER_DEBUG_LOG.md` Entry 13): the Pi's `time.monotonic()` clock and the scope's own internal waveform timebase are not synchronized by anything in this system, so an earlier attempt to say "the burst occurred 86ms before K3 close" by comparing a Pi timestamp to a scope-timebase offset was unsupported. Every number `analyze_forced_diagnostic_waveform` produces comes from `load_waveform`'s scope-preamble-derived time base alone (`x_increment`/`x_origin`), which is self-consistent on its own.

**What it computes**, all via `_first_sustained_run_start` (a cumulative-sum "first sustained run" finder, conceptually the collapse-detection counterpart to §5.2 but operating directly on raw magnitude rather than an envelope, since there's no need here to bridge AC zero-crossings — this is describing one burst shape, not measuring a duration against a locked verdict table):

- `min_v`/`max_v`/`rms_v` — plain extrema and RMS over the whole record.
- `sustained_onset_s` — first point `|v|` exceeds `max(|positive_threshold|, |negative_threshold|)` (default ±20V) for at least `sustained_duration_s` (0.5ms).
- `quiet_baseline_rms_v`/`quiet_baseline_duration_s` — RMS and duration of everything before that onset.
- `collapse_s` — first point after onset where `|v|` drops and stays below `collapse_threshold_v` (default 5V) for `collapse_sustained_s` (1ms).
- `burst_duration_s` — `collapse_s - sustained_onset_s`, if both exist.
- `positive_crossing_count`/`first_positive_crossing_s` and the negative equivalents — via `_threshold_crossings`, which counts rising edges of a boolean mask (`np.diff(mask) == 1`, plus a check for the mask already being `True` at sample 0).

Every field's own docstring/dataclass comment repeats the same warning: purely descriptive, never a measurement, never used for PASS/FAIL. `to_dict()` is what `Sequencer._capture_forced_diagnostic_best_effort` calls to attach this alongside the raw waveform in the `diagnostics/` artifact tree — never `waveforms/`.

---

## 8. Test coverage map

| Behavior | Test class / test(s) |
|---|---|
| `sliding_max` correctness (both alignments, edge cases, brute-force cross-check) | `SlidingMaxTests` |
| Envelope bridges AC zero-crossings within a burst; collapses cleanly after | `EnvelopeTests` |
| Verdict table boundaries (pass limit, no-trip limit, monotonic across the band) | `VerdictBoundaryTests` |
| The half-cycle trap specifically (naive detector vs envelope-based) | `HalfCycleTrapTests` |
| Every sanity check, individually and in combination, never veto verdict | `SanityCheckTests` |
| Probe noise doesn't change verdict; no-signal / no-trip-with-noise edge cases; full 1M-sample record at scale | `NoiseAndEdgeCaseTests` |
| **The V2 onset-refinement defect and the V3 fix** (this session's core finding) | `OnsetNoiseRobustnessTests` |
| t0 precedence (sidecar > preamble > detected onset > trigger); time base recovery from preamble | `TimeBaseTests` |
| Container format handling, malformed/missing preamble, pickled-preamble opt-in | `WaveformFormatTests` |
| Version tagging, replay of historical V1 **and V2** (bug preserved), rejection of unimplemented versions, malformed sidecar rejection | `VersioningTests` |
| `TripResult` round-tripping, verdict re-derivability | `RecordedResultTests` |
| `AnalysisConfig` validation, config-resolution duck-typing, config-hash freezing | `AnalysisConfigTests` |
| Forced-diagnostic summary: burst identification, quiet-record edge case, explicit non-use-for-verdict guard | `tests/test_forced_diagnostic_analysis.py::ForcedDiagnosticAnalysisTests` |

---

## 9. Things to know if you're about to change this file

- **Never edit `V1_ENDPOINT_DEFINITION`, `V2_ENDPOINT_DEFINITION`, or the V1/V2 code branches.** Any algorithm change goes into a new `AnalysisVersion` member, a new `_ENDPOINT_DEFINITION` constant, and `CURRENT_ANALYSIS_VERSION`/`DEFAULT_ENDPOINT_DEFINITION` get bumped — see `tools/replay_waveform.py`'s `_ENDPOINT_DEFINITION_BY_VERSION` mapping, which must also get a new entry or a version override will silently carry the wrong description text.
- `config.yaml`'s `analysis.algorithm_version` and `analysis.endpoint_definition` are part of the config hash — changing either changes what a fresh campaign's config hash resolves to, by design.
- If you add a new sanity check, decide deliberately whether it should be logged-only (the default — see §4.6) or should also gate a `Sequencer`-level halt like `no_pretrigger_leakage`/`record_spans_no_trip_limit` do. Don't let a new check silently veto the verdict inside `_decide` — that's the one thing this module goes out of its way to never do.
- Anything computed from `envelope_lead`/`envelope_end` (forward-looking) can "see" a future burst before it happens; anything computed from `envelope_start`/`envelope_trailing` (backward-looking) cannot. Getting these backwards is exactly how the V2 defect happened — know which one you're using and why.
