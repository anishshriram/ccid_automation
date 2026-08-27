# Legacy Documentation Audit — Pre-Deletion Discrepancy Report

This document exists for one reason: you intend to delete seven legacy files —
`CODING_AGENT_HANDOFF.md`, `coding_instructions.txt`, `DEPLOYMENT.txt`,
`handoff_latest.md`, `IMPLEMENTATION_QUESTIONS.md`, `IMPLEMENTATION_STATUS.md`,
and `PI_SETUP_AND_TEST_PLAN.md` — now that the technical reference in `docs/`
exists. Before that happens, every claim in those seven files has been checked
against current `config.yaml`, current code, git history, and the newly
pulled operator runbook (`Download the CCID Operator Preflight and Runbook.md`).
This records what was wrong, what's still right but needs to move somewhere,
and what's still open. **Nothing here was written from memory of the old
files — every claim below was re-verified against the live repo while writing
this.**

This audit does **not** cover the four newly-pulled issue-log/runbook files —
those are a separate, later documentation pass, per your instruction.

---

## 1. Executive summary — is it safe to delete these seven files?

| File | Verdict |
|---|---|
| `IMPLEMENTATION_STATUS.md` | **Safe to delete.** Fully superseded by `docs/`. Its bottom-line "definition of done" claim is actively wrong (§2.3 below) — a reason to delete it, not keep it. |
| `IMPLEMENTATION_QUESTIONS.md` | **Safe to delete, after fixing two dangling citations** (§6) that currently point to it by filename from inside `docs/`. Its content is carried forward in full at §4 below. |
| `coding_instructions.txt` | **Safe to delete.** It's a build-process instruction file for work that's finished; every safety invariant and boundary it states is still true and is already reflected in `docs/` (distributed across several files, not as one checklist — see §5.1 if you ever want that checklist reassembled). |
| `CODING_AGENT_HANDOFF.md` | **Safe to delete.** Its own "authoritative sources" chain points at three files also being deleted plus `SCOPE_TRIGGER_DEBUG_LOG.md`; its status sections are stale (§2.3). |
| `handoff_latest.md` | **Safe to delete, with two things worth preserving first** — the hardware/wiring "as-built" reference and the traps/rejected-alternatives tables have no home in the current doc set. Preserved compactly at §5.1. Everything else (locked values, test definition, cycle sequence, interlocks) is already accurately reflected in `docs/`. |
| `DEPLOYMENT.txt` | **Hold — real gap.** Its own content is currently *correct* (you fixed it this session), but the new preflight runbook assumes the Pi is already set up and doesn't cover initial provisioning (venv creation, udev install, service-account creation, first-enable sequencing). See §5.2. Also flags a real operational-model question at §2.8 that's worth resolving on purpose, not by accident of deletion. |
| `PI_SETUP_AND_TEST_PLAN.md` | **Hold for the same reason as `DEPLOYMENT.txt`** — Part 1 (Pi bring-up) has no replacement anywhere. Part 2 (step-by-step validation) is superseded by the new runbook. Also contains one factual error worth knowing about regardless of deletion timing (§2.4). |

If you're fine accepting the gap at §5.2 (i.e., a fresh Pi bring-up guide doesn't exist right now and you're not planning to provision a new Pi from scratch soon), all seven are safe to delete once §6's two citations are fixed.

---

## 2. Confirmed discrepancies — old claim vs. current reality

### 2.1 Monitoring service: healthchecks.io → Cronitor

**Old claim** (`handoff_latest.md` §10/§11, `IMPLEMENTATION_STATUS.md` Phase 10, `coding_instructions.txt` §6): the dead-man's-switch liveness monitor is `healthchecks.io` — one HTTP GET per cycle, 5-minute grace window, `/fail` endpoint on halt.

