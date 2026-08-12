# CCID Automation Build and Commissioning Issue Log

**Project:** CCID Endurance Test Automation  
**Platform:** Raspberry Pi 4 Model B Rev 1.5  
**Repository branch:** `development`  
**Purpose:** Comprehensive record of all 71 issues encountered during Raspberry Pi setup, hardware commissioning, oscilloscope integration, webcam calibration, software integration, and real-cycle testing.

---

## 1. Raspberry Pi OS differed from the planned baseline

**Issue:** The plan assumed Raspberry Pi OS Bookworm with Python 3.11, but the Pi was running Debian 13 Trixie, ARM64, with Python 3.13.5.

**Impact:** Raspberry Pi packages, Python wheels, and GPIO libraries could have behaved differently from the documented baseline.

**Resolution:** Updated the system, installed all required packages, installed project dependencies in a virtual environment, and used the complete test suite to confirm compatibility.

**One-line solution:** Validated Debian 13 and Python 3.13 compatibility by successfully running the full project test suite.

---

## 2. GitHub repository was on `development`, not `main`

**Issue:** The complete project existed on the `development` branch, while `main` contained only limited content.

**Impact:** Cloning without specifying a branch would have produced the wrong codebase.

**Resolution:** Cloned the repository directly from the `development` branch.

**One-line solution:** Used `git clone --branch development --single-branch ...`.

---

## 3. Git identity was not configured on the Pi

**Issue:** The first commit failed with `Author identity unknown`.

**Impact:** Git could stage changes but could not create commits.

**Resolution:** Configured the Git author name and email for the repository.

**One-line solution:** Set repository-local `user.name` and `user.email`.

---

## 4. GitHub rejected HTTPS password authentication

**Issue:** Pushing over HTTPS failed because GitHub does not accept account passwords for Git operations.

**Impact:** Local commits could not be pushed.

**Resolution:** Generated an ED25519 SSH key on the Pi, added the public key to GitHub, tested authentication, and changed the remote URL to SSH.

**One-line solution:** Switched the repository remote to `git@github.com:anishshriram/ccid_automation.git`.

---

## 5. No `.gitignore` existed

**Issue:** Python generated many `__pycache__` directories and `.pyc` files, all of which appeared as untracked files.

**Impact:** Git status became cluttered, and generated files could have been committed accidentally.

**Resolution:** Added a `.gitignore` covering Python caches, the virtual environment, run outputs, and large temporary calibration recordings.

**One-line solution:** Added ignore rules for `__pycache__/`, `*.pyc`, `venv/`, `runs/`, and temporary calibration folders.

---

## 6. The Pi hostname sometimes did not resolve

**Issue:** Windows occasionally failed to resolve `ccid-pi.local`.

**Impact:** SSH could not connect by hostname after the Pi or hotspot restarted.

**Resolution:** Connected using the Pi’s hotspot IP address, typically `172.20.10.5`.

**One-line solution:** Used `ssh ccid@172.20.10.5` when mDNS hostname resolution failed.

---

## 7. Confusion between BCM GPIO numbers and physical pin numbers

**Issue:** The software uses BCM numbers such as GPIO17, while the physical header uses numbers such as pin 11.

**Impact:** It initially appeared that the code and measured pins might not match.

**Resolution:** Confirmed the mapping electrically:

- GPIO17 = physical pin 11
- GPIO27 = physical pin 13
- GPIO22 = physical pin 15

**One-line solution:** Kept BCM numbering in software and referenced both BCM and physical pin numbers during wiring.

---

## 8. Initial 3.3 V measurement appeared inconsistent with GPIO LOW

**Issue:** GPIO17 reported input with pulldown and LOW, but an initial meter measurement appeared to show 3.3 V.

**Impact:** Suggested a possible GPIO configuration or wiring problem.

**Resolution:** Determined that the measurement had been taken on the wrong physical pin because of numbering confusion, then verified physical pin 11 correctly.

**One-line solution:** Reidentified physical pin 11 and verified 0 V LOW and 3.297 V HIGH.

---

## 9. GPIO Zero fell back to an experimental backend

**Issue:** Running the real GPIO tool produced warnings that `lgpio`, `RPi.GPIO`, and `pigpio` could not be loaded; GPIO Zero fell back to `NativeFactory`.

**Impact:** The fallback worked for a pulse but was not considered reliable enough for the endurance campaign.

