# CCID Automation: Complete Issue and Resolution Log

This document consolidates the 154 issues encountered during design, commissioning, debugging, campaign execution, analysis, networking, autonomous operation, and monitoring.

## 1. Both mains legs needed switching

**Issue:** The original design switched only one mains leg, leaving part of the EVSE energized relative to ground during safe-off periods.

**Impact:** Safe-off did not fully isolate the EVSE and could leave hazardous voltage present.

**Resolution:** Added separate K1 and K2 contactors to interrupt L1 and L2 independently.

**One-line solution:** Use coordinated K1/K2 contactors to disconnect both mains legs during safe-off.

## 2. Leakage injection needed a dedicated contactor

**Issue:** The earlier contactor naming and topology mixed mains switching and leakage-injection responsibilities.

**Impact:** Ambiguous functions increased wiring, software, and troubleshooting risk.

**Resolution:** Standardized K1 for L1, K2 for L2, and K3 exclusively for leakage injection.

**One-line solution:** Reserve K3 only for the leakage branch and K1/K2 only for mains isolation.

## 3. ZX-517 boards were not optically isolated

**Issue:** GPIO trigger voltage had no defined reference unless the Pi ground and driver signal grounds were connected.

**Impact:** Undefined signal reference could prevent reliable MOSFET switching.

**Resolution:** Bonded all three ZX-517 signal grounds to the Raspberry Pi ground distribution.

**One-line solution:** Share a defined signal ground between the Pi and all ZX-517 inputs.

## 4. ZX-517 boards had no onboard flyback protection

**Issue:** Contactor-coil turn-off could avalanche-stress the MOSFETs and potentially fail a driver shorted.

**Impact:** A failed driver could hold a contactor energized or damage the control electronics.

**Resolution:** Installed external flyback diodes across all three coils with the banded cathode toward OUT+.

**One-line solution:** Fit correctly oriented flyback diodes directly across every contactor coil.

## 5. Contactor coils needed electrical verification

**Issue:** Coil polarity and internal construction were initially unknown.

**Impact:** Incorrect assumptions could cause wiring errors or inappropriate suppression components.

**Resolution:** Measured approximately 25.4 Ω for K1/K2 and approximately 26 Ω for K3, consistent with ordinary 12 V coils.

**One-line solution:** Measure every coil before wiring and document resistance and polarity behavior.

## 6. Three visually similar contactor channels could be mixed up

**Issue:** K1, K2, and K3 hardware channels looked similar and could be cross-connected.

**Impact:** Swapping K3 with a mains channel could create unsafe behavior and misleading no-trip results.

**Resolution:** Physically labeled contactors, drivers, supplies, and coil leads.

**One-line solution:** Label every contactor channel end-to-end before commissioning.

## 7. K1 and K2 independently driven could become mismatched

**Issue:** A software or driver fault could command only one mains leg closed.

**Impact:** The EVSE could be left partially energized or operated in an unintended state.

**Resolution:** Added commanded-state mismatch detection and coordinated open/close behavior.

**One-line solution:** Treat K1 and K2 as a coordinated mains pair and halt on commanded mismatch.

## 8. Physical K1/K2 state is still not measured

**Issue:** Software currently knows only commanded contactor state and cannot detect welded contacts.

**Impact:** A welded or mechanically failed contactor could disagree with software state.

**Resolution:** Auxiliary-contact GPIO feedback was deliberately deferred until after the 10-cycle commissioning stage.

**One-line solution:** Add isolated auxiliary-contact feedback after the current commissioning gate.

## 9. Protective-earth continuity is not directly monitored

**Issue:** The software cannot specifically identify an open output protective-earth path.

**Impact:** A PE fault may appear only as an indirect scope or DUT symptom.

**Resolution:** Only indirect consequences are currently detected; dedicated isolated PE monitoring requires approved hardware.

**One-line solution:** Add an isolated PE continuity monitor if direct software detection is required.

## 10. K3 self-test could hold leakage too long

**Issue:** The generic GPIO exercise tool can hold K3 for human-scale durations.

**Impact:** Using that tool with mains present could apply leakage far longer than intended.

**Resolution:** Restricted K3 exercise to mains-off commissioning with a short 0.2-second pulse.

**One-line solution:** Never use a long generic K3 pulse with the energized leakage circuit.

## 11. Remounting the rig created recommissioning risk

**Issue:** Disconnecting, drilling, mounting, and reconnecting could alter wiring, probe position, camera alignment, or USB connections.

**Impact:** Previously validated assumptions could become invalid after mechanical work.

**Resolution:** Treated the plank remount as a limited recommissioning and repeated inspection and functional checks.

**One-line solution:** Repeat staged preflight whenever the physical rig is moved or rewired.

## 12. `/tmp` was too small for the disk-space guard

**Issue:** `/tmp` was a 1 GB tmpfs while the project required at least 2 GB free.

**Impact:** Many tests halted with `persistence:insufficient_disk_space` despite ample root storage.

**Resolution:** Ran tests with `TMPDIR=$HOME/ccid_test_tmp` on the root filesystem.

**One-line solution:** Redirect test temporary files to a filesystem with more than 2 GB free.

## 13. Root filesystem space was initially mistaken for the problem

**Issue:** Disk-space failures appeared despite roughly 51 GB free on the Pi.

**Impact:** Troubleshooting focused on the wrong filesystem.

**Resolution:** Identified the separate `/tmp` tmpfs limit instead of changing the root-space guard.

**One-line solution:** Check the exact filesystem used by temporary files, not only `/` free space.

## 14. New SSH sessions lacked the scope resource variable

**Issue:** Real-scope commands failed when `CCID_SCOPE_RESOURCE` was not exported.

**Impact:** Scope tools could not locate the VISA instrument.

**Resolution:** Exported the discovered resource and later stored it in `/etc/default/ccid-automation`.

**One-line solution:** Persist the scope resource for both interactive shells and systemd services.

## 15. The service environment file did not initially exist

**Issue:** `/etc/default/ccid-automation` was missing.

**Impact:** Systemd could not receive scope and monitoring environment variables.

**Resolution:** Created the file with `CCID_SCOPE_RESOURCE` and later `CCID_CRONITOR_URL`.

**One-line solution:** Store service-only environment variables in `/etc/default/ccid-automation`.

## 16. The repository service file expected `/opt/ccid`

**Issue:** The existing unit referenced `/opt/ccid`, but tested code lived in `/home/ccid/ccid_automation`.

**Impact:** Starting the unit could run missing or stale code.

**Resolution:** Used transient `systemd-run` services against the tested home-directory repository.

**One-line solution:** Point services only at the exact commissioned code and virtual environment.

## 17. Closing SSH could terminate foreground runs