**Current reality:** replaced by Cronitor (commit `1835f4d`). `docs/cli-lifecycle-and-monitoring.md` documents the current `HttpNotifier` Cronitor+ntfy split. `DEPLOYMENT.txt` and the new operator runbook already use `CCID_CRONITOR_URL`, not a healthchecks.io URL — so the swap is real and already propagated into the operationally-live files; it's only the three files above that never got updated. The new runbook (§10) also documents a detail none of the old files know about: Cronitor can auto-recreate a deleted monitor the next time a heartbeat fires, which has monitoring-lifecycle implications (pause/resume behavior needs revalidating before a long run) not captured anywhere in the seven legacy files, since the whole service is new to them.

### 2.2 Analysis algorithm version: stuck at v1/v2, current is v3

**Old claim:** `handoff_latest.md` §4 calls `analysis.py` "a stub" awaiting the deferred algorithm. `IMPLEMENTATION_STATUS.md` Phase 7 documents only v1. `IMPLEMENTATION_QUESTIONS.md` says `config.yaml`'s `analysis.endpoint_definition` is "currently the v2 text." `CODING_AGENT_HANDOFF.md`'s commit log mentions `fd0fc85 - version corrected pretrigger analysis as v2` as the most recent analysis work.

**Current reality:** `config.yaml` has `algorithm_version: v3` (verified directly). A third version exists, fixing a real onset-refinement defect that could invert a verdict (git commit `30def72`). `docs/trip-time-analysis-algorithm.md` documents the full V1→V2→V3 story, including the real cycle numbers from the actual defect. The new operator runbook's §5 ("Analysis-Version Gate") and §13 (post-run verification) both hard-require `algorithm_version: v3` for current campaigns — confirming V3 is not just implemented but the actual operating configuration.

### 2.3 Hardware/real-campaign validation status: "not yet done" is now wrong

This is the most materially misleading stale claim across the set, because it's a safety-adjacent status claim, not just a version number.

**Old claim:** `IMPLEMENTATION_STATUS.md`'s "Definition-of-done status" (last section) states electrical commissioning, the full campaign, hardware watchdog verification, and camera-fix validation "all remain **NOT RUN - HARDWARE REQUIRED**." `CODING_AGENT_HANDOFF.md`'s "Camera/vision commissioning status" section says the post-gate-fix real-hardware validation is "not confirmed done" and lists specific run IDs already used, implying that's still the full list. Its "What remains" section ranks camera validation as the #1 blocking item before any further hardware work.

**Current reality:** real campaigns have run since. Git history shows `c0e887c` (a real bug found and fixed from live-hardware evidence — the forced-diagnostic loop discarding genuine successes), then `7a15241` "Archive 25-cycle CCID campaign data" — meaning a real 25-cycle campaign completed and its data was archived. `SCOPE_TRIGGER_DEBUG_LOG.md` (kept, not deleted) documents this investigation in full detail through Entry 15, and its own top status section is *itself* stale for the same reason — see §2.9. The new operator runbook's existence and content (specific real-run-ID naming conventions like `real_v3_supervised_...`/`real_v3_systemd_...`, a "Long-Campaign Gate" checklist for 150- and 6,000-cycle campaigns, references to results already having been reviewed) only make sense if real campaigns are now routine, not pending. The list of "do not reuse" run IDs in `CODING_AGENT_HANDOFF.md` is also now incomplete for the same reason — more real run IDs exist than it lists.

**Net effect:** anyone reading only these two files today would believe the project is still waiting on its first real validated cycle. It is not — it has already run a 25-cycle campaign and is operating routinely enough to have a formal preflight/runbook document for further real campaigns.

### 2.4 VISA resource string: a real factual error, not just staleness

**Old claim** (`PI_SETUP_AND_TEST_PLAN.md` §1.7): "vendor ID `0957`/`2391` in decimal, product ID `1798`/`6296` in decimal" and the worked example `export CCID_SCOPE_RESOURCE="USB0::2391::6296::MY58100795::0::INSTR"`.

