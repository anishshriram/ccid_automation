# CCID Automation Preflight and Run Checklist

## Purpose

Use this checklist before every real-hardware CCID campaign. It covers:

- Supervised runs started from SSH
- Autonomous runs started with `systemd-run`
- Camera, scope, contactor, storage, network, and software checks
- Post-run verification
- Common troubleshooting steps

> **Safety boundary:** Software checks do not replace qualified electrical inspection, protective-earth verification, restricted access, correct probe grounding, flyback protection, or an accessible emergency disconnect.

---

# 1. Choose the Run Type

## A. Supervised SSH run

Use for:

- First cycle after hardware changes
- Scope or camera recommissioning
- New analysis-version validation
- Short debugging campaigns

The SSH terminal must remain open unless the run is started inside `systemd` or `tmux`.

## B. Autonomous systemd run

Use only after:

- A supervised real-hardware run has passed
- The current code passed the full test suite
- Camera and scope preflight passed
- The service environment is configured
- A short real `systemd` campaign has already passed

A `systemd-run` campaign continues if SSH disconnects.

---

# 2. Physical Safety Preflight

Complete with EVSE mains and all three 12 V contactor supplies off.

- [ ] Qualified person inspected the setup
- [ ] Protective-earth connections are intact
- [ ] K1 is wired as the L1 mains contactor
- [ ] K2 is wired as the L2 mains contactor
- [ ] K3 is wired only as the leakage-injection contactor
- [ ] K1, K2, and K3 driver channels are labeled correctly
- [ ] Flyback diodes remain connected across all three coils
- [ ] Probe tip is on the approved measurement node
- [ ] Scope reference is connected only to the approved reference point
- [ ] Probe attenuation switch is set to 10x
- [ ] Camera is mechanically secure and aimed at the EVSE LED
- [ ] No loose conductors, damaged insulation, or mounting damage is visible
- [ ] Emergency disconnect is accessible
- [ ] Test area is restricted during energized operation

If the rig was moved, drilled, remounted, rewired, or disconnected, repeat the complete recommissioning sequence before a multi-cycle campaign.

---

# 3. Pi and Repository Preflight

SSH to the Pi and enter the project:

```bash
cd ~/ccid_automation
source venv/bin/activate
```

Check repository state:

```bash
git status --short
git log -3 --oneline --decorate
```

Expected:

- Local `development` matches `origin/development`
- Only known temporary files, such as `camera_gate_check.yaml`, are untracked
- No unexplained modified source files

Check for an active campaign:

```bash
pgrep -af "python.*ccid.main" || echo "No active CCID process"
```

Do not start another campaign if a CCID process is active.

Check disk space:

```bash
df -h "$HOME"
```

Required:

- More than 2 GB free
- For long campaigns, confirm projected artifact storage plus backup margin

---

# 4. Software Test Gate

Use a non-`/tmp` temporary directory:

```bash
mkdir -p ~/ccid_test_tmp
TMPDIR="$HOME/ccid_test_tmp" env -u CCID_SCOPE_RESOURCE python -m unittest discover -s tests -p 'test_*.py'
```

Required:

- [ ] Test suite reports `OK`
- [ ] Only known intentional skips appear
- [ ] No disk-space, analysis, recorder, scope, camera, or sequencer failures

Do not proceed with energized hardware after a failing test suite.

---

# 5. Analysis-Version Gate

Check the configured analysis version:

```bash
grep -n "algorithm_version" config.yaml
```

Required for current campaigns:

```text
algorithm_version: v3
```

When the analysis algorithm changes:

- [ ] Use a new explicit version
- [ ] Do not overwrite historical V1 or V2 results
- [ ] Replay archived waveforms under the new version
- [ ] Validate the version on a fresh short real-hardware campaign

---

# 6. Camera Preflight

Confirm camera enumeration:

```bash
v4l2-ctl --list-devices
```

Required:

- Logitech C270 appears at `/dev/video0`

Set the validated exposure:

```bash
v4l2-ctl --device=/dev/video0 --set-ctrl=auto_exposure=1,exposure_time_absolute=60
```

Verify exposure and format:

```bash
v4l2-ctl --device=/dev/video0 --get-ctrl=auto_exposure,exposure_time_absolute
v4l2-ctl --device=/dev/video0 --get-fmt-video
```

Expected:

