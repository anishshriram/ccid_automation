# System Overview

This is the index and the big picture. Each section below is a short summary with a pointer to the document that has the full depth — read this first to get oriented, then follow the links for anything you need in detail.

---

## 1. What this system does, in one paragraph

A Raspberry Pi runs repeated ground-fault trip tests against a Gen II EVSE: it closes K1 and K2 (double-pole mains disconnect), waits for a webcam to confirm the EVSE reached a real charging state, closes K3 to inject ~30mA leakage current through a resistor bank while an oscilloscope times how long the CCID takes to trip, opens K3 (on trip completion or on a hard 300ms backstop, whichever comes first), commits the cycle's data durably, opens K1/K2, cools down, and repeats — unattended, for campaigns of up to 6000 cycles.

---

## 2. One cycle, start to finish

```
SAFE_OFF → MAINS_CLOSING (K1, K2) → WAITING_FOR_CHARGING (vision gate)
  → SCOPE_CONFIGURING → SCOPE_ARMING → SCOPE_ARMED (TER/armed checkpoints)
  → INJECTING (K3 closes) → ACQUIRING (backstop-guarded poll)
  → INJECTION_OPENING (K3 opens — normal, backstop, or timeout)
  → [DIAGNOSTICS_CAPTURING / FORCED_DIAGNOSTIC_CAPTURING — only on a failure path]
  → TRANSFERRING (waveform pulled off the scope) → analysis runs
  → COMMITTING (crash-safe write) → MAINS_OPENING (K1, K2 open) → COOLDOWN
```

Full state-by-state mechanics, every branch, and exactly how the K3 backstop/forced-diagnostic poll loop works: **[sequencer-and-state-machine.md](sequencer-and-state-machine.md)**.

---

## 3. The four subsystems that meet at the sequencer

`ccid.sequencer.Sequencer` is the orchestrator; it calls into four independent subsystems, each replaceable behind an interface, none of which know about each other:

```
                    ┌─────────────────────────────────────────┐
                    │              Sequencer                   │
                    │   (state machine, retry/halt decisions,   │
                    │    K3 backstop, safety-relevant ordering)  │
                    └───┬───────────┬───────────┬───────────┬───┘
                        │           │           │           │
              ┌─────────▼───┐ ┌─────▼─────┐ ┌───▼────┐ ┌────▼─────┐
              │ classify.py  │ │  HAL       │ │analysis│ │ recorder │
              │ (vision gate)│ │(scope/gpio/│ │  .py   │ │  .py     │
              │              │ │  camera)   │ │(trip-  │ │(crash-   │
              │              │ │            │ │ time)  │ │ safe I/O)│
              └──────────────┘ └────────────┘ └────────┘ └──────────┘
```

- **Vision/charging-gate** — HSV classification of the LED, plus a policy layer deciding when to grant the gate. **[vision-and-charging-gate-classification.md](vision-and-charging-gate-classification.md)**
- **Hardware abstraction (HAL)** — scope, contactors, camera, each with a real and a simulated implementation behind the same interface. **[hardware-abstraction-layer.md](hardware-abstraction-layer.md)**
- **Trip-time analysis** — turns a captured waveform into a number and a verdict; versioned so the algorithm can improve without touching stored data. **[trip-time-analysis-algorithm.md](trip-time-analysis-algorithm.md)**
- **Persistence** — crash-safe commit ordering, config validation/hashing, resume semantics. **[persistence-and-recovery.md](persistence-and-recovery.md)**

Wrapping all of this: **[cli-lifecycle-and-monitoring.md](cli-lifecycle-and-monitoring.md)** (the process entry point, signal handling, systemd watchdog, outbound monitoring, and the auto-retry loop that decides whether a halt actually ends the campaign) and **[tools.md](tools.md)** (commissioning/calibration/offline-analysis CLIs, all built on the same production code paths). **[test-suite-guide.md](test-suite-guide.md)** maps all 364 tests back to the behaviors described across every doc above.