**Issue:** Campaigns started directly in SSH depended on the terminal session.

**Impact:** Laptop sleep, terminal closure, or network loss could stop a long test.

**Resolution:** Validated `systemd-run` so the Pi owns the process independently of SSH.

**One-line solution:** Run long campaigns under systemd, not as foreground SSH jobs.

## 18. The systemd smoke-test unit disappeared after completion

**Issue:** `systemctl status` reported that the transient unit could not be found.

**Impact:** Successful completion could be mistaken for service loss.

**Resolution:** Recognized that `--collect` removes completed transient units and verified the output file.

**One-line solution:** Use journal and artifacts to verify collected transient services after completion.

## 19. The first systemd simulation used the real configuration

**Issue:** `simulate` refused to run because `config.yaml` selected real hardware modes.

**Impact:** The isolation test failed before demonstrating campaign persistence.

**Resolution:** Created a temporary config with GPIO, scope, and camera all set to `sim`.

**One-line solution:** Use a separate temporary simulation config and never edit the real config for smoke tests.

## 20. The systemd simulation halted on an invalid simulated waveform

**Issue:** The temporary simulated waveform failed V3 sanity requirements.

**Impact:** The simulation could not serve as a functional campaign result.

**Resolution:** Used the run only to prove systemd survived SSH disconnection and validated hardware separately.

**One-line solution:** Separate process-supervision validation from waveform-algorithm validation.

## 21. Scope udev permissions initially blocked non-root access

**Issue:** The `ccid` user could not reliably access the Keysight USB instrument.

**Impact:** Real-scope operation required elevated privileges or failed.

**Resolution:** Installed the scope udev rule and confirmed plugdev membership.

**One-line solution:** Grant the service account persistent USB permissions through udev.

## 22. The scope USB resource format differed from the handoff

**Issue:** Earlier documentation used a hexadecimal VISA resource while PyVISA-Py enumerated a decimal form.

**Impact:** Using the documented string could fail instrument connection.

**Resolution:** Standardized on `USB0::2391::6040::MY58100795::0::INSTR` reported by discovery.

**One-line solution:** Use the resource string actually enumerated by the deployed VISA backend.

## 23. RAW waveform setup depended on command order

**Issue:** RAW transfer settings could be ignored or return the wrong point count if applied in the wrong sequence.

**Impact:** Captured records could have insufficient depth or inconsistent format.

**Resolution:** Corrected configuration order and verified RAW/BYTE mode through readback and real captures.

**One-line solution:** Apply waveform source, format, points, and mode deterministically before acquisition.

## 24. `WAVeform:POINts?` sometimes returned zero before acquisition

**Issue:** Pre-acquisition readback sometimes reported `+0` points.

**Impact:** The result suggested waveform memory was misconfigured.

**Resolution:** Determined no completed acquisition record existed yet and verified the real point count after capture.

**One-line solution:** Treat `+0` as no current acquisition record and verify depth after capture.

## 25. Interrupted USBTMC operations wedged the scope

**Issue:** Killing Python during USB transfer left later commands timing out.

**Impact:** The scope became unusable until a disruptive recovery procedure was performed.

**Resolution:** Recovered with a complete cold restart including 60 seconds without scope AC power.

**One-line solution:** Never abandon an in-flight libusb call; cold-reset the scope if USBTMC remains wedged.

## 26. USB reconnect alone did not recover the scope

**Issue:** Unplugging and reconnecting USB did not clear the stuck USBTMC state.

**Impact:** Repeated retries wasted time and did not restore communication.

**Resolution:** Used full scope power removal rather than repeated reconnects.

**One-line solution:** Cold-power-cycle the instrument when its USBTMC engine remains stuck.

## 27. Pi reboot did not recover the wedged USBTMC endpoint

**Issue:** Restarting the Pi still left `*IDN?` timing out.

**Impact:** The host reboot was incorrectly expected to reset instrument-side state.

**Resolution:** Cold-started the oscilloscope itself.

**One-line solution:** Reset the device that owns the wedged endpoint, not only the host.

## 28. A different USB cable and port were required after remount

**Issue:** The scope did not appear in `lsusb` after reconnecting the remounted rig.

**Impact:** VISA could not find any instrument because USB enumeration failed.

**Resolution:** Replaced the cable and moved to a different Pi port, restoring `0957:1798` enumeration.

**One-line solution:** Verify `lsusb` first and replace cable or port before debugging Python.

## 29. Linux did not expose `/dev/usbtmc0`

**Issue:** No kernel USBTMC device node appeared.

**Impact:** The missing node was mistaken for a driver failure.

**Resolution:** Confirmed PyVISA-Py used PyUSB/libusb directly and worked without `/dev/usbtmc0`.

**One-line solution:** Validate the actual backend path before treating a missing device node as fatal.

## 30. Timeout diagnostics waited through many sequential timeouts

**Issue:** Approximately 20 failed scope queries accumulated one timeout each.

**Impact:** Diagnostics could take a long time and delay recovery.

**Resolution:** Moved diagnostics after safe-off, bounded synchronous calls, and stopped after the first failure.

**One-line solution:** Fail fast and bound diagnostic I/O after the rig is already safe.

## 31. Diagnostics originally ran before K1/K2 opened

**Issue:** A wedged scope could delay mains opening while evidence was collected.

**Impact:** The diagnostic feature weakened a safety-critical sequence.

**Resolution:** Reordered timeout handling so K3, K2, and K1 open before diagnostics.

**One-line solution:** Always establish full safe-off before querying a failed peripheral.

## 32. A daemon-thread timeout caused a segmentation fault

**Issue:** A worker stayed blocked inside libusb while the main thread disconnected the VISA session.

**Impact:** Python crashed and the scope USBTMC interface wedged again.

**Resolution:** Removed daemon-thread timeouts and returned to synchronous native VISA timeouts.

**One-line solution:** Never close a VISA session while another thread may still be using it.

## 33. VISA device clear was unsupported

**Issue:** `self._inst.clear()` returned `VI_ERROR_NSUP_OPER` with PyVISA-Py.

**Impact:** Diagnostics aborted before collecting any state.

**Resolution:** Recorded unsupported clear as nonfatal while retaining fail-fast behavior for real clear errors.

**One-line solution:** Continue when clear is explicitly unsupported; abort on other clear failures.

## 34. Screenshot queries exceeded diagnostic timeout

**Issue:** `:DISPlay:DATA? PNG` timed out while scalar setting queries succeeded.

**Impact:** Timeout bundles lacked a scope image.

**Resolution:** Preserved scalar state and recorded screenshot failure without blocking safe-off.

**One-line solution:** Treat screenshot capture as optional evidence, not a safety dependency.