**Root cause:** `python3-lgpio` was installed system-wide, but the isolated virtual environment could not import Debian’s `dist-packages`.

**Resolution:** Linked `lgpio.py` and the ARM64 `_lgpio` extension into the virtual environment and verified `LGPIOFactory`.

**One-line solution:** Symlinked the system `lgpio` files into the venv and verified `LGPIOFactory`.

---

## 10. Multiple boards needed one Raspberry Pi ground

**Issue:** K1, K2, and K3 all required a common signal reference, but a single jumper connector could not safely hold several wires on physical pin 9.

**Impact:** Poor ground distribution could create unreliable trigger behavior.

**Resolution:** Used a breadboard ground rail as a temporary low-voltage star distribution point.

**One-line solution:** Connected Pi ground to a breadboard rail and distributed the common ground to all three ZX-517 boards.

---

## 11. ZX-517 continuity testing illuminated the board LED

**Issue:** Testing continuity between `TRIG/PWM` and `GND` caused the red trigger LED to illuminate.

**Impact:** Initially suggested a solder bridge or damaged board.

**Resolution:** Confirmed all three boards behaved identically and measured approximately 10 kΩ in resistance mode; the continuity tester was supplying enough current to illuminate the LED.

**One-line solution:** Verified the boards in resistance mode instead of interpreting continuity-mode LED illumination as a fault.

---

## 12. Concern about missing GPIO pulldowns

**Issue:** A floating MOSFET trigger during Pi startup could energize a contactor unexpectedly.

**Impact:** Undefined trigger state during boot or power loss.

**Resolution:** Measured approximately 10 kΩ from `TRIG/PWM` to signal ground on all three boards, confirming onboard pulldowns.

**One-line solution:** Verified each ZX-517 has an approximately 10 kΩ trigger pulldown.

---

## 13. GPIO startup state showed `pn`, not internal pulldown

**Issue:** Later `pinctrl` output showed `ip pn | lo` rather than internal pulldown `pd`.

**Impact:** Could imply the Pi pin had no defined startup state.

**Resolution:** The GPIO lines still read LOW, and each driver has a verified external 10 kΩ pulldown.

**One-line solution:** Relied on the measured hardware pulldowns rather than the Pi’s internal pull configuration.

---

## 14. K3 had different wire colors from K1 and K2

**Issue:** K1 and K2 had red and black coil wires, while the Rincon K3 had two white wires and two black wires.

**Impact:** Coil wires and auxiliary-contact wires could have been confused.

**Resolution:** Measured the wire pairs: the white pair measured 26.2 Ω and was the coil; the black pair was the normally-open auxiliary contact.

**One-line solution:** Identified the K3 white pair as the coil by its 26.2 Ω resistance.

---

## 15. K3 coil polarity was not marked

**Issue:** Both K3 coil wires were white and had no visible polarity labels.

**Impact:** Flyback diode orientation could not be selected from wire color.

**Resolution:** Tested the coil in diode mode in both directions and observed equivalent readings, indicating no obvious internal polarity-sensitive suppression; assigned and labeled positive and negative leads.

**One-line solution:** Confirmed the coil was effectively nonpolarized, then assigned and labeled positive and negative leads.

---

## 16. Uncertainty whether a 1N5404 diode was suitable

**Issue:** The available diode was a 1N5404 rather than the originally suggested 1N4007.

**Impact:** Concern that the replacement diode might not safely handle coil flyback.

**Resolution:** Confirmed the 1N5404’s 3 A and 400 V ratings provided ample margin for the approximately 0.46 A, 12 V coils.

**One-line solution:** Used 1N5404 diodes as appropriately rated flyback protection.

---

## 17. In-circuit diode test appeared to show a short

**Issue:** Measuring the installed diode showed approximately 0.026 V and beeping in both directions.

**Impact:** Suggested the diode might be shorted or installed incorrectly.

**Root cause:** The diode was in parallel with the approximately 26 Ω coil, so the meter current took the coil path.

**Resolution:** Disconnected one diode leg and tested the diode independently.

**One-line solution:** Isolated one diode leg and verified approximately 0.500 V forward and `OL` reverse.

---

## 18. Diode location was initially confused with the auxiliary contact

**Issue:** There was a suggestion that the diode might go between an auxiliary-contact connection and power or ground.

**Impact:** The coil would remain unsuppressed, and the auxiliary circuit could be wired incorrectly.

