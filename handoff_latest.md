# Handoff — CCID Endurance Test Automation

**Date:** 2026-07-30 (revised — second session, review pass)
**Status:** Design phase complete. All decisions locked. Next deliverable is the specification document.
**Prior session format:** Structured interview ("grilling") — 19 question rounds, one decision at a time.

**Revision note (third pass — hardware change):** supervisor directed that **both mains legs be switched** and that leakage injection get its own dedicated contactor. Rig is now **three contactors, independently driven**: K1 = L1 mains, K2 = L2 mains, K3 = leakage injection. This resolves the L2-still-live deviation (§6) and moots the pole-count open item. Driver board identified as **ZX-517** — non-isolated, low-side switching, **no onboard flyback** (§5.1). New requirements: ground bonding (§5.1), leg-mismatch detection (§13), open-order interlock (§7). Note: every `K2` in prior revisions referred to leakage injection and is now `K3`.

**Revision note (second pass):** the plan was reviewed rather than re-derived. Changes are confined to: injection tap point now locked (§3.1), cycle-time model corrected to boot-bound (§1, §7, §11), vision-timeout failure modes disambiguated (§8, §13), latch-clear durability raised as an unknown (§13, §14, §15), dead-man's-switch monitoring added (§10, §11), and `cycles.csv` schema pinned (§11). Both items raised for decision during this pass — the K3 stuck-closed pre-trigger check (§13) and the healthchecks.io dead-man's switch (§11) — were **accepted by the user** and are now locked. No open recommendations remain from the review.

---

## 1. Project in one paragraph

The user must run **6000 repeated CCID (Charge Circuit Interrupting Device) trip tests** on an EV charger (EVSE) to UL 2231-2. The manual procedure works but consumes a human operator for the entire duration. Automation is **not** primarily about speed — it is about removing the human from the loop. A Raspberry Pi 4B acts as supervisory controller, switching EVSE mains power and leakage-current injection via two contactors, arming a Keysight oscilloscope for each cycle, capturing waveform and screenshot, and using a webcam to read the EVSE status LED. Estimated run time: **~65–75 s/cycle × 6000 ≈ 108–125 hours (4.5–5 days) continuous, unattended.**

The earlier 55 s/cycle figure was an assumption and is superseded. Only the boot time is measured (45–47 s). Boot plus the 10 s cooldown is a **~57 s fixed floor before any I/O occurs**, which makes the cycle boot-bound, not transfer-bound — this has consequences for capture depth, see §11. All remaining per-cycle timings are assumptions to be replaced with measurements at commissioning stages 4 and 5. **Do not plan the campaign schedule against the estimate; plan it against the stage 5 measurement.**

---

## 2. Working agreement (read this before producing anything)

- **The user writes the code.** They develop in VS Code on Windows (also has a MacBook), assisted by GitHub Copilot Pro, pushing to a shared GitHub repo.
- **The assistant produces architecture and specification only** — interface contracts, state tables, config schema, fault matrix, HAL signatures, commissioning checklists. Not implementations.
- **Communication style:** the user has "caveman mode" active (see `/mnt/skills/user/caveman/SKILL.md`) — terse, compressed, no filler. Drop the compression for safety warnings, multi-step ordered procedures, and anything where fragments create ambiguity. The user has said plainly when they do not understand something; explain in full prose in those cases.
- **The user responds well to being challenged.** They have accepted every correction where the reasoning was shown, and reversed their own position twice. Do not soften technical objections.
- **Immediate next action:** write the specification document. The user was about to say "go" when they requested this handoff instead.

---

## 3. Test definition (locked)

| Item | Value |
|---|---|
| Measurand | Trip time — duration of leakage current flow from injection to CCID clearing |
| Standard | **UL 2231-2, §23.3.1** |
| Pass limit | **≤ 24.97 ms** |
| Injection | **30 mA RMS** (42.4 mA peak), fixed, **L1-to-ground only** |
| Injection tap point | **EVSE output conductors — vehicle side, downstream of the CCID interrupting contacts.** Locked; see §3.1 |
| Mains | 208 VAC line-to-line, **~120 VAC line-to-ground**, 60 Hz |
| Resistor bank | Variable type, set to ~4 kΩ for 30 mA |
| Cycle count | 6000 |
| EV simulator | Spoof device, **static and always-on**, not automated, requires no per-cycle action |

### 3.1 Injection tap point (locked this session)

The resistor bank is connected across the **EVSE output conductors — vehicle side, downstream of the CCID's interrupting contacts.** K3 is a switch in series with that bank.

This was previously implicit and is now stated explicitly, because it is the single fact the entire measurand rests on and a fresh reader would otherwise have to infer it. Three independent lines of evidence confirm it:

1. **The waveform collapses to zero on trip** (§4). If the bank were tapped upstream of the interrupting contacts, the CCID opening would not remove the source of that current — the sine would continue. Current stopping when the device trips is only possible if the device sits between the source and the tap. This is the decisive argument.
2. **It trips at all.** The injected current must pass through the CCID's sensing transformer in an unbalanced way to be detected. Injection outside the sensed loop produces no imbalance and no trip.
3. **The vision gate is necessary** (§8). The design waits for blinking green before closing K3 on the grounds that charging is the only state where the CCID is in service. That gate is only meaningful if the tap point is dead until the EVSE closes its output contactor. Tapped on the input mains, injection would be live the moment K1/K2 closed and the gate would serve no purpose.

**Note on reasoning that does *not* establish this:** "K3 opens and closes the resistor bank" is not evidence of tap location. K3 is in series with the bank; the bank-plus-switch assembly would behave identically connected to input or output terminals. The conclusion is correct, but rests on the three arguments above, not on K3's existence.

**Still verify physically at Stage 1** (open item 11). Not because the conclusion is doubtful, but because this is a rig detail nobody would think to re-check after a pause, a bench reorganisation, or a second operator touching the setup — and getting it wrong produces 6000 clean-looking no-trips with no obvious cause. Trace both bank leads to their terminals, photograph the connection, commit the photo to the repo.

### Verdict table (final — revised three times, this is the version that stands)

| Trip time | Verdict | Action |
|---|---|---|
| ≤ 24.97 ms | PASS | continue |
| 24.97 ms – 100 ms | FAIL | log, alert, **continue** |
| ≥ 100 ms, or no trip | NO-TRIP | **HALT run** |

**No consecutive-failure guard. No cumulative-failure guard.** Both were considered and deliberately dropped — see §8.

---

## 4. Measurement chain

- Oscilloscope **Channel 1 only**. A standard 10:1 passive voltage probe, tip on one end of the resistor bank, **ground clip on ground** (confirmed safe node).
- Because the probe sits across the injection resistor, the observed voltage waveform is the leakage current scaled by R. Voltage and current statements in the prior conversation both refer to this same signal.
- Untripped: continuous 60 Hz sine. Tripped: sine truncates and collapses to zero.
- The leakage current is **AC**, so it crosses zero twice per mains cycle *while still flowing*. A naive threshold detector measures one half-cycle (8.33 ms), not the burst. **Trip time must be derived from the burst envelope, not from a single-cycle width measurement.**
- Probe rated 300 V CAT II — adequate at 120 V.