## 35. Diagnostic screenshot files could be zero bytes

**Issue:** Failed screenshot transfers still left empty PNG paths.

**Impact:** An empty artifact could be mistaken for a valid image.

**Resolution:** Relied on state JSON and checked file size before using screenshots.

**One-line solution:** Validate diagnostic image length before treating the file as evidence.

## 36. Scope inherited the previous front-panel trigger mode

**Issue:** The code sent EDGE parameters without selecting EDGE mode.

**Impact:** Parameters could be inert while the scope remained in Pattern, Glitch, or another mode.

**Resolution:** Added `:TRIGger:MODE EDGE` before edge parameters.

**One-line solution:** Explicitly select trigger mode before configuring mode-specific fields.

## 37. CH1 needed AC coupling

**Issue:** The code initially forced DC coupling despite the qualified measurement plan.

**Impact:** DC content or offset could reduce useful display and trigger behavior.

**Resolution:** Set CH1 to AC coupling and verified real-scope readback.

**One-line solution:** Configure channel coupling explicitly from the approved measurement setup.

## 38. Trigger coupling was separate from CH1 coupling

**Issue:** Channel coupling did not guarantee what the trigger comparator saw.

**Impact:** The displayed waveform and trigger path could behave differently.

**Resolution:** Explicitly set trigger coupling to DC and verified readback.

**One-line solution:** Configure channel and trigger coupling independently when the scope supports both.

## 39. The noise-reject command was unsupported

**Issue:** `:TRIGger:NREJect OFF` left `-113,"Undefined header"` on the MSO-X 2014A.

**Impact:** The scope rejected part of the configuration while software assumed success.

**Resolution:** Removed the unsupported command.

**One-line solution:** Use only firmware-supported SCPI commands and fail on any rejected configuration.

## 40. Configuration errors were briefly drained and discarded

**Issue:** One implementation cleared unsupported-command errors and still marked the scope configured.

**Impact:** Invalid settings could be silently inherited from the front panel.

**Resolution:** Reverted the behavior and made nonzero configuration errors block arming.

**One-line solution:** Drain errors for inspection, but never hide configuration rejection.

## 41. Stale errors falsely failed clean configuration

**Issue:** Interrupted queries left `-410` or `-420` in the scope error queue.

**Impact:** A later valid configuration appeared to be rejected.

**Resolution:** Cleared stale errors before configuration and required the post-config queue to remain clean.

**One-line solution:** Start from a clean error queue and attribute only newly generated errors.

## 42. Configuration lacked a completion barrier

**Issue:** The code could issue `:SINGle` before all settings were internally applied.

**Impact:** K3 could operate while the scope was still configuring.

**Resolution:** Added `*OPC?` after the configuration block.

**One-line solution:** Wait for instrument operation completion before arming.

## 43. Probe ratio was set after scale and offset

**Issue:** Applying probe attenuation last could reinterpret vertical settings.

**Impact:** Trigger and display scaling might not match intended voltage units.

**Resolution:** Moved `:CHANnel1:PROBe` before scale and offset.

**One-line solution:** Set probe ratio before all engineering-unit vertical parameters.

## 44. Probe-order correction did not resolve no-trigger

**Issue:** The command-order fix was plausible but timeouts persisted.

**Impact:** The root cause remained unresolved after another energized test.

**Resolution:** Kept the correct order and continued state-machine investigation.

**One-line solution:** Retain valid cleanup fixes without mistaking them for the root cause.

## 45. LEFT-reference geometry lacked pre-trigger history

**Issue:** One record began with the signal already active at the boundary.

**Impact:** The waveform could not prove a quiet pre-trigger baseline.

**Resolution:** Changed to 50 ms/div with CENTER reference.

**One-line solution:** Center the timebase so the record contains both quiet baseline and post-event response.

## 46. AutoScale produced unusable tiny-signal settings

**Issue:** AutoScale while de-energized selected 100 mV/div, 2 mV trigger, and 200 ns/div.

**Impact:** Those settings could not capture a 120 V millisecond-scale event.

**Resolution:** Restored deterministic automated settings and avoided AutoScale before real runs.

**One-line solution:** Do not use AutoScale on a quiet input for this measurement.

## 47. K3 could close before the scope was armed

**Issue:** `:SINGle` could return before the acquisition system was ready.

**Impact:** The leakage event might pass before trigger monitoring began.

**Resolution:** Polled the scope operation run bit before K3.

**One-line solution:** Block injection until the scope reports an armed state.

## 48. The first armed check was insufficient

**Issue:** The scope could report armed and then lose or consume the state before injection.

**Impact:** A transient or race could invalidate the one-shot acquisition.

**Resolution:** Added a 50 ms wait and a second armed check.

**One-line solution:** Verify armed state again immediately before K3.

## 49. A one-second post-arm dwell was tested

**Issue:** The run bit might become active before the trigger engine fully settled.

**Impact:** The system could inject too soon despite two armed checks.

**Resolution:** Added a diagnostic one-second dwell and final armed/TER check.

**One-line solution:** Use a bounded settle delay only as a controlled diagnostic variable.

## 50. The one-second dwell initially appeared ineffective

**Issue:** The run still looked like a timeout after the longer wait.

**Impact:** Arming readiness was incorrectly retained as the leading theory.

**Resolution:** Later found a force-state bookkeeping bug that discarded a natural acquisition.

**One-line solution:** Inspect state transitions before adding more timing delays.

## 51. TER contained stale trigger events

**Issue:** A nonzero post-configuration `:TER?` caused a halt before arming.

**Impact:** Old trigger history blocked new cycles.

**Resolution:** Read TER once to clear and again to verify a clean baseline.

**One-line solution:** Clear read-to-clear event registers, then verify zero before arming.

## 52. A manual TER edit broke simulated event sequences

**Issue:** Adding an extra TER read consumed queued fake events in tests.

**Impact:** Existing tests failed and code behavior became inconsistent.

**Resolution:** Reverted the manual edit and implemented the change test-first.

**One-line solution:** Update simulator event sequences whenever read-to-clear register usage changes.

## 53. A timeout `TER=1` was overinterpreted

**Issue:** A diagnostic saw a latched trigger event without knowing when it occurred.

**Impact:** The team initially suspected acquisition completion logic incorrectly.

**Resolution:** Instrumented TER at controlled points around configuration, arm, and injection.

**One-line solution:** Timestamp and clear event registers before using them as causal evidence.

## 54. TER instrumentation confirmed one genuine no-trigger

**Issue:** TER stayed zero during the K3 window while the scope remained armed.

**Impact:** No natural trigger evidence or waveform was available.

**Resolution:** Added a diagnostic-only forced trigger to freeze acquisition memory.