**Current reality:** `0x1798` (hex) = **6040** decimal, not 6296. `6296` was simply arithmetically wrong in that file — it was never a valid alternate value. This is confirmed three independent ways: `handoff_latest.md`'s own hex-form resource string (`USB0::0x0957::0x1798::MY58100795::INSTR`), the newly-pulled operator runbook (`USB0::2391::6040::MY58100795::0::INSTR`, used in three places), and the newly-pulled 154-issue log, which records this exact fix: *"Resolution: Standardized on `USB0::2391::6040::MY58100795::0::INSTR` reported by discovery."* So this wasn't just an old value that changed later — the `6296` figure in `PI_SETUP_AND_TEST_PLAN.md` was never correct. Worth knowing regardless of when you delete the file, in case anyone has that exact command in a personal notes file or shell history.

### 2.5 Camera exposure value: 30 → 60

**Old claim** (`CODING_AGENT_HANDOFF.md`, "Camera facts"): "exposure locked via `v4l2-ctl --set-ctrl=auto_exposure=1,exposure_time_absolute=30`."

**Current reality:** the newly-pulled operator runbook (§6, used in two places, most recently dated 2026-08-12) sets and verifies `exposure_time_absolute=60`. The 87-issue register's resolution entry also documents `exposure_time_absolute=30` as an intermediate fix — so the value moved 30 → 60 at some point after that entry, and `CODING_AGENT_HANDOFF.md` was never updated to the current value. If you ever hand-run a `v4l2-ctl` command from memory of the old file, it'll set the wrong exposure.

### 2.6 Camera `device_index` "known gap" — already resolved, file never updated

**Old claim** (`PI_SETUP_AND_TEST_PLAN.md` §1.8): describes `CameraReal` as hardcoded to `device_index=0` with "no existing config knob," framed as something that "will need a small code change... before it will work" if your webcam isn't `/dev/video0`.

**Current reality:** already resolved (commit `449a4ba`). `config.yaml`'s `camera:` section has `device_index: 0` as a real, validated, config-hash-included key (verified directly in `config.yaml`), and `ccid/main.py`'s `build_hal_bundle` wires it through to `CameraRealConfig`. `docs/hardware-abstraction-layer.md` and `docs/cli-lifecycle-and-monitoring.md` both document this as already-existing. `IMPLEMENTATION_QUESTIONS.md`'s own "Resolved this session" section already correctly records this fix — so this is a case of one legacy file (`IMPLEMENTATION_QUESTIONS.md`) being accurate about a fix that a different legacy file (`PI_SETUP_AND_TEST_PLAN.md`) never learned about.

### 2.7 Test suite count — stale everywhere, low stakes but worth naming once

Old counts scattered across the set: 229 (`PI_SETUP_AND_TEST_PLAN.md`), 269 (`IMPLEMENTATION_STATUS.md`, `CODING_AGENT_HANDOFF.md`), and a climbing sequence through `SCOPE_TRIGGER_DEBUG_LOG.md`'s entries (292 → 297 → 302 → 305 → 310 → 322 → 327 → 331 → 340 → 341 → 342). Current, verified count: **349 tests, 2 intentional skips** (`docs/test-suite-guide.md`). None of these numbers were ever wrong at the time they were written — they're a growth log, not an error — but none of the seven files being deleted has the current number, so nothing is lost by deleting them on this point specifically.

### 2.8 Deployment model: a real operational question, not just staleness

This one isn't "old file wrong, here's the fix" — it's a **live discrepancy between two current sources** that's worth resolving deliberately rather than accidentally through deletion order.

`DEPLOYMENT.txt` (as you fixed it this session) and `deploy/ccid-automation.service` (still present in the repo, unmodified) describe a **persistent, enabled systemd service**: `systemctl enable ccid-automation.service`, `ExecStart=... resume --latest`, `Restart=on-failure`, `WantedBy=multi-user.target` — i.e., install once, and the Pi auto-runs/auto-restarts the latest run forever, across reboots.

The newly-pulled operator runbook (§12) describes a completely different pattern for unattended runs: **transient `systemd-run` units**, started manually per campaign with an explicit fresh run ID and `--target-cycles`, using `--collect` (the unit disappears on completion), never `enable`d, never auto-restarting. Its own rules are explicit: *"Do not resume automatically after a halt."*

