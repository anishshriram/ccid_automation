# Scope Trigger Debugging Log

Running log of the oscilloscope no-trigger investigation. Every automated
cycle currently halts with `rig:scope_never_triggered_or_acquire_timeout`.
Append new entries as more hardware testing happens - keep entries in
chronological order, and keep the "Current status" section at the top
up to date rather than buried at the bottom.

---

## Current status (as of 2026-08-10)

**Not solved, but narrowed to a genuine no-trigger condition - do not touch
normal trigger settings again.** Entry 9's config-error-blocking fix has
been **confirmed clean on real hardware** (operator-verified `configure
--real` check: no rejected commands, no exception). Entry 10's TER
instrumentation has now also run on a real energized cycle: `TER = 0`,
`operation_condition = 40`, `hal_status = ACQUIRING` for the full 306.6 ms
K3-closed window. Unlike the Entry 8 reading that motivated Entry 10 (`TER =
+1`, prompting the "triggered but stuck" hypothesis), this run's TER stayed
0 throughout - a **proven, confirmed genuine no-trigger condition**, not a
triggered-but-uncompleted one. Per operator instruction: trigger mode,
coupling, edge source/slope/level, and probe ratio (Entries 1, 7, 8) have
now all been checked and correctly configured multiple times without
resolving the halt - **do not modify normal trigger settings again** without
new evidence specifically implicating one.

Entry 11 adds a diagnostic-only forced capture: if TER is still 0
~100 ms after K3 closes, `:TRIGger:FORCe` (confirmed via the official
Keysight InfiniiVision 2000 X-Series Programmer's Guide - this exact model
family, write-only, no query form, equivalent to the front-panel [Force
Trigger] key) forces the pending single-shot acquisition to complete so the
resulting waveform shows what the analog front end actually looked like
partway through the closed window. The command itself fires live, but the
data transfer is deferred until after full safe-off so it can never delay
the unchanged 300 ms K3 backstop. Data is written only under
`diagnostics/<cycle>/`, explicitly labeled
`"capture_type": "forced_diagnostic_non_measurement"`, and never reaches
`analyze_waveform`, PASS/FAIL, or trip-time calculation. **Untested against
real hardware** - the next energized cycle is the first real test of
whether the forced capture actually shows something diagnostically useful
about why the real trigger never fires.

Also still unconfirmed independently on this unit (though now strongly
supported by the official manual, which explicitly states `:TER?` is
read-and-clear): whether that documented behavior is what this specific
MSO-X 2014A actually does - the project's own experience with
`:TRIGger:NREJect` (documented, but confirmed unsupported on this unit in
Entry 9) is a standing reason not to fully equate "documented" with
"confirmed on this hardware."

Entry 7's probe-ratio-ordering fix was tried on a real
energized cycle (Entry 8) and did **not** fix it - channel/trigger-edge
settings all read back exactly as configured (trigger mode EDGE, source
CHAN1, positive slope, +20V, probe 10x, scale 50V/div, AC channel
coupling), K3 backstop opened correctly at ~308ms, and acquisition still
never completed (`operation_condition = 40` the whole time - the scope
entered a normal "armed" state and just never left it). That data ruled
out probe ratio as the (sole) explanation and pointed at two previously
unexamined areas: trigger coupling/noise-reject (never touched or even
read before now), and whether the operation-condition run bit actually
proves "waiting for the intended trigger" as opposed to something else
that also keeps it set. Entry 8 addresses both, plus adds a `*OPC?`
synchronization barrier after configuration. **Untested against real
hardware.**

Earlier: a strong candidate root cause was found and fixed (Entry 7) via
code review rather than another live trial: `:CHANnel1:PROBe` was being
sent *after* `:CHANnel1:SCALe`/`:OFFSet` in `configure_for_cycle`. On
Keysight scopes, scale/offset are interpreted "at the probe tip" using
whatever probe ratio is already active - setting them first applies them
against a stale ratio, silently leaving the actual digitized range off by
the probe factor while every readback still looks correct. Two other things have been ruled out
(trigger mode, and previously: AC coupling, centered timebase, STOP
verification, double-arm-check).

The first version of timeout diagnostics capture (Entry 2) wedged the real
scope's USBTMC interface badly enough to require a physical AC power cycle
to recover (Entry 3). A corrective version (Entry 4) reorders diagnostics
to run only after full safe-off and adds a hard per-query timeout,
fail-fast-on-first-failure, and a device-clear-first step.

That corrective version was dry-run against the real scope (Entry 5): it
completed in 0.001s without wedging anything, and `*IDN?` still worked
afterward - the sequencing fix works. The `.clear()` handling was then
fixed for `VI_ERROR_NSUP_OPER` (Entry 5) and dry-run again (Entry 6).