**Resolution:** Clarified that the flyback diode must be directly across the coil, not across the auxiliary contact.

**One-line solution:** Installed each diode across `OUT+` and `OUT-`, band toward `OUT+`.

---

## 19. New wall outlet initially supplied no power to the 12 V board

**Issue:** No voltage was measured between `IN+` and `IN-`.

**Impact:** The contactor driver appeared nonfunctional.

**Root cause:** The wall-power source being used at the desk was not supplying power correctly.

**Resolution:** Moved to a working outlet and verified 12 V on all three boards.

**One-line solution:** Used a verified working outlet and rechecked 12 V directly at each driver input.

---

## 20. Only two clicks were heard during a three-contactor K3 test

**Issue:** The K3 exercise commanded K1, K2, and K3, but only two click clusters were audible.

**Impact:** Suggested one or more contactors might not be operating.

**Resolution:** Event timestamps showed the commands were separated by only tens of microseconds; a repeat test with a 3-second hold showed all three trigger LEDs illuminated simultaneously.

**One-line solution:** Verified all three boards visually because near-simultaneous contactor clicks merge acoustically.

---

## 21. Linux detected the scope, but PyVISA found no resources

**Issue:** `lsusb` showed the MSO-X 2014A, but PyVISA returned an empty tuple.

**Impact:** The software could not communicate with the scope.

**Root cause:** The USB device was owned by `root:root`, and the normal user lacked write permission.

**Resolution:** Installed and reloaded the provided udev rule, then reconnected the USB cable.

**One-line solution:** Applied the Keysight udev rule and obtained `root:plugdev` read/write permissions.

---

## 22. PyVISA printed TCP/IP discovery warnings

**Issue:** PyVISA warned that TCP/IP discovery lacked `psutil` and HiSLIP discovery lacked `zeroconf`.

**Impact:** The warnings appeared alarming during USB setup.

**Resolution:** Determined that the warnings only concerned network instruments and did not affect USBTMC.

**One-line solution:** Ignored TCP/IP discovery warnings because the scope uses USB.

---

## 23. Scope configuration initially appeared to hang

**Issue:** `scope_bench configure --real` did not return, and the scope display appeared to show a large black rectangle.

**Impact:** Suggested a scope or display failure.

**Resolution:** The black rectangle was only the waveform graph area; the stuck process was found from a second SSH session and terminated.

**One-line solution:** Used a second SSH session to identify and stop the stuck Python process.

---

## 24. Killing the configuration process stalled USBTMC

**Issue:** After terminating the process, later `*IDN?` operations timed out even after unplugging and reconnecting USB.

**Impact:** The scope remained inaccessible through PyVISA.

**Root cause:** The scope’s internal USBTMC state remained stuck after the interrupted transaction.

**Resolution:** Fully powered the scope off, waited, restarted it, and reconnected USB.

**One-line solution:** Power-cycled the scope because USB reconnection alone did not clear the internal USBTMC stall.

---

## 25. Real scope did not retain RAW mode

**Issue:** The software requested waveform mode `RAW`, but readback returned `MAX`.

**Impact:** Full-depth raw waveform capture could silently be replaced by a display-oriented mode.

**Root cause:** The scope was running when RAW mode was set, and setting `POINts MAXimum` after `POINts:MODE RAW` switched the mode back to `MAX`.

**Resolution:** Added `:STOP` before waveform configuration and reversed the command order.

**One-line solution:** Stopped acquisition, set points first, then set points mode to RAW.

---

## 26. Scope reported 500,000 points during bench configuration

**Issue:** Readback showed 500,000 points rather than the expected 1,000,000.

**Impact:** Raised concern about insufficient capture depth.

**Resolution:** Later completed real captures stored 1,000,000 sample bytes and a preamble reporting 1,000,000 points.

**One-line solution:** Verified actual completed real acquisitions rather than relying only on pre-acquisition point readback.

---

## 27. Scope reported `waveform_points: +0` after restart

**Issue:** Configuration readback after a fresh scope boot returned zero waveform points.

**Impact:** Suggested waveform memory was misconfigured.

**Resolution:** Determined that no completed acquisition record existed yet; all other waveform settings were correct.

**One-line solution:** Treated `+0` as no current acquisition record and verified real point count after capture.

---

## 28. Capture bench timed out without a trigger