### Deferred: the trip-time algorithm

The user's decision, and it is sound: **capture first, compute later.** Rationale — the algorithm is cheap to redo, 6000 test runs are not.

This deferral is only valid because raw waveform data is stored per cycle and can be re-analysed offline (`tools/replay_waveform.py`). It would **not** be valid if only screenshots were saved. Do not let a future session weaken the raw-capture requirement on the grounds that the algorithm is undecided.

`ccid/analysis.py` exists as a stub for this reason.

### Open issue the algorithm must confront

24.97 ms sits **30 µs below the 3-half-cycle mark** (3 × 8.333 ms = 25.00 ms). There is no margin. Consequences:

- The definition of t=0 and t=end can shift a result by milliseconds — enough to flip a verdict. Two reasonable engineers could disagree on identical data.
- **Check UL 2231-2 for its own definition of the measurement endpoints.** If the standard defines them, use its definition. If it does not, write the chosen definition into `config.yaml` before the run and never change it.
- 1 Mpts at 10 MSa/s gives 100 ns resolution — far more than needed. The raw data can answer any definition later chosen.

### Phase randomness — important, affects interpretation

K3 closes at a random point in the mains cycle (the Pi has no phase reference), and contactor pull-in varies by a few ms. Each measured trip time therefore includes a **random 0–8.33 ms component**.

This is correct and intended — real ground faults occur at random phase, and 6000 cycles exist to characterise that distribution. **Do not phase-synchronise the injection.**

But it means: if the unit's median trip time sits near 24.97 ms, identical healthy hardware will produce a mix of PASS and FAIL purely from phase luck. This is the reason both failure-count halt guards were dropped.

---

## 5. Hardware

### Inventory
Raspberry Pi 4B · **3×** TE **ECK100BH4AAA** contactors · **3× ZX-517** dual-MOSFET drive boards (3.3–20 V trigger input, 15 A cont. / 30 A peak, DC 5–36 V) · Logitech C270 webcam · **3× independent 12 V 2 A supplies** (one per coil) + 5.1 V 3 A Pi supply · 64 GB microSD · **Keysight MSO-X 2014A** · variable resistor bank

### Control channels
```
GPIO17 (physical pin 11) -> ZX-517 #1 -> K1 coil -> EVSE mains power, L1
GPIO27 (physical pin 13) -> ZX-517 #2 -> K2 coil -> EVSE mains power, L2
GPIO22 (physical pin 15) -> ZX-517 #3 -> K3 coil -> leakage injection

Each driver fed by its own isolated 12 V 2 A supply. K1 and K2 are driven
INDEPENDENTLY, not ganged - see 5.2 for the failure mode this creates.
```
Both contactors **normally open**. GPIO17/27 chosen because they have no boot-time special function or pull-up; GPIO0–8 were rejected for that reason.

**Labelling matters, more than before.** Three near-identical contactors and three near-identical drivers. K3 swapped with either mains contactor means injecting fault current into an unpowered EVSE — 6000 no-trip results with no obvious cause. K1 swapped with K2 is harmless electrically but will mislead every later diagnosis. **Label every contactor, every coil lead, every driver board, and every supply, physically, before first power-on.**

### Coil
12 VDC nominal, 0.462 A nominal, 5.5 W, operate ≤ 9 VDC, max 13.2 VDC, release ≥ 1.2 VDC. Two coils = 0.92 A, comfortably inside the 12 V 2 A supply.

No +/- markings observed → probably a plain resistive coil, but **verify by measuring resistance (~26 Ω expected)** at commissioning. If it reads much higher, or differs by lead orientation, there is electronics inside and polarity must be established before driving.

**Coil supplies are separate from the Pi supply, and from each other** — three independent 12 V 2 A units, one per coil. A shared rail would let coil inrush reboot the controller mid-run; per-coil supplies additionally prevent one contactor's inrush from sagging another's pull-in voltage. Steady-state load is 0.462 A into a 2 A supply, so there is generous headroom and no need to stagger closings for supply reasons.

### 5.1 Driver board — ZX-517 (identified this session)

Dual MOSFET module. Terminals: `IN+` / `IN-` (12 V supply in), `OUT+` / `OUT-` (coil), `TRIG/PWM` and `GND` (signal from Pi). Two MOSFETs in parallel for current capacity. Onboard: three resistors and an indicator LED.

Three consequences, all of which change what must be built:

**1. Not optoisolated.** No optocoupler on the board. The MOSFET gates are referenced to the supply negative, and the Pi's GPIO is referenced to Pi ground. If those two references are not tied together, the gate-source voltage is undefined — contactors may fail to switch, switch erratically, or latch on.

> **This is a wiring requirement, not an option. Bond the `GND` signal pin of all three driver boards to Pi ground, at a single star point. Because each board's signal ground is common with its own `IN-`, this also ties all three 12 V supply negatives together.** That is acceptable and expected — but it means the three supplies are no longer galvanically isolated from one another, only independently regulated. Do not describe them as isolated in the spec or the diagram.

**2. Low-side switching.** `IN+` passes through to `OUT+`; the MOSFETs switch `OUT-` to `IN-`. So `OUT+` sits at +12 V continuously and `OUT-` is pulled to 0 V to energise the coil. **The flyback diode therefore goes across the coil with its cathode (banded end) to `OUT+`, anode to `OUT-`.** See §9 trap 1.

**3. No onboard flyback diode.** Only three resistors and an LED are present. **All three flyback diodes must be added externally.** Trap #1 was previously written as "inspect the boards, many already include one" — that question is now answered, and the answer is no. This is the single highest-priority hardware task, and it now applies three times over.

Also unverified and worth a meter before power-on: whether R1/R2/R3 include a gate pulldown, and whether a 3.3 V GPIO reliably drives `TRIG/PWM` (rated 3.3–20 V, so nominally yes, but confirm switching is clean and not marginal).

### 5.2 Independent drive of K1 and K2 — accepted, with a consequence

K1 and K2 are driven by separate GPIO and separate drivers rather than ganged from one signal. This was chosen for design clarity and because it leaves single-leg fault testing available later.

Ganging would have been marginally safer on one axis: one signal physically cannot desynchronise, so there would be no software path to a single-leg-live state. Independent drive reintroduces that state — not as a design property, as a **fault** property. A bug, a driver failure, or one welded contactor leaves one leg closed and one open, with EVSE internals sitting at ~120 V to ground.

This is an acceptable trade, but it must be detected rather than assumed away. See the leg-mismatch row in §13.