**One-line solution:** Use forced capture only to inspect input data after a confirmed no-trigger.

## 55. Forced trigger successfully froze diagnostic memory

**Issue:** Normal trigger failure left no completed waveform.

**Impact:** The team could not see what CH1 had sampled.

**Resolution:** Forced one diagnostic acquisition and saved RAW samples after safe-off.

**One-line solution:** Force a diagnostic-only trigger when natural triggering fails, never for measurement verdicts.

## 56. Forced waveform timing was aligned incorrectly to the Pi clock

**Issue:** A Pi-side force timestamp was treated as scope waveform `t=0`.

**Impact:** The leakage burst appeared to occur before K3, creating a false contradiction.

**Resolution:** Stopped cross-mapping independent clocks and used waveform-native timing.

**One-line solution:** Do not map unsynchronized Pi timestamps directly onto scope sample time.

## 57. Forced diagnostic halt reason was misleading

**Issue:** A forced trigger set TER and produced a natural-trigger-style halt reason.

**Impact:** Troubleshooting could confuse diagnostic and electrical triggers.

**Resolution:** Separated forced-trigger state from natural-trigger evidence.

**One-line solution:** Record whether the trigger was forced and classify the halt accordingly.

## 58. The main loop confused a force check with a force command

**Issue:** `forced_diagnostic_attempted` was set before TER decided whether forcing was needed.

**Impact:** A real natural trigger was found, but normal completion was still ignored.

**Resolution:** Changed the logic to track whether `:TRIGger:FORCe` was actually issued.

**One-line solution:** Set the forced-path flag only after the force command is sent successfully.

## 59. Natural captures were discarded after successful trigger

**Issue:** The scope stopped normally, but software remained in the diagnostic branch until timeout.

**Impact:** Valid waveforms were not transferred or analyzed.

**Resolution:** Fixed the force-issued flag so natural acquisitions continue to transfer.

**One-line solution:** Allow naturally completed acquisitions through the normal capture path.

## 60. The apparent no-trigger root cause was software state logic

**Issue:** Many runs suggested the scope missed a valid electrical trigger.

**Impact:** Effort was spent changing trigger settings and hardware assumptions.

**Resolution:** Corrected the forced-diagnostic state machine, after which natural captures became repeatable.

**One-line solution:** Audit control-state flags before changing proven electrical settings.

## 61. K3 stayed closed for the full scope timeout

**Issue:** A blocking scope wait prevented the outer loop from enforcing the 300 ms backstop.

**Impact:** Leakage could remain applied for several seconds.

**Resolution:** Changed acquisition polling to short calls with an independent K3 deadline.

**One-line solution:** Never let peripheral I/O block the K3 safety timer.

## 62. K3 backstop needed regression coverage

**Issue:** The blocking-wait defect could recur.

**Impact:** A future scope change could silently weaken leakage-duration safety.

**Resolution:** Added a blocking-scope regression and checked opening within tolerance.

**One-line solution:** Keep a regression that proves K3 opens on time even when the scope blocks.

## 63. Repeated no-trigger attempts produced no new evidence

**Issue:** Multiple energized cycles followed the same timeout pattern.

**Impact:** Hardware was exercised without improving diagnosis.

**Resolution:** Paused repeats and implemented diagnostics before further runs.

**One-line solution:** Do not repeat identical energized failures without a new evidence mechanism.

## 64. K3 opened normally after the acquisition fix

**Issue:** Earlier cycles depended on the backstop.

**Impact:** Normal operation had not been shown to release leakage promptly.

**Resolution:** Validated normal K3 opening in single, five-cycle, and later campaigns.

**One-line solution:** Require normal K3 opening in commissioning before endurance testing.

## 65. The original camera classifier stayed in BOOTING

**Issue:** The old temporal classifier reported BOOTING through visible flashing green.

**Impact:** The charging gate timed out and blocked scope testing.

**Resolution:** Reworked the gate around recent green evidence.

**One-line solution:** Use a charging-specific temporal policy rather than the generic boot classifier.

## 66. The first camera-gate redesign granted during boot

**Issue:** Three green frames in two seconds matched the green phase of the startup sequence.

**Impact:** The system could inject before charging was established.

**Resolution:** Required green evidence spanning at least 3.5 seconds in a 6-second window.

**One-line solution:** Require temporally distributed green observations, not a short burst.

## 67. Red and blue frames needed to reset green evidence

**Issue:** Boot-sequence green frames could survive later color transitions.

**Impact:** Old evidence could produce a false charging grant.

**Resolution:** Made red or blue clear the rolling green window.

**One-line solution:** Reset charging evidence whenever a conflicting LED color appears.

## 68. The gate needed a current green frame

**Issue:** Historical green evidence could grant after the LED changed.

**Impact:** The system might proceed in a noncharging state.

**Resolution:** Required the current frame to be green when granting.

**One-line solution:** Grant only when both history and the current observation support charging.

## 69. Camera logic needed real-hardware validation

**Issue:** Unit tests could not reproduce the physical EVSE boot sequence.

**Impact:** Classifier timing could still be wrong on the C270.

**Resolution:** Ran a K3-disabled real-camera gate test that granted after sustained flashing green.

**One-line solution:** Validate temporal vision gates against the real device before enabling injection.

## 70. Camera moved during the plank remount

**Issue:** The LED was outside or near the fixed ROI boundary.

**Impact:** The classifier stayed in BOOTING despite visible flashing green.

**Resolution:** Captured warmed frames and repositioned the camera to center the LED.

**One-line solution:** Recheck full-frame alignment and ROI after any mechanical change.

## 71. Initial camera captures were black

**Issue:** FFmpeg saved the first frame before the C270 warmed up.

**Impact:** A healthy camera appeared dead or underexposed.

**Resolution:** Discarded initial frames and captured after a three-second warm-up.

**One-line solution:** Warm the camera stream before saving diagnostic frames.

## 72. The FFmpeg three-frame selector hung

**Issue:** The selection expression did not terminate as expected.

**Impact:** Camera preflight stalled for more than 45 seconds.

**Resolution:** Stopped FFmpeg and used a simpler warmed single-frame command.

**One-line solution:** Prefer bounded, simple capture commands for preflight evidence.

## 73. Camera image was dark and purple

**Issue:** Exposure 30 was too short in the remounted low-light setup.

**Impact:** The LED color was difficult to classify reliably.

**Resolution:** Raised manual exposure to 60 and revalidated flashing green.

**One-line solution:** Set exposure from reviewed real frames, then rerun the classifier gate.

## 74. Classifier timed out after camera shift

**Issue:** The EVSE visibly flashed green while software remained BOOTING.

**Impact:** Real campaigns could not proceed after remount.

**Resolution:** Repositioned the camera and retained exposure 60.

