# IMPLEMENTATION STATUS

## Current phase
- Phase 10: CLI, lifecycle, monitoring, and deployment (implemented).

## Phase 1-10 Implementation Summary
- Phase 1: Domain model, errors, clock, config with locked defaults
- Phase 2: HAL base contracts and protocol tests
- Phase 3: GPIO simulator, SafeOff aggregation, safety layer tests
- Phase 4: Deterministic scope and camera simulators with fault branches
- Phase 5: Recorder/resume with crash-safe commit order and orphan cleanup
- Phase 6: HSV LED classification, temporal window, and charging-gate polling
- Phase 7: Versioned analysis boundary, burst-envelope trip time, sanity checks
- Phase 8: Explicit sequencer state machine with retry/degrade/halt orchestration
- Phase 9: Real GPIO/scope/camera HAL modules with hardware-guarded tests
- Phase 10: CLI entry point, signal-safe lifecycle, status commands, watchdog/notification wiring, and deployment assets

## Locked values set
- `gpio_k1=17`, `gpio_k2=27`, `gpio_k3=22`
- `cooldown_s=10`
- `cooldown_retry_s=60`
- `boot_timeout_s=90`
- `scope_arm_timeout_s=2.0` (explicitly confirmed)
- `scope_acquisition_timeout_s=5`
- `k3_backstop_s=0.300`
- `pass_limit_s=0.02497`
- `no_trip_limit_s=0.100`
- `heartbeat_grace_s=300`
- `mains_stagger_ms=0`

## Validation rules enforced
- Reject reused GPIO numbers
- Reject invalid/negative timeout and timing values
- Reject `pass_limit_s >= no_trip_limit_s`
- Reject `k3_backstop_s <= no_trip_limit_s`
- Reject unsupported scope/camera modes
- Reject missing run/output paths
- Reject unknown keys (strict schema)

## Tests passing/failing
- Passing: `python -m unittest discover -s tests -p 'test_*.py'` (175 tests)
- Passing: `python -m unittest discover -s tests -p 'test_analysis.py'` (67 tests)
- Passing: `python -m unittest discover -s tests -p 'test_classify.py'` (45 tests)
- Passing: `python -m unittest discover -s tests -p 'test_sequencer.py'` (10 tests)
- Passing: `python -m unittest discover -s tests -p 'test_gpio_real.py'` (3 tests)
- Passing: `python -m unittest discover -s tests -p 'test_scope_real.py'` (2 tests)
- Passing: `python -m unittest discover -s tests -p 'test_camera_real.py'` (2 tests)
- Passing: `python -m unittest discover -s tests -p 'test_main.py'` (7 tests)
- Failing: none in any phase

## Hardware-dependent items not executed
- Real GPIO/scope/camera behavior not executed
- No actuator behavior implemented

## Implemented in Phase 2
- HAL base contracts in [base.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/base.py)
- HAL exports in [__init__.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/__init__.py)
- Contract-compatibility tests with fake implementations in [test_scope_protocol.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_scope_protocol.py)

## HAL contract coverage delivered
- Contactor commands and commanded-state snapshot
- Charging-gate token scoped per cycle
- Safe opening (`safe_open_all`) contract
- Scope identity/config/readback/arm/armed polling/acquisition polling/capture contract
- Timestamped camera frame and camera-health state
- Notification + heartbeat abstraction

## Implemented in Phase 3
- GPIO simulation controller in [gpio_sim.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/gpio_sim.py)
- SafeOff aggregate failure handling in [safety.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/safety.py)
- HAL exports updated in [__init__.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/__init__.py)
- Safety/interlock tests in [test_safety.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_safety.py)

## Phase 3 safety behavior covered
- K3 close blocked unless both K1 and K2 are commanded closed
- K1/K2 open blocked while K3 is commanded closed
- Single-use charging gate token per cycle for K3 close
- Deterministic command-failure injection for simulator
- Monotonic event logging for command attempts/successes
- Mains mismatch detection with bounded stagger window
- Idempotent SafeOff routine attempting K3 -> K2 -> K1 even under errors
- Aggregated SafeOff error reporting across all failed open steps

## Implemented in Phase 4
- Deterministic scope simulator in [scope_sim.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/scope_sim.py)
- Replay-or-fixture camera simulator in [camera_sim.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/camera_sim.py)
- HAL exports updated in [__init__.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/__init__.py)
- Scope simulator tests in [test_scope_sim.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_scope_sim.py)
- Camera simulator tests in [test_camera_sim.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_camera_sim.py)