**Issue:** The bench tool returned `acquired: false`, `armed: true`, and `reason: acquisition_timeout`.

**Impact:** Could be interpreted as a scope failure.

**Resolution:** Recognized that no electrical trigger was present, so the timeout was expected.

**One-line solution:** Accepted acquisition timeout as correct behavior when testing scope arming without an injected event.

---

## 29. Scope did not turn on after equipment reconnection

**Issue:** The scope appeared dead after moving and reconnecting the system.

**Impact:** Suggested a hardware failure.

**Root cause:** The power cable was not properly seated.

**Resolution:** Reseated the scope power connection.

**One-line solution:** Corrected the scope’s physical power connection.

---

## 30. Many `/dev/video*` devices made device selection ambiguous

**Issue:** The Pi exposed numerous codec and ISP video nodes in addition to `/dev/video0` and `/dev/video1`.

**Impact:** The hardcoded camera index could have selected the wrong device.

**Resolution:** Used `v4l2-ctl --list-devices` to confirm the C270 owns `/dev/video0` and `/dev/video1`, with `/dev/video0` as the usable capture stream.

**One-line solution:** Confirmed the C270 capture stream is `/dev/video0`.

---

## 31. `/dev/video1` could not be opened

**Issue:** OpenCV reported that camera index 1 was out of range.

**Impact:** Suggested part of the webcam interface might be broken.

**Resolution:** Determined `/dev/video1` is a secondary node rather than the usable video stream.

**One-line solution:** Used `/dev/video0` exclusively.

---

## 32. Initial OpenCV captures were entirely black

**Issue:** OpenCV successfully returned a 640 × 480 frame, but every pixel was zero.

**Impact:** Suggested camera or driver failure.

**Root cause:** The C270 required active streaming for exposure and gain to settle; sleeping without retrieving frames did not advance startup.

**Resolution:** Continuously read frames for several seconds before using the final image.

**One-line solution:** Warmed the C270 by actively reading frames instead of only sleeping after opening it.

---

## 33. Camera angle required several iterations

**Issue:** Initial images did not clearly frame the EVSE LED diffuser.

**Impact:** The classifier ROI would have contained incomplete LED geometry or unrelated background.

**Resolution:** Repositioned the camera repeatedly and saved an accepted fourth reference view.

**One-line solution:** Iterated the camera angle until the full LED diffuser was clearly visible and stable.

---

## 34. Default center ROI cut off the LED

**Issue:** The generated center ROI cut off much of the diffuser’s left and lower sides.

**Impact:** Color classification could miss significant LED regions.

**Resolution:** Replaced it with `x=35`, `y=120`, `width=450`, `height=350` and visually verified the overlay.

**One-line solution:** Expanded and shifted the ROI to enclose the entire diffuser.

---

## 35. Auto white balance could not be disabled

**Issue:** Commands to set `white_balance_automatic=0` appeared to succeed but immediately read back as `1`.

**Impact:** White balance could drift with ambient lighting.

**Resolution:** Accepted automatic white balance, fixed camera position and lighting, and relied on real calibration images.

**One-line solution:** Kept auto white balance enabled and controlled the environment instead.

---

## 36. Manual exposure reset after webcam reconnection

**Issue:** After USB or power reconnection, the C270 returned to aperture-priority exposure 156.

**Impact:** Calibration and runtime image appearance changed between sessions.

**Resolution:** Restored manual exposure after each reconnect.

**One-line solution:** Reapply `auto_exposure=1, exposure_time_absolute=30` whenever the webcam reconnects.

---

## 37. Exposure 80 heavily clipped the illuminated LED

**Issue:** Green frames at exposure 80 clipped roughly 20% to 30% of image pixels.

**Impact:** Saturation could erase hue information and make colors harder to distinguish.

**Resolution:** Reduced exposure to 30 and repeated the K1/K2 boot recording.

**One-line solution:** Lowered manual exposure from 80 to 30.

---

## 38. First off-state concern was a misunderstanding

**Issue:** The operator briefly thought the LED might have been flashing red during the off capture.

**Impact:** Would have invalidated the off calibration set.

**Resolution:** Clarified that “LED” had been misread as “red” and confirmed the LED was actually off.

**One-line solution:** Confirmed the 15 off-state frames were valid.

---

## 39. `propose-hsv` was run on a full mixed-state recording

**Issue:** The tool processed boot, green, dark, blue, and red frames together.

