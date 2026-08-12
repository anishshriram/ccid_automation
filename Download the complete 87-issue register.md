# CCID Automation: Complete 87-Issue Register

This document records all 87 issues encountered during Raspberry Pi setup, hardware commissioning, camera-gate development, waveform-analysis repair, oscilloscope debugging, and campaign preparation.

Resolved issues describe the implemented fix. Unresolved issues explicitly retain their current disposition or recommended next action.

---

## 1. `lgpio` unavailable inside the virtual environment

**Issue:** The Pi had `python3-lgpio` installed system-wide, but the project virtual environment could not import the module.

**Impact:** GPIO Zero fell back to an unsuitable experimental backend, blocking reliable GPIO commissioning.

**Resolution:** Linked `lgpio.py` and the compiled `_lgpio` library into the virtual environment and verified that GPIO Zero selected `LGPIOFactory`.

**One-line solution:** Linked the system `lgpio` package into the venv and confirmed `LGPIOFactory`.

---

## 2. GPIO, scope, and camera permissions required session refresh

**Issue:** The `ccid` account needed membership in the `gpio`, `plugdev`, and `video` groups, and new group memberships did not apply to the current login session.

**Impact:** GPIO, USB scope, or webcam access could fail even though the account configuration looked correct.

**Resolution:** Confirmed the required groups and restarted the login session so the memberships became active.

**One-line solution:** Added the user to the required device groups and restarted the session.

---

## 3. Scope was invisible to PyVISA because of USB permissions

**Issue:** Linux detected the oscilloscope, but PyVISA returned no usable resources because the normal user lacked adequate USB access.

**Impact:** The automation could not identify, configure, arm, or read the oscilloscope.

**Resolution:** Installed the Keysight USBTMC udev rule, reloaded udev, reconnected the scope, and verified the VISA resource.

**One-line solution:** Installed the udev rule and verified the Keysight VISA resource as the normal user.

---

## 4. Git pushes failed because HTTPS password authentication was unavailable

**Issue:** The repository remote used HTTPS, but GitHub no longer accepted account passwords for Git pushes.

**Impact:** Completed fixes could not be published from the Raspberry Pi.

**Resolution:** Generated an ED25519 SSH key, added the public key to GitHub, and changed the repository remote to SSH.

**One-line solution:** Switched the Git remote from HTTPS to authenticated SSH.

---

## 5. `ccid-pi.local` was not consistently resolvable

**Issue:** Multicast DNS resolution was unreliable while the Pi and development machine were connected through a phone hotspot.

**Impact:** SSH access intermittently failed even though the Pi was online.

**Resolution:** Used the Pi's direct hotspot IPv4 address whenever the `.local` hostname failed.

**One-line solution:** Used the Pi's direct IPv4 address when mDNS was unreliable.

---

## 6. Camera frames were initially black

**Issue:** The Logitech C270 returned black frames while automatic exposure and gain were still settling after the stream opened.

**Impact:** Calibration and runtime classification could incorrectly treat valid LED states as off or unknown.

**Resolution:** Changed startup behavior to read and discard frames continuously during warm-up instead of sleeping without reads.

**One-line solution:** Continuously read frames during camera warm-up instead of merely sleeping.

---

## 7. Camera exposure reset after USB or power reconnection

**Issue:** The C270 reverted to automatic exposure and an exposure time near 156 after reconnects.

**Impact:** LED colors clipped or shifted, making HSV classification unreliable.

**Resolution:** Standardized startup on manual exposure mode with `exposure_time_absolute=30` and verified the readback before tests.

**One-line solution:** Reapply manual exposure 30 after every camera reconnect.

---

## 8. The Pi's `/tmp` filesystem caused widespread disk-space test failures

**Issue:** The Pi root filesystem had roughly 51 GB free, but `/tmp` was a 1 GB tmpfs while the new cycle guard required 2 GB free.

**Impact:** Most sequencer-based tests halted before their intended scenarios with `persistence:insufficient_disk_space`.

**Resolution:** Created `~/ccid_test_tmp` and ran tests with `TMPDIR` pointed to the main filesystem.

**One-line solution:** Run tests with `TMPDIR=$HOME/ccid_test_tmp` instead of the 1 GB `/tmp`.

---

## 9. The scope resource environment variable disappeared in new SSH sessions

**Issue:** A freshly opened shell did not inherit `CCID_SCOPE_RESOURCE`.

**Impact:** Real scope tools refused to run because no VISA resource was specified.

**Resolution:** Re-exported the exact USB VISA resource in each new shell or session.