## Phase 4 simulator behaviors covered
- 120 V-equivalent 60 Hz burst synthesis with configurable phase and trip/no-trip behavior
- 20 ms pre-trigger model and optional pre-trigger leakage injection
- never-triggered case (acquisition never completes)
- configurable arm/acquisition delays
- injected communication errors by operation
- truncated transfer fault mode
- invalid/missing preamble field fault modes
- configurable sample count and preamble metadata overrides
- camera replay loading from file when available
- deterministic fixture fallback explicitly marked as fixture source
- camera unavailable/failed health path

## Implemented in Phase 5
- RunRecorder with crash-safe commit ordering in [recorder.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/recorder.py)
- Extended exception taxonomy in [errors.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/errors.py) (PersistenceError, ResumeBlockedError, ConfigHashMismatchError)
- Recorder tests in [test_recorder.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_recorder.py)
- Crash-injection resume tests in [test_resume.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_resume.py)

## Phase 5 persistence behaviors covered
- Crash-safe commit ordering: artifacts (.npz, .png, .jpg, .json) → cycles.csv → atomic runstate.json → heartbeat
- Orphan reconciliation on resume (deletes orphaned artifacts, truncates CSV to last completed cycle)
- Config hash mismatch detection blocks resume with ResumeBlockedError
- Atomic runstate.json writes (temp file + fsync + os.replace)
- Heartbeat sent only after durable state achieved
- CSV schema: cycle_index, run_id, utc_timestamp, monotonic_start, trip_time_s, verdict, analysis_version, led_state_at_gate, degraded_flags, notes
- Deterministic crash injection points (before CSV flush, after CSV, before runstate)
- All 38 unit tests passing at end of Phase 5

## Implemented in Phase 6
- HSV LED classifier, temporal window, and charging gate in [classify.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/classify.py)
- Vision exception taxonomy in [errors.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/errors.py) (`VisionError`, `VisionFrameError`)
- Vision tests in [test_classify.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_classify.py) (45 tests)
- `numpy>=1.26` added to `requirements.txt` (no OpenCV dependency in the domain module)

## Phase 6 vision behaviors covered
- HSV (not RGB) classification over a single fixed ROI, with a centred-fallback ROI
- Per-frame classification to `off / blue / green / red / booting / unknown` with confidence
- Domain mapping: blue -> READY, green -> CHARGING, red -> FAULTED, multiple hues -> BOOTING,
  no reliable LED -> OFF_OR_UNKNOWN, no frame source -> CAMERA_UNAVAILABLE
- ~3 s temporal window at ~15 fps (45-frame ring buffer) classified by hue presence,
  deliberately independent of blink rate
- N=5 consecutive agreeing full-window classifications required before a state change is declared
- Partial windows never declare a state; a spurious single-frame hue cannot flip the window
- Confidence scoring from hue dominance, saturation, and value (dim/desaturated scores lower)
- Dropped frames are tolerated without resetting agreement; 15 consecutive drops mean camera failure
- Exposure variation (darkening to off, brightening) and sensor noise fixtures
- Deterministic seeded fixtures: solid, blinking, booting, exposure ramp, dropped-frame sequences
- `await_charging_gate()` returns `(success, led_state_at_timeout, degraded)` with injected clock/sleep
- Vision-gate timeout branches via `gate_timeout_action()`:
  faulted (blinking red) -> retry once with 60 s extended cooldown (`latch_slow_clear`);
  ready (blue) -> HALT, no retry; off/unknown -> HALT, no retry; stuck booting -> HALT;
  camera unavailable -> degraded fixed 60 s wait
- Vision never kills the run: camera exceptions and FAILED health are caught, logged, and
  degraded to the fixed 60 s wait with a `vision_camera_unavailable_fixed_wait` degraded flag
- All optical/temporal thresholds live in `LedOpticalConfig`; no scattered constants
- Classification never computes trip time

## Phase 6 deviation notes
- Camera-unavailable degradation returns `LedState.CAMERA_UNAVAILABLE` rather than a generic
  "unknown", so the sequencer cannot confuse it with the off/no-LED halt branch.

## Remaining work after Phase 6
- Phase 7: Analysis boundary (analysis.py) - versioned analysis interface, sanity checks,
  burst envelope extraction, boundary tests at 24.97 ms and 100 ms

## Implemented in Phase 7
- Versioned analysis boundary in [analysis.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/analysis.py)
  (`AnalysisVersion`, `Verdict`, `AnalysisConfig`, `TripResult`, `Waveform`,
  `analyze_waveform` / `analyze_waveform_file` / `analyze_samples`)
- Analysis exception taxonomy in [errors.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/errors.py)
  (`AnalysisError`, `WaveformAnalysisError`, `WaveformFormatError`)
