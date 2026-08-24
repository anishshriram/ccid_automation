# Test Suite Guide

**25 files, 364 tests, 2 intentional skips, ~7s to run the whole thing.** Run with:
```
python3 -m unittest discover -s tests
```

Every other document in this series cross-references the specific tests that prove the behavior it describes — this document is the map that ties them all together: what each file is for, the shared patterns used throughout, and the handful of small utility modules that don't get their own doc elsewhere.

---

## 1. Shared testing philosophy

A few conventions hold across essentially the entire suite, and knowing them makes any individual test file easier to read cold:

- **No real time, ever.** Every test that involves timing uses a deterministic, manually-advanced clock (`_ManualClock`/`ManualClock`, reimplemented locally in most test files rather than shared — `now()` returns a stored value, `sleep(seconds)` just advances it). Nothing in the suite waits on a real `time.sleep`, which is why 349 tests including full simulated campaigns run in under 4 seconds.
- **No real hardware, ever.** Every test drives the `Sim` HAL implementations (or hand-written fakes conforming to the same `ABC`s, e.g. `test_scope_protocol.py`'s `_FakeScope`) — never `ScopeReal`/`GpioRealContactorController`/`CameraReal` against actual instruments. Where the *real* implementations are tested (`test_scope_real.py`, `test_gpio_real.py`, `test_camera_real.py`), they're tested against injected fakes standing in for the transport layer (`_FakeInstrument`/`_FakeRM` for PyVISA, `_FakeOutput` for gpiozero, `_FakeCapture` for OpenCV) — proving the real implementation's *logic* (command ordering, retry behavior, error handling) without needing the physical device.
- **Fault injection is a first-class, reusable pattern**, not ad hoc per test: `ScopeSimScenario`'s many boolean/timing flags, `GpioSimContactorController.inject_failure`, `CameraSim`'s `fail_after_samples`, `CrashInjector`'s checkpoint targeting. Tests construct a scenario describing exactly the failure they want to prove a response to, rather than mocking individual calls.
- **Tests read as documentation of intent**, not just assertions — most test names in this codebase are full sentences describing the property being proven (`test_natural_trigger_discovered_at_forced_checkpoint_still_counts_as_success`, `test_two_second_green_segment_during_boot_does_not_grant`). This is deliberate enough that grepping test names is often faster than reading the source to answer "does this system handle X."

---

## 2. File-by-file map

Grouped by which subsystem doc has the full behavioral detail. Where a doc already exists, the coverage-map section there is the authoritative cross-reference — this table is for orientation.

### Sequencer & state machine → [sequencer-and-state-machine.md](sequencer-and-state-machine.md)
| File | Covers |
|---|---|
| `test_sequencer.py` | The full cycle state machine, TER checkpoints, forced-diagnostic capture, backstop timing, every halt/retry path, the acquisition-poll timeout-boundary regression — the single largest test file behaviorally |
| `test_faultmatrix.py` | Every row of `coding_instructions.txt`'s fault matrix, end to end through the real `Sequencer` — see §4 below |
| `test_safety.py` | `safe_off` and the interlocks it depends on (K3-requires-K1&K2, K1/K2-blocked-while-K3-closed, single-use gate tokens, mismatch-stagger debouncing) |
| `test_states.py` | `CycleState` enum completeness, `CycleDecision` immutability — thin, since `states.py` itself is almost pure enums |

### Trip-time analysis → [trip-time-analysis-algorithm.md](trip-time-analysis-algorithm.md)
| File | Covers |
|---|---|
| `test_analysis.py` | The full versioned algorithm (12 test classes) — envelopes, sanity checks, verdict boundaries, the V1/V2/V3 onset-refinement story, config validation, container format handling |
| `test_forced_diagnostic_analysis.py` | The diagnostic-only forced-capture summary module, including its explicit non-use-for-verdict guard |

### Hardware abstraction layer → [hardware-abstraction-layer.md](hardware-abstraction-layer.md)
| File | Covers |
|---|---|
| `test_scope_real.py` | `ScopeReal` against a fake VISA instrument — config command ordering, the OPC sync barrier, TER semantics, and the entire defensively-written `capture_timeout_diagnostics` behavior (18 dedicated tests) |
| `test_scope_sim.py` | `ScopeSim`'s synthetic waveform generation and every fault-injection scenario flag |
| `test_scope_protocol.py` | All four HAL `ABC`s' contract shape, via hand-written fakes (`_FakeContactors`/`_FakeScope`/`_FakeCamera`/`_FakeNotify`) |
| `test_gpio_real.py` | `GpioRealContactorController` against a fake output device — confirms real and sim contactor behavior stay in parity |
| `test_camera_real.py` | `CameraReal`'s reader-thread/staleness/failure-counting logic against a fake capture source |
| `test_camera_sim.py` | `CameraSim`'s fixture and replay-file modes |

### Vision/classification → [vision-and-charging-gate-classification.md](vision-and-charging-gate-classification.md)
| File | Covers |
|---|---|
| `test_classify.py` | The largest test file in the suite (820 lines, 8 classes, ~50 tests) — HSV math, per-frame classification, the temporal window layer, the independent charging-gate-grant policy, timeout-action mapping, config validation |

### Persistence & config → [persistence-and-recovery.md](persistence-and-recovery.md)
| File | Covers |
|---|---|
| `test_recorder.py` | The commit path, diagnostic writes, sticky halt-reason persistence |
| `test_resume.py` | **Real crash-injection proofs** — not just unit assertions, actual simulated crashes at each commit checkpoint followed by a verified-correct resume |
| `test_config.py` | Strict validation (every rejected-input case), hash stability, cross-field invariants, `camera.device_index` accepting either an int or a stable string device path |

### CLI/lifecycle/monitoring → [cli-lifecycle-and-monitoring.md](cli-lifecycle-and-monitoring.md)
| File | Covers |
|---|---|
| `test_main.py` | HAL bundle construction, systemd watchdog sleep-splitting, Cronitor/ntfy notifier behavior, all three energizing commands' happy paths, the safe `status` command, the auto-retry streak logic (`_run_campaign_with_auto_retry` — recovery, both streak limits, streak-reset-on-progress, the cycle_index-skip-on-retry safety fix), periodic and reactive equipment refresh |

### Tools → [tools.md](tools.md)
| File | Covers |
|---|---|
| `test_tools_simulate.py` | Campaign completion/fault reporting, crash-resume, sticky-halt-check, the `CrashInjector` mechanism itself |
| `test_tools_replay_waveform.py` | Offline re-analysis correctness, non-destructive output guarantees, CLI subcommands |
| `test_tools_calibrate_camera.py` | ROI/hue-range proposal (including the circular-wrap trick), temporal verification against real `LedClassifier`, replay-footage round-tripping |
| `test_tools_gpio_selftest.py` | Guarded contactor exercise, the interlock-cannot-be-bypassed property, mismatch probing |
| `test_tools_scope_bench.py` | Bench identify/configure/arm/capture flows against `ScopeSim` |

### Small utility modules — no dedicated doc; covered here
| File | Covers |
|---|---|
| `test_clock.py` | `ccid/clock.py` — see §3 |
| `test_errors.py` | The `CcidError` exception hierarchy shape |

---

## 3. Two small modules worth flagging: written, tested, not wired up

Consistent with a pattern noted across several of the other docs (`CycleDecision`, `CameraInterface.await_charging_gate`, `PathsConfig.output_root`, `CycleArtifacts.fault_jpg_burst`) — two more exist purely at the utility-module level:

- **`ccid/clock.py`** (`utc_now`, `monotonic_now`, `elapsed_s`, `MonotonicDeadline`/`make_deadline`) is fully implemented and tested (`test_clock.py`), but **nothing in `ccid/` or `tools/` actually imports it.** Every real timing operation in the codebase uses `time.monotonic()` directly, injected as a `monotonic_now` callable parameter (`Sequencer.__init__`, `ScopeReal.__init__`, etc.) rather than going through this module's `MonotonicDeadline` abstraction.
- **`ccid.errors.TimeoutError`** (shadows the Python builtin of the same name, deliberately scoped to `ccid.errors.TimeoutError` via its full import path) is defined and part of the exception hierarchy test (`test_error_hierarchy`), but is never raised anywhere in the codebase — every actual timeout condition currently returns a boolean (`wait_until_armed` → `False`, `_poll_scope_armed` → `False`) and is handled by the caller deciding what to do, rather than by catching this exception.

Neither is a bug — they're available infrastructure that the rest of the codebase simply hasn't reached for yet. Worth knowing if you go looking for "where are timeouts represented" and only find boolean returns everywhere.

---

## 4. `test_faultmatrix.py` — a special role

Unlike every other test file (organized by *module*), this one is organized by **fault-matrix row** — each test corresponds directly to one row of the fault matrix `coding_instructions.txt` §7 requires, run end to end through the real `Sequencer` rather than testing one function in isolation. This is the suite's closest thing to an integration test layer, and it's also where the project's own testing philosophy about *what not to test* is most visible — two deliberate skips, each with a full explanation in the skip message itself rather than just a bare `@skip`:

- **`test_k1_k2_physically_stuck_closed_row`** — skipped because this is "explicitly undetectable" per `coding_instructions.txt` §7: the software tracks only *commanded* contactor state, with no auxiliary-contact or voltage readback (this is the same open item tracked in `legacy-documentation-audit.md` §4 as K1/K2 physical-state readback — still open, per the project's own most recent decision). The skip message is explicit that this is "cannot be validated by a unit test without real hardware readback wiring that does not exist" — a documented gap, not an oversight.
- **`test_missing_external_heartbeat_row`** — skipped because a missing heartbeat is detected by *Cronitor's own* expected-frequency alerting (external service behavior), not by anything in this codebase — "documented rather than falsely unit-tested locally." Testing this locally would mean testing Cronitor's infrastructure, not this project's code.

Both skips are the right call, not a coverage gap to close: they represent properties that are either physically unverifiable from software alone, or owned by an external system this codebase deliberately doesn't reimplement.

---

## 5. If you're adding a test

- **Match the existing naming convention** — a full sentence describing the property, not `test_case_1`. It pays for itself the first time someone needs to answer "does this handle X" without opening the file.
- **Reach for a scenario/fixture dataclass before reaching for a mock.** `ScopeSimScenario`, `GpioSimContactorController.inject_failure`, `CameraSim`'s fixture sequences — these compose, are reusable across tests, and (per §1) prove behavior against the same code path production uses rather than an ad hoc double.
- **If you're proving a crash-safety or ordering property, prove it, don't just assert it from inspection** — `test_resume.py`'s crash-injection tests and `tools/simulate.py`'s `opening_order_is_safe`/`no_skipped_cycles` checks are the model: force the actual failure mode via an injector, then check the real resulting state, rather than trusting that a commit-order comment in the source is followed correctly.
- **A new fault-matrix row belongs in `test_faultmatrix.py`, following its row-per-test organization** — even if the underlying mechanism is also unit-tested elsewhere (e.g. in `test_sequencer.py`), the fault-matrix file is what maps 1:1 back to `coding_instructions.txt`'s own table, and that traceability is the point of keeping it separate.