- Manual exposure mode
- Exposure absolute = 60
- 640 x 480
- YUYV format

Capture a warmed frame if the camera was moved:

```bash
timeout 10s ffmpeg -hide_banner -loglevel error -y -f v4l2 -video_size 640x480 -input_format yuyv422 -i /dev/video0 -ss 3 -frames:v 1 -update 1 ~/camera_preflight_warmed.jpg
```

Review the image:

- [ ] Entire EVSE LED area is visible
- [ ] LED is near the center of the frame
- [ ] LED is inside the configured ROI
- [ ] Image is not black
- [ ] Image is not severely blurred
- [ ] Exposure does not wash out the green LED

## Camera-only classifier validation after camera movement

1. Physically disable K3's 12 V coil supply.
2. Use real K1/K2, real camera, and simulated scope through `camera_gate_check.yaml`.
3. Confirm the gate waits through boot and grants only on sustained flashing green.
4. De-energize the rig.
5. Restore K3's coil supply.

The resulting simulated PASS is camera evidence only, not an electrical trip result.

---

# 7. Contactor Preflight After Wiring or Mounting Changes

Keep EVSE mains off. Power only the 12 V coil supplies.

Check K1:

```bash
python -m tools.gpio_selftest exercise --contactor K1 --pulses 1 --hold-s 0.5 --cooldown-s 1 --real --i-understand-this-energizes-hardware
```

Check K2:

```bash
python -m tools.gpio_selftest exercise --contactor K2 --pulses 1 --hold-s 0.5 --cooldown-s 1 --real --i-understand-this-energizes-hardware
```

Check K3 with a short pulse:

```bash
python -m tools.gpio_selftest exercise --contactor K3 --pulses 1 --hold-s 0.2 --cooldown-s 1 --real --i-understand-this-energizes-hardware
```

Required:

- [ ] Each contactor pulls in once
- [ ] Each contactor releases cleanly
- [ ] No buzzing or sticking
- [ ] Final safe-off completes

The current software proves commanded state only. Auxiliary-contact GPIO feedback is still deferred.

---

# 8. Oscilloscope Preflight

Confirm USB enumeration:

```bash
lsusb | grep -i "0957:1798"
```

Expected:

```text
0957:1798 Agilent Technologies, Inc. MSO-X 2014A
```

If no result appears:

1. Confirm the scope is fully powered.
2. Reseat the USB cable.
3. Try a known-good USB data cable.
4. Try another Pi USB port.
5. Re-run `lsusb`.

Set the scope environment variable for the SSH session:

```bash
export CCID_SCOPE_RESOURCE="USB0::2391::6040::MY58100795::0::INSTR"
```

Identify the scope:

```bash
timeout 15s python -m tools.scope_bench identify --real
```

Expected identity:

- Model: MSO-X 2014A
- Serial: MY58100795

Clear stale errors:

```bash
timeout 10s python -c 'import os,pyvisa; rm=pyvisa.ResourceManager("@py"); s=rm.open_resource(os.environ["CCID_SCOPE_RESOURCE"]); [print(s.query(":SYSTem:ERRor?").strip()) for _ in range(5)]; s.close(); rm.close()'
```

All final responses must be:

```text
+0,"No error"
```

Configure the scope:

```bash
timeout 30s python -m tools.scope_bench configure --real
```

Expected critical settings:

- CH1 source
- +20 V positive edge trigger
- 50 ms/div
- Center reference
- RAW waveform mode
- BYTE waveform format
- Configuration completes without a `ScopeConfigurationError`

## Probe compensation after hardware changes

- [ ] Connect the same CH1 probe to the scope compensation output
- [ ] Confirm a stable square wave
- [ ] Restore the probe to the approved measurement node
- [ ] Re-run automated scope configuration afterward

---

# 9. Network Preflight for Autonomous Runs

For Verizon USB tethering, verify `usb0`:

```bash
ip -brief address show usb0
```

Expected deployed address has previously been:

```text
192.168.1.121/24
```

Verify internet routing:

```bash
ip route get 8.8.8.8
```

Expected:

- Route uses `dev usb0`
- Gateway is the Verizon hotspot

Verify internet access:

```bash
ping -c 3 8.8.8.8
```

Verify remote access from the operator computer:

```text
ssh ccid@192.168.1.121
```