- Optional strict `analysis:` section in [config.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/config.py)
  and [config.yaml](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/config.yaml),
  included in the canonical config hash
- Analysis tests in [test_analysis.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_analysis.py) (67 tests)
- Synthetic waveform fixtures (`synthesize_burst_samples`, `synthesize_waveform_npz`) exported for
  tests and for future `tools/replay_waveform.py`

## Phase 7 analysis behaviors covered
- `AnalysisVersion` enum (v1) recorded on every `TripResult`; a future v2 algorithm can be added
  without changing the .npz waveform format or the `cycles.csv` schema
- `TripResult(trip_time_s, verdict, sanity_checks, notes, algorithm_version)` with `to_dict()` /
  `from_dict()` so trip time and verdict are recorded separately and re-analysis stays possible
- Burst envelope extraction via an O(n) sliding-max over a half-mains-cycle window, which bridges
  AC zero crossings: the measurand is the whole burst, never a single 8.33 ms half cycle
- Endpoint refinement plus a robust `reference_amplitude` (median of the largest magnitudes) and a
  block-RMS noise estimate, so thresholds scale with the record instead of being hard-coded
- t=0 precedence: explicit sidecar `injection_time_s` -> preamble `k3_close_time_s` -> detected
  conduction onset -> trigger time; the source is written into `notes` as `t0_source=...`
  (the scope trigger is never assumed to be t=0, since +20 V can fire up to a half cycle late)
- Verdict table: `<= 24.97 ms` PASS, `24.97-100 ms` FAIL (alert, continue), `>= 100 ms` or no-trip
  NO_TRIP (`Verdict.halts_run`)
- Asymmetric `endpoint_uncertainty_s` applied only at the no-trip boundary; the pass limit stays
  strict, so endpoint ambiguity can only push a verdict toward the cautious HALT
- Six sanity checks always present in every result (`signal_present`, `no_pretrigger_leakage`,
  `record_spans_no_trip_limit`, `burst_starts_near_t0`, `collapse_is_clean`, `no_trip_persistent`);
  failures are logged and appended to notes but never veto the verdict
- Pre-trigger leakage (K3 stuck closed) detected both by pre-t0 RMS and by conduction already
  present in the first envelope sample of a record with real pre-trigger depth
- Both waveform containers loaded: numpy `.npz` (`samples` + JSON `preamble`) and the recorder's
  zip bundle (`samples.bin` BYTE codes + `preamble.json`), with integer-to-volts scaling
- Endpoint definition text is frozen in `config.yaml`, matched against `DEFAULT_ENDPOINT_DEFINITION`
  by test, and covered by the config hash, so it cannot drift mid-campaign
- Analysis is offline-only: nothing here re-analyzes at runtime

## Phase 7 deviation notes
- The trip-time algorithm remains deliberately deferred; v1 is a documented threshold/envelope stub
  measured at ~8 us of truth on clean synthetic data and biased slightly early under heavy noise.
- The endpoint definition should ultimately be taken from UL 2231-2; until that is confirmed on
  paper, `config.yaml` `analysis.endpoint_definition` is the authoritative, hash-frozen definition.
- Adding the `analysis:` section to the canonical hash changes config hashes computed before
  Phase 7; no committed hash constants existed, so no stored run is invalidated.

## Implemented in Phase 8
- Explicit state machine sequencer in [sequencer.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/sequencer.py)
  with state transition logging (`SAFE_OFF` through `COMPLETE`)
- Sequencer integration tests in [test_sequencer.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_sequencer.py)
  covering normal, retry, degraded, and halt branches

## Phase 8 sequencer behaviors covered
- Required sequence enforced: mains close -> charging gate -> scope configure/arm/poll ->
  K3 injection -> acquisition poll -> K3 open/backstop -> transfer -> analysis -> commit ->
  mains open -> cooldown
- Vision timeout branch handling:
  - red timeout -> one retry with `cooldown_retry_s`; successful recovery logs `latch_slow_clear`
  - blue timeout -> immediate HALT (no retry)
  - off/unknown timeout -> immediate HALT (no retry)
  - camera unavailable -> degraded fixed wait and continue
- Distinguishes rig and DUT terminal outcomes:
  - scope never triggered/acquisition timeout -> `RIG_FAULT`
  - pre-trigger current sanity failure -> `RIG_FAULT`
  - no-trip verdict -> `NO_TRIP` with sticky halt reason