**One-line solution:** Export `CCID_SCOPE_RESOURCE` after reconnecting by SSH.

---

## 10. An SSH disconnect could have left a real cycle running

**Issue:** The SSH client reset while a real cycle command had been launched.

**Impact:** The operator could not assume the Python process or hardware sequence had stopped.

**Resolution:** Physically de-energized first, reconnected, and used `pgrep` to verify that no `ccid.main` process remained.

**One-line solution:** De-energize physically, then verify no CCID process remains with `pgrep`.

---

## 11. Physical GPIO numbering was easy to confuse

**Issue:** The software uses BCM GPIO numbers while wiring work uses physical Raspberry Pi header pins.

**Impact:** A mapping mistake could command the wrong contactor or leave a contactor uncontrolled.

**Resolution:** Locked and repeatedly verified K1=GPIO17/pin11, K2=GPIO27/pin13, K3=GPIO22/pin15, and signal ground=pin9.

**One-line solution:** Documented and verified both BCM and physical pin mappings.

---

## 12. Contactors initially did not click during a live attempt

**Issue:** The sequencer reached mains-closing states without the expected audible contactor actuation.

**Impact:** The EVSE was not being energized as intended, making the cycle invalid.

**Resolution:** Stopped, de-energized, located the wiring or coil-power-path problem, and corrected it before rerunning.

**One-line solution:** Corrected the low-voltage contactor wiring or power path before another run.

---

## 13. K2 driver LED was not visible

**Issue:** K1 and K2 clicks occurred close together, but K2's driver indicator did not visibly confirm actuation.

**Impact:** K2 physical closure could not be distinguished from K1 by sound alone, while software tracked commanded state only.

**Resolution:** Stopped and inspected K2 trigger, signal ground, 12 V input, output wiring, coil path, and indicator behavior.

**One-line solution:** De-energized and inspected the complete K2 low-voltage path.

---

## 14. Continuity testing illuminated the driver LEDs

**Issue:** The multimeter's continuity current caused driver-board indicator LEDs to glow.

**Impact:** The behavior looked like unintended board activation and could have been misdiagnosed as a wiring fault.

**Resolution:** Reproduced the effect across all three boards and identified the meter's test current as the cause.

**One-line solution:** Confirmed continuity-mode test current was lighting the indicators.

---

## 15. Installed flyback diodes appeared to conduct in both directions

**Issue:** Diode measurements across installed coils showed about 0.026 V in both directions because the coil provided a parallel path.

**Impact:** The reading suggested failed or reversed diodes even though the measurement method was invalid in circuit.

**Resolution:** Lifted one diode leg, verified about 0.500 V forward and open reverse, then reinstalled the diode correctly.

**One-line solution:** Test each flyback diode with one leg disconnected from the coil.

---

## 16. Driver boards did not provide adequate coil flyback suppression

**Issue:** The ZX-517-style MOSFET boards did not include the required external suppression for the contactor coils.

**Impact:** Inductive voltage spikes could damage drivers, create EMI, or destabilize the Pi.

**Resolution:** Installed external 1N5404 diodes on all three coils with the cathode toward `OUT+`.

**One-line solution:** Installed correctly oriented external flyback diodes across every coil.

---

## 17. K3 mechanical operation needed independent confirmation

**Issue:** Repeated no-trigger cycles raised concern that K3 might not be physically closing.

**Impact:** A failed K3 would make scope and leakage-path debugging misleading.

**Resolution:** Tested K3 independently at low voltage with mains de-energized and confirmed mechanical actuation.

**One-line solution:** Verified K3 independently under low-voltage, de-energized conditions.

---

## 18. Software cannot verify physical contactor movement

**Issue:** The GPIO HAL records only commanded states and has no auxiliary-contact or load-voltage feedback.

**Impact:** A welded, stuck, or non-actuating K1/K2/K3 contactor can be invisible to software.

**Resolution:** Documented the limitation as an unresolved hardware gap and deferred the auxiliary-contact decision.

**One-line solution:** Add isolated physical-state feedback or explicitly accept the gap before later stages.

---

## 19. Scope waveform-point mode read back `MAX` instead of `RAW`

**Issue:** The MSO-X 2014A required a specific stopped-state and command order before accepting RAW waveform-point mode.

**Impact:** The scope could return the wrong record format or memory behavior.

**Resolution:** Applied `:STOP`, set points to `MAXimum`, then set points mode to `RAW`, and verified the readback.

**One-line solution:** Stop the scope, set maximum points, then set RAW points mode.

---

## 20. Killing PyVISA left the scope USBTMC connection stalled

