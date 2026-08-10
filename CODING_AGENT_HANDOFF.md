# Coding Agent Handoff

This file is for a fresh coding agent resuming implementation work in this repository.

## Authoritative sources

0. **[SCOPE_TRIGGER_DEBUG_LOG.md](SCOPE_TRIGGER_DEBUG_LOG.md) is the current state of truth for the active
   scope no-trigger investigation** — read its "Current status" section first. Everything below this
   point in the present file (test counts, "what remains," etc.) predates that investigation and has
   not been updated to reflect it; do not treat this file or IMPLEMENTATION_STATUS.md as current for
   anything scope-related until they're explicitly refreshed.
1. [handoff_latest.md](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/handoff_latest.md) is the locked technical/safety source.
2. [coding_instructions.txt](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/coding_instructions.txt) is the required implementation workflow.
3. [IMPLEMENTATION_STATUS.md](C:/Users/shrirama/OneDrive%20-%20Legrand%20France/Desktop/EE_InternFiles/ccid_automation/IMPLEMENTATION_STATUS.md) is the current implementation log.
4. [IMPLEMENTATION_QUESTIONS.md](IMPLEMENTATION_QUESTIONS.md) records the material items that are still genuinely
   unresolved (not routine implementation choices) — read this before assuming something is settled.

## Operator rules & safety boundaries

Durable rules from a hardware-commissioning handoff document that was pasted into a prior chat session
but never saved as a file — reproduced here so they aren't lost. These are operating rules, not status;
they don't go stale the way test counts do.

**Communication:**
- Progressive workflow: one small action at a time unless a complete procedure is explicitly requested.
- Explain rationale once per new topic, not before every command.
- Stop and diagnose failed gates before advancing; don't repeat an identical energized attempt hoping
  for a different result.
- Distinguish clearly among software-only work, low-voltage contactor-coil work, mains-powered EVSE
  work, and K3 leakage-injection work when describing what an action will touch.
- Keep a clear distinction between proven facts, observations, hypotheses, and unresolved questions —
  don't let a hypothesis get cited later as if it were confirmed.
- No Copilot/AI attribution in commits. Confirm before pushing, and before any critical behavior change.

**Safety (mains + intentional leakage-current injection):**
- A qualified person controls physical energization, reconnection, protective-earth work, probe
  placement, and emergency-disconnect readiness — this project's software is not a substitute for that.
- Never disconnect protective earth or a grounded bench-scope reference while energized.
- `gpio_selftest exercise` must never be used as a live K3 leakage test — it can hold K3 closed for
  human-scale seconds. K3's software backstop is 300 ms, but the physical emergency disconnect is still
  required; the backstop is a last-resort limiter, not the primary safety mechanism.
- The rig has no dedicated protective-earth continuity input. A PE loss downstream is only detected
  indirectly (scope never triggers → K3 backstop opens → acquisition times out → halt) — this is
  consequence detection, not PE-continuity detection.
- Auxiliary-contact GPIO feedback is deferred (open item). Software contactor state is commanded state
  only, never physically confirmed.
- Never weaken `no_pretrigger_leakage` or another V2 sanity check to make a run pass. A numerical PASS
  with a failed sanity check is invalid and must not be accepted.
- Don't reuse a run ID, ever, including for a resumed/retried attempt.

**Before authorizing a 5-cycle (or larger) energized campaign, all of these must hold for one fully
automated cycle, no manual scope interaction:**
camera waits through boot and grants correctly on sustained green; scope reaches Stop, is verified
stopped, is configured, is commanded Single, reaches Armed, and stays Armed through a pre-injection
re-check; K3 closes; the scope triggers *automatically*; K3 opens normally or within the 300 ms backstop;
acquisition and waveform transfer complete; screenshot and green-LED image are saved; V2 analysis runs
with a genuine quiet pre-trigger baseline (`no_pretrigger_leakage=true`, `record_spans_no_trip_limit=true`,
`burst_starts_near_t0=true`, `collapse_is_clean=true`); no rig halt reason; human review accepts the
waveform and screenshot. This has not yet been achieved — see `SCOPE_TRIGGER_DEBUG_LOG.md`.

