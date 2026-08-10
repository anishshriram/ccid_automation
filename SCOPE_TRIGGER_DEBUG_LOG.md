# Scope Trigger Debugging Log

Running log of the oscilloscope no-trigger investigation. Every automated
cycle currently halts with `rig:scope_never_triggered_or_acquire_timeout`.
Append new entries as more hardware testing happens - keep entries in
chronological order, and keep the "Current status" section at the top
up to date rather than buried at the bottom.

---

## Current status (as of 2026-08-10)

**Not solved.** Root cause of the no-trigger condition is still unknown.
Two things have been ruled out (trigger mode, and previously: AC coupling,
centered timebase, STOP verification, double-arm-check).

The first version of timeout diagnostics capture (Entry 2) wedged the real
scope's USBTMC interface badly enough to require a physical AC power cycle
to recover (Entry 3). A corrective version (Entry 4) reorders diagnostics
to run only after full safe-off and adds a hard per-query timeout,
fail-fast-on-first-failure, and a device-clear-first step. **This
corrective version is verified only in the simulator/unit tests - it has
not yet been re-tested against the real scope.** Given what happened last
time, do not assume it is safe on real hardware just because the tests
pass. The next de-energized dry run should specifically watch for: does
`.clear()` succeed on this instrument/backend, does the hard timeout
actually bound a real slow/wedged query, and does the scope survive the
attempt in a state where `*IDN?` still responds afterward.

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

**Commit:** (pending)

**Testing:** 7 new/rewritten tests in `test_scope_real.py` (device-clear-first, abort-on-clear-failure, fail-fast-after-first-failure, hard per-query timeout via an actually-slow fake query, total-budget enforcement) and one new test in `test_sequencer.py` - `test_diagnostics_capture_happens_only_after_full_safe_off` - which snapshots commanded contactor state at the exact moment diagnostics capture is invoked and asserts K1, K2, and K3 are all open. This is the test that would have caught the Entry 3 incident directly; it did not exist before this entry. Full suite: 297 tests, OK, same 2 intentional skips. All software-only.

**What this tells us:** Nothing new about the scope's actual no-trigger root cause - this only corrects a defect in the diagnostics tooling itself. **Not yet verified against real hardware** - the device-clear call, the per-query timeout's interaction with the real USBTMC backend, and whether the fail-fast behavior actually prevents a repeat of Entry 3 are all unconfirmed. Treat the next real-scope dry run as the first real test of this fix, not a formality.

---

## Template for new entries

```
## Entry N - YYYY-MM-DD - <short title>

**What was tried:**

**Commit:**

**Result:**

**What this tells us:**
```
