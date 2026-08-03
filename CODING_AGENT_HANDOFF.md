# Coding Agent Handoff

This file is for a fresh coding agent resuming implementation work in this repository.

## Authoritative sources

1. [handoff_latest.md](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/handoff_latest.md) is the locked technical/safety source.
2. [coding_instructions.txt](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/coding_instructions.txt) is the required implementation workflow.
3. [IMPLEMENTATION_STATUS.md](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/IMPLEMENTATION_STATUS.md) is the current implementation log.

## Current repository state

- Branch: `development`
- Latest commit: `9646e6a` - `add CLI lifecycle deployment assets and monitoring hooks`
- Working tree status at handoff creation: clean
- Tests passing: `python -m unittest discover -s tests -p 'test_*.py'` -> **175 passed**

## What is complete

### Phase 1
- Domain model, errors, monotonic/wall clock helpers, strict config loader
- Locked defaults captured in [config.yaml](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/config.yaml)

### Phase 2
- HAL base contracts in [ccid/hal/base.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/base.py)

### Phase 3
- Safety/interlock simulation in [ccid/hal/gpio_sim.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/gpio_sim.py)
- SafeOff logic in [ccid/safety.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/safety.py)

### Phase 4
- Scope simulator in [ccid/hal/scope_sim.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/scope_sim.py)
- Camera simulator in [ccid/hal/camera_sim.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/camera_sim.py)

### Phase 5
- Crash-safe recorder/resume semantics in [ccid/recorder.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/recorder.py)

### Phase 6
- HSV LED classifier and charging gate logic in [ccid/classify.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/classify.py)

### Phase 7
- Versioned waveform analysis boundary in [ccid/analysis.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/analysis.py)

### Phase 8
- Explicit state-machine sequencer in [ccid/sequencer.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/sequencer.py)

### Phase 9
- Real HALs:
  - [ccid/hal/gpio_real.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/gpio_real.py)
  - [ccid/hal/scope_real.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/scope_real.py)
  - [ccid/hal/camera_real.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/camera_real.py)

### Phase 10
- CLI/lifecycle entrypoint in [ccid/main.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/main.py)
- Deployment assets:
  - [deploy/ccid-automation.service](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/deploy/ccid-automation.service)
  - [deploy/99-keysight-usbtmc.rules](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/deploy/99-keysight-usbtmc.rules)
  - [DEPLOYMENT.txt](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/DEPLOYMENT.txt)

## Important locked behavior already encoded

- Contactor mapping: `K1=GPIO17`, `K2=GPIO27`, `K3=GPIO22`
- Cooldown: `10 s`
- Retry cooldown: `60 s`
- Boot timeout: `90 s`
- Scope arm timeout: `2.0 s`
- Scope acquisition timeout: `5 s`
- K3 backstop: `300 ms`
- Pass limit: `24.97 ms`
- No-trip limit: `100 ms`
- Heartbeat grace: `300 s`
- Mains stagger default: `0 ms`

## Safety-critical invariants already implemented

- K3 may not close unless both K1 and K2 are commanded closed
- K1/K2 may not open while K3 is commanded closed
- K1/K2 mismatch detection exists and is treated as a rig fault
- Failure paths use SafeOff ordering `K3 -> K2 -> K1`
- Recorder commit order is durable and crash-safe
- Heartbeat is sent only after committed durable state
- Vision failure degrades; it must not kill the run
- Analysis stores raw `trip_time_s` separately from verdict

## Current config/schema notes

- `modes` now includes:
  - `gpio_mode`
  - `scope_mode`
  - `camera_mode`
- Real scope mode requires environment variable `CCID_SCOPE_RESOURCE`
- Heartbeat URL is resolved from env var named by `monitoring.heartbeat_url_env`
- Optional ntfy URL is read from `CCID_NTFY_TOPIC_URL`

## High-signal files to read first

1. [IMPLEMENTATION_STATUS.md](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/IMPLEMENTATION_STATUS.md)
2. [ccid/main.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/main.py)
3. [ccid/sequencer.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/sequencer.py)
4. [ccid/analysis.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/analysis.py)
5. [ccid/classify.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/classify.py)
6. [ccid/recorder.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/recorder.py)
7. [ccid/hal/base.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/ccid/hal/base.py)

## Current test inventory

- [tests/test_analysis.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_analysis.py)
- [tests/test_camera_real.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_camera_real.py)
- [tests/test_camera_sim.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_camera_sim.py)
- [tests/test_classify.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_classify.py)
- [tests/test_clock.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_clock.py)
- [tests/test_config.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_config.py)
- [tests/test_errors.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_errors.py)
- [tests/test_gpio_real.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_gpio_real.py)
- [tests/test_main.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_main.py)
- [tests/test_recorder.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_recorder.py)
- [tests/test_resume.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_resume.py)
- [tests/test_safety.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_safety.py)
- [tests/test_scope_protocol.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_scope_protocol.py)
- [tests/test_scope_real.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_scope_real.py)
- [tests/test_scope_sim.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_scope_sim.py)
- [tests/test_sequencer.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_sequencer.py)
- [tests/test_states.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tests/test_states.py)

## What remains

Only **Phase 11** remains.

Phase 11 tools to implement:
- [tools/gpio_selftest.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tools)
- [tools/scope_bench.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tools)
- [tools/calibrate_camera.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tools)
- [tools/simulate.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tools)
- [tools/replay_waveform.py](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/tools)

Recommended order if budget is tight:
1. `simulate.py`
2. `replay_waveform.py`
3. `gpio_selftest.py`
4. `scope_bench.py`
5. `calibrate_camera.py`

## User preferences and workflow constraints

- Progressive commits/pushes have been used successfully.
- User does **not** want Copilot co-author attribution in commit messages.
- User wants pauses for:
  - critical design/behavior edits
  - push confirmation before publishing changes
- User may ask to tweak commit messages before push.
- Do not overstate hardware readiness: real hardware code exists, but hardware execution remains **NOT RUN - HARDWARE REQUIRED**.

## Useful recent commits

- `9646e6a` - add CLI lifecycle deployment assets and monitoring hooks
- `2e1126f` - add real hardware HAL implementations and guarded tests
- `743f949` - add sequencer state machine and integration tests
- `0f61207` - add phase 7 versioned waveform analysis boundary
- `931c035` - add phase 6 vision classification with hue-based led state detection
- `38d8c8c` - add phase 5 recorder/resume and persistence error handling

## Recommended next action

Implement Phase 11 in bounded pieces, starting with:

1. `tools/simulate.py`
2. `tools/replay_waveform.py`

These give the highest value for remaining software validation while avoiding hardware coupling.