## Current repository state

- Branch: `development`
- All 11 phases are implemented. Two rounds of post-Phase-11 work have landed since:
  a camera charging-gate redesign (commit `51d8b24`) and a software-only hardening pass adding
  missing fault-matrix coverage, a disk-space check, camera config, and doc/comment cleanup
  (commit `449a4ba`). See "Post-Phase-11 work" below for what each did.
- Tests passing: `python -m unittest discover -s tests -p 'test_*.py'` -> **269 passed, 2 intentionally skipped**
  (the 2 skips are fault-matrix rows the spec itself says cannot be locally unit-tested — see
  `tests/test_faultmatrix.py`)

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

### Phase 11
- Commissioning and offline-replay tools, all defaulting to simulated/de-energized hardware:
  - [tools/simulate.py](tools/simulate.py) - accelerated simulated campaigns, fault injection, crash-injection/resume verification, sticky-halt verification
  - [tools/replay_waveform.py](tools/replay_waveform.py) - offline re-analysis of committed waveforms, read-only against original data, auditable change report
  - [tools/gpio_selftest.py](tools/gpio_selftest.py) - guarded single-contactor exercise + mains-mismatch-detector test
  - [tools/scope_bench.py](tools/scope_bench.py) - scope IDN/config/readback/arm-polling/memory-depth/capture-timing bench
  - [tools/calibrate_camera.py](tools/calibrate_camera.py) - ROI + HSV hue-range proposal, temporal classification verification, CameraSim replay-footage generation

## Post-Phase-11 work

Two rounds of work landed after Phase 11 was marked complete, both purely software (no hardware
access in either session). Full detail is in each commit message; summary here.

### Camera charging-gate redesign (`51d8b24`)

Triggered by a real live-hardware commissioning attempt (Pi/GPIO/scope/C270 camera/ROI/analysis all
commissioned) that halted with `vision_gate_timeout_stuck_booting` even though the operator visually
observed the EVSE LED flashing green. Root cause, confirmed by reproduction: `LedClassifier.window_classification()`
in [ccid/classify.py](ccid/classify.py) could be tipped from GREEN into a window-level BOOTING verdict
by just 2 frames with a spurious secondary hue (camera noise/glare) anywhere in the ~3 s/45-frame
history — a real risk on live footage, not reproducible with clean synthetic green/dark alternation
alone.

Fix: a new `ChargingGatePolicy` class, independent of the existing window/consecutive-agreement
classifier (which is kept, unchanged, for FAULTED/READY/OFF/BOOTING *timeout-diagnostic* reporting
only). The new policy grants on recent green-frame density (`vision.charging_green_window_s`,
`vision.charging_green_required_frames` — new required `config.yaml` keys, defaults `2.0`/`3`),
blocked only by red. `await_charging_gate()` in `ccid/classify.py` and `Sequencer.__init__` in
`ccid/sequencer.py` were updated accordingly. See the commit message and `ccid/classify.py`'s
`ChargingGatePolicy` docstring for full rationale.

**This fix has not yet been validated against real hardware or real footage.** See "Camera/vision
commissioning status" below — that is the next real-world step, not something completed by this
software-only session.

### Fault-matrix, disk-space, camera-config, and doc hardening pass (`449a4ba`)

An audit of `coding_instructions.txt` against actual repo state, then implementation of everything
found missing that's genuinely unit-testable without hardware:
- `tests/test_faultmatrix.py` (new) — was required by the repo structure spec but didn't exist.
  One row per fault-matrix entry, asserting the full required property set, not just terminal.
- Disk-space check: `paths.min_free_disk_gb` (new required `config.yaml` key, default `2`) +
  injectable `disk_usage` in `Sequencer`, halts before energizing anything if free space is low.