**No state readback.** The contactors have no auxiliary contacts, so software knows only what it *commanded*, never what actually happened. A welded K1 or K2 is invisible to the current design — unlike K3, which the scope's pre-trigger window catches (§13). Options, in increasing cost: accept and log the gap; add aux contacts and two GPIO inputs for true readback; or voltage-sense the EVSE input. **Not a blocker for MVP. Logged as a known gap, not as solved** — see open item 16.

### Safety measures required before cycle 1

1. **Gate pulldown resistors (~10 kΩ, gate-to-source) on each of the three MOSFET inputs.** During a Pi reboot, GPIO reverts to inputs; a floating gate leaves the contactor state undefined. Pulldowns force both contactors open on any reboot or power loss. **Verify whether the driver boards already have them — measure, do not assume.**
2. **Flyback diodes** — see §9, this is the most important outstanding hardware item.
3. Upstream protection: 16 existing breakers in the system, retained. The automation layer is inserted downstream, so protection remains independent of any software.
4. Lab is semi-restricted, hazard signage present, accessible for emergency intervention.
5. **Software detection of K3 stuck closed** (§13) — per-cycle check for current in the scope's pre-trigger window. **This is a detector, not a protection.** Once a MOSFET has shorted, nothing in software can open that contactor; the check only converts a silent hazard into an immediate halt and alert. It is **not** a substitute for item 2 — the flyback diodes are what prevent the short from happening in the first place.

### Known accepted deviation — RESOLVED

**Previously:** K1 broke L1 only, leaving EVSE internals at ~120 V to ground through L2 during every off interval, cooldown, and halt state across four unattended days. Accepted for MVP under signage and restricted access.

**Now closed.** K1 and K2 break L1 and L2 respectively, so the EVSE is fully de-energised between cycles. The ECK100BH4AAA pole-count question is moot — two single-pole devices are used rather than one two-pole device. Retained here as a record of why the third contactor exists, not as an outstanding item.

---

## 6. Oscilloscope

**Keysight MSO-X 2014A**, firmware 02.43.2018020635.
VISA resource: `USB0::0x0957::0x1798::MY58100795::INSTR`

USB transport chosen by the user. Communication has been **proven on Windows only** (NI-VISA + PyVISA, `*IDN?` and live measurements verified).

### Locked configuration — written explicitly every cycle, read back into the per-cycle JSON

```
:TIMebase:SCALe 0.02          # 20 ms/div -> 200 ms window
:TIMebase:REFerence LEFT      # 20 ms pre-trigger, 180 ms post
:CHANnel1:SCALe 50            # 120 V peak = 170 V, 3.4 of 4 div
:CHANnel1:OFFSet 0
:CHANnel1:COUPling DC
:CHANnel1:PROBe 10
:TRIGger:SWEep NORMal
:TRIGger:SOURce CHANnel1
:TRIGger:LEVel 20
:TRIGger:SLOPe POSitive
:ACQuire:TYPE NORMal
:WAVeform:SOURce CHANnel1
:WAVeform:FORMat BYTE
:WAVeform:POINts:MODE RAW
:WAVeform:POINts MAXimum
```

**Never inherit front-panel state.** The user's manual setup was "default settings" — undocumented and non-reproducible. A scope knocked to different settings at cycle 3000 would silently corrupt everything after.

### Rationale for the values that were changed from the user's manual setup

- **200 ms window** (was 100 ms): 50 ms post-trigger could not distinguish a no-trip from a 60 ms trip — both simply run off screen. 180 ms post-trigger separates them.
- **`REFerence LEFT`** avoids the `:TIMebase:POSition` sign-convention trap.
- **Trigger +20 V** (was −1.25 V): a near-zero trigger level on a floating node invites noise false-triggers over a 4-day unattended run. +20 V is 0.4 div — clear of noise, still crossed within one half-cycle of injection. It was deliberately **not** raised to 50% of peak, because K3 may close near a peak and the next crossing would come 16.7 ms later, consuming the entire budget. True t=0 comes from pre-trigger data in post-processing regardless, so this setting is about robustness, not accuracy.
- 8-bit ADC → `BYTE` format loses nothing versus `WORD` in normal acquisition mode.

---

## 7. Cycle sequence

```
1.  K1 close, K2 close                 (mains L1 and L2)
2.  wait for vision = blinking green   (timeout 90 s; fallback: fixed 60 s wait)
3.  :SINGle
4.  POLL until armed                   <-- see traps, this is a real race
5.  K3 close, log timestamp            (leakage injection)
6.  poll until acquisition complete    (timeout 5 s)
7.  K3 open                            (hard backstop 300 ms)
8.  transfer .npz, scope .png, camera .jpg, read back settings -> .json
9.  K3 confirmed open, THEN K2 open, K1 open
10. cooldown 10 s
```

**Close order (step 1):** K1 and K2 are commanded together. No stagger is needed for supply reasons — each coil has its own 2 A supply against a 0.462 A load. A short stagger may still be used if inrush proves noisy on the bench; if so, it must be a **defined, bounded** stagger, because the mismatch detector in §13 keys off it.

**Open order (step 9) — strict, never reorder.** K3 opens first and is confirmed open before either mains contactor is commanded. Leakage current is killed before mains, never the other way round. Opening mains while injection is still closed leaves the resistor bank connected to a de-energising EVSE output, which serves no purpose and puts the trip event outside the captured window.

**Boot time 45–47 s, measured** (K1/K2 close to charging state) — this is the only per-cycle timing currently backed by measurement. **Cooldown 10 s** — not thermal; it ensures the EVSE bulk capacitors discharge fully so the microcontroller gets a clean power-on reset. The user had previously used 2 s; over 6000 cycles that risks intermittent indeterminate boot states that would waste days of debugging.

Boot + cooldown = **~57 s of fixed cost per cycle before any I/O**. The cycle is therefore boot-bound. See §11 for why this settles the capture-depth question.

**Extended cooldown on retry:** when the vision gate times out and the cycle is retried, the retry uses a **60 s cooldown, not the standard 10 s** — see §8. A retry that repeats the same 10 s interval is unlikely to succeed where the first attempt failed, and a longer de-energised interval is precisely what clears a latched CCID.

**K3 hard backstop 300 ms** — was 500 ms, tightened once the no-trip halt line moved to 100 ms. Acquisition ends at 180 ms post-trigger, so current flowing beyond 300 ms yields no data and only stresses the DUT and resistor bank.

### Interlocks
- K3 can **never** close unless **both** K1 and K2 are closed and the LED gate has confirmed charging state.
- Neither K1 nor K2 opens while K3 is closed.
- K1 and K2 must hold matching commanded states outside the defined stagger window. A mismatch that persists is a fault, not a transient — see §13.
- Enforced in `safety.py`/`contactors.py` — the lowest layer — so no future edit to the sequencer can bypass it.
- **Every failure path opens all three contactors** via `try/finally`, not `except`, and in the correct order (K3 first). An unhandled exception must still de-energise every coil.

---