**Issue:** After forcibly terminating a blocked PyVISA process, later scope commands continued timing out.

**Impact:** USB reconnection alone did not restore reliable instrument communication.

**Resolution:** Fully power-cycled the oscilloscope to reset its internal USBTMC state.

**One-line solution:** Power-cycle the scope after a killed process leaves USBTMC stalled.

---

## 21. Scope reported `waveform_points: +0` before acquisition

**Issue:** Configuration readback after a fresh scope setup returned zero waveform points.

**Impact:** The value suggested waveform memory was misconfigured.

**Resolution:** Determined that no completed acquisition record existed yet and verified the real point count after capture.

**One-line solution:** Treat `+0` as no current acquisition record and verify point count after capture.

---

## 22. Autoscale overwrote the project's scope configuration

**Issue:** Pressing Autoscale changed channel scale, timebase, trigger, and other acquisition settings.

**Impact:** Manual debugging could silently invalidate the automated configuration assumptions.

**Resolution:** Reapplied the complete project scope configuration with `tools.scope_bench configure --real` after Autoscale.

**One-line solution:** Always restore the software-controlled scope configuration after Autoscale.

---

## 23. Manual front-panel settings were overwritten by the sequencer

**Issue:** The sequencer reapplied all `ScopeSettings` during `SCOPE_CONFIGURING`.

**Impact:** Manual changes made before a run did not necessarily affect the actual acquisition.

**Resolution:** Treated `ScopeSettings` as authoritative and moved persistent changes into tested software defaults.

**One-line solution:** Change and test `ScopeSettings` instead of relying on front-panel state.

---

## 24. The scope driver did not verify STOP completed

**Issue:** `configure_for_cycle()` sent `:STOP` and immediately continued without observing the run bit clear.

**Impact:** A prior acquisition state could overlap with new configuration or Single arming.

**Resolution:** Added `_wait_until_stopped()` and a real-driver regression before applying cycle settings.

**One-line solution:** Wait for the operation-register run bit to clear after STOP.

---

## 25. The sequencer checked armed state only once

**Issue:** A Single acquisition could potentially be consumed after the first check but before K3 closure.

**Impact:** K3 could inject while the oscilloscope was no longer waiting for the intended event.

**Resolution:** Added a 50 ms settling delay and a second armed-state check immediately before K3 injection.

**One-line solution:** Verify the scope is still armed immediately before allowing K3 to close.

---

## 26. The `SCOPE_ARMED` transition log could be misleading

**Issue:** The transition was logged before `_poll_scope_armed()` completed in the earlier sequence.

**Impact:** Logs could imply verified arming earlier than the verification actually occurred.

**Resolution:** The misleading behavior was identified, but further log changes were paused until the trigger mechanism was better understood.

**One-line solution:** Log `SCOPE_ARMED` only after verified arming when the sequence is next revised.

---

## 27. `:TRIGger:STATus?` timed out on the real scope

**Issue:** The attempted trigger-status query did not return through the current firmware and PyVISA path.

**Impact:** The proposed direct status check could not be used for automation diagnostics.

**Resolution:** Returned to the supported `:OPERegister:CONDition?` operation-register query.

**One-line solution:** Use the supported operation-condition register instead of `:TRIGger:STATus?`.

---

## 28. Operation-register state interpretation needed real-device proof

**Issue:** There was uncertainty whether bit 3 represented a useful stopped-versus-running distinction.

**Impact:** Incorrect interpretation could cause false armed or complete decisions.

**Resolution:** Queried the actual scope and observed condition 32 after STOP and 40 after SINGLE, confirming bit 3 changed as expected.

**One-line solution:** Validated the operation-register run bit on the real instrument.

---

## 29. The scope once displayed `Trig'd` while the rig was de-energized

**Issue:** A de-energized arm check appeared to complete without the intended leakage event.

**Impact:** An intermittent transient might consume Single before K3 injection.

**Resolution:** Followed with a 10-second de-energized monitor and a capture-bench test; the behavior did not repeat consistently.

**One-line solution:** Treat the false trigger as intermittent and preserve state evidence on future occurrences.

---

## 30. A de-energized Single acquisition needed stability verification

**Issue:** The project needed to know whether software-issued Single could remain armed without hardware activity.

**Impact:** An unstable armed state would invalidate the injection sequence.

**Resolution:** Monitored the operation register for 10 seconds and observed the scope remain armed throughout.

**One-line solution:** Confirmed a fresh Single acquisition can remain armed for 10 seconds de-energized.

---

## 31. Scope coupling was initially set to DC

