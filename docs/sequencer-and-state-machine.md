# Sequencer & State Machine

**Source files:** `ccid/sequencer.py` (1265 lines), `ccid/states.py` (53 lines), `ccid/safety.py` (42 lines)
**Tests:** `tests/test_sequencer.py` (1077 lines, class `SequencerTests`), `tests/test_faultmatrix.py` (class `FaultMatrixTests`, one test per fault-matrix row), `tests/test_safety.py`

This is the core of the whole system. Every other module (`analysis.py`, `classify.py`, the HAL, `recorder.py`) exists to be *called by* this one. If you only ever read one file to understand how the rig behaves, read this one.

---

## 1. What this module owns, and what it explicitly does not own

`Sequencer` orchestrates **one cycle at a time**: mains close → charging gate → scope arm → K3 inject → acquisition → K3 open → analysis → commit → mains open → cooldown, plus every retry/degrade/halt branch off that path. It decides *when* to call things and *what to do* with the result.

It does **not** own safety enforcement. The docstring at the top of `sequencer.py` says this explicitly: "Safety-critical invariants (K3 interlock, K1/K2 blocked from opening while K3 is closed, SafeOff ordering) are enforced one layer down, in the HAL... and in `ccid/safety.py`; this module orchestrates the sequence and cannot bypass those checks even if it tried." Concretely: if `Sequencer` ever tried to call `close_k3()` before `close_k1()`/`close_k2()`, or tried to `open_k1()` while K3 is still commanded closed, `GpioSimContactorController`/`GpioRealContactorController` would raise `SafetyViolationError` regardless of what the sequencer intended. This is a deliberate layering choice: the sequencer's logic can be wrong and the rig still won't do something unsafe, because the enforcement lives underneath it, not inside it.

---

## 2. `ccid/states.py` — the vocabulary

Four types, no logic:

- **`CycleState`** (str Enum) — every state a single cycle can be observed in, in roughly chronological order: `SAFE_OFF → MAINS_CLOSING → WAITING_FOR_CHARGING → SCOPE_CONFIGURING → SCOPE_ARMING → SCOPE_ARMED → INJECTING → ACQUIRING → INJECTION_OPENING → [FORCED_DIAGNOSTIC_CAPTURING | DIAGNOSTICS_CAPTURING] → TRANSFERRING → COMMITTING → MAINS_OPENING → COOLDOWN`, plus the off-path states `RETRY_COOLDOWN`, `DEGRADED_FIXED_WAIT`, `HALTED`, `COMPLETE`. Every value here shows up as a `StateTransition.state` in the cycle's JSON sidecar (`state_transitions` array) — this is literally the audit trail of what happened and when.
- **`Terminal`** — the outcome recorded for a *cycle*, one of `PASS`, `FAIL`, `NO_TRIP`, `RIG_FAULT`, `HALTED`, `COMPLETE`. Not the same axis as `CycleState`: a cycle passes through many `CycleState`s but ends in exactly one `Terminal`.
- **`LedState`** — `BOOTING`, `READY`, `CHARGING`, `FAULTED`, `OFF_OR_UNKNOWN`, `CAMERA_UNAVAILABLE`. Produced by `ccid/classify.py` (its own doc), consumed here only as an opaque result to branch on.
- **`CycleDecision`** — a small frozen dataclass (`terminal`, `notes`, `metadata`) that's defined here but not actually constructed anywhere in `sequencer.py` itself; it looks like a vestigial/forward-looking type. Worth knowing it exists if you go looking for where verdicts get decided — that's actually `Sequencer._map_verdict`, not this class.

---

## 3. `ccid/safety.py` — `safe_off`

One function, fully reproduced here because it's short and everything about it matters:

```python
def safe_off(contactors: ContactorInterface) -> None:
    failures: list[SafeOffStepFailure] = []
    for operation, action in (
        ("open_k3", contactors.open_k3),
        ("open_k2", contactors.open_k2),
        ("open_k1", contactors.open_k1),
    ):
        try:
            action()
        except Exception as exc:
            failures.append(SafeOffStepFailure(operation=operation, error=exc))
    if failures:
        raise SafeOffExecutionError(failures)
```

Three properties, each deliberate:

1. **Strict K3 → K2 → K1 order.** K3 (leakage injection) always opens first. This matters because the HAL layer blocks K1/K2 from opening while K3 is still commanded closed anyway (see below), so this ordering isn't just a preference — it's the only order that can ever succeed against that lower-layer interlock.
2. **Every step is attempted even if an earlier one fails.** This is the "failure-resilient" part of the docstring: a failed `open_k3()` does not short-circuit the attempt to also `open_k2()` and `open_k1()`. You want maximum de-energization even in a partially-failed shutdown, not an early return that leaves K1/K2 energized because K3's open command happened to raise.
3. **Idempotent.** Calling it again when everything is already open just re-issues open commands that are no-ops at the HAL level. This is why it's safe to call from `finally` blocks and from every halt path without checking prior state first.

`SafeOffExecutionError(CcidError)` aggregates all failures into one exception with a readable summary (`"SafeOff encountered failures: open_k3: ..., open_k2: ..."`) rather than only surfacing the first one — you get the complete picture of what failed, not just what failed first.

Tested directly in `test_safety.py`: `test_safe_off_attempts_all_steps_and_aggregates_failures` and `test_safe_off_is_idempotent_and_orders_opens`.

---

## 4. The `Sequencer` object

### 4.1 Construction (`__init__`)