**Entry 6 found a serious flaw in the timeout mechanism itself**, not the
sequencing: the daemon-thread-based per-query timeout gave up waiting on a
slow `:WAVeform:POINts?` query, but the abandoned thread stayed blocked
inside libusb - and when the main thread went on to touch the same
connection afterward, the two threads raced and the process **segfaulted**.
The scope needed another full AC-power-removal to recover. The
thread-based timeout design has been removed entirely and replaced with
PyVISA's native, synchronous per-resource `.timeout` (no thread involved -
the bound is enforced by the transport call itself, not by Python giving
up on waiting for it). `:WAVeform:POINts?` was also dropped from the
diagnostics query list entirely (nonessential, and the one query that was
slow). **This redesign has not yet been tested against real hardware** -
that is the next required dry run, and per the operator's explicit
instruction, no further real-scope test should happen until it's confirmed
no background VISA operation can ever outlive a timed-out call.

---

## Entry 1 - 2026-08-10 - `:TRIGger:MODE EDGE` fix

**What was tried:** Read `ccid/hal/scope_real.py`'s `configure_for_cycle()`
and found it sent `:TRIGger:EDGE:SOURce`/`:LEVel`/`:SLOPe` but never sent
`:TRIGger:MODE EDGE`. On Keysight InfiniiVision scopes, the `:TRIGger:EDGE:*`
parameter commands are inert unless `:TRIGger:MODE` is explicitly `EDGE` -
the scope otherwise keeps triggering on whatever mode was last active on
the front panel (Pattern, Glitch, etc.). This looked like a strong
candidate: it explained why isolated bench checks and manual front-panel
Single presses "worked" (front-panel navigation sets Edge mode) while fully
automated runs never triggered.

**Commit:** `4d8b508` - "Set trigger mode to EDGE before configuring edge
trigger parameters." Added a regression test asserting `:TRIGger:MODE EDGE`
is sent before any `:TRIGger:EDGE:*` command.

**Result: did not fix it.** A supervised live run after this fix
(`full_real_edge_mode_20260810T150245Z`) reproduced the exact same halt.
Readback confirmed `:TRIGger:MODE?` correctly returned `EDGE` and all other
settings looked correct, but the scope still showed `Trig'd?` (never
actually triggered) on a flat trace at the end of the run.