**Impact:** It proposed a huge, invalid green hue range spanning much of the color wheel.

**Resolution:** Stopped the command and separated state-specific frames before analysis.

**One-line solution:** Separated state-specific frames before proposing or verifying HSV ranges.

---

## 40. Initial green ranking command had syntax errors

**Issue:** A compressed single-line Python command became malformed.

**Impact:** The scan did not run.

**Resolution:** Replaced it with a readable multiline Python block.

**One-line solution:** Used a multiline script instead of a dense one-liner.

---

## 41. Fifteen green frames were not enough for temporal stability

**Issue:** Fifteen visually green frames produced `stable_color: null`.

**Impact:** It appeared the classifier could not recognize green.

**Root cause:** The classifier uses approximately a 3-second, 45-frame temporal window plus agreement frames.

**Resolution:** Built a larger dataset.

**One-line solution:** Supplied more than the classifier’s required temporal-window frame count.

---

## 42. A continuous “green” range verified as booting

**Issue:** Continuous frame ranges included blue, red, and green boot frames.

**Impact:** The verifier correctly returned `stable_color: booting`.

**Resolution:** Scanned every frame by color fraction and copied only green-only frames.

**One-line solution:** Filtered individual frames instead of copying continuous ranges from the multicolor boot sequence.

---

## 43. Scattered green frames were mistaken for one continuous interval

**Issue:** The first and last 30 green-only filenames were scattered through the recording, but continuous ranges were copied for verification.

**Impact:** Those ranges contained non-green frames and verified as booting.

**Resolution:** Built `calib/green_filtered` using per-frame hue tests.

**One-line solution:** Copied only frames that passed green, blue, and red fraction thresholds.

---

## 44. Green confidence looked low at 0.323

**Issue:** The confidence value appeared to mean only a 32.3% chance of green.

**Impact:** Raised concern that calibration was unreliable.

**Resolution:** Clarified that confidence is a signal-margin score, not a probability; the state matched green across 161 frames.

**One-line solution:** Judged calibration by stable matched state and dataset consistency, not by interpreting confidence as probability.

---

## 45. Full blinking-green recording verified as off

**Issue:** The verifier was run against the entire recording, including boot, dark blink phases, and shutdown.

**Impact:** The final temporal window ended off, producing `stable_color: off`.

**Resolution:** Extracted state-specific illuminated frames.

**One-line solution:** Verified a green-only folder rather than the entire mixed recording.

---

## 46. Blue and red calibration initially came from boot

**Issue:** Blue and red calibration images were extracted from the multicolor boot sequence rather than final operational states.

**Impact:** They validate hue recognition but not final ready-state blue or post-trip fault-red behavior.

**Resolution:** Preserved them as baseline calibration data and planned real-state confirmation during later cycles.

**One-line solution:** Kept boot-derived blue/red as baseline and deferred real-state confirmation to later cycles.

---

## 47. Simulated camera caused a real-GPIO cycle to wait 90 seconds and halt

**Issue:** A GPIO-real, scope-sim, camera-sim campaign halted with `vision_gate_timeout_led_off_or_unknown` even though the simulator logically reached charging.

**Root cause:** The sequencer used the real optical classifier, while CameraSim supplied tiny 1 × 1 dark image bytes.

**Resolution:** Added a regression test through the real optical gate and replaced the simulator fixtures with classifier-compatible 16 × 16 BGR frames.

**One-line solution:** Made CameraSim’s image fixtures match the real classifier’s input requirements.

---

## 48. Existing camera simulator test covered the wrong path

**Issue:** The original test called `CameraSim.await_charging_gate()` directly, but the sequencer uses `classify.await_charging_gate()`.

**Impact:** Tests passed despite the integration path failing.

**Resolution:** Added a regression test through `classify.await_charging_gate()`.

**One-line solution:** Tested the same camera path actually used by the sequencer.

---

## 49. First CameraSim fix introduced a circular import

**Issue:** `camera_sim.py` imported `LedColor` and `make_led_frame` from `classify.py`, while `classify.py` indirectly imported the HAL package.

**Impact:** Test imports failed with a partially initialized module error.

**Resolution:** Removed the cross-layer import and generated small BGR fixtures locally in `camera_sim.py`.

**One-line solution:** Avoided the circular dependency by generating local BGR test frames.

---

## 50. Several manual fixture edits became syntactically malformed