Takes everything it needs as injected dependencies — `config`, `contactors` (a `ContactorInterface`), `scope` (a `ScopeInterface`), `camera`, `recorder` (a `RunRecorder`), `monotonic_now`/`sleep` callables, and an injectable `disk_usage` callable (defaults to `shutil.disk_usage`, overridden in tests to simulate a near-full disk without needing an actual near-full filesystem). This is why the entire sequencer is unit-testable without any real hardware: every side-effecting boundary is a constructor parameter, not a module-level import.

Two derived objects get built once here and reused every cycle:
- `self._vision_roi` — a `RegionOfInterest(x, y, width, height)` built from `config.vision.roi_*`.
- `self._vision_optical_config` — `DEFAULT_OPTICAL_CONFIG` from `classify.py`, with `charging_green_window_s`/`charging_green_required_frames`/`charging_green_min_span_s` overridden from `config.vision.*`. Everything else in the optical config stays at its hardcoded default (the module docstring elsewhere notes VISA backend/reconnect_attempts and GPIO active_high/initial_value are hardcoded the same way — the spec locks them, they're not meant to be per-deployment config).

### 4.2 `run(*, run_dir, state)` — the campaign loop

This is the outermost entry point (called once per `ccid.main` `start`/`resume`/`simulate` invocation). Logic:

```
connect to scope
try:
    cycle_index = state.last_completed_cycle + 1
    while cycle_index <= state.target_cycles:
        run one cycle
        if that cycle's terminal is NO_TRIP, RIG_FAULT, or HALTED:
            return immediately (campaign stops)
        cycle_index += 1
finally:
    safe_off(contactors)      # always, no matter how the loop exited
    scope.disconnect()        # always, after safe_off
return COMPLETE
```

Two things worth calling out precisely:

- **Only `NO_TRIP`, `RIG_FAULT`, and `HALTED` stop the campaign.** `PASS` and `FAIL` do not — this matches the locked verdict table in `analysis.py`'s docstring (`FAIL` "log, alert, **continue**"; only `NO_TRIP` "HALT run"). A single over-limit trip time is expected/tolerated data, not a rig fault; a confirmed no-trip is a DUT safety failure and stops everything.
- **The `finally` block is unconditional.** Whether the loop finishes normally (`COMPLETE`), returns early on a halt, or an exception escapes upward, `safe_off` runs and the scope disconnects. This is the single most safety-relevant piece of control flow in the file — no code path exists that can leave the campaign loop without attempting full de-energization.

### 4.3 `_run_cycle` — one cycle, with the retry envelope

`_run_cycle` wraps `_attempt_cycle` in a `while True:` loop with a single `try/except` handling five distinct outcomes. This is the mechanism the module docstring refers to when it says "one place that decides retry-vs-halt-vs-continue for the whole state machine" — every failure inside `_attempt_cycle`, no matter how deep, surfaces here as one of exactly five exception types:

| Exception caught | What happens | Retryable? |
|---|---|---|
| `_RetryCycle` | If this is the cycle's first retry: `safe_off`, sleep `cooldown_retry_s` (60s), `continue` the `while True:` loop to re-attempt the same `cycle_index`. If a retry was **already used** this cycle: halt with reason `f"{retry.reason}_retry_exhausted"`. | Once, per cycle |
| `_SequencerHalt` | Halt immediately with the exception's own `terminal`/`category`/`reason`. | No |
| `PersistenceError` | Halt as `Terminal.HALTED`, `FaultCategory.PERSISTENCE`, reason `f"persistence_error:{type(exc).__name__}"`. | No |
| `CcidError` (any other) | Halt as `Terminal.HALTED`, `FaultCategory.RIG`, reason `f"rig_error:{type(exc).__name__}"`. | No |
| `Exception` (anything else — defensive net, marked `# pragma: no cover`) | Halt as `Terminal.HALTED`, `FaultCategory.CONTROLLER`, reason `f"unexpected:{type(exc).__name__}"`. | No |

Only one thing in the entire sequencer is retryable: the vision-gate timeout (see §5.3), and only once per cycle. Everything else is a hard halt on first occurrence. `retry_used` is a local boolean in `_run_cycle`, reset fresh for every new `cycle_index` — a cycle gets at most one retry, ever, then the *next* cycle starts with a clean slate.

If `_attempt_cycle` returns normally (no exception), execution falls through the `while True:` loop's `break` and proceeds to capture + analyze + commit (§6).

---

## 5. `_attempt_cycle` — the actual sequence, phase by phase

This is the method that does the real work. Read top to bottom, it *is* the protocol.

### 5.1 Disk space gate

First thing, before anything is energized: `_assert_sufficient_disk_space(run_dir)` compares `shutil.disk_usage(run_dir).free` against `config.paths.min_free_disk_gb` (2 GB in `config.yaml`). Below that, `_SequencerHalt(HALTED, PERSISTENCE, "insufficient_disk_space")` — nothing gets energized for a cycle whose artifacts might not fit. This is a fault-matrix row (`test_disk_below_threshold_halts_before_energizing_row`), and it's checked before mains close specifically so a disk-full condition never leaves K1/K2 energized while failing to write anything.

### 5.2 Mains close

`CycleState.SAFE_OFF` transition → `safe_off(contactors)` (idempotent, ensures a clean starting point even mid-retry) → `CycleState.MAINS_CLOSING` → `close_k1()`, `close_k2()` → `_assert_no_mains_mismatch()`.

The mismatch check calls `contactors.detect_mains_command_mismatch(allowed_stagger_ms=config.timing.mains_stagger_ms, now_monotonic_s=now())`. `mains_stagger_ms` is `0` in `config.yaml` (per `handoff_latest.md` item 19: "0 unless bench inrush proves noisy"), so in practice any observed K1≠K2 commanded-state mismatch halts immediately as `_SequencerHalt(RIG_FAULT, RIG, "k1_k2_command_mismatch")`. This same check is called again immediately after `close_k3()` (§5.6) — mismatch can't sneak in during the injection step either.

### 5.3 Charging gate wait

`CycleState.WAITING_FOR_CHARGING` → `await_charging_gate(camera, roi, timeout_s=config.timing.boot_timeout_s, ...)` from `classify.py` (own doc covers the vision logic itself; this is only what the sequencer does with the result).

`gate` carries `.led_state`, `.success`, `.degraded`. Three branches:

1. **`gate.degraded`** (camera unavailable) → append `DEGRADED_FLAG_CAMERA_UNAVAILABLE` to `context.degraded_flags` (once — guarded by an `if ... not in` check) and transition through `CycleState.DEGRADED_FIXED_WAIT`. Cycle *continues* — this is a degrade-and-continue path, not a halt.
2. **Timeout, not degraded** → `gate_timeout_action(gate.led_state)` from `classify.py` returns an `(action, reason)` pair:
   - `RETRY_EXTENDED_COOLDOWN` → open mains (no cooldown), raise `_RetryCycle(reason)`. This is the *only* retryable failure in the whole sequencer (§4.3) — triggered when the LED was blinking red, i.e. plausibly a transient fault that clears on its own.
   - `HALT` → open mains (no cooldown), raise `_SequencerHalt(HALTED, RIG, reason)`.
3. **Success** → falls through, `context.led_state_at_gate` is already set to `gate.led_state` regardless of which branch was taken (recorded even on halt/retry, for diagnosis).

### 5.4 Scope configuration and the TER baseline check

`CycleState.SCOPE_CONFIGURING` → `scope.configure_for_cycle(scope_settings)` → `_record_diagnostic_stage(context, "configuration_completion")` (§7).

Then the **baseline TER (Trigger Event Register) check** — this exists because of a real bug (`scope-trigger-debug-log.md` Entry 12). `:TER?` is a **read-and-clear** register: reading it returns whether a trigger event occurred *and clears the flag*. A single nonzero read right after configuring can't distinguish "an active problem" from "harmless stale residue left over from before this cycle even started." The fix: read it **twice**.

```python
baseline_clear_ter = self._scope.read_trigger_event_register()   # clears any stale event
self._record_diagnostic_stage(context, "baseline_ter_clear_read", trigger_event_register=baseline_clear_ter)
baseline_verify_ter = self._scope.read_trigger_event_register()  # verifies baseline is now clean
self._record_diagnostic_stage(context, "baseline_ter_verify_read", trigger_event_register=baseline_verify_ter)
if baseline_verify_ter:
    # halt — this means something is *actively* re-setting TER, not stale residue
    raise _SequencerHalt(RIG_FAULT, RIG, "scope_stale_trigger_event_before_arm")
```

Only the **second** (verify) read can halt the cycle. The first read's whole purpose is to consume any stale flag so it can't falsely trip the second read. Test coverage: `test_stale_trigger_event_before_arm_is_cleared_and_does_not_halt` (first read clears, cycle proceeds) vs `test_stuck_trigger_event_before_arm_halts_before_arming` (a scope that keeps re-setting TER — genuinely stuck — halts on the verify read) vs `test_trigger_event_between_baseline_reads_halts_before_arming` (a trigger arrives *between* the two reads — the verify read catches it).

### 5.5 Arming and pre-injection checks

`CycleState.SCOPE_ARMING` → `_record_diagnostic_stage("single_command_start")` → `scope.arm_single()` → `_record_diagnostic_stage("single_command_return")` → `CycleState.SCOPE_ARMED`.

Then a sequence of confirmation checks, all before K3 is allowed to close:

1. `_poll_scope_armed()` (§5.5.1) — must return `True` within `scope_arm_timeout_s` (2.0s), else `_SequencerHalt(RIG_FAULT, RIG, "scope_not_armed_timeout")`. → `_record_diagnostic_stage("armed_observation_1")`.
2. `sleep(0.05)` — "allow the fresh Single acquisition to settle."
3. Re-check `_poll_scope_armed()` — same halt reason (`"scope_lost_armed_before_injection"`) if it's no longer armed; this catches something consuming the armed acquisition in the 50ms window. → `_record_diagnostic_stage("armed_observation_2")`.
4. **`sleep(1.0)`** — the Entry 14 diagnostic-only dwell. Purely observational: a longer window than the 50ms settle above, to see whether armed-state or TER drifts over a longer pre-injection period than had previously been checked. Explicitly documented in-code as not touching any trigger setting or the K3/backstop timing that follows (verified true: the backstop deadline is computed from `k3_closed_s`, captured *after* this delay — see §5.6). → `_record_diagnostic_stage("pre_injection_diagnostic_delay")`.
5. Third armed re-check, same halt reason again (deliberately reused — "same underlying condition detected later," not a new failure mode). → `_record_diagnostic_stage("armed_observation_3")`.
6. **`pre_injection_ter = scope.read_trigger_event_register()`** — one more independent signal (not just armed-state) that nothing fired before the deliberate K3 close. If nonzero: `_SequencerHalt(RIG_FAULT, RIG, "scope_trigger_event_before_injection")`. → `_record_diagnostic_stage("pre_injection_ter_read", trigger_event_register=pre_injection_ter)`.

#### 5.5.1 `_poll_scope_armed`

```python
def _poll_scope_armed(self) -> bool:
    start_s = self._now()
    timeout_s = self._config.timing.scope_arm_timeout_s
    while self._now() - start_s <= timeout_s:
        if self._scope.wait_until_armed(timeout_s=timeout_s, now_monotonic_s=self._now()):
            return True
        self._sleep(0.01)
    return False
```
A simple bounded poll, 10ms cadence, delegating the actual "is it armed" answer to the HAL (`ScopeInterface.wait_until_armed`).

### 5.6 K3 close (injection)

`CycleState.INJECTING` → build a `ChargingGateToken(cycle_index, granted_at_monotonic_s=now())` → `contactors.close_k3(gate_token)`.

**This is the one hard-safety choke point in the whole sequence.** The token exists specifically so `close_k3` can enforce "at most once per cycle" (`GpioSimContactorController.close_k3` raises `SafetyViolationError` if the same `cycle_index` is presented twice) and "only after K1 and K2 are both commanded closed" (same method, checked before the token bookkeeping) — both enforced at the HAL layer, independent of anything the sequencer does or doesn't check.

Immediately after: `k3_closed_s = self._now()`, stored as `context.k3_closed_monotonic_s` — **this is the timestamp the 300ms backstop deadline is computed from**, and it is captured strictly after every diagnostic delay/check above. → `_record_diagnostic_stage("k3_close")` → `_assert_no_mains_mismatch()` again.

### 5.7 Acquisition polling with the K3 backstop — the most complex part of the file

`CycleState.ACQUIRING` → `_poll_acquisition_with_backstop(context, k3_closed_s)`.

This single method has to juggle three independent, time-based concerns simultaneously in one poll loop, at 10ms cadence, bounded by `scope_acquisition_timeout_s` (5s):

1. **The K3 backstop** — a hard, non-negotiable deadline. `k3_deadline = k3_closed_s + k3_backstop_s` (300ms). If the scope's own acquisition-complete signal hasn't fired by then, K3 opens anyway, unconditionally. This is "handoff safety invariant 9" — leakage injection must never stay closed indefinitely just because a polling call happens to be slow or a scope hangs.
2. **The forced-diagnostic checkpoint** — `forced_diagnostic_deadline = k3_closed_s + _FORCED_DIAGNOSTIC_DELAY_S` (100ms — comfortably inside the 300ms backstop). At this point, `_issue_forced_diagnostic_trigger` runs exactly once (`forced_diagnostic_checked` is a single-fire latch). This exists because of `scope-trigger-debug-log.md` Entry 11: real-hardware evidence of TER=0 for the entire 306.6ms K3-closed window confirmed a genuine no-trigger condition, and the team wanted diagnostic visibility into what the analog front end actually looks like when the real trigger doesn't fire.
3. **Normal acquisition-complete polling** — `scope.wait_until_acquisition_complete(...)`, called with a tiny per-iteration timeout (`min(0.01, remaining_budget)`) so it can't itself block past the loop's own cadence.

`opened` is a single-fire latch shared across all three concerns — whichever of "backstop fires" or "normal completion detected" happens first is the one that actually calls `contactors.open_k3()`; the other path, if it later also decides to act, sees `opened == True` and skips the redundant call. `context.k3_open_reason` records which one it was: `"normal"`, `"backstop"`, or `"acquisition_timeout"` (the loop's own bound expiring without either the backstop deadline model triggering — which shouldn't normally happen since 300ms backstop < 5s acquisition timeout, but the code doesn't assume that ordering can't be misconfigured).

#### 5.7.1 The Entry 15 bug and its fix — "checked" vs "forced" are not the same thing

This is worth understanding precisely because it's a real bug this project shipped and fixed mid-session, and the code comments deliberately preserve the story:

```python
if context.force_command_return_monotonic_s is not None:
    # A trigger was actually forced ...
    ...
    self._sleep(0.01)
    continue
if self._scope.wait_until_acquisition_complete(...):
    ...
```

The loop stops polling for *real* acquisition completion only when `force_command_return_monotonic_s is not None` — i.e., only when `_issue_forced_diagnostic_trigger` actually issued `:TRIGger:FORCe`. The **old, buggy** version instead gated on "the forced-diagnostic checkpoint ran at all" — but `_issue_forced_diagnostic_trigger` does *not* force a trigger if it finds TER already `1` at the checkpoint (§5.7.2); in that case a real, natural trigger had already occurred, and forcing would be both unnecessary and wrong. The old code still treated "checkpoint ran" as "stop trusting normal completion," which meant: a cycle that triggered naturally right around the 100ms mark, whose acquisition then went on to complete completely normally, got silently discarded and reported as a failure — because the loop had stopped listening for the real completion signal for no actual reason. Fixed by keying off `force_command_return_monotonic_s` (only set when forcing *actually happened*) instead of a separate "did we check" flag. Regression test: `test_natural_trigger_discovered_at_forced_checkpoint_still_counts_as_success`.

Once a trigger genuinely has been forced, though, the acquisition subsystem can no longer distinguish "this is a real trigger" from "we forced it" — so from that point on, the loop *never* re-enters the `wait_until_acquisition_complete` branch for that cycle. It falls through to backstop/timeout exactly as an ordinary no-trigger cycle would, and (diagnostic-only, doesn't affect `acquired`/`return True`/`False`) opportunistically watches `read_operation_condition()` for the run-bit clearing, purely to timestamp `forced_acquisition_completion_monotonic_s` for the diagnostic capture later.

#### 5.7.2 `_issue_forced_diagnostic_trigger`

```
gate_ter = scope.read_trigger_event_register()
record_diagnostic_stage("forced_diagnostic_ter_gate_read", trigger_event_register=gate_ter)
if gate_ter:
    context.live_trigger_event_seen = True   # real trigger — do NOT force
    return
context.force_command_start_monotonic_s = now()
record_diagnostic_stage("force_command_start")
scope.force_trigger()
context.force_command_return_monotonic_s = now()
record_diagnostic_stage("force_command_return")
```

Only two operations happen inside the live acquisition window here: one `:TER?` read and one fire-and-forget `:TRIGger:FORCe` write — both the same class of cheap, bounded call already trusted elsewhere in the loop (`:OPERegister:CONDition?` is polled every ~10ms throughout). The actual waveform/PNG transfer — the part that could plausibly stall on a slow USBTMC link — is **deliberately not here**; it's deferred to `_capture_forced_diagnostic_best_effort`, called only after full safe-off (§8). Both scope calls are wrapped in `try/except`, logged-and-return on failure — a communication error here degrades the diagnostic, it never escalates into a cycle fault.

`context.live_trigger_event_seen` is set here specifically because `:TER?` is read-and-clear: if a real trigger is found at this checkpoint, that fact would otherwise be lost the moment anything else reads `:TER?` again (including the post-timeout diagnostics bundle's own query). This flag is the only durable record of it, and it's what lets `_capture_timeout_diagnostics_best_effort` (§8.2) later choose the correct, more specific halt reason.

#### 5.7.3 The timeout-boundary `ValueError` bug and its fix

A second real bug in this same loop, found the same way as the Entry 15 bug above — by writing a regression test against a real reported incident, not by inspection. Leading hypothesis (unconfirmed as the definitive cause, since the original traceback was lost — see the persistence doc's controller-exception-diagnostics section for why that gap itself got closed) for `halt_reason=controller:unexpected:ValueError` at cycle 38 of campaign `5800_v3_real_20260813T175531Z`:

```python
poll_now_s = self._now()
remaining_s = acq_timeout_s - (poll_now_s - start_s)
if remaining_s > 0 and self._scope.wait_until_acquisition_complete(
    timeout_s=min(0.01, remaining_s),
    now_monotonic_s=poll_now_s,
):
```

`ScopeReal.wait_until_acquisition_complete()` (and `ScopeSim`'s identical check) raises `ValueError("timeout_s must be > 0")` for `timeout_s <= 0`. The **old** version computed the passed timeout from `now_s`, a value read once at the *top* of the loop body — one line before the `while self._now() - start_s <= acq_timeout_s:` guard, which is itself boundary-inclusive (`<=`). Two independent monotonic reads a moment apart, straddling an inclusive deadline: if the guard passes right at the edge, real scheduling jitter (GIL contention, GC, actual USBTMC/GPIO I/O happening in the same process) can let monotonic time tick past the deadline before the second read, making the computed remaining value already zero or negative — reachable rarely enough to surface once in many thousands of cycles rather than every time, consistent with this showing up once at cycle 38 rather than immediately. Note this is *not* a race caused by a slow intervening HAL call as such a call might first suggest — the calls between the two reads (the forced-diagnostic checkpoint, the K3-backstop check) all happen *after* `now_s` is already fixed and can only delay reaching the already-decided call, not change what value it was given.

Fixed by computing `remaining_s` fresh, immediately before the call (`poll_now_s`, not the stale `now_s`), and never calling the HAL at all when it isn't strictly positive — the loop just falls through to `self._sleep(0.01)` and lets the existing backstop/timeout paths handle it on the next iteration or after this one exits, exactly as an ordinary acquisition timeout already does. No other similar shrinking-remaining-time-into-a-validating-call pattern exists elsewhere in the codebase (checked directly — `_poll_scope_armed` and every other polling loop pass a *fixed* timeout on every iteration, not a shrinking one). Regression tests (`tests/test_sequencer.py`): reaching the deadline at the loop boundary with the fake clock landing exactly on zero and slightly negative remaining time; proving the HAL is never called with `timeout_s <= 0`; proving K3 still opens safely and exactly once in both cases.

---

## 6. Success path — capture, analyze, commit

Back in `_run_cycle`, once `_attempt_cycle` returns without raising:

1. `capture = scope.capture_after_acquire()` → `CycleState.TRANSFERRING`.
2. `waveform_blob = _pack_waveform_blob(capture.samples, capture.preamble)` — zips `samples.bin` + `preamble.json` into one in-memory blob (used both for the real committed artifact and as the exact input `analyze_waveform` consumes — see the analysis doc for what happens inside).
3. `analysis = analyze_waveform(waveform_blob, config.analysis)`.
4. **Two sanity checks can halt here, even though most sanity checks never veto a verdict** (see the analysis doc for the general "diagnostic-only, logged, never a veto" rule):
   - `SANITY_NO_PRETRIGGER_LEAKAGE` false → `_commit_and_halt(..., "k3_pretrigger_current_detected", RIG, RIG_FAULT)` — current was already flowing before K3 closed, i.e. K3 possibly stuck closed. This is exceptional: this specific sanity check *does* gate a halt, because it represents a distinct, actionable safety condition, not just measurement uncertainty.
   - `SANITY_RECORD_SPANS_NO_TRIP_LIMIT` false → halt as `"scope_record_too_short_for_no_trip_window"` — the record wasn't long enough to conclusively rule out a no-trip, so the rig can't trust its own "it tripped" conclusion.
5. Otherwise: `_map_verdict(analysis.verdict)` → `(Terminal, halt_reason, category)`:
   - `PASS` → `(Terminal.PASS, None, None)`
   - `FAIL` → `(Terminal.FAIL, None, None)` — **not a halt.** `halt_reason=None` means the campaign continues.
   - anything else (`NO_TRIP`) → `(Terminal.NO_TRIP, "dut:dut_no_trip", FaultCategory.DUT)` — this *does* halt (checked back in `run()`'s loop, §4.2).
6. `CycleState.COMMITTING` → `_build_record_payload` builds the CSV row and the full artifact bundle (waveform samples, preamble, scope PNG, camera gate JPEG, cycle JSON sidecar with the full `state_transitions` list) → `recorder.record_cycle(...)` (crash-safe write, its own doc).
7. `_open_mains_with_cooldown(context, include_cooldown=halt_reason is None)` — cooldown (10s) only runs when the cycle *didn't* halt; a halting cycle opens mains without waiting, since the campaign is stopping anyway.

---

## 7. Diagnostic instrumentation — `_record_diagnostic_stage` and `diagnostic_timeline`

Called at every named checkpoint throughout `_attempt_cycle` (`configuration_completion`, `baseline_ter_clear_read`, `baseline_ter_verify_read`, `single_command_start`, `single_command_return`, `armed_observation_1/2/3`, `pre_injection_diagnostic_delay`, `pre_injection_ter_read`, `k3_close`, `forced_diagnostic_ter_gate_read`, `force_command_start`, `force_command_return`, `acquisition_completion_observed`, `k3_open`). Each call appends one dict to `context.diagnostic_timeline`:

```python
{"stage": stage, "monotonic_s": now(), "operation_condition": int|None, "trigger_event_register": bool|None, "hal_status": str|None}
```

Three rules, all load-bearing:

- **Best-effort, never raises into the caller.** `read_operation_condition()` and `status()` are each wrapped in their own `try/except`, recorded as `None` on failure rather than aborting the whole snapshot.
- **Never issues its own `:TER?` query.** `trigger_event_register` is only ever populated from a value the *caller* already read for its own reason (the baseline reads, the pre-injection read, the forced-diagnostic gate read). Since `:TER?` is read-and-clear, an extra read here would silently consume/corrupt the evidence a real checkpoint elsewhere depends on.
- **Purely descriptive.** Nothing about this timeline ever influences a branch decision — it exists only to be persisted (only on failure paths — normal `PASS`/`FAIL` cycles don't carry it into the committed sidecar) so a human can later reconstruct exactly what the scope reported at each named instant.

---

## 8. Failure-path diagnostics: strict "after full safe-off" ordering

Two best-effort capture methods run when `_poll_acquisition_with_backstop` returns `False`:

### 8.1 `_capture_forced_diagnostic_best_effort` (only if a trigger was actually forced this cycle)

`CycleState.FORCED_DIAGNOSTIC_CAPTURING` → `scope.capture_after_acquire()` → `analyze_forced_diagnostic_waveform(blob).to_dict()` (its own doc; failure here is caught and just means `waveform_analysis=None`, the raw waveform still gets written) → `recorder.write_forced_diagnostic_capture(...)` with every Pi-side timestamp collected (`force_command_start/return`, `forced_acquisition_completion`, `k3_closed`), the full `diagnostic_timeline`, and the waveform analysis. **Explicitly diagnostic-only** — this data is never used for PASS/FAIL and is written to a `diagnostics/` path, not `waveforms/`.

### 8.2 `_capture_timeout_diagnostics_best_effort` (always, on this halt path)

Chooses the halt reason to return **first**, before attempting anything that could fail:
```python
reason = SCOPE_TRIGGERED_BUT_ACQUISITION_NOT_COMPLETED_REASON if context.live_trigger_event_seen else SCOPE_TIMEOUT_REASON
```
Then `CycleState.DIAGNOSTICS_CAPTURING` → `scope.capture_timeout_diagnostics()` → if that succeeds and its own settings snapshot also independently shows a trigger event (`_diagnostics_trigger_event_seen`, parsed defensively — a missing or unparseable value returns `False`, never asserts a trigger on ambiguous evidence), the reason is upgraded to the more specific one even if `live_trigger_event_seen` was somehow false → `recorder.write_timeout_diagnostics(...)`.

**Both of these run only after `_open_mains_with_cooldown` has already fully executed** — the call sites in `_attempt_cycle` are unambiguous about this ordering. The reason is spelled out in a comment referencing `scope-trigger-debug-log.md` Entry 3: a hung or wedged diagnostics query must never be able to delay de-energizing the EVSE mains. Tests: `test_diagnostics_capture_happens_only_after_full_safe_off`, `test_diagnostics_capture_never_recloses_k3_or_rearms`, `test_diagnostics_capture_failure_does_not_block_safe_off`, `test_diagnostics_write_failure_does_not_block_safe_off_or_change_halt_reason`.

### 8.3 `_capture_controller_exception_diagnostics` (the defensive catch-all path)

Called from `_run_cycle`'s last-resort `except Exception as exc:` handler — the one that produces `controller:unexpected:{ExcType}` halts — **before** `_halt_without_capture` collapses the exception down to just its class name. Added after a real incident (`5800_v3_real_20260813T175531Z`, cycle 38: `controller:unexpected:ValueError`) where the Pi became unreachable and non-persistent journald lost the original traceback with it, leaving a theory instead of a confirmed root cause.

Persists, via `RunRecorder.write_controller_exception_diagnostics` (persistence doc), a JSON file under `diagnostics/<cycle_index>/controller_exception.json` containing: exception type and message, the full formatted traceback (`traceback.format_exception`), the last known `CycleState` (`context.transitions[-1].state.value`), the complete transition list for that cycle, and both the capture and cycle-start monotonic timestamps. Same rules as the other diagnostics methods in this section: writes only under `diagnostics/`, never touches `runstate.json`/`cycles.csv`, and is wrapped in its own `try/except` — a *second* failure while trying to persist evidence about the *first* failure must not mask the original exception or prevent `_halt_without_capture` from running.

Unlike §8.1/8.2, this one is **not** gated on running after safe-off — it's a local filesystem write (bounded, no live hardware I/O), and the normal per-cycle commit path already writes local artifacts before mains open on the success path (persistence doc §1), so this isn't a new risk pattern.

---

## 9. Equipment refresh — `_maybe_refresh_equipment` / `_refresh_equipment`

Added after a second real incident: the camera reported `CAMERA_UNAVAILABLE` immediately, before the vision gate even reached the boot-wait phase, for three consecutive cycles mid-campaign (482–484) — root-caused to the C270 re-enumerating from `/dev/video0` to `/dev/video1` after a USB reconnect event, consistent with a wedged reader thread that only a fresh `stop()`/`start()` (not just waiting) actually clears.

Checked at the very top of `run()`'s per-cycle `while` loop, **before `_run_cycle` is even called** — guaranteed to run before mains are ever commanded closed for that `cycle_index`, never mid-cycle, never energized. Two independent triggers, either of which fires a refresh:

- **Scheduled**: every `timing.equipment_refresh_interval_cycles`-th cycle (default 50; `0` disables it).
- **Reactive**: after `timing.equipment_refresh_after_consecutive_camera_unavailable` consecutive cycles carrying `classify.DEGRADED_FLAG_CAMERA_UNAVAILABLE` in their `degraded_flags` (default 3, matching the real incident; `0` disables it). The streak is tracked in `run()`'s loop, incremented/reset right after each `_run_cycle` call based on `execution.degraded_flags`, and passed into `_maybe_refresh_equipment` on the next iteration.

Either trigger firing resets the streak to 0 — the reactive trigger always needs a *fresh* run of consecutive failures after any refresh before firing again, rather than refreshing every single cycle against a camera that's genuinely broken in a way a software refresh can't fix.

`_refresh_equipment` itself is best-effort and **never raises into the caller**: `self._camera.stop(); self._camera.start()` and `self._scope.disconnect(); self._scope.connect()` are each wrapped in their own `try/except`, logging via `self._logger.exception(...)` on failure rather than propagating. If the refresh itself fails, the cycle proceeds normally on the existing connection — any real underlying problem still surfaces through the cycle's own proper halt path (and, since the CLI/lifecycle doc §8, gets auto-retried up to the normal streak limits) rather than being masked by a confusing failure inside a maintenance operation.

**Deliberately does not attempt to distinguish "worth refreshing" from a scope already marked permanently unusable** after an unrecoverable diagnostics timeout (`ScopeReal._connection_unusable`, HAL doc — set once and never cleared, per `scope-trigger-debug-log.md` Entry 6's segfault-risk reasoning). `connect()` simply raises again in that case, exactly as it would on the next real scope operation regardless — refresh isn't trying to be smarter than that flag, just to catch the milder degradation case it wasn't designed for.

`Sequencer` calls `self._camera.stop()`/`start()` directly here — this is new; previously `Sequencer` never touched camera lifecycle at all, only `_execute_campaign` did (once, bracketing the whole campaign). The scope's `connect()`/`disconnect()` were already `Sequencer`-owned (§4), just previously only ever called once each, at the very top/bottom of `run()`.

---

## 10. `FaultCategory` — the other halt taxonomy

Five values, always paired with a `Terminal` and a free-text `reason` string as `f"{category.value}:{reason}"` in `halt_reason`:

| Category | Meaning | Example reasons |
|---|---|---|
| `DUT` | The device under test failed | `dut_no_trip` |
| `RIG` | Something about the test rig itself | `scope_never_triggered_or_acquire_timeout`, `k1_k2_command_mismatch`, `scope_stale_trigger_event_before_arm`, `k3_pretrigger_current_detected` |
| `PERIPHERAL` | (Reserved; not raised anywhere in the current sequencer — camera failures degrade rather than halt) | — |
| `PERSISTENCE` | Storage/recording layer failure | `insufficient_disk_space`, `persistence_error:{ExcType}` |
| `CONTROLLER` | Software itself misbehaved | `unexpected:{ExcType}` |

---

## 11. Internal control-flow exceptions

`_SequencerHalt` and `_RetryCycle` are both frozen dataclasses that subclass `Exception` — used purely as typed signals to unwind the call stack straight to `_run_cycle`'s single handler, rather than threading a halt/retry decision back up through every intermediate return value. The module docstring calls this out as the explicit alternative to "an ad hoc chain of nested conditionals." `_SequencerHalt` carries `terminal`/`category`/`reason` directly; `_RetryCycle` carries just `reason` (its terminal/category are implicit — it either succeeds on retry or becomes `_retry_exhausted` under `RIG`/`HALTED`).

---

## 12. Config values this module reads

All under `AppConfig`, resolved once at construction or read fresh per-cycle from `self._config`:

| Field | Used for |
|---|---|
| `timing.cooldown_s` (10s) | Post-success dwell before the next cycle |
| `timing.cooldown_retry_s` (60s) | Dwell after a `_RetryCycle` before re-attempting, and (CLI/lifecycle doc §8) between auto-retried halts |
| `timing.boot_timeout_s` (90s) | Charging-gate wait timeout |
| `timing.scope_arm_timeout_s` (2.0s) | Each `_poll_scope_armed` call's budget |
| `timing.scope_acquisition_timeout_s` (5s) | `_poll_acquisition_with_backstop`'s outer bound |
| `timing.k3_backstop_s` (0.300s) | The hard K3 backstop deadline |
| `timing.mains_stagger_ms` (0) | Grace window before a K1/K2 mismatch halts |
| `timing.equipment_refresh_interval_cycles` (50) | Scheduled camera/scope refresh interval (§9); `0` disables |
| `timing.equipment_refresh_after_consecutive_camera_unavailable` (3) | Reactive camera/scope refresh trigger (§9); `0` disables |
| `paths.min_free_disk_gb` (2) | Pre-cycle disk-space gate |
| `vision.roi_*`, `vision.charging_green_*` | Built once into `_vision_roi`/`_vision_optical_config` |
| `analysis.*` | Passed through unchanged to `analyze_waveform` |

`_FORCED_DIAGNOSTIC_DELAY_S = 0.1` (100ms) is **not** config-driven — it's a module-level constant, deliberately: it's a diagnostic implementation detail, not a per-deployment tunable, and keeping it out of `config.yaml` means it can't accidentally get set to something close to or past the 300ms backstop.

---

## 13. Test coverage map

| Behavior | Test(s) |
|---|---|
| Happy path, full pass | `test_normal_pass_cycle_completes` |
| Vision retry (blinking red) succeeds on 2nd attempt | `test_red_timeout_retries_once_then_succeeds` |
| Vision retry exhausted → halt | `test_red_timeout_retry_exhausted_halts` |
| Non-retryable vision timeouts | `test_blue_timeout_halts_without_retry`, `test_off_timeout_halts_without_retry` |
| Camera degrade-and-continue | `test_camera_failure_degrades_and_continues` |
| Genuine no-trigger halt | `test_scope_never_triggered_halts_as_rig_fault` |
| Triggered-but-incomplete reclassification | `test_scope_triggered_but_acquisition_not_completed_reclassifies_halt` |
| TER unavailable → generic reason preserved | `test_scope_never_triggered_keeps_generic_reason_when_ter_unavailable` |
| Forced-diagnostic capture, TER still 0 | `test_forced_diagnostic_capture_when_ter_still_zero` |
| Forced-diagnostic skipped, TER already 1 | `test_forced_diagnostic_capture_skipped_when_trigger_event_already_confirmed` |
| Entry 15 regression (natural trigger at forced checkpoint still counts) | `test_natural_trigger_discovered_at_forced_checkpoint_still_counts_as_success` |
| Baseline TER clear-then-verify | `test_stale_trigger_event_before_arm_is_cleared_and_does_not_halt`, `test_stuck_trigger_event_before_arm_halts_before_arming`, `test_trigger_event_between_baseline_reads_halts_before_arming` |
| Pre-injection TER halts before K3 ever closes | `test_trigger_event_before_injection_never_closes_k3` |
| Diagnostics ordering/robustness | `test_diagnostics_capture_failure_does_not_block_safe_off`, `test_diagnostics_write_failure_does_not_block_safe_off_or_change_halt_reason`, `test_diagnostics_capture_never_recloses_k3_or_rearms`, `test_diagnostics_capture_happens_only_after_full_safe_off` |
| Scope config rejected → never arms/injects | `test_rejected_configuration_command_blocks_arming_and_k3_injection` |
| Armed acquisition consumed before injection | `test_scope_consumed_before_injection_never_closes_k3`, `test_scope_consumed_during_diagnostic_delay_never_closes_k3` |
| Backstop fires ahead of a blocking acquisition poll | `test_k3_backstop_opens_before_blocking_acquisition_timeout` |
| Pretrigger leakage / no-trip / mismatch halts | `test_pretrigger_leakage_halts_as_rig_fault`, `test_no_trip_halts_as_dut_fault`, `test_mains_mismatch_halts` |
| Stuck-booting timeout | `test_stuck_booting_timeout_halts` |
| Fast green-flash gate grant | `test_green_flashing_grants_charging_quickly` |
| Every fault-matrix row, end to end | `tests/test_faultmatrix.py::FaultMatrixTests` (one test per row — disk, scope comms drop/reconnect, K1/K2 mismatch, Cronitor swallow-failure, camera degrade, all vision-timeout variants, etc.) |
| `safe_off` itself | `tests/test_safety.py` |
| Timeout-boundary `ValueError` never reaches the HAL; K3 still opens exactly once at the boundary | (see §5.7.3) — regression tests in `tests/test_sequencer.py` covering exact-zero and slightly-negative remaining time |
| Periodic/reactive equipment refresh, refresh-failure isolation | (see §9) — `tests/test_main.py` (`test_equipment_refresh_*`), since the fixed-interval/reactive-trigger decision lives in `run()` but is most directly exercised end-to-end via `_execute_campaign`'s tests |

---

## 14. Things to know if you're about to change this file

- Any new halt path must decide, deliberately, whether it's a `_SequencerHalt` (immediate) or should be added to the five-way `except` list in `_run_cycle` — don't invent a sixth ad hoc branch.
- Anything added to the acquisition poll loop (§5.7) must not add unbounded blocking calls — the backstop's safety property depends on the loop's own cadence staying near 10ms even under a slow/hanging scope call.
- Any new timeout-driven value passed to a HAL call that validates `timeout_s > 0` (§5.7.3) must be computed fresh, immediately before the call — never carried forward from an earlier read in the same loop iteration, even if it looks like the same instant.
- Equipment refresh (§9) must keep running strictly before `_run_cycle` is called for that `cycle_index` — never move it inside `_attempt_cycle` itself, since retry (`_RetryCycle`) can re-enter `_attempt_cycle` for the same `cycle_index` and would double-fire the refresh.
- Diagnostic capture code (§8) must stay strictly downstream of `_open_mains_with_cooldown` — this is the single most safety-relevant ordering constraint in the file, and it's enforced by convention/tests, not by the type system, so it's easy to violate accidentally in a refactor.
- If you touch `:TER?` handling anywhere, remember it's read-and-clear — a new read added anywhere will silently consume the flag an existing checkpoint depends on.