**What this tells us:** the trigger-mode bug was real and worth fixing
(it's still correct to have `:TRIGger:MODE EDGE` in the command sequence),
but it was not the (or not the only) cause of the no-trigger condition.
Whatever is actually happening is still unknown.

---

## Entry 2 - 2026-08-10 - Timeout diagnostics capability added

**What was done:** Since repeated no-trigger halts were preserving zero
evidence about scope state at the moment of failure, implemented a
best-effort, read-only diagnostics capture that fires on
`rig:scope_never_triggered_or_acquire_timeout`, after K3 is confirmed open
and before K1/K2 open.

Captures (on a real scope, via direct queries that bypass the normal
reconnect-retry logic so a wedged instrument can't delay safe-off):
operation-condition register, active trigger mode/sweep/edge
source/slope/level, channel 1 display/coupling/scale/offset/probe
ratio/bandwidth-limit/invert, timebase scale/reference, acquisition type,
waveform source/format/points, a bounded (≤20) drain of the SCPI error
queue, and a display screenshot (`:DISPlay:DATA? PNG` - the one query here
already proven working on real hardware via `capture_after_acquire`).

Persisted to `runs/<run_id>/diagnostics/<cycle_index>/`:
- `scope_timeout.png` - display screenshot at the moment of timeout
- `scope_state.json` - all the readbacks above, plus K3 close/open
  timestamps, duration, backstop-vs-timeout reason, and the primary halt
  reason
- `scope_errors.txt` - drained error queue, or an explicit "no errors" line

Explicitly does not touch `runstate.json`, `cycles.csv`, or any normal
per-cycle artifact path - it cannot advance `last_completed_cycle` or be
mistaken for a completed acquisition. A failure capturing or writing
diagnostics is logged and swallowed; it can never block K1/K2 safe-off or
change the halt reason.

**Commit:** (pending - see below)

**Testing:** 18 new unit tests across `test_scope_real.py`,
`test_scope_sim.py`, `test_scope_protocol.py`, `test_recorder.py`,
`test_sequencer.py`, plus extended assertions in `test_faultmatrix.py`.
Full suite: 292 tests, OK, 2 intentional skips (same 2 as before - no
regressions). All software-only; nothing here has touched real hardware
yet.

**What this tells us:** nothing about the actual root cause yet - this is
instrumentation, not a fix. The value is entirely in what the *next*
supervised energized cycle's diagnostics bundle will show.

---

## Next steps

1. Before another energized cycle: do a de-energized dry run on the Pi
   that deliberately forces a scope timeout (e.g. `never_triggered`-style
   scenario against the real scope, or simply let a de-energized cycle
   time out) and confirm `capture_timeout_diagnostics()` actually queries
   the real instrument cleanly - i.e. confirm the new SCPI queries
   (`:TRIGger:MODE?`, `:CHANnel1:BWLimit?`, `:CHANnel1:INVert?`, etc.)
   return sane values on this specific scope, not just on the fake/sim
   test doubles. These were written from the Keysight command-tree
   convention already used elsewhere in `scope_real.py`, not verified
   against the physical MSO-X 2014A.
2. Once diagnostics capture is confirmed working, perform one supervised
   energized cycle. If it times out again, inspect
   `runs/<run_id>/diagnostics/1/` before proposing any further energized
   attempt - do not just rerun.
3. Use the handoff document's interpretation guide (flat display, waveform
   below +20V, negative-only waveform, "Trig'd" before K3, armed with no
   waveform during K3) to narrow down what the screenshot + `scope_state.json`
   actually show.


## Entry 3 - 2026-08-10 - Real-scope diagnostics exposed USBTMC timeout risk

**What was tried:** A de-energized dry run invoked the timeout-diagnostics method against the physical MSO-X 2014A. EVSE mains and all three 12 V contactor supplies were off.

**Result:** The operation appeared to hang and required termination from a second SSH session. A completed attempt later reported operation_condition = -1, png_bytes = 0, and query_failures = 20. Every scope operation failed with Errno 110: Operation timed out. Basic IDN communication also failed afterward.

USB reconnect, a normal scope power cycle, a Pi reboot, a replacement USB cable, and a different Pi USB port did not restore communication. Linux continued to detect the correct scope, serial number, VISA resource, and USBTMC interface.

A true cold restart of the oscilloscope, including removing its AC power for 60 seconds, restored communication. After recovery, the IDN query returned AGILENT TECHNOLOGIES, MSO-X 2014A, MY58100795, 02.43.2018020635 with exit status 0.

**Commit:** 39a801e contains the initial timeout-diagnostics implementation. A corrective commit is required.

**What this tells us:** Diagnostics must not run while K1/K2 remain energized. The corrected sequence must open K3, open K2/K1, and only then attempt diagnostics. Diagnostic operations require strict per-query and overall time limits. Failures must preserve partial evidence and must never replace the primary halt reason, rig:scope_never_triggered_or_acquire_timeout.

---

## Entry 4 - 2026-08-10 - Corrective fix for the USBTMC wedge (Entry 3)

**What was tried:** Redesigned `capture_timeout_diagnostics()` per Entry 3's findings, in two parts:

1. **Sequencing** (`ccid/sequencer.py`): diagnostics now run strictly after `_open_mains_with_cooldown` (full safe-off - K1, K2, and K3 all commanded open), not between K3-open and K1/K2-open as in the original version. A hung diagnostics call can no longer delay de-energizing the EVSE mains.
2. **Real-driver hardening** (`ccid/hal/scope_real.py`):
   - Every query now runs through a new `_run_with_timeout()` helper that executes the call in a daemon thread and bounds the wait with `Thread.join(timeout=...)` (default 1.0 s per query), since PyVISA's own configured timeout did not reliably bound the wedged call in Entry 3.
   - The whole capture is bounded by a total wall-clock budget (default 5.0 s), checked before each query.
   - Behavior changed from best-effort-continue-on-every-field to **fail-fast**: the first query failure (including a failed device clear) now aborts the entire capture immediately rather than continuing to fire more queries at a potentially unhealthy connection. Entry 3's evidence is that continuing after a failure is what plausibly desynced the USBTMC session badly enough to need a physical power cycle.
   - Added a `self._inst.clear()` (VISA/USBTMC device clear) as the very first operation, before any query - the standard fix for a desynced write/read session. If it fails, diagnostics aborts immediately without sending a single query.

**Commit:** `27d5c71`

**Testing:** 7 new/rewritten tests in `test_scope_real.py` (device-clear-first, abort-on-clear-failure, fail-fast-after-first-failure, hard per-query timeout via an actually-slow fake query, total-budget enforcement) and one new test in `test_sequencer.py` - `test_diagnostics_capture_happens_only_after_full_safe_off` - which snapshots commanded contactor state at the exact moment diagnostics capture is invoked and asserts K1, K2, and K3 are all open. This is the test that would have caught the Entry 3 incident directly; it did not exist before this entry. Full suite: 297 tests, OK, same 2 intentional skips. All software-only.

**What this tells us:** Nothing new about the scope's actual no-trigger root cause - this only corrects a defect in the diagnostics tooling itself. **Not yet verified against real hardware** - the device-clear call, the per-query timeout's interaction with the real USBTMC backend, and whether the fail-fast behavior actually prevents a repeat of Entry 3 are all unconfirmed. Treat the next real-scope dry run as the first real test of this fix, not a formality.

---

## Entry 5 - 2026-08-10 - Real dry run of the corrective fix: sequencing/timeout works, `.clear()` is unsupported on this backend

**What was tried:** A real de-energized dry run of the Entry 4 corrective diagnostics implementation against the physical MSO-X 2014A.

**Result:** Completed in 0.001s - no wedging, no hang. `self._inst.clear()` raised `VI_ERROR_NSUP_OPER` (PyVISA-Py does not implement device clear on this backend/resource type). Because the fail-fast logic treated *any* clear failure as grounds to abort, diagnostics stopped immediately without sending a single query. A subsequent `*IDN?` succeeded, confirming the scope's communication stayed healthy throughout - unlike Entry 3, nothing was left in a bad state.

**Commit:** `fc9d799`

**What this tells us:** The sequencing fix (diagnostics after full safe-off) held up - the 0.001s completion time and healthy post-run `*IDN?` are real evidence of that. **Correction, added after Entry 6:** the claim below that "the hard-timeout mechanism holds up on real hardware" was premature - this run aborted at the `.clear()` step before a single query was attempted, so the per-query timeout was never actually exercised here. It was exercised for the first time in Entry 6, and that is what segfaulted the process. Treat this entry as confirming the sequencing fix only.

Fixed here: `VI_ERROR_NSUP_OPER` specifically is now recorded in `settings["device_clear"]` and diagnostics proceeds to the normal bounded, fail-fast queries; every other clear failure still aborts immediately as before. New regression test: `test_timeout_diagnostics_continues_when_device_clear_unsupported`.

---

## Entry 6 - 2026-08-10 - Real dry run segfaulted: daemon-thread timeout raced with the main thread and crashed the process

**What was tried:** A real de-energized dry run of the Entry 5 diagnostics implementation (with the `VI_ERROR_NSUP_OPER` fix) against the physical MSO-X 2014A.

**Result:** Most diagnostic queries succeeded, but `:WAVeform:POINts?` exceeded the 1.0s daemon-thread timeout. `_run_with_timeout()` gave up waiting and returned - but the thread it had spawned was still blocked inside libusb, and kept running in the background. During disconnect or interpreter cleanup, libusb printed `[libusb_open] open 0.0` and Python crashed with a **segmentation fault**. The scope's USBTMC interface again required a full AC-power removal to recover.

**Commit:** (pending)

**What this tells us:** The daemon-thread-based timeout design (Entry 4) was fundamentally unsafe, not just imperfect: giving up on waiting for a call from a supervising thread does not stop the call - the real libusb operation kept running unbounded in the background, and letting the main thread proceed to touch the same PyVISA session (including eventual `disconnect()`) while that background operation might still be in flight is a genuine data race at the C-library level, which is what produced the segfault, not just a hang.

Fixed by removing the threading approach entirely:
- Every diagnostics query is now synchronous, in the same thread, bounded by PyVISA's own native per-resource `.timeout` (set once, in milliseconds, before any query) - the actual transport call is what gets bounded, at the libusb/kernel level, not just how long our Python code waits for it.
- On any query failure, the connection is marked permanently unusable for the rest of the process (`self._connection_unusable`): `connect()` refuses to reconnect, `disconnect()` skips `.close()` entirely and just drops the references, and `_require_connected()` (used by every other real-driver method) raises immediately. Nothing touches a connection whose transport state can't be trusted again.
- `:WAVeform:POINts?` - the query that stalled - was removed from the diagnostics query list. It isn't essential (`waveform_points_mode`/`waveform_format`/`waveform_source` already describe the waveform subsystem configuration) and isn't worth the risk of being the one query that destabilizes the transport again.

Safe-off ordering, the total time budget, and the primary halt reason are all unchanged. New tests: `test_timeout_diagnostics_sets_native_visa_timeout_before_queries`, `test_timeout_diagnostics_marks_connection_unusable_after_query_failure`, `test_timeout_diagnostics_does_not_mark_connection_unusable_on_success`, and directly answering the requirement to prove no background operation survives a call - `test_timeout_diagnostics_spawns_no_background_threads` and `test_timeout_diagnostics_spawns_no_background_threads_on_failure`, both asserting `threading.active_count()` is unchanged before/after (success and failure cases). Full suite: 302 tests, OK, same 2 intentional skips. **Not yet tested against real hardware.**

---

## Entry 7 - 2026-08-10 - Code review found a channel probe-ratio ordering bug (untested)

**What was tried:** A focused code review of the scope acquisition state machine (`ScopeReal.configure_for_cycle`/`arm_single`/`wait_until_armed`/`wait_until_acquisition_complete`/`_run_bit_set` and the sequencer's `_poll_scope_armed`/`_poll_acquisition_with_backstop`), asked directly: is something in this logic causing the no-trigger condition, not another live trial.

**Result:** `configure_for_cycle` sent `:CHANnel1:SCALe`/`:CHANnel1:OFFSet` before `:CHANnel1:PROBe`. On Keysight scopes, scale/offset are interpreted "at the probe tip" using whatever probe ratio is already active at the time they're sent - so every cycle's `50 V/div` was being applied against a stale probe ratio (possibly `1x` left over from a prior session), then silently reinterpreted once `:CHANnel1:PROBe 10` landed a few commands later. `:CHANnel1:SCALe?` would still read back `50.0` afterward - this bug is invisible to a settings readback, and invisible to `ScopeSim`, which doesn't model probe-ratio-to-scale physics at all.

**Commit:** `bf62eb9`

**What this tells us:** This is a strong, internally-consistent candidate for the actual root cause, not just another ruled-out setting:
- A real burst reinterpreted through a stale probe ratio could appear as only a fraction of its true voltage in the scope's actual digitized range - consistent with the flat/near-invisible trace in the most recent screenshot and the earlier "no visible CH1 waveform" observations with K1/K2 only.
- It explains why the trigger never fires at +20 V even though the physical signal is clearly present (EVSE faults red).
- It explains why manual front-panel Single presses worked and automated cycles never did: front-panel operation is stateful (probe ratio already correct from a prior session), while `configure_for_cycle()` reconfigures from scratch every cycle, hitting the ordering bug every time.
- It explains why 300+ passing tests never caught this: the simulator has no equivalent physics to get wrong.

Fixed: `:CHANnel1:PROBe` now sent first, before `:CHANnel1:SCALe`/`:CHANnel1:OFFSet`/`:CHANnel1:COUPling`. New regression test: `test_configure_sets_probe_ratio_before_scale_and_offset`. **Not yet tested against real hardware** - this is a hypothesis based on documented SCPI scope programming convention, not a confirmed fix. The next real dry run (once diagnostics capture is itself confirmed safe per Entry 6) is what actually tests it.

---

## Entry 8 - 2026-08-10 - Real trial: probe-ratio fix didn't solve it; trigger coupling/noise-reject/OPC-sync/TER added

**What was tried:** A real energized cycle with the Entry 7 probe-ratio fix in place.

**Result:** Failed the same way. Diagnostics captured cleanly this time (no wedge, no segfault - Entries 6/7's fixes held): `operation_condition = 40` for the whole acquisition window, `hal_status` stuck at `ACQUIRING`. Settings readback: trigger mode EDGE, source CHAN1, positive slope, +20V level, probe ratio 10x, channel scale 50V/div, channel coupling AC - all exactly as configured. K3 remained closed ~308ms then opened via the backstop (300ms configured + overhead) - the safety mechanism worked correctly. No acquisition ever completed.

**Commit:** (pending)

**What this tells us:** Every channel/trigger-edge setting we can see is correct, and it still doesn't trigger - this rules out probe ratio as the sole explanation and narrows the remaining unknowns to two things nothing has touched or read before: (1) trigger coupling and noise reject, which are separate from channel coupling and control what the trigger *comparator* sees, not what gets digitized; (2) whether the operation-condition run bit (bit 3, `operation_condition`) actually proves "armed and waiting for the configured edge" as opposed to some other state that also happens to keep bit 3 set (e.g. repeated re-triggering without ever reaching Stop) - `operation_condition = 40` matches the exact "just armed" signature from the original handoff's own real-hardware characterization, which is at least consistent with genuine waiting, but doesn't prove it.

Addressed both, plus a related synchronization gap noticed during the review - `configure_for_cycle` had no barrier ensuring the scope finished processing configuration before `arm_single()` fires immediately after:
- New `ScopeSettings.trigger_coupling` field, default `DC` (not AC) - the trigger comparator needs the raw absolute voltage for a one-shot transient against a fixed level; AC-coupling the trigger path independently high-pass filters the signal, letting the effective 0V reference drift with recent signal history instead of staying fixed.
- `:TRIGger:NREJect OFF` sent unconditionally (locked, like `:TRIGger:MODE EDGE` - there's exactly one correct value for this application, not a per-deployment tunable). Noise reject adds trigger-comparator hysteresis, raising the effective threshold above the configured level.
- `*OPC?` synchronization barrier added at the end of `configure_for_cycle`, before returning - blocks until every queued command has actually finished executing, not just been sent.
- `:TRIGger:COUPling?`, `:TRIGger:NREJect?`, and `:TER?` (trigger event register - a top-level status register, separate from the operation condition register, that directly answers "has a trigger event occurred" independent of whether acquisition ever reached Stop) added to the timeout-diagnostics query list.

New tests: `test_configure_sets_trigger_coupling_dc_and_disables_noise_reject`, `test_configure_sends_opc_sync_barrier_after_commands`, plus diagnostics assertions for the three new settings keys. Full suite: 305 tests, OK, same 2 intentional skips.

---

## Entry 9 - 2026-08-10 - Rejected configuration commands must block the cycle, not be silently absorbed

**What was tried:** A `configure --real` check of Entry 8 found `-113,"Undefined header"` in the error queue. Rather than guess which of the two new trigger commands was unsupported, an interim fix (commit `99b8e37`) had the scope drain and discard any configuration error, treating it as non-fatal so cycles could keep running regardless of which command was bad. The operator then determined via direct real-hardware testing which command was actually the problem: `:TRIGger:NREJect` is unsupported on this MSO-X 2014A (confirmed unsupported), `:TRIGger:COUPling` is supported and reads back correctly as `DC` (confirmed working).

**Commit:** (pending)

**What this tells us:** Discarding configuration errors was the wrong default, not just an imprecise one - reverted (commit `99b8e37` reverted via `git revert`). If a scope rejects any configuration command, the resulting state is only partially known: proceeding to arm and inject leakage current against a configuration that wasn't fully applied is a bigger risk than halting the cycle. Corrected:

- `:TRIGger:NREJect OFF` removed from `configure_for_cycle` entirely - not made conditional, not retried, just not sent, since it's confirmed unsupported on this instrument.
- `:TRIGger:COUPling {trigger_coupling}` kept as-is (confirmed working).
- `:TRIGger:NREJect?` removed from the timeout-diagnostics query list (querying a confirmed-unsupported command wastes a diagnostics slot for no benefit).
- After `*OPC?`, `configure_for_cycle` now drains the error queue into a bounded list (`_drain_configuration_errors`, same 20-read bound as elsewhere) and, if it's nonempty, raises a new `ScopeConfigurationError` naming every rejected command. This relies on the sequencer's existing exception handling - `_attempt_cycle` only calls `arm_single()`/`close_k3()` after `configure_for_cycle()` returns normally, so the exception reaches the run loop before either can happen, and `Sequencer.run()`'s `finally` still opens K1/K2 via `safe_off()` regardless. No sequencer changes were needed - the existing `CcidError` halt path already does exactly the right thing.

New tests: `test_configure_raises_when_scope_rejects_a_configuration_command`, `test_configure_raises_with_all_rejected_commands_listed`, `test_configure_error_drain_is_bounded_and_still_raises`, `test_configure_does_not_raise_when_error_queue_is_clean` (`test_scope_real.py`), and `test_rejected_configuration_command_blocks_arming_and_k3_injection` (`test_sequencer.py`) - the last one is the test that actually proves the safety property: a scope whose `configure_for_cycle` raises never gets `close_k3` called, and K1/K2/K3 all end up open. Full suite: 310 tests, OK, same 2 intentional skips. **Not yet tested against real hardware** - the next `configure --real` check should come back clean with no error and no exception raised.

---

## Entry 10 - 2026-08-10 - Trigger event register instrumentation: distinguish "triggered but stuck" from "never triggered"

**What was tried:** A real energized cycle's post-timeout diagnostics bundle
(Entry 8's `:TER?` query) showed `trigger_event_register = +1` while
`operation_condition` stayed at `40` (run bit set) for the entire acquisition
window and `hal_status` stayed `ACQUIRING`. Since `:TER?` is a top-level
status register answering "has a trigger event occurred" independent of the
operation-condition run bit, this is direct evidence that the trigger
comparator fired even though the acquisition subsystem never reported Stop -
a materially different condition from a genuine no-trigger, previously
indistinguishable under the single `scope_never_triggered_or_acquire_timeout`
halt reason.

Added:
- `ScopeInterface.read_trigger_event_register() -> bool` (new abstract
  method), implemented in `ScopeReal` (`:TER?` via the normal retried
  `_query`) and `ScopeSim` (scenario-driven, read-and-clear semantics
  modeled explicitly for tests).
- A baseline checkpoint read immediately after `configure_for_cycle`
  returns, before `arm_single()`: a latched trigger event here means either
  stale state from a prior cycle/session or a spurious trigger during
  configuration, so the cycle halts (`scope_stale_trigger_event_before_arm`)
  rather than arming against an unknown baseline.
- A second checkpoint read at the existing pre-injection recheck (after the
  0.05 s settle sleep, before K3 closes) alongside the existing
  `wait_until_armed` recheck: a latched trigger event here means something
  fired before the deliberate K3 close, so the resulting waveform would not
  correspond to the intended transient. Halts with
  `scope_trigger_event_before_injection` and, like the existing armed
  recheck, never calls `close_k3`.
- Reclassification of the post-timeout halt: `_capture_timeout_diagnostics_best_effort`
  now inspects the `trigger_event_register` value already present in the
  diagnostics bundle it captures (no new query) and returns
  `scope_triggered_but_acquisition_not_completed` instead of the generic
  `scope_never_triggered_or_acquire_timeout` when that value is confirmed
  `1`. A missing key (diagnostics aborted early) or an unparseable value
  falls back to the generic reason - the classification only fires on an
  unambiguous positive reading, never as a default.

Deliberately **not** done: polling `:TER?` inside the 10 ms
`wait_until_acquisition_complete`/K3-backstop loop. That loop's timing
margin is what the 300 ms K3 hard backstop depends on; doubling the SCPI
round-trips per tick (`:OPERegister:CONDition?` plus `:TER?`) risks eating
into that margin for a question the post-timeout diagnostics bundle already
answers without any additional live query during the energized window.

**Commit:** (pending)

**Result:** Not yet tried against real hardware.

**What this tells us:** Nothing new about root cause yet by itself - this is
instrumentation and halt-reason classification, same as Entry 2's diagnostics
capture was. Its value is in what the *next* real timeout's halt reason
says: if `scope_triggered_but_acquisition_not_completed` appears, that
confirms the trigger-comparator/acquisition-complete distinction as real and
redirects the investigation toward why the acquisition subsystem doesn't
recognize a trigger it demonstrably received (holdoff, memory depth,
acquire-type interaction, or something not yet examined) rather than toward
trigger-condition settings, which have now been checked repeatedly (Entries
1, 7, 8) without resolving the halt. If the generic reason still appears
instead, that's evidence the Entry 8 diagnostics observation was specific to
that one cycle rather than a reproducible pattern.

New tests: `test_read_trigger_event_register_false_when_clear`,
`test_read_trigger_event_register_true_when_latched` (`test_scope_real.py`);
`test_trigger_event_register_clear_by_default`,
`test_trigger_event_register_latched_stale_before_arm`,
`test_trigger_event_register_latched_before_injection` (`test_scope_sim.py`);
`test_scope_triggered_but_acquisition_not_completed_reclassifies_halt`,
`test_scope_never_triggered_keeps_generic_reason_when_ter_unavailable`,
`test_stale_trigger_event_before_arm_halts_before_arming`,
`test_trigger_event_before_injection_never_closes_k3` (`test_sequencer.py`);
`test_scope_triggered_but_acquisition_not_completed_row`,
`test_scope_stale_trigger_event_before_arm_row`,
`test_scope_trigger_event_before_injection_row` (`test_faultmatrix.py`). Full
suite: 322 tests, OK, same 2 intentional skips.

---

## Entry 11 - 2026-08-10 - Diagnostic-only forced trigger when TER stays 0 through the K3 window

**What was tried:** A real energized cycle with Entry 10's TER
instrumentation live produced `TER = 0`, `operation_condition = 40`,
`hal_status = ACQUIRING` for the entire 306.6 ms K3-closed window - TER
never latched at all. Unlike Entry 8's `TER = +1` reading (which motivated
Entry 10's "triggered but stuck" hypothesis), this is unambiguous: a genuine
no-trigger condition, confirmed rather than hypothesized. Per operator
instruction, normal trigger settings (mode, coupling, edge source/slope/
level, probe ratio - Entries 1, 7, 8) are not to be changed again without
new evidence specifically implicating one of them.

**SCPI command confirmation (done before writing any code):** Fetched the
official Keysight InfiniiVision 2000 X-Series Oscilloscopes Programmer's
Guide (`https://www.keysight.com/us/en/assets/9018-06893/programming-guides/9018-06893.pdf`,
mirrored copy fetched from `batronix.com` and text-extracted with
`pdftotext` since the direct Keysight fetch returned only site chrome) -
this model family covers the MSO-X 2014A directly, not a cross-series
inference. Confirmed:
- `:TRIGger:FORCe` - write-only, no arguments, no query form ("n/a" for
  both in the command summary table). "Causes an acquisition to be captured
  even though the trigger condition has not been met... equivalent to the
  front panel [Force Trigger] key." Documented since firmware v1.20; this
  unit's firmware is v2.43 (from Entry 3's `*IDN?` capture), so it predates
  this unit's software easily.
- `:TER?` (already in use since Entry 8) is explicitly documented as
  read-and-clear: "After the Trigger Event Register is read, it is
  cleared." This confirms an assumption Entry 10 had flagged as unverified.
- Caution carried forward from Entry 9: `:TRIGger:NREJect` is *also*
  documented in this same manual's command summary table, yet was confirmed
  unsupported on this specific unit. Documentation is strong evidence, not
  proof-on-this-unit - `:TRIGger:FORCe` is still unconfirmed against the
  real instrument until the next real dry run.

**Design (two safety properties that needed explicit handling, beyond what
was directly specified):**
1. `:TRIGger:FORCe` completes the *same* single-shot acquisition a real
   trigger would - it is not a separate diagnostic channel. Once issued,
   `wait_until_acquisition_complete`'s run-bit-clear signal can no longer
   distinguish "genuinely triggered" from "we just forced it." So
   `_poll_acquisition_with_backstop` now latches `forced_diagnostic_attempted`
   and, once set, never again calls `wait_until_acquisition_complete` for
   real-measurement-success purposes - the loop only returns via the
   unchanged backstop/timeout paths from that point on, exactly as an
   unforced no-trigger cycle already does.
2. The force command itself (single write) and a `:TER?` pre-check (single
   read) are fast, bounded live-window calls - the same class already
   trusted in this loop (e.g. `:OPERegister:CONDition?`, polled every
   ~10 ms). The waveform/preamble/PNG *transfer*, however, is exactly the
   kind of call that stalled for over a second in Entry 6 - so it is
   deliberately deferred to `_capture_forced_diagnostic_best_effort`,
   called only after full safe-off (same ordering already established for
   Entry 8's timeout diagnostics, same reason: a slow transfer here must
   never be able to delay opening K3). The acquisition itself is already
   frozen in the scope's memory the instant `:TRIGger:FORCe` completes it;
   only reading that memory out is deferred, not what gets captured.

Also: since `:TER?` is read-and-clear, the `:TER?` pre-check that gates
forcing (only force if TER is confirmed 0) would itself destroy evidence of
a real trigger if TER happened to be 1 at that exact checkpoint - so that
read latches into `context.live_trigger_event_seen`, which Entry 10's
post-timeout reclassification now also consults (OR'd with the diagnostics
bundle's own fresh `:TER?` read) rather than relying solely on a
diagnostics-time read that could no longer see an already-cleared 1.

**Implementation:**
- `ScopeInterface.force_trigger()` (new abstract method), implemented in
  `ScopeReal` (`:TRIGger:FORCe` via `_write`) and `ScopeSim`
  (`wait_until_acquisition_complete` now returns `True` immediately once
  forced, mirroring the real completion behavior described above; reset
  each `configure_for_cycle`).
- `_poll_acquisition_with_backstop` gains a `_FORCED_DIAGNOSTIC_DELAY_S =
  0.1` checkpoint: if TER is confirmed 0 and K3 hasn't already opened,
  issues the force (fast phase only) via `_issue_forced_diagnostic_trigger`.
- `_capture_forced_diagnostic_best_effort` (deferred phase, called only
  after `_open_mains_with_cooldown`) reuses `capture_after_acquire()` to
  retrieve the already-frozen waveform/preamble/PNG and persists it via a
  new `RunRecorder.write_forced_diagnostic_capture()` - writes only
  `diagnostics/<cycle>/forced_diagnostic_{waveform.npz,scope.png,state.json}`,
  never `waveforms/` or `images/`. `forced_diagnostic_state.json` includes
  `"capture_type": "forced_diagnostic_non_measurement"`, the SCPI command
  used, elapsed time since K3 closed, and an explicit note that it must
  never be used for PASS/FAIL or trip-time calculation. Nothing in this
  path touches `analyze_waveform`, the cycle's `Verdict`, or any file
  outside the diagnostics tree - a forced cycle still halts via the
  existing (Entry 10-aware) timeout/reclassification path exactly as an
  unforced no-trigger cycle does.
- The 300 ms K3 backstop's own deadline check, deadline value, and
  `open_k3()` call are untouched - the forced-diagnostic checkpoint is
  purely additive and only ever fires strictly before the backstop
  (`not opened` guard), never after.

**Commit:** (pending)

**Result:** Not yet tried against real hardware.

**What this tells us:** Nothing about root cause yet - same as Entry 2 and
Entry 10, this is instrumentation. Its value is entirely in what the forced
capture's waveform/screenshot show on the next real no-trigger cycle: signal
absent entirely (front-end/probe/wiring issue upstream of the trigger
comparator), signal present but below/at the configured +20 V level (trigger
level or comparator issue), or signal present and clearly above +20 V
(points toward the trigger circuit itself, e.g. holdoff or an
acquire-mode interaction, rather than anything already checked).

New tests: `test_force_trigger_sends_documented_command` (`test_scope_real.py`);
`test_force_trigger_completes_a_never_triggered_acquisition`,
`test_force_trigger_state_resets_on_next_configure` (`test_scope_sim.py`);
`test_forced_diagnostic_capture_when_ter_still_zero`,
`test_forced_diagnostic_capture_skipped_when_trigger_event_already_confirmed`
(`test_sequencer.py`). Full suite: 327 tests, OK, same 2 intentional skips.

---

## Template for new entries

```
## Entry N - YYYY-MM-DD - <short title>

**What was tried:**

**Commit:**

**Result:**

**What this tells us:**
```