**One-line solution:** Fix framing before changing HSV or temporal classifier thresholds.

## 75. Camera-only validation produced a synthetic PASS

**Issue:** Real K1/K2 and camera were used with a simulated scope.

**Impact:** The result could be mistaken for electrical trip evidence.

**Resolution:** Explicitly labeled it as camera commissioning only.

**One-line solution:** Never treat mixed real/sim camera-gate runs as electrical results.

## 76. Camera device index required revalidation

**Issue:** USB reconnection could change `/dev/videoN` numbering.

**Impact:** The application might open the wrong device.

**Resolution:** Confirmed the C270 stayed on `/dev/video0` at 640×480 YUYV.

**One-line solution:** Enumerate camera devices after USB or hardware changes.

## 77. Camera control queries looked frozen

**Issue:** `v4l2-ctl` commands were slow enough to seem hung.

**Impact:** The camera could be unnecessarily reset or processes killed.

**Resolution:** Allowed completion and confirmed driver and exposure state.

**One-line solution:** Use bounded patience and verify process state before killing camera tools.

## 78. The original leading envelope looked into the future

**Issue:** Future burst energy contaminated early samples.

**Impact:** V1 could infer false pre-trigger leakage or onset.

**Resolution:** Introduced V2 with raw-sample pre-trigger checks and corrected refinement.

**One-line solution:** Avoid forward-looking filters when deciding whether earlier samples contain signal.

## 79. V1 results needed historical preservation

**Issue:** Changing the algorithm could silently rewrite prior measurements.

**Impact:** Auditability and comparison across campaigns would be lost.

**Resolution:** Kept V1 and introduced explicitly versioned V2 behavior.

**One-line solution:** Version every analysis change and leave historical outputs untouched.

## 80. Replay version and endpoint text could disagree

**Issue:** A replay override could compute one version while displaying another version’s definition.

**Impact:** Reports could misstate how endpoints were calculated.

**Resolution:** Made version overrides select matching endpoint-definition text.

**One-line solution:** Keep algorithm version, notes, and computation synchronized.

## 81. A boundary-active waveform yielded a numerical PASS

**Issue:** The signal was present from the first sample without a quiet baseline.

**Impact:** The computed trip time was not trustworthy.

**Resolution:** V2 rejected it with `no_pretrigger_leakage=false`.

**One-line solution:** Require demonstrable quiet pre-trigger data before accepting trip time.

## 82. A numerical PASS could be committed with failed sanity checks

**Issue:** One run reported 0.0 s and `collapse_is_clean=false` but was stored as PASS.

**Impact:** Invalid data could silently contaminate a campaign.

**Resolution:** Added a guard so any failed sanity check prevents PASS and halts review.

**One-line solution:** Make sanity-check success a mandatory prerequisite for PASS.

## 83. `t_end` could precede `t0`

**Issue:** V2 calculated an end point 0.5 µs before onset and clamped duration to zero.

**Impact:** An impossible endpoint order became a false PASS.

**Resolution:** Marked the result invalid and preserved the waveform for correction.

**One-line solution:** Reject any result where `t_end < t0`; never clamp it into PASS.

## 84. The first cycle sometimes produced a zero-time result

**Issue:** A visible waveform could be analyzed as 0.0 s on the first cycle.

**Impact:** Campaign statistics and verdicts became unreliable.

**Resolution:** Preserved the waveform as debugging evidence and excluded it from performance statistics.

**One-line solution:** Flag first-cycle zero-time records as invalid until the startup defect is fixed.

## 85. V2 onset refinement could jump backward on noise

**Issue:** A single residual-floor crossing could drag onset backward by nearly a mains cycle.

**Impact:** Trip time was inflated and verdicts could invert.

**Resolution:** Created V3 requiring a sustained raw-sample run above the residual floor.

**One-line solution:** Require consecutive above-threshold samples before declaring onset.

## 86. Cycle 17 became a false 33.36 ms V2 FAIL

**Issue:** V2 moved onset backward by about 16.8 ms.

**Impact:** A likely passing electrical event was classified as a failure.

**Resolution:** Created V3 and retained V2 results only for audit history.

**One-line solution:** Replay suspicious V2 cycles under V3 before making electrical conclusions.

## 87. Cycles 5, 14, and 18 showed similar onset jumps

**Issue:** Those cycles had more than 10 ms backward refinements.

**Impact:** Additional verdicts or times could be biased.

**Resolution:** Flagged them for V3 replay and comparison.

**One-line solution:** Review all large onset refinements, not only the most obvious outlier.

## 88. V3 needed explicit config selection

**Issue:** Creating V3 did not ensure real runs used it.

**Impact:** Hardware campaigns could continue producing V2 results.

**Resolution:** Set `analysis.algorithm_version: v3` in `config.yaml` and verified it.

**One-line solution:** Confirm the configured analysis version before every validation campaign.

## 89. V3 required fresh hardware validation

**Issue:** Unit tests could not prove real-waveform behavior.

**Impact:** The new onset rule might fail on real noise and amplitude conditions.

**Resolution:** Completed a three-cycle V3 campaign with three PASS results.

**One-line solution:** Validate every analysis-version change on fresh real captures.

## 90. Raw waveforms were essential for algorithm correction

**Issue:** Screenshots could not support accurate replay or endpoint debugging.

**Impact:** Historical cycles would be impossible to reassess.

**Resolution:** Retained `.npz` samples and preambles for every committed cycle.

**One-line solution:** Archive raw samples, not just verdicts and screenshots.

## 91. The first fully automated valid cycle was delayed by state bugs

**Issue:** Repeated runs timed out or captured invalid records.

**Impact:** Commissioning could not advance to multiple cycles.

**Resolution:** After the force-flag fix, obtained a 23.1295 ms PASS with all sanity checks true.

**One-line solution:** Require one fully automatic valid cycle before scaling test count.

## 92. Five-cycle commissioning needed a valid single-cycle gate

**Issue:** Starting multiple cycles earlier would multiply invalid data.

**Impact:** More hardware cycles would not improve confidence.

**Resolution:** Required one accepted natural capture before authorizing five cycles.

**One-line solution:** Use staged campaign-size gates tied to evidence quality.

## 93. Five-cycle commissioning passed

**Issue:** Repeatability across multiple natural acquisitions was unproven.

**Impact:** Single-cycle success could have been accidental.

**Resolution:** Completed five cycles with five PASS results and normal K3 opening.

**One-line solution:** Advance only after a clean multi-cycle commissioning campaign.

## 94. The plank one-cycle validation produced an invalid zero-time PASS

**Issue:** The first post-remount capture exposed endpoint and sanity-guard defects.

**Impact:** A remounted system could have launched 25 cycles with invalid analysis.

