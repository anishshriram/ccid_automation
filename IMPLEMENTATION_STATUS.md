# IMPLEMENTATION STATUS

## Current phase
- Phase 6: Vision classification (implemented).

## Phase 1-6 Implementation Summary
- Phase 1: Domain model, errors, clock, config with locked defaults
- Phase 2: HAL base contracts and protocol tests
- Phase 3: GPIO simulator, SafeOff aggregation, safety layer tests
- Phase 4: Deterministic scope and camera simulators with fault branches
- Phase 5: Recorder/resume with crash-safe commit order and orphan cleanup
- Phase 6: HSV LED classification, temporal window, and charging-gate polling

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
- Passing: `python -m unittest discover -s tests -p 'test_*.py'` (83 tests)
- Passing: `python -m unittest discover -s tests -p 'test_classify.py'` (45 tests)
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
- Phase 8+: Sequencer state machine (including the three-way vision-timeout branch and the
  extended-cooldown retry path), real HALs, deployment
