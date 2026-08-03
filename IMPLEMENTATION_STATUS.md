# IMPLEMENTATION STATUS

## Current phase
- Phase 2: HAL contracts (implemented).

## Implemented in this phase
- Domain enums/value objects in [states.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/states.py)
- Exception taxonomy in [errors.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/errors.py)
- UTC + monotonic helpers in [clock.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/clock.py)
- Strict config loading/validation/canonical hash in [config.py](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/ccid/config.py)
- Example locked config in [config.yaml](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/config.yaml)
- Phase 1 tests in [tests/](C:/Users/shrirama/OneDrive - Legrand France/Desktop/EE_InternFiles/ccid_automation/tests)

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
- Passing: `python -m unittest discover -s tests -p 'test_*.py'` (14 tests)
- Failing: none in this phase

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

## Remaining work after Phase 2
- Phase 3+ (simulation-first safety layer, scope/camera simulators, recorder/resume, sequencer, real HALs, deployment)