- Camera `device_index` is now config-driven (new `camera:` section, `camera.device_index`) instead
  of hardcoded to `0` in `ccid/hal/camera_real.py`'s `CameraRealConfig` default.
- Per-cycle sidecar JSON now carries `config_hash` and `software_version` (new `ccid.__version__`)
  alongside the existing `analysis_version`.
- `IMPLEMENTATION_QUESTIONS.md` created, recording the two genuinely open items (see that file).
- Module docstrings and WHY-comments added to `ccid/recorder.py`, `ccid/sequencer.py`, `ccid/main.py`,
  `ccid/config.py`; dead imports removed; a tab/space indentation defect fixed in `ccid/config.py`.

## Camera/vision commissioning status

Per a hardware-commissioning handoff document (dated 2026-08-05, not itself a repo file — if you
have it, it is the fullest account of what was physically done):

- **Commissioned:** Raspberry Pi, GPIO contactor drivers, Keysight scope, Logitech C270 webcam,
  calibrated ROI, waveform analysis, safety sequencing.
- **Camera facts:** C270 at `/dev/video0` (now configurable, see above), nominal 640x480 YUYV 30 fps,
  exposure locked via `v4l2-ctl --set-ctrl=auto_exposure=1,exposure_time_absolute=30` and confirmed
  read back correctly during the failed attempt (so the failure was *not* the known C270
  exposure-reset issue).
- **Calibration data committed to this repo:** [calib/off/](calib/), `calib/blue/`, `calib/green/`,
  `calib/red/` (real captured frames) plus per-colour `calib/<color>_verify.json` reports from
  `tools/calibrate_camera.py verify` — all matched at the time they were captured. `.gitignore` also
  references `calib/booting/`, `calib/green_exposure30*/`, `calib/green_filtered/` — these existed on
  the commissioning machine but were deliberately not committed (raw footage).
- **Not yet done, per that handoff document's own Phase E and acceptance criteria — status unknown
  to this coding session, confirm before further hardware work:**
  - A dedicated real flashing-green footage capture (`calib/green_flash_diag_*`, ~10-20 s at exposure
    30, K3 physically disconnected) was recommended but its capture/replay validation against the new
    `ChargingGatePolicy` is not confirmed done.
  - One supervised, fully real cycle with: charging gate granted, scope trigger + acquisition
    completed, K3 within its 300 ms backstop, analysis version V2, all waveform sanity checks true,
    and every artifact manually reviewed — not confirmed done since the gate fix landed.
  - Do not reuse these run IDs (already used in prior commissioning attempts):
    `gpio_real_check`, `full_real_cycle_001`, `full_real_cycle_004`,
    `v2_live_validation_20260805T203532Z`, `v2_live_validation_20260805T204107Z`,
    `v2_live_validation_20260805T204658Z` (the last one is the vision failure that triggered the
    redesign above).

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

- `modes` includes:
  - `gpio_mode`
  - `scope_mode`
  - `camera_mode`
- `vision:` section: ROI (`roi_x/y/width/height`) plus charging-gate policy
  (`charging_green_window_s`, `charging_green_required_frames` — added with the gate redesign)
- `camera:` section (new): `device_index` — which `/dev/videoN` the real camera opens
- `paths:` section: `run_root`, `output_root`, plus `min_free_disk_gb` (new)
- All of the above are required keys, strictly validated, and included in the canonical config hash
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
- [tests/test_tools_simulate.py](tests/test_tools_simulate.py)
- [tests/test_tools_replay_waveform.py](tests/test_tools_replay_waveform.py)
- [tests/test_tools_gpio_selftest.py](tests/test_tools_gpio_selftest.py)
- [tests/test_tools_scope_bench.py](tests/test_tools_scope_bench.py)
- [tests/test_tools_calibrate_camera.py](tests/test_tools_calibrate_camera.py)
- [tests/test_faultmatrix.py](tests/test_faultmatrix.py) - consolidated fault-matrix reference, see "Post-Phase-11 work"

## What remains