**Issue:** The software forced DC coupling every cycle, while a qualified engineer specified AC coupling for this measurement.

**Impact:** Manual AC selection was overwritten and baseline behavior could differ from the intended setup.

**Resolution:** Changed the central default to AC, added a regression, passed all tests, and confirmed AC readback on the real scope.

**One-line solution:** Use tested AC coupling in the software-controlled scope configuration.

---

## 32. AC coupling did not eliminate the no-trigger behavior

**Issue:** A fully real AC-coupled cycle still remained in Single and timed out.

**Impact:** Coupling was not the only cause of the intermittent acquisition failure.

**Resolution:** Retained the engineer-approved AC setting but stopped treating coupling as the complete solution.

**One-line solution:** Keep AC coupling, but continue diagnosing the automated trigger path.

---

## 33. K3 could remain closed for the five-second scope timeout

**Issue:** The real scope acquisition wait blocked the sequencer long enough to defeat the intended 300 ms backstop.

**Impact:** Leakage injection remained active far longer than the safety design allowed.

**Resolution:** Changed acquisition waiting to short polls so the outer sequencer could independently enforce the K3 deadline.

**One-line solution:** Poll acquisition in short intervals so K3 always opens at the hard backstop.

---

## 34. Fifty-millisecond acquisition polls overshot the K3 deadline

**Issue:** The first polling implementation combined 50 ms waits and extra sleeps, opening K3 around 360 ms.

**Impact:** The hard backstop was still exceeded by an unacceptable margin.

**Resolution:** Reduced the scope poll timeout to 10 ms and verified the duration with a blocking-scope regression.

**One-line solution:** Use 10 ms acquisition polls to meet the 300 ms K3 backstop.

---

## 35. The GPIO self-test was unsafe for live K3 leakage injection

**Issue:** The K3 exercise command could hold K3 for human-scale seconds.

**Impact:** Using the tool on an energized leakage path could bypass the production backstop.

**Resolution:** Restricted live K3 injection to the full sequencer and used the self-test only for guarded commissioning.

**One-line solution:** Never use the GPIO exercise tool as a live K3 leakage test.

---

## 36. The EVSE faulted but the scope repeatedly did not trigger

**Issue:** The EVSE entered flashing red after K3 operation while the scope remained armed until timeout.

**Impact:** The leakage reached the DUT, but the automated measurement path did not reliably capture it.

**Resolution:** Narrowed the unresolved fault to the scope trigger, sensing, or acquisition sequence rather than the K3-to-EVSE path.

**One-line solution:** Treat the remaining blocker as an intermittent automated measurement-path failure.

---

## 37. Scope timeout runs discarded diagnostic evidence

**Issue:** Timed-out runs saved no scope screenshot, operation register, error queue, or partial displayed waveform.

**Impact:** The team could not tell whether CH1 was flat, below threshold, inverted, off-screen, or incorrectly triggered.

**Resolution:** Designed the next recommended task as a dedicated timeout-diagnostics capture path.

**One-line solution:** Save a timeout screenshot, scope state, settings, errors, and K3 timestamps.

---

## 38. Repeated identical energized attempts produced no new evidence

**Issue:** Multiple cycles repeated the same no-trigger result.

**Impact:** The work consumed time and hardware cycles without narrowing the cause.

**Resolution:** Stopped repetitive testing and required better diagnostics before another attempt.

**One-line solution:** Do not repeat an energized failure without improving the evidence captured.

---

## 39. The initial camera ROI cut off part of the diffuser

**Issue:** The default centered ROI did not fully cover the EVSE status-light diffuser.

**Impact:** Color fractions and temporal classifications could be wrong even with good exposure.

**Resolution:** Calibrated and locked ROI `x=35, y=120, width=450, height=350`.

**One-line solution:** Use the calibrated diffuser ROI in all runtime classification.

---

## 40. Runtime code initially ignored the calibrated ROI

**Issue:** Calibration tools used the correct ROI, but production classification did not receive it.

**Impact:** Calibration success did not guarantee runtime behavior.

**Resolution:** Added strict vision configuration, config hashing, and sequencer wiring for the ROI.

**One-line solution:** Pass the hash-frozen calibrated ROI into the production charging gate.

---

## 41. Camera simulator frames did not exercise the optical classifier

**Issue:** The simulator's tiny dark frames did not match the logical LED states it reported.

**Impact:** Integration tests could pass logical states without testing the real HSV path.

**Resolution:** Replaced the fixtures with classifier-compatible 16×16 BGR images and added an integration regression.