Both are internally consistent with the sticky-halt design (a halted campaign never silently resumes either way), so this isn't a safety bug — but it is a real fork in how you're actually supposed to run a long campaign, and only one of the two documents describing it will survive if `DEPLOYMENT.txt` is deleted without a decision. Worth explicitly deciding: keep the persistent-service pattern as the documented one and treat the runbook's `systemd-run` examples as the exception, or the reverse (which the recency and specificity of the runbook suggests is now actual practice) — and if the latter, `deploy/ccid-automation.service` becomes dead weight worth removing too, not just undocumented.

### 2.9 `SCOPE_TRIGGER_DEBUG_LOG.md` — not being deleted, but flagged anyway

Not one of your seven, but it's the file `CODING_AGENT_HANDOFF.md` designates as *"the current state of truth for the active scope no-trigger investigation,"* and once `CODING_AGENT_HANDOFF.md` is gone, nothing else carries that designation forward. Its own "Current status" section still frames the investigation as open/unresolved as of Entry 15 ("untested against real hardware" appears repeatedly), but §2.3 above shows a real campaign completed afterward — meaning the investigation this file is tracking apparently reached a real resolution that was never written back into its own status section. Not something to fix as part of this audit (out of scope, per your instruction), but worth a one-line status update the next time you touch that file, since it's the one piece of the old "authoritative sources" chain that's staying.

---

## 3. Open items from `handoff_latest.md` §15 that *were* actually resolved — with the real answer

The design doc's open-items table (§15) has entries that read as unresolved but were, in fact, settled by real data since. Recorded here since the table itself goes away with the file:

| Item (as originally phrased) | Resolution |
|---|---|
| #6: "1 Mpts transfer time on the Pi → decide 1 Mpts vs 100 kpts" | **Kept at 1 Mpts.** `docs/hardware-abstraction-layer.md`/`ScopeSettings` defaults confirm full-depth capture is still the live default; no evidence the contingency to drop to 100 kpts was ever exercised. |
| #13: "Real measured cycle time → replace the §1 estimate (~65–75 s/cycle) and recompute campaign duration" | **Resolved: ~60.91 s/cycle mean**, computed directly from the real 25-cycle archived campaign data (`7a15241`) — faster than the original estimate, not slower. |
| #19: "Decide `mains_stagger_ms`" | **Resolved: kept at 0**, confirmed directly in current `config.yaml`. No evidence bench inrush ever proved noisy enough to require a nonzero stagger. |
| #4: "ECK100BH4AAA pole count" | Already marked moot in the source table itself (three single-pole contactors used) — no new information, just noting it's fully closed, not a live open item. |
| #8: "UL 2231-2 definition of measurement endpoints... blocks the next deliverable" | **Partially resolved.** The *procedural* blocker (write the spec) was resolved — `coding_instructions.txt` is that spec. The *substantive* question (does UL 2231-2 define these endpoints differently from the project's own provisional definition) is still genuinely open — see §4 below, item 2. Don't read the procedural resolution as closing the substantive one. |

Items #7 (LED blink rate > 0.5 Hz), #9 (first-100 trip-time distribution vs. 24.97 ms), and #12 (latch-clear durability at volume) have no confirmed-resolved status anywhere in the current repo that this audit found — the camera and vision system demonstrably works (25-cycle campaign completed, gate redesign shipped), which is strong indirect evidence, but none of the three specific numeric questions has a written answer anywhere in `docs/` or the new runbook. Not urgent to resolve as part of this audit, but don't assume they're closed just because the campaign ran.

---

## 4. Still-open items — carried forward from `IMPLEMENTATION_QUESTIONS.md` in full

Both items below are still genuinely unresolved as of this audit. This is the complete content of that file's "Open" section, preserved here since the file is being deleted:

**1. K1/K2 physical-state readback.** The software tracks only commanded contactor state (`ContactorInterface.snapshot()`); there is no auxiliary-contact or voltage readback confirming K1/K2 physically opened or closed. A K1/K2 stuck physically closed while commanded open is an explicitly undetectable known gap, covered by a skipped, documented test (`tests/test_faultmatrix.py::test_k1_k2_physically_stuck_closed_row`) rather than a false unit test. You confirmed this session that it remains open as a deliberate future task, not a blocker.