**Issue:** Text replacements created lines such as `listdef fixture`.

**Impact:** `camera_sim.py` failed to compile.

**Resolution:** Restored a known-good backup and applied smaller scripted replacements.

**One-line solution:** Restored the file and reapplied the change with controlled scripted edits.

---

## 51. `roi.json` was not used by the real sequencer

**Issue:** Calibration produced a correct ROI file, but runtime configuration contained no camera or vision section.

**Impact:** The real camera gate continued using the default center ROI.

**Resolution:** Added `VisionConfig`, strict YAML validation, hashing, and sequencer ROI injection.

**One-line solution:** Added the calibrated ROI to hashed runtime configuration and passed it into `await_charging_gate()`.

---

## 52. Vision ROI initially was not included in the configuration hash

**Issue:** After adding vision configuration, the canonical hash remained identical to the old hash.

**Impact:** ROI changes would not invalidate or distinguish campaign configurations.

**Resolution:** Added `vision` to `_canonical_for_hash()`.

**One-line solution:** Included `raw["vision"]` in canonical hash serialization.

---

## 53. New required vision section broke temporary test configs

**Issue:** Config and main tests constructed YAML without `vision:`.

**Impact:** Tests failed with `'vision' must be a mapping`.

**Resolution:** Added test ROI sections to all temporary valid configs.

**One-line solution:** Updated test fixtures to include valid vision mappings.

---

## 54. Tabs broke YAML and Python indentation

**Issue:** Several Nano edits inserted tabs into Python and YAML test fixtures.

**Impact:** Produced `TabError` and YAML parser errors.

**Resolution:** Used `sed -n ...l` to reveal tabs and replaced them with spaces.

**One-line solution:** Detected hidden tabs and normalized indentation to spaces.

---

## 55. Real ROI broke 8 × 8 sequencer test frames

**Issue:** Production ROI `(35, 120, 450, 350)` lay entirely outside the synthetic 8 × 8 test images.

**Impact:** Test frames became unusable and camera logic degraded to unavailable.

**Resolution:** Added an 8 × 8 `VisionConfig` only in sequencer tests.

**One-line solution:** Used a test-specific ROI matching the synthetic frame dimensions.

---

## 56. First real attempt detected pre-trigger current

**Issue:** A real capture halted with `k3_pretrigger_current_detected`.

**Impact:** The numerical result could not be accepted.

**Resolution:** Preserved the run and investigated the waveform rather than rerunning blindly.

**One-line solution:** Treated the sanity halt as a rig fault and retained the artifacts for analysis.

---

## 57. One real cycle never triggered

**Issue:** The scope armed, K3 was commanded, but no trigger arrived within 5 seconds.

**Impact:** No waveform transfer or cycle result was produced.

**Resolution:** Isolated and tested K3’s coil and driver, confirming K3 itself opens and closes correctly; injection and measurement paths remained separate diagnostic targets.

**One-line solution:** Separated K3 mechanical actuation from injection-path and scope-trigger diagnostics.

---

## 58. Numerical PASS coexisted with a rig halt

**Issue:** `full_real_cycle_004` recorded a 20.1676 ms PASS but also halted with `k3_pretrigger_current_detected`.

**Impact:** `pass_count: 1` could be misread as a valid accepted cycle.

**Resolution:** Distinguished numerical verdict from waveform-sanity validity.

**One-line solution:** Do not accept PASS when any required sanity check or rig halt fails.

---

## 59. Analyzer placed onset at the first sample

**Issue:** Analysis reported `t0_s=-0.020000065` with `t0_source=detected_onset`, which is the first sample in the record.

**Impact:** Trip time and pre-trigger status were calculated from the wrong onset.

**Evidence:** Raw data showed a quiet interval from approximately −20 ms to −15 ms before the burst.

**One-line solution:** Add a real-waveform regression test and correct burst-onset refinement so onset cannot jump to the record boundary.

---

## 60. False pre-trigger leakage from a forward-looking envelope

**Issue:** `_pretrigger_leakage_ok()` checks `envelope_lead[0]`, but the leading envelope looks forward into later samples.

**Impact:** Later fault energy contaminated the first envelope value and produced a false stuck-K3 indication.

**One-line solution:** Base stuck-K3 detection on genuinely earlier raw or trailing data, not a forward-looking envelope at the first sample.

---

## 61. K3 remained commanded closed for the full scope timeout