**Resolution:** Blocked the campaign, added the guard, and ran a valid 7.7415 ms recheck.

**One-line solution:** Recommission analysis and hardware together after a physical rebuild.

## 95. The 25-cycle campaign included one overridden invalid cycle

**Issue:** Cycle 1 recorded 0.0 ms and was manually overridden to continue.

**Impact:** The operation count exceeded the valid-measurement count.

**Resolution:** Preserved Cycle 1 for debugging and excluded it from performance statistics.

**One-line solution:** Report recorded operations and valid measurements as separate totals.

## 96. The 25-cycle campaign appeared to contain a real FAIL

**Issue:** Cycle 17 was 33.3605 ms with all V2 sanity checks true.

**Impact:** The device appeared to fail the 24.97 ms limit.

**Resolution:** Traced the result to the V2 onset-refinement defect and created V3.

**One-line solution:** Do not equate clean waveform sanity with correct endpoint placement.

## 97. Valid-performance population differed from operation count

**Issue:** The campaign completed 25 operations but only 24 valid V2 measurements.

**Impact:** Pass-rate calculations could be misleading.

**Resolution:** Reported both the 25 recorded operations and the 24 valid results.

**One-line solution:** Always state exclusions and denominator explicitly.

## 98. Statistics initially included Cycle 1’s zero

**Issue:** The minimum and mean were distorted by an invalid overridden record.

**Impact:** Campaign performance looked better or worse for the wrong reason.

**Resolution:** Computed valid-result statistics excluding Cycle 1 while keeping raw data.

**One-line solution:** Keep invalid records in the archive but out of performance summaries.

## 99. Images dominated storage

**Issue:** Twenty-five cycles used about 23 MB of images versus 3.8 MB of waveforms.

**Impact:** Image retention, not waveform depth, drove the 6,000-cycle estimate.

**Resolution:** Projected roughly 6.5 GB for 6,000 cycles and confirmed sufficient disk space.

**One-line solution:** Base endurance storage planning on measured artifact categories.

## 100. ZIP and TAR archives could not be uploaded in chat

**Issue:** The complete run archive could not be attached.

**Impact:** Centralized analysis through chat was blocked.

**Resolution:** Performed command-line analysis on the Pi and selectively committed data.

**One-line solution:** Use local analysis and selective artifact export when archive upload is unavailable.

## 101. `runs/` was ignored by Git

**Issue:** The campaign directory matched `.gitignore`.

**Impact:** Important measurements could not be staged normally.

**Resolution:** Used `git add -f` for selected campaign artifacts.

**One-line solution:** Force-add only reviewed run files, not the entire ignored directory.

## 102. Uploading all images to Git was unnecessary

**Issue:** Scope and camera images added about 23 MB.

**Impact:** Git history would grow with limited analytical benefit.

**Resolution:** Committed JSON, CSV, waveforms, config, run state, and statistics only.

**One-line solution:** Keep bulk images outside normal Git unless specifically needed.

## 103. Cycle 1 needed preservation for debugging

**Issue:** Excluding the value statistically risked losing the evidence.

**Impact:** The startup zero-time bug would be harder to reproduce.

**Resolution:** Kept its JSON, waveform, CSV row, and explicit analysis note.

**One-line solution:** Exclude invalid data from metrics without deleting the underlying evidence.

## 104. Campaign data needed an external archive

**Issue:** Raw results initially existed only on the Pi microSD.

**Impact:** A storage failure could erase the campaign.

**Resolution:** Pushed selected 25-cycle data to GitHub in commit `7a15241`.

**One-line solution:** Maintain at least one off-Pi copy of every important campaign.

## 105. The Pi initially depended on an iPhone hotspot

**Issue:** The network required a phone to remain available.

**Impact:** Long autonomous tests lacked a stable connection path.

**Resolution:** Evaluated and adopted the Verizon hotspot over USB tethering.

**One-line solution:** Use a dedicated network path for unattended operation.

## 106. Verizon Wi-Fi was missing from the first scan

**Issue:** The desired SSID did not initially appear.

**Impact:** The Pi could not be moved off the iPhone network.

**Resolution:** Forced an `nmcli` rescan and found `Verizon-RC400L-84`.

**One-line solution:** Rescan before assuming an SSID is unavailable.

## 107. Wi-Fi switching disconnected SSH before password setup was clear

**Issue:** The connection reset before a password prompt appeared.

**Impact:** The Pi’s active network became uncertain.

**Resolution:** Reconnected through the iPhone and removed the incomplete profile.

**One-line solution:** Keep a fallback network while changing remote connectivity.

## 108. `ccid-pi` did not resolve on Verizon Wi-Fi

**Issue:** Windows could not resolve the hostname.

**Impact:** SSH failed despite possible network connectivity.

**Resolution:** Used the Pi’s explicit IPv4 address.

**One-line solution:** Document and use a stable address when mDNS is unreliable.

## 109. `usb0` was mistaken for the camera

**Issue:** The new USB network interface was confused with `/dev/video0`.

**Impact:** Network troubleshooting mixed unrelated device namespaces.

**Resolution:** Clarified that `usb0` is networking and `/dev/video0` is the camera.

**One-line solution:** Distinguish Linux network interfaces from video device nodes.

## 110. Verizon USB tethering needed validation

**Issue:** A stable non-phone network path was required.

**Impact:** Unattended monitoring and SSH were uncertain.

**Resolution:** Connected the RC400L by USB and obtained `192.168.1.121/24` on `usb0`.

**One-line solution:** Use USB tethering when hotspot Wi-Fi management is unreliable.

## 111. Internet routing source was uncertain

**Issue:** A successful ping did not prove which interface carried traffic.

**Impact:** Monitoring could still depend on the iPhone.

**Resolution:** `ip route get 8.8.8.8` confirmed `usb0` via `192.168.1.1`.

**One-line solution:** Verify the chosen interface with the kernel route decision.

## 112. SSH over USB tether needed a direct IP

**Issue:** The hostname did not resolve across the hotspot.

**Impact:** Remote access failed until the address was known.

**Resolution:** Connected with `ssh ccid@192.168.1.121`.

**One-line solution:** Use the USB-tether IPv4 address for reliable SSH.

## 113. Dual default routes could conflict

**Issue:** Both iPhone Wi-Fi and Verizon USB tether were active.

**Impact:** Traffic could leave through the wrong network.

**Resolution:** Verified `usb0` metric 100 and `wlan0` metric 600.

**One-line solution:** Confirm route metrics so the intended network is primary.

## 114. The existing systemd unit was not installed

**Issue:** `ccid-automation.service` could not be found.

**Impact:** The project could not yet run as a persistent service.