`usb0` is the network tether. `/dev/video0` is the camera. They are unrelated.

---

# 10. Monitoring Preflight

Monitoring is optional for short supervised tests but required before a long unattended campaign.

Current note:

- The previous Cronitor monitor was deleted.
- Cronitor may auto-create a monitor when a configured heartbeat URL sends its first event.
- Monitoring lifecycle, pause/resume, schedule, grace, and alert behavior must be revalidated before a long run.

Verify the persistent environment without printing secrets:

```bash
sudo sed -E 's/=.*/=<configured>/' /etc/default/ccid-automation
```

Expected variables when Cronitor is enabled:

```text
CCID_SCOPE_RESOURCE=<configured>
CCID_CRONITOR_URL=<configured>
```

Before a long unattended run:

- [ ] Create a dedicated final monitor key, such as `ccid-endurance-6000`
- [ ] Verify a normal heartbeat
- [ ] Verify a failure heartbeat
- [ ] Verify recovery behavior
- [ ] Configure expected interval and grace
- [ ] Attach the correct alert recipients
- [ ] Disable notification on every successful heartbeat
- [ ] Define how the monitor is paused when no campaign is expected

Do not place private URLs or API keys in Git.

---

# 11. Supervised SSH Run Procedure

Use this only when the SSH session will remain open.

Set environment and camera exposure:

```bash
cd ~/ccid_automation
source venv/bin/activate
export CCID_SCOPE_RESOURCE="USB0::2391::6040::MY58100795::0::INSTR"
v4l2-ctl --device=/dev/video0 --set-ctrl=auto_exposure=1,exposure_time_absolute=60
```

Create a fresh run ID and start the requested target:

```bash
RUN_ID="real_v3_supervised_$(date -u +%Y%m%dT%H%M%SZ)"
echo "RUN_ID=$RUN_ID"
python -m ccid.main --config config.yaml start --target-cycles 5 --run-id "$RUN_ID"
```

Rules:

- [ ] Never reuse a run ID
- [ ] Do not press scope or camera controls during the campaign
- [ ] Do not resume automatically after a halt
- [ ] Use the emergency disconnect for unexpected hardware behavior
- [ ] Fully de-energize before reviewing artifacts

---

# 12. Autonomous systemd Run Procedure

Use only after all preflight gates pass.

The tested transient service pattern is:

```bash
RUN_ID="real_v3_systemd_$(date -u +%Y%m%dT%H%M%SZ)" && echo "RUN_ID=$RUN_ID" && sudo systemd-run --unit=ccid-real-v3-campaign --uid=ccid --gid=ccid --working-directory=/home/ccid/ccid_automation --property=EnvironmentFile=/etc/default/ccid-automation --setenv=TMPDIR=/home/ccid/ccid_test_tmp --collect /home/ccid/ccid_automation/venv/bin/python -m ccid.main --config config.yaml start --target-cycles 10 --run-id "$RUN_ID"
```

Change only:

- Run ID prefix
- `--target-cycles`
- Unit name, if needed

Confirm active status before disconnecting:

```bash
systemctl is-active ccid-real-v3-campaign.service
```

Expected:

```text
active
```

Safely disconnect SSH:

```bash
exit
```

Do not use `Ctrl+C` to disconnect.

## Check progress after reconnecting

```bash
journalctl -u ccid-real-v3-campaign.service --no-pager -n 50
```

## Check whether still active

```bash
systemctl is-active ccid-real-v3-campaign.service
```

## Completed transient unit is missing

If `systemctl status` says the unit cannot be found after completion, this is expected with `--collect`.

Use:

```bash
journalctl -u ccid-real-v3-campaign.service --no-pager -n 50
```

and inspect the run directory.

---

# 13. Post-Run Verification

Fully de-energize mains and all three 12 V supplies before reviewing results.

Check run status:

```bash
python -m ccid.main --config config.yaml status --run-id RUN_ID
```

Verify:

- [ ] `last_completed_cycle` matches target or the expected halt point
- [ ] `target_cycles` is correct
- [ ] `halt_reason` is null for a completed campaign
- [ ] PASS and FAIL counts are plausible

Review the CSV:

```bash
cat runs/RUN_ID/cycles.csv
```

For every accepted PASS:

- [ ] Trip time is greater than zero
- [ ] `t_end >= t0`
- [ ] Analysis version is V3
- [ ] All waveform sanity checks are true
- [ ] Camera state is CHARGING
- [ ] No unexplained degraded flags

List artifacts:

```bash
find runs/RUN_ID -maxdepth 3 -type f -printf '%p %s bytes\n' | sort
```

Expected per completed cycle:

- `cycles/<n>.json`
- `waveforms/<n>.npz`
- `images/<n>_scope.png`
- `images/<n>_green.jpg`

Also expected:

- `config.yaml`
- `cycles.csv`
- `runstate.json`

Do not delete invalid or failed records. Preserve them for replay and debugging.

---

# 14. Common Troubleshooting

## Camera remains BOOTING while LED flashes green

Check:

```bash
v4l2-ctl --list-devices
v4l2-ctl --device=/dev/video0 --get-fmt-video
v4l2-ctl --device=/dev/video0 --get-ctrl=auto_exposure,exposure_time_absolute
```

Then capture a warmed image and inspect framing. Reposition the camera before changing classifier thresholds.

## Camera frame is black

Cause:

- First frame captured before C270 warm-up

Fix:

- Capture after a three-second warmed stream

## Scope not found

Check:

```bash
lsusb | grep -i "0957:1798"
```

If absent:

- Reseat USB
- Replace cable
- Try another port
- Confirm scope power

Do not debug VISA until `lsusb` sees the scope.

## Scope commands time out after an interrupted transfer

- Stop further retries
- Keep rig de-energized
- Disconnect scope USB
- Remove scope AC power for 60 seconds
- Boot fully
- Reconnect USB
- Verify `*IDN?`

## Scope configure reports `Query INTERRUPTED` or `Query UNTERMINATED`

- Drain the stale error queue
- Retry clean configuration once
- Do not ignore newly generated configuration errors

## Scope screenshot is zero bytes

- Scalar diagnostics may still be valid
- Treat the screenshot as unavailable
- Do not interpret an empty PNG

## Trip time is `0.0`

- Do not accept PASS
- Inspect `t0`, `t_end`, and all sanity checks
- Preserve waveform for replay
- Treat first-cycle zero-time behavior as unresolved until fixed

## `collapse_is_clean` is false

- Do not accept numerical PASS
- Preserve the raw waveform
- Replay under the configured analysis version
- Investigate endpoint extraction or waveform shape

## A transient systemd unit “could not be found”

- With `--collect`, completed units are removed
- Use `journalctl -u UNIT_NAME`
- Inspect the run directory and `runstate.json`

## Systemd says failed after a safe CCID halt

- Read the application halt reason in the journal
- Do not infer hardware danger from the generic systemd status alone
- Do not auto-resume

## Cronitor monitor reappears after deletion

- The configured heartbeat URL auto-provisions the monitor again
- Remove or change `CCID_CRONITOR_URL` only when no campaign is active

---

# 15. Safe Shutdown

After the campaign finishes and all hardware supplies are off:

```bash
sudo poweroff
```

Wait at least 20 seconds and until Pi activity-light flashing stops before removing Pi power.

Campaign data is saved before shutdown if the run reached its commit points. Maintain an off-Pi backup for important runs.

---

# 16. Long-Campaign Gate

Before a 150-cycle or 6,000-cycle campaign, require all of the following:

- [ ] Current code committed and synchronized
- [ ] Full suite passes
- [ ] Analysis V3 selected
- [ ] Archived V2 campaign replayed under V3 where required
- [ ] First-cycle zero-time behavior resolved or automatically rejected
- [ ] Camera framing and classifier validated
- [ ] Contactors checked after hardware changes
- [ ] Scope USB cable and port stable
- [ ] Scope error queue clean
- [ ] Real supervised validation campaign passed
- [ ] Real systemd campaign passed without SSH
- [ ] Network route validated
- [ ] External monitoring recreated and tested
- [ ] Monitoring pause/resume lifecycle defined
- [ ] Storage projection checked
- [ ] Off-Pi backup process ready
- [ ] Watchdog, reboot, and sticky-halt resume tested
- [ ] Auxiliary-contact feedback decision revisited
- [ ] Campaign-level acceptance criteria defined
- [ ] Operator understands how to inspect journal, run state, and artifacts

Do not authorize the long campaign by time pressure alone. Every gate should have explicit evidence.