**2. UL 2231-2 endpoint definition confirmation.** `config.yaml`'s `analysis.endpoint_definition` (now the V3 text) is still the project's own provisional definition of the trip-time measurement endpoints, used because UL 2231-2 §23.3.1 has not been confirmed against the actual standard on paper. Frozen by the config hash so it can't drift silently, but the underlying question — whether UL 2231-2 defines these endpoints differently — remains open.

Both were already resolved *for the code* (`camera.device_index` config-driven, disk-space check via `min_free_disk_gb`) — those two "Resolved this session" items from the same file are already reflected in `docs/hardware-abstraction-layer.md` and `docs/persistence-and-recovery.md` respectively and don't need separate preservation.

---

## 5. Content that's still true but has no home after deletion

### 5.1 Hardware "as-built" reference, traps, and rejected alternatives (from `handoff_latest.md`)

Mostly still accurate, with one correction below the wiring list is now updated to reflect. None of it is duplicated in `docs/` (which is software-only) or the new runbook (which is a pre-run checklist, not a wiring reference). If `handoff_latest.md` is deleted without capturing this, it's genuinely gone:

**Wiring, still accurate:**
- K1 = L1 mains (GPIO17/pin 11), K2 = L2 mains (GPIO27/pin 13), K3 = leakage injection (GPIO22/pin 15) — three independently-driven single-pole contactors, each via its own ZX-517 dual-MOSFET driver board and its own isolated 12 V 2 A supply.
- ZX-517 is non-isolated, low-side switching, confirmed **no onboard flyback diode** — the original design intent called for external 1N4007 flyback diodes (cathode to `OUT+`, anode to `OUT-`; reversed = dead short on power-up), but **none have actually been installed in the as-built rig** — this remains the single highest-priority hardware gap, open rather than solved (`docs/build-and-commissioning-issue-log.md` §2, §9 item 1).
- All three driver boards' `GND` signal pins must be bonded to Pi ground at a single star point (the boards are not optoisolated — MOSFET gates reference supply negative, not Pi ground, without this bond).
- The original design intent also called for ~10 kΩ gate pulldown resistors on each MOSFET input, so a not-yet-initialized GPIO pin defaults to holding the contactor open rather than floating — **these are also not installed**; a future task, not a completed mitigation.
- Coil spec: 12 VDC nominal, 0.462 A, ~26 Ω expected resistance, operate ≤9 VDC / max 13.2 VDC / release ≥1.2 VDC.
- Probe: 10:1 passive, 300 V CAT II, tip on one end of the resistor bank, ground clip on ground. (This is the setting directly relevant to the trigger investigation from your last two messages — the original design intent was full-bank measurement, not a midpoint tap.)
- Scope: Keysight MSO-X 2014A, firmware 02.43.2018020635, USB transport (NI-VISA/PyVISA validated on Windows first, then `pyvisa`+`pyvisa-py`+`pyusb` on the Pi since there's no ARM VISA build).

**Traps worth keeping** (condensed from the original 14-row table — full detail was in `handoff_latest.md` §9):
1. No flyback diode → avalanche failure → MOSFET usually fails **shorted**, defeating the NO-contactor/timeout backstops simultaneously. **This trap is not hypothetical here — neither flyback diodes nor gate pulldowns are installed on this rig, so the condition it describes is currently live, not mitigated.**
2. "Dual MOSFETs in parallel" only halves R_DS(on); it does nothing for inductive kickback.
3. `:WAVeform:POINts:MODE` defaults to `NORMal` (~1000 pts) regardless of memory depth — must explicitly set `RAW` + `POINts MAXimum`.
4. `:DIGitize` blanks the display, breaking the screenshot capture — use `:SINGle` instead.
5. `:SINGle` returns before the scope is actually armed — poll the run bit, never `time.sleep()` for instrument sync.
6. No NI-VISA/Keysight IO Libraries build for ARM — Windows validation doesn't transfer; `pyvisa-py`+`pyusb` only.
7. Running an unattended multi-day service as root is bad practice — udev rule + `plugdev` group instead.
8. `pyvisa-py`'s USBTMC backend is weak on large binary transfers — this predicted exactly the Entry 3/6 USBTMC wedge/segfault incidents documented in `SCOPE_TRIGGER_DEBUG_LOG.md`.
9. Scope built-in width/period measurements return one AC half-cycle, not the burst — must compute from the raw envelope offline.
10. Never PWM the contactor coil — DC on/off only.
11. Flyback-only suppression slows contactor drop-out 2-3× — would be harmless here since K3 opening isn't the measurand and the backstop has margin, **but doesn't currently apply since no flyback suppression is installed on this rig at all** — worth knowing before adding it as a future task, not evidence it's already accounted for.
12. NTP time steps can corrupt duration measurements over a multi-day run — all durations must come from `time.monotonic()`, never wall clock.
13. SD card is a single point of failure — fsync per artifact, commit-then-counter ordering.
14. `apt upgrade` mid-run can break a running campaign — pinned `requirements.txt` in a venv.

**Rejected alternatives worth keeping** (so nobody re-proposes them): scope built-in `:MEASure` for trip time (measures one half-cycle, not the burst); vision as the trip detector (33 ms/frame vs. 24.97 ms limit — physically impossible); `:DIGitize` for acquisition (blanks the display); `time.sleep()` for instrument sync (works for 50 cycles, corrupts data at cycle 3000); screenshots as the primary record (can't re-run a new algorithm over a picture); dropping to 100 kpts to shorten the campaign (boot-bound, not transfer-bound — saves ~3%, costs the whole deferred-algorithm strategy); 2 s cooldown (insufficient capacitor discharge); phase-synchronized injection (defeats the point — real faults occur at random phase); halting on 5 consecutive FAILs or 25 cumulative FAILs (phase randomness alone produces spurious runs of failures near the pass/fail boundary); hardware K3 timeout circuit (deferred post-MVP, extra parts); automating the EV simulator spoof (unnecessary, it works always-on).

**Glossary** (small, easy to keep): CCID = Charge Circuit Interrupting Device (the DUT). EVSE = Electric Vehicle Supply Equipment. CP = Control Pilot. Spoof/EV simulator = static always-on fake vehicle presence device. K1/K2/K3 = L1 mains / L2 mains / leakage injection (note: revisions before 2026-08-03 used `K2` for the leakage contactor — now `K3`). SafeOff = the invariant state, all three contactors open. Trip = the CCID clearing the fault. No-trip = CCID failed to clear within 100 ms, the only DUT condition that halts the run.

### 5.2 Fresh-Pi bring-up steps — real gap, not preserved here

`PI_SETUP_AND_TEST_PLAN.md` Part 1 and `DEPLOYMENT.txt` steps 1-4 cover: creating the `ccid` service account, `apt` package prerequisites, `raspi-config` interface setup, `gpio`/`plugdev`/`video` group membership, installing the udev rule, cloning the repo and creating the venv, and (once decided per §2.8) installing the systemd unit. **None of this is covered anywhere in `docs/` (by design — it's ops, not code) or in the new operator runbook (which explicitly assumes `cd ~/ccid_automation && source venv/bin/activate` already works).** If you provision a second Pi, or rebuild this one from a fresh SD card, there is currently no document that walks through that from zero. Not fixed as part of this audit — flagged so it's a deliberate choice, not an accident, if both source files go away before a replacement exists.

---

## 6. Dangling references to fix once `IMPLEMENTATION_QUESTIONS.md` is deleted

Two places inside `docs/` currently cite it by filename:

- `docs/system-overview.md` §5: *"Still an open item — see `IMPLEMENTATION_QUESTIONS.md`."* → should point at §4 of this file instead.
- `docs/test-suite-guide.md` §4: *"...this is the same open item tracked in `IMPLEMENTATION_QUESTIONS.md` as K1/K2 physical-state readback..."* → same fix.

Small, mechanical, not done as part of this audit since you asked for the discrepancy file specifically — flagging so it doesn't get missed.