**One-line solution:** Use optical fixtures that the production classifier can actually recognize.

---

## 42. Green calibration footage contained boot colors

**Issue:** A nominal green recording included red and blue frames from the EVSE boot sequence.

**Impact:** Temporal verification classified the recording as booting rather than charging.

**Resolution:** Filtered the footage to green-dominant frames with low red and blue fractions.

**One-line solution:** Build the green calibration set from green-dominant frames only.

---

## 43. Real flashing green was classified as `BOOTING`

**Issue:** The long temporal classifier could be tipped by a few secondary-hue frames.

**Impact:** A real charging state timed out after 90 seconds even though flashing green was visible.

**Resolution:** Separated the charging decision from the diagnostic temporal classifier and used recent green evidence.

**One-line solution:** Use a dedicated charging-gate policy instead of the boot-state window verdict.

---

## 44. The first redesigned gate granted during the boot sequence

**Issue:** Three green frames within two seconds were enough to accept the green section of the multicolor boot animation.

**Impact:** The sequencer could proceed toward K3 before the EVSE was truly charging.

**Resolution:** Added a hardware-derived regression and required green evidence spanning at least 3.5 seconds within six seconds.

**One-line solution:** Require sustained green over 3.5 seconds, not merely three rapid green frames.

---

## 45. Blue did not initially clear accumulated green evidence

**Issue:** The first recent-green policy cleared evidence only on red.

**Impact:** Blue followed by short boot green could contribute to a false charging grant.

**Resolution:** Changed both red and blue observations to clear accumulated green evidence.

**One-line solution:** Reset charging qualification on either red or blue.

---

## 46. Dark frames between green flashes needed special handling

**Issue:** Clearing all evidence on dark frames would reject a genuine flashing-green indication.

**Impact:** The system would return to false-negative timeout behavior.

**Resolution:** Allowed dark or unknown frames between green observations while granting only on a current green frame.

**One-line solution:** Let dark frames preserve evidence but never grant the gate themselves.

---

## 47. The charging gate took roughly 45 to 50 seconds

**Issue:** The corrected gate appeared slow compared with the earlier false-positive grant.

**Impact:** The delay could have been mistaken for another classifier failure.

**Resolution:** Validated that the delay matched the full EVSE boot sequence plus sustained-green qualification.

**One-line solution:** Accept the 45-50 second gate delay as validated real startup behavior.

---

## 48. Camera-only validation runs produced synthetic PASS results

**Issue:** The temporary gate configuration used a simulated scope with real GPIO and camera.

**Impact:** A reported trip result could be mistaken for real electrical evidence.

**Resolution:** Explicitly labeled all results from `camera_gate_check.yaml` as synthetic and used them only for vision validation.

**One-line solution:** Never treat a simulated-scope result as a real trip measurement.

---

## 49. The original analysis falsely flagged pre-trigger leakage

**Issue:** A forward-looking envelope included future burst energy in the first sample's classification.

**Impact:** A valid captured waveform was rejected and onset was placed at the record boundary.

**Resolution:** Changed V2 to use raw-sample pre-trigger checks and corrected onset refinement.

**One-line solution:** Use raw pre-trigger samples and non-contaminating onset refinement in V2.

---

## 50. Onset refinement incorrectly selected the first record sample

**Issue:** When no fully silent future-looking envelope window existed, the algorithm forced onset to index zero.

**Impact:** Trip time was overestimated and quiet baseline could be misclassified.

**Resolution:** V2 preserves the sustained threshold-crossing index instead of forcing sample zero.

**One-line solution:** Preserve the detected sustained onset when silence cannot be proven by the envelope.

---

## 51. The first analysis fix weakened genuine stuck-K3 detection

**Issue:** Removing the contaminated envelope check caused a true pre-trigger-leakage test to pass.

**Impact:** A real hazardous pre-existing current condition could have gone undetected.

**Resolution:** Added a boundary-specific raw-sample check over the first quarter cycle.

**One-line solution:** Inspect the initial raw quarter-cycle when onset is at the record boundary.

---

## 52. Corrected analysis was initially still labeled V1

**Issue:** The corrected algorithm changed a real waveform from 20.1676 ms to 13.5334 ms but retained the V1 label.

**Impact:** Historical results and new results would be semantically indistinguishable.

**Resolution:** Added `AnalysisVersion.V2`, preserved V1 behavior, and migrated new results to V2.

**One-line solution:** Version material analysis changes instead of modifying V1 in place.

---

## 53. V1 replay used V2 endpoint metadata