The software implementation is complete and up to date: all 11 phases plus the two post-Phase-11
rounds above are implemented and unit-tested off target (269 tests passing, 2 intentionally skipped).

What remains is hardware-side, which no coding agent can complete from software alone:

1. **Camera charging-gate real-hardware validation** (see "Camera/vision commissioning status"
   above) — real flashing-green footage capture/replay, then one supervised live cycle. This is the
   direct, unfinished continuation of the gate redesign and should come before any other hardware
   step, since it's what the last live attempt was blocked on.
2. **Hardware-side watchdog verification** — kernel hang, Pi hang, script-hang-then-restart, and the
   dead-man's-switch power-kill test (kill the Pi's power, confirm the phone alert arrives). None of
   this is unit-testable; it needs the real rig (`coding_instructions.txt` section 7, handoff
   document section 14/Stage 5).
3. **System-level watchdog config** — `RuntimeWatchdogSec=10`/`RebootWatchdogSec=60` in
   `/etc/systemd/system.conf` is documented in `DEPLOYMENT.txt` section 4 but not confirmed actually
   applied on the target Pi. Confirm, don't assume.
4. **K1/K2 physical-state readback** — open hardware item 16. Software only ever tracks commanded
   state, never confirmed by auxiliary contact or voltage sensing. `coding_instructions.txt` section
   13 requires this explicitly accepted-as-a-gap or resolved before Stage 6 — no decision has been
   made either way. Tracked in `IMPLEMENTATION_QUESTIONS.md`.
5. **UL 2231-2 endpoint definition** — `config.yaml`'s `analysis.endpoint_definition` (the `v2` text)
   is still this project's own provisional definition, not yet confirmed against the actual standard
   on paper. Tracked in `IMPLEMENTATION_QUESTIONS.md`.
6. **Full 6,000-cycle campaign** — not run. Stage 5 (10 cycles, then 100 cycles, per
   `coding_instructions.txt` section 13 and `PI_SETUP_AND_TEST_PLAN.md`) is the next real milestone,
   after item 1 above.

(Camera `device_index` being hardcoded was previously on this list — resolved in `449a4ba`, see
"Post-Phase-11 work" above.)

## User preferences and workflow constraints

- Progressive commits/pushes have been used successfully.
- User does **not** want Copilot co-author attribution in commit messages.
- User wants pauses for:
  - critical design/behavior edits
  - push confirmation before publishing changes
- User may ask to tweak commit messages before push.
- Do not overstate hardware readiness: real hardware code exists, but hardware execution remains **NOT RUN - HARDWARE REQUIRED**.

## Useful recent commits

- `449a4ba` - add missing fault-matrix coverage, disk-space check, camera config, docs
- `51d8b24` - fix charging gate misclassifying flashing green as booting
- `fd0fc85` - version corrected pretrigger analysis as v2
- `7c2001a` - fix false pretrigger leakage detection
- `d00b876` - enforce K3 backstop during blocking scope acquisition
- `324184a` - add calibrated vision ROI to runtime config
- `2e1126f` - add real hardware HAL implementations and guarded tests
- `743f949` - add sequencer state machine and integration tests
- `0f61207` - add phase 7 versioned waveform analysis boundary
- `931c035` - add phase 6 vision classification with hue-based led state detection
- `38d8c8c` - add phase 5 recorder/resume and persistence error handling

## Recommended next action

There is no further software phase to implement. The next action is hardware, in this order:

1. Capture and offline-replay real flashing-green footage against the new `ChargingGatePolicy`
   (`tools/calibrate_camera.py`), then run one supervised live cycle — see "Camera/vision
   commissioning status" above. Use a fresh run ID, not one of the ones already listed as used.
2. Confirm the system-level watchdog config is actually applied, then exercise the dead-man's-switch
   power-kill test.
3. Make an explicit decision on K1/K2 physical-state readback (item 4 above) before Stage 6.
4. Proceed to Stage 5 (10 then 100 cycles) per `PI_SETUP_AND_TEST_PLAN.md`.