---

## 4. Where safety actually lives

A question worth answering precisely, since it's easy to assume "the sequencer" is where safety is enforced — it isn't, entirely:

- **The K3 interlock** (K3 can only close once K1 *and* K2 are both commanded closed; K1/K2 cannot open while K3 is commanded closed; a charging-gate token is single-use per cycle) is enforced **independently in both `gpio_real.py` and `gpio_sim.py`** — not by the sequencer calling things in the right order. The sequencer's logic could be entirely wrong about ordering and the rig still couldn't do something unsafe, because the enforcement is one layer down. See [hardware-abstraction-layer.md §2](hardware-abstraction-layer.md#2-contactors--gpio_realpy--gpio_simpy).
- **The K3 300ms backstop** lives in `Sequencer._poll_acquisition_with_backstop` — this one genuinely is sequencer-level, because it's fundamentally about timing (K3 must open by a deadline regardless of what the scope reports), not about command ordering. See [sequencer-and-state-machine.md §5.7](sequencer-and-state-machine.md#57-acquisition-polling-with-the-k3-backstop--the-most-complex-part-of-the-file).
- **`safe_off`'s strict K3→K2→K1 open ordering and full-attempt-even-on-partial-failure semantics** live in `ccid/safety.py`, called from every halt/retry/completion path in the sequencer and again, redundantly, in `ccid.main._execute_campaign`'s own `finally` block as a second backstop. See [sequencer-and-state-machine.md §3](sequencer-and-state-machine.md#3-ccidsafetypy--safe_off) and [cli-lifecycle-and-monitoring.md §7](cli-lifecycle-and-monitoring.md#7-_execute_campaign--the-shared-body).
- **Vision can never halt or kill the campaign on its own failure** — a camera fault degrades to a fixed wait and the run continues in logged degraded mode; it can only ever *contribute* to a halt via the same halt/retry decision the sequencer makes for any other timeout. See [vision-and-charging-gate-classification.md §9](vision-and-charging-gate-classification.md#9-await_charging_gate--the-orchestration-function).
- **Outbound monitoring (Cronitor/ntfy) can never halt the campaign** — every network call is caught and logged, never raised. See [cli-lifecycle-and-monitoring.md §4](cli-lifecycle-and-monitoring.md#4-outbound-monitoring--httpnotifier).
- **A crash mid-cycle can never lose or double-count a cycle** — the commit order (artifacts → CSV → runstate → heartbeat) plus atomic `runstate.json` replacement plus orphan reconciliation on resume together guarantee this, and it's the one property in the whole system actually *proven* by injected-crash tests rather than just asserted. See [persistence-and-recovery.md §1](persistence-and-recovery.md#1-ccidrecorderpy--the-commit-order-contract).
- **An unexpected exception no longer disappears without a trace** — `Sequencer._capture_controller_exception_diagnostics` persists the full type/message/traceback/cycle-state to `diagnostics/<cycle>/controller_exception.json` before the halt path collapses it down to just the exception's class name, added after a real incident where the only record of a crash was lost to non-persistent journald. See [sequencer-and-state-machine.md §8.3](sequencer-and-state-machine.md#83-_capture_controller_exception_diagnostics-the-defensive-catch-all-path).
- **A halt no longer ends the campaign by itself.** `ccid.main._run_campaign_with_auto_retry` clears a halt and resumes in-process, up to a streak limit that resets on any cycle that actually completes: 3 consecutive for `NO_TRIP` (a real DUT failure should reach a human faster than rig flakiness), 5 for everything else (`RIG_FAULT`/`CONTROLLER`/`PERSISTENCE`). This changes what "halt" means operationally, but not the underlying safety story above — every retry still goes through the exact same `safe_off`/K3-backstop/interlock machinery each time, since it's just calling `Sequencer.run()` again, not bypassing anything. Sticky-halt semantics for the *CLI* (`resume` refusing to auto-continue a halted run without an explicit flag) are unchanged; auto-retry is a separate, in-process layer above that, and only ever engages while the original process is still alive. See [cli-lifecycle-and-monitoring.md §8](cli-lifecycle-and-monitoring.md#8-_run_campaign_with_auto_retry--a-halt-no-longer-ends-the-campaign-by-itself).
- **Camera/scope connections get periodic and reactive maintenance, never energized.** `Sequencer._maybe_refresh_equipment` disconnects/reconnects the scope and stops/starts the camera every N cycles and after M consecutive camera-unavailable cycles, always before mains close for that cycle — a best-effort mitigation for gradual USB/reader-thread degradation over a multi-day campaign, added after a real incident where a re-enumerated camera stayed stuck unavailable for several cycles in a row. See [sequencer-and-state-machine.md §9](sequencer-and-state-machine.md#9-equipment-refresh--_maybe_refresh_equipment--_refresh_equipment).

---

## 5. The versioning discipline

Two things in this codebase are explicitly "versioned boundaries" — deliberately unfinished/improvable, with the mechanism for changing them built in from the start rather than requiring a later refactor:

- **`AnalysisVersion`** (`ccid/analysis.py`) — the trip-time algorithm. Three real versions exist (V1, V2, V3); the rule is "supersede only by re-versioning, never by editing in place," and it's been exercised for real this session (a genuine onset-detection bug found and fixed as V3, with V1/V2 preserved exactly as originally shipped for replay fidelity). Full story: [trip-time-analysis-algorithm.md §6](trip-time-analysis-algorithm.md#6-the-v1--v2--v3-story-precisely).
- **`config.yaml`'s `analysis.endpoint_definition`** — the project's own provisional definition of trip-time measurement endpoints, used because the governing standard (UL 2231-2 §23.3.1) hasn't been confirmed against the actual document yet. Frozen by the config hash so it can't drift silently mid-campaign. Still an open item — see `legacy-documentation-audit.md` §4.

---

## 6. Notable "written but not load-bearing" findings

A few things surfaced repeatedly enough while writing these docs that they're worth collecting in one place — none are bugs, all are worth knowing if you go looking for where a piece of logic actually lives:

| Thing | Status |
|---|---|
| `CameraInterface.await_charging_gate` (both real and sim implementations) | Implemented, tested, **never called by the sequencer** — the real gate logic is `ccid.classify.await_charging_gate`, built directly on `camera.sample_state()`. |
| `PathsConfig.output_root` | Validated, part of the config hash, **never read anywhere else in the codebase**. Reserved. |
| `CycleArtifacts.fault_jpg_burst` | Full write-path and orphan-cleanup support exists, **nothing currently populates it**. Reserved for a future feature. |
| `ccid/clock.py` (`MonotonicDeadline`, etc.) | Fully implemented and tested, **not imported anywhere**. Every real timeout in the codebase uses `time.monotonic()` directly instead. |
| `ccid.errors.TimeoutError` | Defined, part of the exception hierarchy test, **never raised anywhere**. Timeouts are represented as boolean returns instead. |
| `states.CycleDecision` | Defined, **never constructed** — verdict decisions actually happen in `Sequencer._map_verdict`, which returns a plain tuple, not this type. |

None of these are wrong; they're either forward-looking infrastructure or an earlier design that was superseded by a different mechanism elsewhere. Flagged here so nobody spends time looking for a caller that doesn't exist.

---

## 7. Reading order recommendation

If you're new to this codebase and want to actually understand it (not just look something up), the built-up order this documentation was written in is a reasonable path: **sequencer → analysis → HAL → vision → persistence → CLI/monitoring → tools → tests**, i.e. this document's §3 list, in order. Each doc was written with the assumption you might be reading it cold, so none of them strictly require having read the others first — but that order goes from "the thing that calls everything else" outward, which tends to build the right mental model fastest.