**Issue:** The replay CLI overrode the algorithm tag but retained V2 endpoint text from the active configuration.

**Impact:** The result metadata was not audit-safe.

**Resolution:** Made the replay override select both the requested implementation and its matching endpoint definition.

**One-line solution:** Bind replay algorithm versions to their matching frozen endpoint definitions.

---

## 54. Historical V1 behavior needed to remain replayable

**Issue:** Replacing V1 would erase the ability to reproduce older campaign calculations.

**Impact:** Audit and comparison of historical results would be compromised.

**Resolution:** Kept V1 endpoint text, V1 branches, and explicit V1 replay tests.

**One-line solution:** Preserve old algorithms and endpoint text alongside new versions.

---

## 55. V2 migration changed the canonical configuration hash

**Issue:** Changing the algorithm version and endpoint definition produced a different hash.

**Impact:** Old and new runs could not safely share a campaign identity.

**Resolution:** Accepted the change intentionally and froze V2 settings under a new hash.

**One-line solution:** Use a new canonical hash whenever analysis semantics change.

---

## 56. One real waveform had a clean quiet baseline

**Issue:** `full_real_probe_recheck_20260806T173658Z` showed quiet pre-trigger data, a burst, and collapse.

**Impact:** The capture demonstrated that the complete measurement chain can produce valid data.

**Resolution:** V2 measured 8.0218 ms PASS with all six sanity checks true.

**One-line solution:** Use the 8.0218 ms run as the reference shape, while noting manual scope interaction.

---

## 57. One automatic waveform was active from the first sample

**Issue:** `full_real_stop_verified_20260806T180219Z` contained large voltage throughout the pre-trigger record.

**Impact:** The actual onset occurred outside the record, so the reported 20.1632 ms value was not trustworthy.

**Resolution:** V2 halted the run through `no_pretrigger_leakage=false` despite the numerical PASS.

**One-line solution:** Reject any result that lacks a proven quiet pre-event baseline.

---

## 58. The `no_pretrigger_leakage` name implied more certainty than the evidence

**Issue:** The flag could mean genuinely stuck K3 or simply that the record began after conduction started.

**Impact:** The result could be overinterpreted as a confirmed hardware fault.

**Resolution:** Used the conservative interpretation 'quiet pre-event baseline not proven.'

**One-line solution:** Treat the flag as onset/baseline uncertainty unless hardware evidence proves stuck K3.

---

## 59. Twenty milliseconds of pre-trigger history was sometimes insufficient

**Issue:** A captured event was already underway at the first stored sample.

**Impact:** The analyzer could not establish the event start or reliable trip duration.

**Resolution:** Changed the scope default from 20 ms/div LEFT to 50 ms/div CENTER.

**One-line solution:** Increase pre-trigger depth with a centered, longer acquisition window.

---

## 60. Larger centered capture geometry did not solve no-trigger runs

**Issue:** The scope still sometimes remained in Single and timed out at 50 ms/div CENTER.

**Impact:** Capture depth and trigger reliability were separate problems.

**Resolution:** Kept the improved record geometry but continued treating trigger intermittency as unresolved.

**One-line solution:** Retain better record bounds without claiming they solve the no-trigger fault.

---

## 61. Example configuration test hard-coded simulated GPIO mode

**Issue:** The Pi's commissioned `config.yaml` used real modes while the test expected `sim`.

**Impact:** The full suite failed even though the real configuration was intentional.

**Resolution:** Updated the test to accept either valid mode instead of hard-coding simulation.

**One-line solution:** Validate mode membership rather than requiring `sim` in the deployed config.

---

## 62. New `charging_green_min_span_s` broke strict YAML fixtures

**Issue:** Inline test configurations omitted the newly required key.

**Impact:** Config parsing failed before the intended test scenarios could run.

**Resolution:** Added `charging_green_min_span_s: 3.5` to all valid and deliberately invalid fixtures.

**One-line solution:** Update every strict YAML fixture when adding a required config key.

---

## 63. Policy tests assumed three rapid green frames should grant

**Issue:** Older tests encoded the permissive behavior that caused the boot-sequence false positive.

**Impact:** Correct production behavior appeared to break the suite.

**Resolution:** Rewrote tests around the 3.4/3.5-second boundary and recurring green/dark sequences.

**One-line solution:** Test sustained duration, not just frame count.

---

## 64. Retry-success camera fixture left residual red frames

**Issue:** The first timeout consumed fewer red frames than expected, leaving red at the beginning of the retry window.

**Impact:** The second gate lacked enough clean green time to qualify and the test halted.