**Issue:** In a no-trigger real cycle, K3 remained commanded closed for approximately 5.016 seconds despite `k3_backstop_s: 0.300`.

**Impact:** The configured 300 ms software backstop did not work during a blocking scope wait.

**Root cause:** The real scope call blocked internally for the full acquisition timeout, preventing the outer loop from checking the K3 deadline.

**One-line solution:** Poll the scope using short time slices so the sequencer can recheck and enforce the K3 deadline.

---

## 62. Original tests did not verify K3 duration

**Issue:** The existing never-triggered test checked only that the terminal state became `RIG_FAULT`.

**Impact:** The test passed even though K3 could remain closed too long.

**Resolution:** Added a blocking scope fake and measured the interval between `close_k3` and the first later `open_k3`.

**One-line solution:** Added a regression assertion on actual simulated K3 commanded duration.

---

## 63. Initial backstop test selected the wrong `open_k3` event

**Issue:** Initial `safe_off()` creates an `open_k3` event before `close_k3`, and the first test selected that event.

**Impact:** It calculated a negative duration and passed incorrectly.

**Resolution:** Selected the first `open_k3` event whose timestamp was at or after `close_k3`.

**One-line solution:** Filtered the event search by timestamp relative to `close_k3`.

---

## 64. A 50 ms poll interval was still too slow

**Issue:** Reducing the blocking scope call to 50 ms still produced approximately 360 ms K3 duration.

**Root cause:** Each 50 ms scope call was followed by an additional 10 ms outer sleep.

**Resolution:** Reduced each blocking scope poll to 10 ms.

**One-line solution:** Changed acquisition polling slices from 50 ms to 10 ms.

---

## 65. Current backstop fix was not fully validated at the stopping point

**Issue:** The focused regression test passed after changing the poll interval to 10 ms, but the complete sequencer and full project suites had not yet been rerun after the final edit.

**Impact:** The change was not ready to commit or use on energized hardware at that stopping point.

**One-line solution:** Complete regression validation and commit the 10 ms polling fix before another real cycle.

---

## 66. Markdown backticks were accidentally pasted into Bash

**Issue:** Lines containing Markdown backticks were entered into the terminal.

**Impact:** Bash printed harmless `command not found` errors.

**Resolution:** Ignored the formatting characters and reran only the command itself.

**One-line solution:** Paste command contents without Markdown fences.

---

## 67. Placeholder `NEW_PID` was entered literally

**Issue:** `ps -p NEW_PID` was run without replacing the placeholder.

**Impact:** `ps` reported invalid process ID syntax.

**Resolution:** Used `jobs -l` or `pgrep -af` to find the actual PID.

**One-line solution:** Replace placeholders with real values before running commands.

---

## 68. `scp` was run inside the Pi SSH session

**Issue:** A Windows-style destination path and `start` command were entered in Bash on the Pi.

**Impact:** The image copied to the Pi rather than Windows, and `start` was unavailable.

**Resolution:** Opened a separate Windows PowerShell window and ran `scp` from there.

**One-line solution:** Run image-download and `start` commands from Windows PowerShell, not the Pi shell.

---

## 69. `git diff` opened in the `less` pager

**Issue:** The terminal showed `END`, which appeared to be a stuck state.

**Impact:** The normal prompt was not visible.

**Resolution:** Pressed `q` to exit the pager.

**One-line solution:** Use `q` to leave `less`.

---

## 70. Several compressed Python one-liners became malformed

**Issue:** Long one-line scripts were corrupted during copying, causing syntax errors.

**Impact:** Analysis commands failed and caused confusion.

**Resolution:** Replaced them with multiline heredoc scripts.

**One-line solution:** Use readable multiline Python blocks for nontrivial analysis.

---

## 71. A contact-sheet script failed with a syntax error

**Issue:** The row-building expression was malformed.

**Impact:** No contact sheet was created.

**Resolution:** Confirmed Python failed before execution and extracted representative frames individually instead.

**One-line solution:** Used individual frame copying rather than retrying the malformed contact-sheet expression.

---

# Closing note

This document intentionally records both technical defects and process-level problems. The issue history is useful because several problems that initially appeared to be hardware failures were actually caused by permissions, configuration order, camera startup behavior, mixed calibration datasets, test-fixture mismatches, or blocking software calls. Preserving the complete history should prevent future debugging sessions from repeating the same investigations.