## 8. Vision subsystem

**Scope: exactly one job** — confirm the EVSE reached charging state before K3 closes. It never touches the measurand.

LED states: off (semi-transparent grey), blue, green, red. Blinking through all colours = booting. Solid or blinking blue = ready. **Blinking green = charging.** Blinking red = faulted.

The gate condition is **blinking green, not blue** — that is the only state where the CCID is actually in service. Because the EV simulator spoof is always on, the EVSE transitions to charging on its own shortly after boot.

### Implementation notes
- Classify over a **~3 s temporal window at ~15 fps**, by *which hues are present*, ignoring blink rate. Multiple hues → booting; green present → charging; red present → faulted. Rate-independent unless the blink is slower than ~0.5 Hz (unverified — confirm with a stopwatch once).
- **HSV, not RGB.** Single fixed ROI over the LED.
- **Disable C270 auto-exposure and auto white balance** via `v4l2-ctl`. Both will drift over four days and destroy thresholding.
- **Shroud the LED and camera.** Lab lighting will change day-to-night, or someone flips a switch.
- Require N consecutive agreeing frames before declaring a state change.
- **Vision must not be able to kill the run.** If the camera fails, degrade to a fixed 60 s wait and continue in logged degraded mode.
- Vision may also serve as *secondary* trip confirmation (blinking red) — logged as evidence, never acted on.

**Vision can never be the trip detector.** C270 runs at 30 fps = 33 ms/frame, against a 24.97 ms limit. Physically impossible.

### Vision gate timeout — three distinct failures, not one (added this session)

The 90 s gate timeout previously collapsed three unrelated problems into a single "retry once, then HALT". The LED state *at the moment of timeout* distinguishes them, and they warrant different handling. Record the observed state in the halt reason and in the notification — a halt at 3 a.m. on day three should say which of these it was.

| LED state at timeout | Meaning | Response |
|---|---|---|
| **Blinking red** | CCID latched from the previous cycle's trip and did not clear on the standard power cycle | Retry once with **60 s extended cooldown**. If it clears, log as `latch_slow_clear` and continue. If not, HALT |
| **Solid or blinking blue** | EVSE booted correctly but never entered charging — the EV simulator spoof has dropped out | **HALT immediately, no retry.** A retry cannot fix a disconnected spoof and only wastes a cycle |
| **Off / no LED detected** | EVSE never powered up — K1 or K2 failed to close, upstream breaker opened, or supply lost | **HALT immediately, no retry.** Rig fault |
| **Camera unavailable** | Vision subsystem itself failed | Degrade to fixed 60 s wait, continue in logged degraded mode (unchanged, see above) |

**Count and report `latch_slow_clear` events.** A single one is unremarkable. A rising rate across the campaign means the CCID's reset behaviour is degrading with cumulative operations — which is itself a finding about the DUT, and one that is invisible if these are silently retried. See §15 open item 12.

---

## 9. Traps and non-obvious failure modes

These were each discovered during the design conversation. A fresh agent is likely to walk into them.

| # | Trap | Consequence | Mitigation |
|---|---|---|---|
| 1 | **No flyback diode on contactor coils — CONFIRMED ABSENT** | Coil turn-off drives the drain hundreds of volts positive. The MOSFET body diode is reverse-biased and offers no path, so the device absorbs it in avalanche. **Avalanche-stressed MOSFETs usually fail shorted.** A shorted K3 driver = leakage injection permanently closed, with no software remedy — it defeats the pulldowns, the NO contactors, and the timeout simultaneously. A shorted K1 or K2 driver welds a mains leg closed with no readback to detect it (§5.2). | **The ZX-517 has no onboard flyback** (§5.1) — this is now confirmed, not an open question. Fit 1N4007 across **all three** coils, **cathode (banded end) to `OUT+`**, anode to `OUT-`. Reversed = dead short on power-up. Highest-priority hardware task; now applies three times. |
| 2 | "Dual MOS parallel" implies flyback protection | It does not. Parallel MOSFETs share current and halve R<sub>DS(on)</sub>; they do nothing for inductive loads. | See above. |
| 3 | **`:WAVeform:POINts:MODE` defaults to `NORMal`** | Returns ~1000 points regardless of 1 Mpts in memory. All 6000 captures would be silently truncated. | Set `RAW` + `POINts MAXimum`. Valid only when acquisition is stopped — true after `:SINGle` completes, so the sequence is already correct. |
| 4 | **`:DIGitize` blanks the display** on InfiniiVision | The subsequent `:DISPlay:DATA?` screenshot returns empty. | Use `:SINGle`. |
| 5 | **Arming race after `:SINGle`** | `:SINGle` returns immediately; the scope is not yet armed. Closing K3 too quickly misses the trigger entirely. | Poll `:OPERegister:CONDition?` bit 3 (Run bit) until set. **Never `time.sleep()` as synchronisation** — sleep-based sync works for 50 cycles then corrupts data at cycle 3000. |
| 6 | **NI-VISA and Keysight IO Libraries have no ARM/Raspberry Pi build** | The Windows validation does not transfer to the Pi. | `pyvisa` + `pyvisa-py` + `pyusb` + `libusb`. Resource string and PyVISA API stay identical; only the backend changes. Verify with `pyvisa-info`, then repeat `*IDN?` on the Pi. |
| 7 | udev permissions | Running a 92-hour unattended service as root is poor practice. | udev rule for VID 0957 / PID 1798, `MODE="0666"`, `GROUP="plugdev"`; add user to `plugdev`; reload rules. |
| 8 | **pyvisa-py USBTMC is weak on large binary transfers** | `:DISPlay:DATA? PNG` is exactly that. Most likely thing to break on the Pi. | Test screenshot transfer **early** (commissioning stage 2). Time a 1 Mpts transfer too. |
| 9 | Scope measurement functions on AC leakage | Built-in width/period measurements return one half-cycle, not the burst duration. Wrong number, 6000 times. | Compute from the raw envelope offline. |
| 10 | **Do not PWM the contactor coil** | The driver board advertises 0–20 kHz PWM. Irrelevant here, and actively harmful if the coil turns out to contain electronics. | DC on/off only. |
| 11 | Flyback slows drop-out | Diode-only suppression lets coil current decay gently; the contactor opens perhaps 2–3× slower than bare. | Harmless here — K3 opening is not the measurand, and the 300 ms backstop has room. Noted so it does not surprise anyone on the scope. |
| 12 | **NTP time steps over a 4-day run** | Wall-clock jumps corrupt duration measurements. | All durations from `time.monotonic()`. Enforce in `clock.py`. |
| 13 | SD card is a single point of failure | 6000 cycles of writes plus possible power interruption. | `fsync` per artifact; commit-then-counter ordering (see §11). Consider imaging the card before the run. |
| 14 | `apt upgrade` mid-run | A library version change can break a run in progress. | Pinned `requirements.txt` in a venv. |

---

## 10. Software architecture