**Resolution:** Reduced the initial faulted sequence from 70 frames to 60.

**One-line solution:** Align retry fixtures with the actual frame consumption and qualification duration.

---

## 65. Scope-bench missing-resource test saw the exported real resource

**Issue:** The shell's `CCID_SCOPE_RESOURCE` made a test expecting no resource succeed instead of raise.

**Impact:** A correct test failed because of ambient environment state.

**Resolution:** Ran the suite with `env -u CCID_SCOPE_RESOURCE`.

**One-line solution:** Hide the real scope environment variable during software-only tests.

---

## 66. Long copied commands were mangled by formatting

**Issue:** Line numbers, HTML entities, or broken line continuations entered shell and Python commands.

**Impact:** Commands failed with pathspec errors, permission errors, syntax errors, and malformed identifiers.

**Resolution:** Switched to short single-line commands or small edit blocks with immediate verification.

**One-line solution:** Use short commands and syntax-check after every manual edit.

---

## 67. A scope test double contained `super(*.__init__`

**Issue:** A copying artifact changed `super().__init__` into an invalid expression.

**Impact:** The test file would not compile.

**Resolution:** Corrected the constructor call and reran `py_compile`.

**One-line solution:** Fix the copy artifact and compile the test before execution.

---

## 68. A scope test double contained `now_m*notonic_s`

**Issue:** A copying artifact inserted an asterisk into the parameter name.

**Impact:** The test halted with `controller:unexpected:NameError` instead of testing the intended behavior.

**Resolution:** Corrected the identifier to `now_monotonic_s`.

**One-line solution:** Correct malformed identifiers before interpreting test failures.

---

## 69. A temporary diagnostic print remained in the regression

**Issue:** A print block was inserted to expose terminal state, halt reason, armed-check count, and operations.

**Impact:** The test output remained noisy after the underlying typo was identified.

**Resolution:** Removed the temporary block once the diagnostic purpose was complete.

**One-line solution:** Remove temporary test logging after the failure mechanism is known.

---

## 70. Fake scope run-bit sequences became stale after STOP verification

**Issue:** The new STOP poll consumed values that older tests expected to use for arming and completion.

**Impact:** The scope-real test failed even though production behavior was correct.

**Resolution:** Expanded the fake sequence to represent STOP settling and a separate Single acquisition.

**One-line solution:** Update stateful fakes whenever production adds a new state transition.

---

## 71. Real `config.yaml` needed safe handling during simulation tests

**Issue:** The deployed file contained real HAL modes, while tests should not touch hardware.

**Impact:** A careless test run could fail configuration assumptions or risk real-device access.

**Resolution:** Backed up or path-stashed the file, temporarily selected simulation, ran tests, and restored the exact file.

**One-line solution:** Temporarily isolate real-mode config and restore it exactly after testing.

---

## 72. Only `config.yaml` needed stashing

**Issue:** A broad stash would also hide uncommitted production and regression changes under validation.

**Impact:** The intended code might not actually be tested.

**Resolution:** Used `git stash ... -- config.yaml` to limit the stash path.

**One-line solution:** Stash only the local deployment config, not the code under test.

---

## 73. Temporary `camera_gate_check.yaml` risked accidental commit

**Issue:** The file intentionally mixed real GPIO/camera with simulated scope for one diagnostic purpose.

**Impact:** Committing it could confuse deployment or future campaigns.

**Resolution:** Kept it untracked and explicitly excluded it from every `git add` command.

**One-line solution:** Leave `camera_gate_check.yaml` untracked and out of commits.

---

## 74. A multiline `git add` copied visible line numbers

**Issue:** The shell interpreted `2`, `3`, and subsequent file paths as separate commands.

**Impact:** Staging failed and produced misleading permission errors.

**Resolution:** Reissued `git add` as a single command line.

**One-line solution:** Use a one-line staging command when copy formatting is unreliable.

---

## 75. `status --latest` selected an old run

**Issue:** The newest-by-tool run did not match the run the operator intended to inspect.

**Impact:** Troubleshooting focused on stale run data.

**Resolution:** Queried run state by exact run ID and listed run directories by modification time.

**One-line solution:** Always inspect commissioning runs by explicit unique run ID.

---

## 76. Reusing `$RUN_ID` could collide with an existing run directory

**Issue:** An aborted command could still have initialized a run even without a completed cycle.

**Impact:** A later start could reuse or confuse historical state.

**Resolution:** Generated a fresh UTC timestamped ID for every attempt.

**One-line solution:** Never reuse or resume a commissioning run ID unless explicitly intended.

---