**Resolution:** Used transient `systemd-run` units for staged validation.

**One-line solution:** Validate transient services before installing a permanent unit.

## 115. The service assumed a missing deployment layout

**Issue:** The unit expected `/opt/ccid`, which did not exist.

**Impact:** Enabling it would fail or run the wrong copy.

**Resolution:** Ran transient units from `/home/ccid/ccid_automation`.

**One-line solution:** Use only the commissioned repository path in service commands.

## 116. Systemd independence from SSH was unproven

**Issue:** The team needed assurance that closing the laptop would not stop a run.

**Impact:** A 6,000-cycle campaign could be lost to SSH disconnection.

**Resolution:** Ran a smoke test, closed SSH, and verified completion afterward.

**One-line solution:** Prove service ownership by disconnecting and checking journal or artifacts.

## 117. The simulated systemd test failed with real modes

**Issue:** The command used `config.yaml` with real hardware modes.

**Impact:** The simulation exited before testing persistence.

**Resolution:** Created `/tmp/ccid_systemd_sim.yaml` with all modes set to sim.

**One-line solution:** Never repurpose the real config for simulation.

## 118. Autonomous real systemd operation needed proof

**Issue:** Simulation did not validate camera, scope, GPIO, or artifacts.

**Impact:** The production execution path remained uncertain.

**Resolution:** Completed a real five-cycle systemd campaign with five PASS results.

**One-line solution:** Validate the exact real command under systemd before long campaigns.

## 119. A collected transient unit vanished after completion

**Issue:** `systemctl status` returned “unit not found.”

**Impact:** The operator could think the run was lost.

**Resolution:** Used `journalctl` and run-state files to verify successful completion.

**One-line solution:** Expect collected units to disappear and rely on durable logs and artifacts.

## 120. Breakers were left off for a systemd run

**Issue:** The camera stayed in BOOTING for 90 seconds.

**Impact:** The autonomous campaign halted before any cycle completed.

**Resolution:** The gate opened mains safely and the journal identified operator preflight error.

**One-line solution:** Confirm breakers and supplies before starting an autonomous service.

## 121. A deliberate halt appeared as systemd failure

**Issue:** The application exited nonzero after a safe camera-gate halt.

**Impact:** Systemd’s generic failed state obscured the CCID-specific reason.

**Resolution:** Read the CCID journal halt reason rather than the systemd label alone.

**One-line solution:** Diagnose autonomous runs from application logs and run state.

## 122. Healthchecks.io was not the desired monitor

**Issue:** The initial monitoring plan used an unwanted external service.

**Impact:** The monitoring design did not match operator preference.

**Resolution:** Replaced it with Cronitor heartbeat support.

**One-line solution:** Choose the monitoring platform before finalizing service environment variables.

## 123. Uptime Kuma required a separate host

**Issue:** Running Kuma on the CCID Pi would fail with the Pi itself.

**Impact:** A local monitor could not detect Pi power loss.

**Resolution:** Deferred Kuma unless hosted on another always-on device.

**One-line solution:** Place dead-man monitoring outside the system being monitored.

## 124. Cronitor URL was not persistent

**Issue:** `CCID_CRONITOR_URL` was absent from the service environment.

**Impact:** Systemd runs could not send heartbeats.

**Resolution:** Stored the private URL in `/etc/default/ccid-automation`.

**One-line solution:** Persist telemetry secrets outside Git and shell sessions.

## 125. The scope variable also needed persistence

**Issue:** Systemd would not inherit the interactive scope export.

**Impact:** Autonomous scope connection could fail.

**Resolution:** Added `CCID_SCOPE_RESOURCE` to the environment file.

**One-line solution:** Put all required service variables in one persistent environment file.

## 126. Cronitor heartbeat URL required secrecy

**Issue:** The telemetry URL contains an embedded key.

**Impact:** Exposure could permit unauthorized heartbeat events.

**Resolution:** Masked checks and kept the URL out of chat, Git, and command history.

**One-line solution:** Store heartbeat URLs in a protected environment file and never print them.

## 127. Cronitor success delivery needed validation

**Issue:** Configuration did not prove events reached the service.

**Impact:** A campaign could appear monitored while sending nothing.

**Resolution:** Sent a bounded heartbeat and saw it online.

**One-line solution:** Test the exact telemetry URL before relying on alerts.

## 128. Cronitor failure delivery needed validation

**Issue:** The new `state=fail` path was unproven.

**Impact:** Halts might not raise an external alert.

**Resolution:** Sent a failure event, observed Failing, then sent recovery and observed Healthy.

**One-line solution:** Test both failure and recovery transitions before unattended use.

## 129. Per-event notifications were too noisy

**Issue:** Cronitor could notify on every successful heartbeat.

**Impact:** Long runs would create alert fatigue.

**Resolution:** Disabled notifications for every received event.

**One-line solution:** Alert on missed or failed heartbeats, not routine success events.

## 130. The expected interval was hard to locate

**Issue:** The overview did not clearly show heartbeat schedule configuration.

**Impact:** The monitor could alert too early or too late.

**Resolution:** Found the schedule assertion and set two minutes with 300-second grace.

**One-line solution:** Verify expected interval and grace on the monitor’s edit page.

## 131. Intentional idle time looked like failure

**Issue:** Cronitor only sees missing heartbeats, not operator intent.

**Impact:** A stopped campaign could trigger false alarms.

**Resolution:** Paused or deleted the monitor between campaigns and planned lifecycle handling.

**One-line solution:** Pause dead-man monitoring whenever no campaign is expected to run.

## 132. Cronitor Management API returned 401

**Issue:** A telemetry or incorrectly scoped key was used to pause the monitor.

**Impact:** Command-line pause failed.

**Resolution:** Distinguished telemetry, SDK, and custom management keys.

**One-line solution:** Use a management key with `monitor:read` and `monitor:write` for monitor control.

## 133. Cronitor management was too complex for short tests

**Issue:** Pause/resume and API-key setup added commissioning friction.

**Impact:** Monitoring work distracted from hardware validation.

**Resolution:** Temporarily simplified the monitor and deferred automation.

**One-line solution:** Keep short-run monitoring simple and harden lifecycle control before endurance testing.

## 134. Cronitor assertion and pause behavior were temporarily simplified

**Issue:** The original monitor configuration was cumbersome.

**Impact:** The operator risked false alerts during commissioning.

**Resolution:** Removed the assertion and set pause after one failure, with a reminder to restore settings.

**One-line solution:** Restore schedule, grace, and alert behavior before any long unattended campaign.

## 135. The Cronitor monitor was deleted

**Issue:** The current monitor was no longer wanted.

**Impact:** No external dead-man monitoring remained active.

**Resolution:** Planned creation of a fresh monitor when unattended operation resumes.