Structure is the user's own tree from a prior session (photographed and supplied), **adopted with two changes**. An initial simpler 6-file proposal was made and then withdrawn — see §12.

```
/opt/ccid/
├── config.yaml                # single source of tunable values
├── ccid/
│   ├── main.py                # CLI entry, orchestration, signal wiring
│   ├── config.py              # load, validate, hash
│   ├── errors.py              # exception taxonomy
│   ├── clock.py               # monotonic/wall time helpers
│   ├── states.py              # enums: CycleState, Terminal
│   ├── hal/
│   │   ├── __init__.py        # factory: real vs sim from config
│   │   ├── base.py            # abstract interfaces
│   │   ├── gpio_real.py   / gpio_sim.py
│   │   ├── scope_real.py  / scope_sim.py
│   │   └── camera_real.py / camera_sim.py
│   ├── safety.py              # SafeOff, Heartbeat, TimeoutSupervisor, Progress
│   ├── analysis.py            # waveform envelope -> trip result   [STUB, deferred]
│   ├── classify.py            # camera frames -> charger state     [OpenCV]
│   ├── sequencer.py           # the state machine
│   └── recorder.py            # CSV, JSON sidecar, waveform, image, fsync, run state
├── tools/
│   ├── gpio_selftest.py       # commissioning stage 1
│   ├── scope_bench.py         # commissioning stage 2
│   ├── calibrate_camera.py    # commissioning stage 3; also records camera_sim footage
│   ├── replay_waveform.py     # re-run analysis on saved captures  [the deferral payoff]
│   └── simulate.py            # long sim runs with fault injection
├── tests/
│   ├── test_analysis.py
│   ├── test_sequencer.py
│   ├── test_safety.py
│   └── test_faultmatrix.py
└── runs/<run_id>/
    ├── config.yaml            # frozen copy
    ├── cycles.csv             # rollup — scan 6000 results without parsing 6000 JSONs
    ├── cycles/<n>.json
    ├── waveforms/<n>.npz
    ├── images/<n>_scope.png
    ├── images/<n>_green.jpg
    ├── images/<n>_fault_<k>.jpg
    └── run.log
```

**Changes made to the user's tree:** `test_config.py` cut (covered by running config validation); `runstate.py` folded into `recorder.py` (same concern, too thin to stand alone).

### Stack
Python 3.11+ · single process, single thread plus one camera reader thread (no async — concurrency buys nothing here and makes failures unreproducible) · `pyvisa` + `pyvisa-py` + `pyusb` · `gpiozero` with `lgpio` backend (**not** `RPi.GPIO`, deprecated) · `opencv-python-headless` · `systemd` service with auto-restart and watchdog · venv with pinned versions · **ntfy.sh** for phone alerts (free, no account) · **healthchecks.io** as a dead-man's switch for liveness (see §11).

### HAL simulation fidelity
The user **has all hardware in hand** and can work from Windows or macOS, which reduces how much the sims must do:

| HAL | Sim role |
|---|---|
| `gpio_sim` | **Essential** — no GPIO on Mac or Windows. Logs state changes; must enforce the *same* interlock as the real implementation. |
| `scope_sim` | **Moderate** — the real scope works over USB from Windows and Mac. Sim exists for CI, unit tests, and fault injection. Synthesises a 120 V 60 Hz sine truncating at a configurable trip time, plus injectable faults: no-trip, no-trigger, comms timeout, truncated transfer, malformed preamble. Not an instrument model. |
| `camera_sim` | **Replay, not synthesis** — `calibrate_camera.py` records real footage of all four LED states; `camera_sim` replays it. Real blink rate, real lighting, real sensor noise beats any synthetic LED. |

---

## 11. Data, storage, and resume

### Per-cycle artifacts
- **`waveforms/<n>.npz`** — samples plus the `:WAVeform:PREamble?` scaling values bundled in one self-describing file. **The preamble is non-negotiable**; without x_increment, x_origin, y_increment, y_origin, y_reference and points, the samples are meaningless numbers.
- **`images/<n>_scope.png`** — human evidence of the waveform.
- **`images/<n>_green.jpg`** — camera frame at the moment the charging gate was confirmed. If trip times drift at cycle 4000, the first question is "was it actually charging" — this frame answers it. JPEG not PNG (photographic content; PNG would be ~5× larger for no benefit).
- **`images/<n>_fault_<k>.jpg`** — 5-frame burst at 1 s spacing, only on fault, documenting the halt state.
- **`cycles/<n>.json`** — cycle index, UTC timestamps, run ID, full preamble, **all scope settings read back from the instrument (not assumed)**, GPIO event timestamps (K1 close, K2 close, K3 close, K3 open, K2 open, K1 open), LED state transitions, inline sanity verdict, software version.

### Storage budget
1 Mpts BYTE ≈ 1 MB/cycle + ~150 KB images + JSON ≈ **~6.5 GB total** on a 64 GB card. Comfortable. **Do not skimp on capture depth** — skimping saves nothing and re-running 6000 cycles is the expensive outcome.

**Capture depth is settled: keep 1 Mpts. Do not drop to 100 kpts to save schedule time** (revised this session — this reverses the earlier contingency).

The reasoning changed once the cycle-time model was corrected. The cycle carries a ~57 s fixed floor from boot and cooldown alone (§7), so it is **boot-bound, not transfer-bound**. Even a sluggish 5 s waveform-plus-screenshot transfer is under 10% of cycle time; halving it saves roughly 4 hours across a 110-hour campaign, or about 3%. In exchange it discards the capture depth that makes the deferred-algorithm decision viable (§4). That is a bad trade, and it will look superficially attractive to a future session reading stage 2's transfer timings in isolation.

If stage 2 shows pyvisa-py transfers are slow, **accept the slower transfer.** Only reduce depth if a transfer proves so slow that it genuinely dominates the cycle — meaning tens of seconds, not single-digit seconds — and record the measured number in the decision if so.

### `cycles.csv` schema — one requirement that matters (added this session)

**Trip time is stored as a raw float in seconds, in its own column, separate from the verdict string.** Never store only the verdict, and never store the trip time pre-rounded to the verdict's precision.

This is the same principle as the deferred algorithm (§4), applied one layer up. All campaign-level analysis is deliberately deferred to the user, offline, after the run (§16). If the pass limit moves, or the endpoint definition in `analysis.py` changes, or the campaign criterion turns out to need percentiles rather than a per-cycle threshold, verdicts must be re-derivable from `cycles.csv` alone — without reparsing 6000 JSON sidecars or re-running 6000 waveforms.

Minimum columns: `cycle_index`, `run_id`, `utc_timestamp`, `monotonic_start`, `trip_time_s` (float, null if no trip), `verdict`, `analysis_version`, `led_state_at_gate`, `degraded_flags`, `notes`. `analysis_version` matters — it lets a later reader tell which rows were computed under which algorithm after a `replay_waveform.py` pass.