## 77. Automated scope triggering remains intermittent

**Issue:** The scope and probe work in isolation, but many full-chain K3 events remain untriggered.

**Impact:** The system cannot yet run a reliable unattended campaign.

**Resolution:** No definitive root cause has been established; further energized repetition was stopped.

**One-line solution:** Implement evidence-preserving timeout diagnostics before another energized attempt.

---

## 78. Timeout runs do not preserve scope state

**Issue:** No-trigger runs lack a screenshot, instrument condition, settings snapshot, or partial display data.

**Impact:** The primary remaining fault cannot be differentiated among signal, trigger, and state causes.

**Resolution:** Specified a dedicated diagnostic artifact bundle for timeout paths.

**One-line solution:** Add `capture_timeout_diagnostics()` without fabricating a successful acquisition.

---

## 79. Manual Single interaction correlated with one clean capture

**Issue:** The clean 8.0218 ms run included a manual Single-button press during the broader sequence.

**Impact:** The run could not fully prove unattended automation even though software later issued Single too.

**Resolution:** Preserved the run as valid waveform evidence but not as final unattended qualification.

**One-line solution:** Use the clean run as a reference, not as proof of autonomous reliability.

---

## 80. Front-panel colors were not synchronized with software timestamps

**Issue:** Run/Stop red and Single yellow were observed during intervals as short as 50-80 ms.

**Impact:** Human observation could not reliably establish the exact instrument state at K3 closure.

**Resolution:** Recommended timestamped state and screenshot diagnostics instead of relying on button-color recollection.

**One-line solution:** Capture synchronized scope state at software checkpoints.

---

## 81. A different approximately 203 V supply raised topology concerns

**Issue:** The rig moved from a nominal 208 V source to one measuring about 203 V.

**Impact:** Source topology, grounding, or phase relationships could affect the measurement even if the fixture wiring was unchanged.

**Resolution:** Determined that the 2.4% voltage difference alone was insufficient to explain loss of a roughly 199 V historical signal.

**One-line solution:** Treat source topology as a variable, not the small voltage difference itself.

---

## 82. The same reported setup produced valid and invalidly bounded waveforms

**Issue:** One run had a quiet baseline while another had large signal from the first sample.

**Impact:** The measurement chain or acquisition timing is not yet repeatable.

**Resolution:** Stopped claiming a single cause and prioritized richer timeout/state evidence.

**One-line solution:** Preserve diagnostics before changing more settings or repeating tests.

---

## 83. K1/K2 physical-state feedback remains absent

**Issue:** The software cannot prove whether mains contactors physically opened or closed.

**Impact:** A welded or failed contactor remains an undetectable hardware risk.

**Resolution:** Recorded the issue in `IMPLEMENTATION_QUESTIONS.md` as a required future decision.

**One-line solution:** Add isolated auxiliary-contact feedback or formally accept the limitation.

---

## 84. Protective-earth continuity is not directly monitored

**Issue:** The Pi has no isolated input that reports output PE continuity.

**Impact:** A disconnected PE may only be inferred indirectly after another test fails.

**Resolution:** Defined the need for an approved isolated PE monitor and a sticky halt reason.

**One-line solution:** Add dedicated isolated PE-continuity sensing for specific detection.

---

## 85. UL 2231-2 endpoint definition remains provisional

**Issue:** The V2 endpoint definition is project-defined and hash-frozen but has not been confirmed against the standard text.

**Impact:** Formal compliance conclusions could use the wrong measurement endpoints.

**Resolution:** Preserved the definition explicitly and tracked standards review as an open item.

**One-line solution:** Review the standard and create a new analysis version if endpoints differ.

---

## 86. Hardware watchdog and dead-man tests remain incomplete

**Issue:** Unit tests cannot prove behavior during Pi power loss, kernel hang, process hang, or external monitoring failure.

**Impact:** The deployed system's fail-safe and alert behavior are not fully commissioned.

**Resolution:** Kept the tests on the hardware-validation backlog rather than simulating false confidence.

**One-line solution:** Perform controlled hardware watchdog and power-loss tests before unattended operation.

---

## 87. Five-cycle and larger campaigns are not yet accepted

**Issue:** The automated scope trigger path is still intermittent and timeout evidence is inadequate.

**Impact:** Repeated campaigns could generate invalid data or repeatedly stress the apparatus without actionable results.

**Resolution:** Blocked multi-cycle progression until one clean, fully automated, manually reviewed cycle succeeds.

**One-line solution:** Do not start multi-cycle campaigns until one unattended cycle passes every V2 sanity gate.

---