**One-line solution:** Recreate and validate monitoring as a formal preflight item.

## 136. Instructions were sometimes too long

**Issue:** Large multi-step responses made the current action unclear.

**Impact:** Operator confusion and accidental sequencing errors increased.

**Resolution:** Switched to one small action at a time.

**One-line solution:** Give one bounded command or decision per step during commissioning.

## 137. Commands were sometimes not visible

**Issue:** Some formatted code blocks did not render in the client.

**Impact:** The operator could not execute the requested step.

**Resolution:** Repeated commands as single plain-text lines.

**One-line solution:** Provide a plain one-line command when code formatting is not visible.

## 138. Manual one-line edits lost indentation

**Issue:** Pasted Python replacements damaged `sequencer.py` indentation.

**Impact:** The code failed to import.

**Resolution:** Ran syntax checks and restored the committed file.

**One-line solution:** Avoid complex inline source edits during live commissioning.

## 139. A broad `sed` replacement changed another TER block

**Issue:** The pattern matched both the target and a later `try` block.

**Impact:** A second code path became syntactically invalid.

**Resolution:** Reverted and moved the change back to test-first development.

**One-line solution:** Use exact context-aware edits and focused tests for repeated patterns.

## 140. Front-panel colors were overinterpreted

**Issue:** Red Run/Stop was assumed to mean the scope was not listening.

**Impact:** A valid Single-armed state was questioned.

**Resolution:** Distinguished red Run/Stop with yellow Single from fully stopped state.

**One-line solution:** Interpret the complete scope state, not one button color.

## 141. “Remote operation completed” was overinterpreted

**Issue:** A delayed front-panel message was treated as trigger evidence.

**Impact:** Troubleshooting leaned on an unsynchronized visual cue.

**Resolution:** Used TER, operation registers, timelines, and saved waveforms instead.

**One-line solution:** Prefer machine-readable scope state over front-panel notification timing.

## 142. Forced-waveform timestamp interpretation was overconfident

**Issue:** A large waveform was discounted because cross-device timing placed it before K3.

**Impact:** The likely leakage burst was initially misclassified as stale.

**Resolution:** Recognized independent clocks and switched to waveform-native analysis.

**One-line solution:** Never infer subsecond alignment across unsynchronized devices without a hardware marker.

## 143. The measurement path was questioned despite manual history

**Issue:** A misaligned forced record suggested the probe might be on the wrong node.

**Impact:** Hardware was suspected after the probe had already passed compensation and manual tests.

**Resolution:** Revisited the timing assumption and retained the large burst as likely leakage data.

**One-line solution:** Challenge timing assumptions before moving a verified measurement connection.

## 144. The Cycle 1 override complicated statistics

**Issue:** A zero-time PASS remained in campaign CSV.

**Impact:** Summary metrics mixed invalid and valid populations.

**Resolution:** Preserved the record but computed a separate valid-performance dataset.

**One-line solution:** Document every override and exclude it transparently from performance statistics.

## 145. Cycle 1 zero-time behavior remains unresolved

**Issue:** The first capture after some startup sequences can show a waveform but analyze as zero.

**Impact:** Long campaigns may begin with an invalid result.

**Resolution:** Retained Cycle 1 for V3 debugging and did not count it as valid performance data.

**One-line solution:** Resolve or automatically reject first-cycle zero-time results before endurance testing.

## 146. V3 still needs replay across the archived 25-cycle dataset

**Issue:** V2 may have misplaced onset for Cycles 5, 14, 17, and 18.

**Impact:** Historical conclusions remain uncertain until replayed.

**Resolution:** Archived all raw waveforms and introduced V3 without rewriting V2 records.

**One-line solution:** Batch-replay the complete campaign under V3 and compare endpoints and verdicts.

## 147. Campaign-level statistical acceptance is undefined

**Issue:** Per-cycle verdicts do not automatically define campaign acceptance.

**Impact:** A run can complete with mixed PASS, FAIL, and invalid records.

**Resolution:** Deferred campaign acceptance to offline engineering review.

**One-line solution:** Define campaign acceptance criteria before the 6,000-cycle run.

## 148. UL 2231-2 endpoint interpretation remains provisional

**Issue:** The project endpoint text has not been independently confirmed against the full standard.

**Impact:** Analysis may not match the final compliance interpretation.

**Resolution:** Versioned the endpoint definitions and preserved every historical result.

**One-line solution:** Obtain formal standard review before claiming compliance measurements.

## 149. Auxiliary-contact GPIO feedback remains deferred

**Issue:** Physical K1/K2/K3 state is still not read by software.

**Impact:** Welded or failed contacts remain undetectable through GPIO.

**Resolution:** Kept the agreed deferral until after the 10-cycle commissioning gate.

**One-line solution:** Revisit auxiliary feedback before extended unattended operation.

## 150. A permanent systemd service is not installed

**Issue:** Only transient `systemd-run` units have been validated.

**Impact:** Operators lack a single installed start/stop workflow.

**Resolution:** Proved autonomous execution first and deferred permanent installation and runbook.

**One-line solution:** Install the permanent unit only after its paths, restart policy, and resume behavior are reviewed.

## 151. Cronitor production lifecycle is not finalized

**Issue:** The monitor was simplified and later deleted.

**Impact:** Long unattended tests currently lack a finalized external dead-man process.

**Resolution:** Plan to create and validate a new monitor before endurance testing.

**One-line solution:** Make monitor creation, activation, pause, failure, and recovery part of preflight.

## 152. Full watchdog and reboot-resume commissioning is incomplete

**Issue:** SSH independence is proven, but reboot, power loss, watchdog, and sticky-halt resume are not.

**Impact:** A 6,000-cycle campaign could fail in untested recovery paths.

**Resolution:** Deferred these controlled destructive tests until short-run operation stabilized.

**One-line solution:** Test reboot and watchdog recovery before authorizing unattended endurance operation.

## 153. Long-duration backup planning is incomplete

**Issue:** A 6,000-cycle dataset should not live only on the Pi microSD.

**Impact:** Storage failure could destroy weeks of evidence.

**Resolution:** Estimated capacity and created selective Git backups, but external raw backup remains needed.

**One-line solution:** Arrange automatic off-Pi backup before the endurance campaign.

## 154. The 6,000-cycle campaign is not yet authorized

**Issue:** Short-run hardware and systemd operation work, but monitoring, documentation, replay, and recovery tasks remain.

**Impact:** Starting now would expose unresolved operational and analytical risks at scale.

**Resolution:** Continue staged commissioning and complete the remaining gates first.

**One-line solution:** Authorize 6,000 cycles only after analysis, monitoring, backup, watchdog, and operator runbook gates pass.