### Inline sanity check
A crude threshold check runs every cycle even though the real algorithm is deferred. Its only purpose is to catch "the scope captured nothing" immediately rather than at cycle 6000. It is **not** the reported number.

### Resume semantics

**`run_id`** = `YYYYMMDD_HHMMSS` at first start. A resume reuses the same run_id and directory — one campaign, one directory, even across reboots.

**State file** `runs/<run_id>/runstate.json`, written **after** each cycle is fully committed: `run_id`, `last_completed_cycle`, `target_cycles`, `config_hash`, `pass_count`, `fail_count`, `halt_reason` (null while running).

**Commit order — the ordering is the point:**
1. Write `.npz`, `.png`, `.jpg`, `.json`
2. `fsync` each
3. Append row to `cycles.csv`, fsync
4. Update `runstate.json` atomically (temp file → fsync → `os.replace`)
5. Send heartbeat ping (§11 liveness) — **after** step 4, never before, so a ping certifies a fully committed cycle. Non-blocking, failures logged and ignored

A crash can occur between any two steps. Data is written *before* the counter advances, so a crash mid-cycle leaves artifacts the counter never acknowledged. **On resume, delete orphans above `last_completed_cycle` and redo that cycle.** The reverse order would mark a cycle complete with truncated data — silent corruption discovered only during analysis.

**Config hash check on resume:** stored hash ≠ current config → refuse to start without an explicit override flag. This prevents changing scope settings halfway through 6000 cycles and producing a dataset that is not internally comparable.

**Halt is sticky:** with `halt_reason` non-null, the service does not auto-resume on boot. A human must clear it. Otherwise a watchdog reboot would restart the run into a device that already failed, and loop for days.

**Resume never re-closes contactors on startup.** Boot → read state → verify both coils de-energised → log → begin cycle N+1.

### Watchdogs
- **System level:** `RuntimeWatchdogSec=10`, `RebootWatchdogSec=60` in `/etc/systemd/system.conf`. Catches kernel hangs. BCM2711 max timeout ~15 s.
- **Service level:** the test script sends `sd_notify("WATCHDOG=1")` each cycle. Catches a hung script while the kernel is healthy — layer 1 cannot see this case.
- **Watchdogs are not a safety mechanism.** During reboot the GPIO reverts to inputs; only the hardware gate pulldowns keep the contactors open. See §5.

### Liveness monitoring — dead-man's switch (locked, in MVP)

**The problem:** ntfy.sh is a push service. It fires on faults. A Pi that has died hard — kernel panic, SD card failure, power loss, network drop — produces no faults and therefore no messages. **Silence and success look identical.** Over a 4–5 day unattended run this is a real gap: the run could stop at hour 6 and go unnoticed until someone checks the bench.

**The fix:** invert the direction. The Pi pings an external service every cycle; that service alerts when the pings *stop*. `healthchecks.io` is free and purpose-built for this — one HTTP GET per cycle, configured to alert if no ping arrives within a grace window.

- Grace window: **5 minutes.** Comfortably above the ~75 s cycle, tight enough to catch a stall quickly.
- Ping **after** `runstate.json` is committed (step 4 of the commit order), so a ping means a cycle genuinely completed, not merely that the loop is spinning.
- Include `last_completed_cycle` in the ping body — the dashboard then doubles as remote progress monitoring.
- Failure to ping must **never** halt the run or raise. Wrap in `try/except`, log, continue. Monitoring is not permitted to become a failure mode. This mirrors the vision rule in §8.
- A halt should ping the service's `/fail` endpoint explicitly, so an intentional halt is distinguishable from a dead controller.

Roughly three lines of code. Given the run is unattended for four days, this belongs in the MVP rather than deferred.

---

## 12. Rejected alternatives — do not relitigate