- K3 hard backstop honored by opening K3 at `k3_backstop_s` even if acquisition completion has not yet been observed
- Every failure path de-energizes via `safe_off` and preserves K3 -> K2 -> K1 open ordering at the HAL layer
- Commits per-cycle artifacts/CSV/runstate through [recorder.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/recorder.py)
  and persists sticky halt reason when stopping

## Remaining work after Phase 8
- Phase 10: CLI/lifecycle/monitoring/deployment (`main.py`, service + udev assets)
- Phase 11: commissioning and replay tools (`gpio_selftest.py`, `scope_bench.py`,
  `calibrate_camera.py`, `simulate.py`, `replay_waveform.py`)

## Implemented in Phase 9
- Real GPIO HAL in [gpio_real.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/gpio_real.py)
  (`GpioRealContactorController`) using `gpiozero` digital outputs with the same
  K3/K1/K2 interlocks as simulation and startup-safe outputs inactive
- Real scope HAL in [scope_real.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/scope_real.py)
  (`ScopeReal`) using PyVISA-compatible transport, explicit per-cycle config, run-bit
  armed/acquisition polling, RAW BYTE waveform transfer, full preamble query, PNG capture,
  and bounded reconnect attempts
- Real camera HAL in [camera_real.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/camera_real.py)
  (`CameraReal`) with one bounded reader thread, stale/failure detection, bounded frame
  buffer, and injected LED state classification callback
- HAL exports updated in [__init__.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/__init__.py)
  to expose real + sim implementations
- Dependency pins updated in [requirements.txt](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/requirements.txt)
  (`gpiozero`, `pyvisa`, `pyvisa-py`, `pyusb`, `opencv-python-headless`)

## Phase 9 behaviors covered
- Real GPIO path enforces:
  - K3 close only when K1 and K2 are commanded closed
  - K1/K2 open blocked while K3 is commanded closed
  - single-use gate token per cycle
  - mains commanded-state mismatch detection with stagger window
- Real scope path enforces:
  - explicit cycle configuration (no front-panel inheritance)
  - `:SINGle` arming semantics
  - polling via `:OPERegister:CONDition?` Run bit (no sleep-only synchronization)
  - waveform + preamble + PNG capture bundle
  - bounded reconnect attempts before raising HAL error
- Real camera path enforces:
  - single reader thread and bounded in-memory frame queue
  - stale-frame vs failed-camera health distinction
  - camera failure represented as `LedState.CAMERA_UNAVAILABLE`
- New non-hardware tests validate logic and protocol behavior without claiming
  electrical commissioning completion

## Remaining work after Phase 9
- Phase 11: commissioning and replay tools (`gpio_selftest.py`, `scope_bench.py`,
  `calibrate_camera.py`, `simulate.py`, `replay_waveform.py`)

## Implemented in Phase 10
- CLI/lifecycle entry point in [main.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/main.py)
  with `start`, `resume`, `status`, and guarded `simulate` commands
- Config schema extended in [config.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/config.py)
  and [config.yaml](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/config.yaml)
  to include `modes.gpio_mode`
- Resume override support added to [recorder.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/recorder.py)
  via unchecked runstate read for explicit config-hash override paths
- Deployment assets:
  - systemd unit [ccid-automation.service](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/deploy/ccid-automation.service)
  - udev rule [99-keysight-usbtmc.rules](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/deploy/99-keysight-usbtmc.rules)
  - operator instructions [DEPLOYMENT.txt](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/DEPLOYMENT.txt)
- CLI/lifecycle tests in [test_main.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests/test_main.py)

## Phase 10 behaviors covered
- CLI supports:
  - starting a new run
  - resuming a named or latest run
  - explicit config-hash override on resume
  - safe status reporting without energizing hardware
  - guarded simulation/fault-injection entry path separate from production defaults
- HAL selection now includes GPIO/contactor mode alongside scope and camera mode
- SIGINT/SIGTERM handlers request a safe stop instead of abruptly abandoning the run
- SafeOff is invoked on all lifecycle exits in the CLI path
- systemd notify integration:
  - `READY=1` and `STOPPING=1`
  - watchdog pings independent of external heartbeat reachability
  - watchdog-aware sleep splits long waits into pingable chunks
- Outbound monitoring/notification behavior:
  - healthchecks heartbeat sent only through recorder commit hook after durable cycle commit
  - heartbeat fail endpoint used on terminal halt
  - ntfy + heartbeat failures are logged and never halt the campaign
- Deployment assets document venv setup, udev permissions, environment-secret injection, and service usage

## Remaining work after Phase 10
- Phase 11: commissioning and replay tools (`gpio_selftest.py`, `scope_bench.py`,
  `calibrate_camera.py`, `simulate.py`, `replay_waveform.py`)
