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
centered timebase, STOP verification, double-arm-check). A new capability
(timeout diagnostics capture) was added so the *next* failed cycle
preserves evidence instead of nothing - that capability itself has not yet
been exercised against real hardware.

Do not attempt another energized cycle until the diagnostics capability
has had at least one real-scope, de-energized dry run to confirm it
actually queries the instrument cleanly (see "Next steps" below).

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

---

## Template for new entries

```
## Entry N - YYYY-MM-DD - <short title>

**What was tried:**

**Commit:**

**Result:**

**What this tells us:**
```