| Proposal | Rejected because |
|---|---|
| Scope built-in `:MEASure` for trip time | Measures one AC half-cycle, not the burst |
| Vision as trip detector | C270 33 ms/frame vs 24.97 ms limit — physically impossible |
| `:DIGitize` for acquisition | Blanks the display, breaks screenshot capture |
| `time.sleep()` for instrument sync | Works for 50 cycles, corrupts data at cycle 3000 |
| `WORD` waveform format | 8-bit ADC — no additional information in normal acquisition |
| Screenshots as the primary record | A PNG is a picture of a number, not a number. Cannot re-run a new algorithm over 6000 PNGs — this would invalidate the deferred-algorithm decision |
| Dropping to 100 kpts to shorten the campaign | Added this session. The cycle is boot-bound (~57 s fixed floor), so halving transfer time saves ~3% of schedule while discarding the capture depth the deferred algorithm depends on. Only revisit if measured transfer time is tens of seconds — see §11 |
| Retrying a vision-gate timeout with the standard 10 s cooldown | Added this session. If 10 s failed once it is unlikely to succeed on repetition; a latched CCID needs a *longer* de-energised interval. Retry uses 60 s, and only for the blinking-red case |
| Flat output directory (assistant's first proposal) | The user's `runs/<run_id>/` layout with frozen config and CSV rollup is better |
| Simplified 6-file structure (assistant's first proposal) | Under-scoped. The HAL sim layer is justified by cross-platform development; `replay_waveform.py` is what makes the deferred algorithm viable; safety and resume infrastructure *is* the product at 92 hours |
| 2 s cooldown | Insufficient capacitor discharge for a clean EVSE power-on reset |
| Phase-synchronised injection | Random phase is the correct test — real faults occur at random phase |
| Halt on trip time > 30 ms | User revised: a 40 ms trip is still protected. That is a performance result, not a safety failure |
| Halt on 5 consecutive FAILs | With phase randomness, if the median sits near the limit ~half of cycles fail by luck; P(run of 5) ≈ 1/32 → ~190 spurious halts across 6000 cycles |
| Halt on 25 cumulative FAILs | Could trigger by cycle 200 on a perfectly healthy unit |
| Hardware K3 timeout (555/RC monostable) | Recommended, but **deferred post-MVP** by user decision — extra parts. Logged as a known gap, not as solved |
| Automating the EV simulator spoof | Too difficult, and testing showed it can simply be left always-on |
| LAN/VXI-11 transport | User prefers USB; USB path already validated on Windows |

---

## 13. Fault matrix

| Fault | Detection | Response | Notify |
|---|---|---|---|
| No trip (≥100 ms current) | waveform runs to end | **HALT** | yes |
| Trip 24.97–100 ms | analysis | log FAIL, continue | yes |
| Scope never triggered | poll timeout 5 s | **HALT** — rig fault, not DUT fault | yes |
| **K3 stuck closed** (welded contacts or shorted MOSFET) | **current present in the 20 ms pre-trigger window, before the K3 command** | **HALT** — see note below | yes |
| **K1/K2 commanded-state mismatch** persists beyond the defined stagger window | internal state check in `contactors.py` | **HALT** — single-leg-live condition (§5.2) | yes |
| **K1 or K2 stuck closed** | **not detectable** — no aux contacts, no readback | known gap, open item 16 | — |
| Heartbeat ping fails | HTTP exception | log only, **continue** — monitoring must never halt the run | no |
| Controller dead / run stalled | **external**: no heartbeat received within 5 min | external alert to phone | yes |
| Vision timeout 90 s, LED **blinking red** | timer + LED state | CCID latched — retry once with **60 s** cooldown; log `latch_slow_clear`; HALT if still red | on halt |
| Vision timeout 90 s, LED **blue** | timer + LED state | spoof dropped out — **HALT, no retry** | yes |
| Vision timeout 90 s, LED **off** | timer + LED state | EVSE unpowered, K1 or upstream fault — **HALT, no retry** | yes |
| Scope comms drop | VISA exception | reconnect ×3, then HALT | on halt |
| Camera failure | frame read fail | degrade to fixed 60 s wait, continue | once |
| Disk < 2 GB free | check per cycle | **HALT** | yes |
| Script hang | systemd watchdog | restart service, resume | yes |
| Pi hang | hardware watchdog | reboot, resume | yes |
| Power loss | none | contactors open by design; resume on boot | on resume |

**Principle: anything touching the device under test halts. Only peripheral failures degrade or retry.**

**Critical distinction the code must preserve:** "scope triggered but current ran the full window" (= CCID no-trip, the DUT failure being tested for) versus "scope never triggered at all" (= no injection occurred; K3 failed, resistor bank open, or probe detached — a rig fault). A naive implementation sees the same symptom. They mean opposite things.

### On detecting K3 stuck closed (locked)

Trap #1 (§9) identifies a shorted K3 driver as the worst hardware failure available: it defeats the gate pulldowns, the normally-open contactor, and the 300 ms backstop simultaneously, and leaves leakage injection permanently energised with no software remedy. The original fault matrix had **no detection for it**.

Detection is free with data already being captured. `:TIMebase:REFerence LEFT` gives **20 ms of pre-trigger data** in every acquisition. In a healthy cycle that window is quiet — K3 has not yet closed. **Current present in the pre-trigger window means injection was already flowing before it was commanded**, which means K3 is welded or its driver has shorted.

Check this per cycle alongside the inline sanity check (§11), and HALT on it. No new hardware, no new capture, one test on an existing array. It cannot open the contactor — nothing in software can, once the MOSFET is shorted — but it converts a silent hazard into an immediate halt and notification instead of something discovered days later.

`tests/test_faultmatrix.py` drives the full sim stack through every row and asserts SafeOff is reached each time.

---

## 14. Commissioning plan — six staged gates

Accepted in full by the user, who explicitly asked that **nothing be marked already complete** despite prior Windows-side validation. Each stage must pass before the next begins.

1. **Bench, no mains.** Expanded for the three-contactor build — do these in order:
   - **Ground bond first.** Tie all three ZX-517 `GND` pins to Pi ground at a single star point. Nothing below is valid until this is done (§5.1, open item 15).
   - **Fit all three flyback diodes**, cathode to `OUT+`. Verify orientation on each, individually, before applying 12 V. Reversed = dead short on power-up.
   - Measure R1/R2/R3 on one board to establish whether a gate pulldown exists (open item 18).
   - GPIO toggle verified with a multimeter (black on pin 9, red on pin 11, toggle GPIO17, confirm 0 V / 3.3 V; repeat for pin 13/GPIO27 and pin 15/GPIO22).
   - Confirm 3.3 V actually switches `TRIG/PWM` cleanly on each board, not marginally (open item 17).
   - Coils on 12 V; listen for clean pull-in and drop-out on each. Measure coil resistance and actual pull-in current per coil.
   - **Label everything physically** — three contactors, three drivers, three supplies, all coil leads. Three near-identical assemblies is the new failure mode (§5).
   - Verify K1/K2 close and open together under normal command, and that the mismatch detector fires when one is forced open.
2. **Scope comms on the Pi.** `pyvisa-info`, udev rule, `*IDN?`. Time a 1 Mpts BYTE transfer and a PNG transfer. Decide 1 Mpts vs 100 kpts from the measured numbers.
3. **Vision alone.** Camera fixed and shrouded, auto-exposure and AWB disabled. Classify all four LED states by hand-driving the EVSE. Confirm charging-state detection is reliable. **Record footage here for `camera_sim`.**
4. **One full cycle, human watching.** Inspect the `.npz`, `.png`, `.jpg`, `.json` by hand. Confirm trip time is computable from the raw data. **This is the gate that matters — one fully hand-validated cycle before any volume.**
5. **10 cycles unattended, then 100.** Verify resumability by killing the process mid-run and restarting. **Plot the first-100 trip-time distribution against 24.97 ms** — this gives the expected pass rate before committing four to five days. Two additional checks added this session:
   - **Latch-clear durability.** Confirm that *every* cycle cleared the CCID on the standard 10 s cooldown, and that `latch_slow_clear` count is zero. Known to work on initial testing; unverified at volume. If slow clears appear even occasionally at 100 cycles, resolve before committing to 6000 — the failure mode is that it degrades around cycle 800 and the run halts on day two.
   - **Measure the real cycle time** and replace the estimate in §1 with the measured number. Recompute the campaign duration from it.
   - Verify the dead-man's switch fires: kill the Pi's power and confirm the phone alert arrives.
   - Confirm the strict open order held on every cycle: K3 confirmed open before K1/K2 in all 100.
6. **Full 6000.**

### Pi GPIO pin identification
The header is unlabelled. Do **not** count pins by eye alone:
- Flip the Pi over — **pin 1 is the only square solder pad**, all others are round. Same corner as the microSD end. This is the only method that cannot mislead.
- `sudo apt install python3-gpiozero` then `pinout` prints an ASCII diagram with board orientation. `gpioinfo` shows live line states.
- Verify electrically with a meter before attaching any driver board.

---

## 15. Open items

| # | Item | Resolve at |
|---|---|---|
| 1 | Coil resistance (~26 Ω expected) → confirm plain, non-polarised coil | Stage 1 |
| 2 | Flyback diode already present on driver boards? Inspect | Stage 1 |
| 3 | Gate pulldown resistors already present on driver boards? Measure | Stage 1 |
| 4 | ~~ECK100BH4AAA pole count~~ — **moot.** Three single-pole devices used; L2 now broken by K2 | — |
| 5 | Scope memory depth — run `:ACQuire:POINts?` and `:WAVeform:POINts? MAXimum` | Stage 2 |
| 6 | 1 Mpts transfer time on the Pi → decide 1 Mpts vs 100 kpts | Stage 2 |
| 7 | LED blink rate — confirm faster than ~0.5 Hz | Stage 3 |
| 8 | UL 2231-2 definition of measurement endpoints (t=0, t=end) | **Before the spec** — it shapes the `analysis.py` interface, not just its body. The only open item that blocks the next deliverable |
| 9 | First-100 trip-time distribution vs 24.97 ms | Stage 5 |
| 10 | GitHub multi-account merge workflow refresher (user requested, deferred) | Anytime |
| 11 | **Physically trace and photograph the injection tap point** on the EVSE output conductors; commit photo to repo | Stage 1 |
| 12 | **Latch-clear durability at volume** — does the 10 s cooldown still clear the CCID at cycle 800? Unknown; verified only on initial testing | Stage 5, monitor through Stage 6 |
| 13 | Real measured cycle time → replace the §1 estimate and recompute campaign duration | Stage 5 |
| 14 | ~~Confirm proposed items with the user~~ — **resolved: both accepted.** K3 stuck-closed pre-trigger check (§13) and healthchecks.io dead-man's switch (§11) are locked and in scope for the spec | — |
| 15 | **Ground bonding** — tie all three ZX-517 `GND` signal pins to Pi ground at a single star point. **Blocking: no power-on test valid until done** (§5.1) | Stage 1, before any switching test |
| 16 | **No readback on K1/K2** — welded mains contactor undetectable. Decide: accept, add aux contacts, or voltage-sense EVSE input (§5.2) | Before Stage 6 |
| 17 | Verify 3.3 V GPIO drives ZX-517 `TRIG/PWM` cleanly, not marginally (rated 3.3–20 V) | Stage 1 |
| 18 | Verify gate pulldown present on ZX-517 (R1/R2/R3 unidentified) — measure, do not assume | Stage 1 |
| 19 | Decide `mains_stagger_ms` — 0 unless bench inrush proves noisy. Value feeds the §13 mismatch detector | Stage 2 |

## 16. Explicit non-goals

- **Campaign-level acceptance criteria and statistical analysis.** Explicitly the user's, offline, after the run. The priority now is standing the test up and running it. The automation's job is to produce a dataset that supports *any* later criterion — which is why `trip_time_s` is stored as a raw float (§11) and raw waveforms are retained (§4). A future session should not invent a campaign pass/fail rule and bake it into the software
- Automating the EV simulator spoof
- Hardware K3 timeout circuit (deferred post-MVP)
- ~~2-pole K1 / breaking L2~~ — **no longer a non-goal; implemented via K2** (§6)
- Finalising the trip-time algorithm (deliberately deferred to `replay_waveform.py`)
- Measuring leakage current magnitude (fixed at 30 mA, verified with a Fluke DMM; DMMs read RMS)
- Channel 2 / EVSE output voltage capture — recommended for independent electrical trip confirmation, **not implemented yet**. The JSON schema is built with a `channels: {}` map keyed by channel so Ch2 can be added later with no format migration and full comparability to existing data
- The assistant writing implementation code

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **CCID** | Charge Circuit Interrupting Device — the ground-fault protection in the EVSE. The device under test |
| **EVSE** | Electric Vehicle Supply Equipment — the charger |
| **CP** | Control Pilot — the EVSE↔vehicle signalling line |
| **Spoof / EV simulator** | Device presenting a fake vehicle so the EVSE energises its output. Static, always on |
| **K1 / K2 / K3** | K1 = EVSE mains contactor, L1. K2 = EVSE mains contactor, L2. K3 = leakage injection contactor. **In revisions before 2026-08-03, `K2` meant the leakage contactor** — that role is now K3 |
| **ZX-517** | Dual-MOSFET driver module, one per contactor. Non-isolated, low-side switching, no onboard flyback (§5.1) |
| **SafeOff** | The invariant state: both contactors open, coils de-energised. Reached on every failure path |
| **Trip** | CCID clearing the fault — the event whose timing is measured |
| **No-trip** | CCID failed to clear within 100 ms. The only condition that halts the run |

---

## 18. Suggested skills

| Skill | When to invoke |
|---|---|
| **`caveman`** (`/mnt/skills/user/caveman/SKILL.md`) | **Invoke at session start.** The user has this active as a standing preference — terse, compressed responses, no filler. Level: `full`. Drop the compression for safety warnings, ordered procedures, and anything the user says they do not understand; resume immediately after. |
| **`grilling`** (`/mnt/skills/user/grilling/SKILL.md`) | If the user wants to stress-test a further plan or decision. This entire session used it: one question at a time, each with a recommended answer, waiting for a response before continuing. The fuller of the two grill skills — prefer it over `grill-me`. |
| **`pdf-reading`** (`/mnt/skills/public/pdf-reading/SKILL.md`) | If the user uploads UL 2231-2, the ECK100BH4AAA datasheet, the MSO-X 2014A programmer's guide, or MOSFET board documentation. Several open items are resolved by reading exactly these documents |
| **`file-reading`** (`/mnt/skills/public/file-reading/SKILL.md`) | If files appear at `/mnt/user-data/uploads/` whose content is not already visible in context — photographs of the rig, captured waveforms, `cycles.csv` |
| **`xlsx`** (`/mnt/skills/public/xlsx/SKILL.md`) | When analysing `cycles.csv` results — trip-time distribution, pass rate, drift over 6000 cycles |
| **`docx`** (`/mnt/skills/public/docx/SKILL.md`) | If a formal qualification report is required at the end of the campaign |

Not relevant: `pptx`, `frontend-design`, `product-self-knowledge`, `morning`, `skill-creator`.

---

## 19. Where to pick up

The specification document is the next deliverable and has not been started. Scope agreed with the user:

1. Interface contracts per module (`hal/base.py` abstract signatures first)
2. State table for `sequencer.py` — states, transitions, guards, timeouts. Must now include the three-way vision-timeout branch (§8) and the extended-cooldown retry path
3. `config.yaml` schema with every value fixed in this document — including the values added this session: `cooldown_s: 10`, `cooldown_retry_s: 60`, `boot_timeout_s: 90`, `healthcheck_url`, `heartbeat_grace_s: 300`, `mains_stagger_ms` (0 unless bench testing shows inrush noise), `gpio_k1: 17`, `gpio_k2: 27`, `gpio_k3: 22`
4. Fault matrix formalised for `test_faultmatrix.py` — now 17 rows, including the three vision-timeout branches, the K3 stuck-closed check, and the leg-mismatch row
5. `cycles.csv` column schema (§11) — pin it in the spec, it is load-bearing for all later analysis
6. Commissioning checklist as a runnable procedure

Written for the user to hand to GitHub Copilot — Copilot fills bodies well when given exact signatures and invariants, so precision in the contracts matters more than prose.

**Confirm the open items in §15 have not been resolved since this handoff was written before assuming they are still open.**

**Every decision in this document has been made or accepted by the user.** Nothing is left in a recommended-but-unconfirmed state. The two items raised during the review pass — the K3 stuck-closed pre-trigger check and the healthchecks.io dead-man's switch — were both accepted and are locked; treat them as requirements of the spec, not options.
