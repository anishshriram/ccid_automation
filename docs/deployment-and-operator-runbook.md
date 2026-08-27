# Deployment, Pi Bring-Up, and Operator Runbook

**Supersedes:** `DEPLOYMENT.txt`, `PI_SETUP_AND_TEST_PLAN.md`, `Download the CCID Operator Preflight and Runbook.md` (three previously separate, occasionally-conflicting documents — see §0 for what was inconsistent and how it was resolved).
**Also relevant:** `deploy/ccid-automation.service`, `deploy/99-keysight-usbtmc.rules`, `deploy/99-c270-camera.rules`, `config.yaml`, `requirements.txt`.

This is the operational counterpart to the rest of the technical reference: how to take a freshly flashed Raspberry Pi all the way to a monitored real CCID campaign. Read §1 once per Pi, §2 once per significant code change (or before first real-hardware use), and §3 before every real-hardware campaign.

> **Safety boundary, stated once, applies throughout:** software checks do not replace qualified electrical inspection, protective-earth verification, restricted access, correct probe grounding, or an accessible emergency disconnect. **The as-built rig currently has no flyback diodes and no MOSFET gate pulldown resistors on any of the three contactor driver boards** (§1.6) — a real, unmitigated hardware gap, not something a preflight checklist can substitute for.

---

## 0. What was inconsistent across the three source documents, and what this document decides

- **Persistent systemd service vs. transient `systemd-run` — resolved in favor of transient, because that's what's actually been tested.** `DEPLOYMENT.txt` and `deploy/ccid-automation.service` describe a persistent, `enable`d service (`Restart=on-failure`, `WantedBy=multi-user.target`, auto-resumes across reboots forever). Every real validated campaign to date has instead used a transient `systemd-run` unit (`docs/build-and-commissioning-issue-log.md` §6 confirms directly: "the persistent, `enable`d service... has never actually been exercised against the real deployment path"). This document documents **transient `systemd-run`** as the primary, tested pattern (§3.12) and keeps the persistent-service path as a documented-but-unvalidated alternative (§3.13). If the persistent path is ever adopted for real, note that `deploy/ccid-automation.service` as committed points at `/opt/ccid` while every tested real campaign has run from `/home/ccid/ccid_automation` — that unit file's paths need to be corrected to match wherever the Pi is actually deployed before it's trustworthy, not just enabled as-is.
- **VISA resource string — a real arithmetic error corrected.** The old setup plan gave `0x1798` as decimal `6296`; that's simply wrong. `0x1798` = **6040** decimal. Every worked example in this document uses the confirmed-correct `USB0::2391::6040::MY58100795::0::INSTR` — but see §1.7's actual instruction: always use the exact string your own Pi's `list_resources()` call prints, never a copied worked example, since the serial number is specific to one physical instrument.
- **Camera exposure — updated to the current validated value.** `exposure_time_absolute` moved from an earlier `30` to the current `60` after a rig remount changed ambient lighting enough that `30` came out dark. Every instruction below uses `60`.
- **Camera `device_index` — no longer a code gap.** The old setup plan described `CameraReal` as hardcoded to `device_index=0` with "no existing config knob" and framed it as something that would need a code change. That's stale: `config.yaml`'s `camera.device_index` already accepts either a raw index or a stable device path, and the current deployment uses `/dev/ccid_camera` — a udev-created symlink keyed to the camera's USB serial (`deploy/99-c270-camera.rules`), immune to the `/dev/videoN` re-enumeration that caused a real mid-campaign incident (`docs/build-and-commissioning-issue-log.md` §8).
- **Monitoring service — Cronitor throughout, no remaining `healthchecks.io` references** (the swap happened after the original `DEPLOYMENT.txt` text was written for some earlier drafts of these files; this document only ever refers to Cronitor).
- **Test suite count** — current is **364 tests, 2 intentional skips** (`docs/test-suite-guide.md`), not the 229/269 figures the old source files variously gave.
- **Fresh-Pi bring-up steps are preserved in full** (§1 below) — this was the one piece of genuinely non-superseded content in the old `DEPLOYMENT.txt`/`PI_SETUP_AND_TEST_PLAN.md`; nothing in `docs/` (by design — that's code reference, not ops) or the old runbook covered it.

---

## 1. Fresh Raspberry Pi bring-up (once per Pi)

Assumes Raspberry Pi OS (Bookworm or newer, 64-bit recommended) already flashed and booted, with keyboard/monitor or SSH access.

### 1.1 System update and base packages

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv python3-pip python3-dev build-essential
```

`opencv-python-headless` ships as a wheel but occasionally needs a few system shared libraries on Raspberry Pi OS Lite. If `pip install` later fails with a missing `.so` error:

```bash
sudo apt install -y libatlas-base-dev libjpeg-dev libopenjp2-7 libtiff6 ffmpeg
```

Raspberry Pi OS's pip is pre-configured to pull pre-built ARM wheels from piwheels.org, so `numpy`/`opencv-python-headless` shouldn't need to compile from source. Confirm Python is 3.11+:

```bash
python3 --version
```

### 1.2 Enable interfaces (`raspi-config`)

```bash
sudo raspi-config
```

- **Interface Options → SSH**: enable if working headless.
- **Camera**: only relevant for the Raspberry Pi CSI camera module. The real camera driver (`ccid/hal/camera_real.py`) uses `cv2.VideoCapture(device_index)`, which expects a standard V4L2 `/dev/videoN` device. A USB webcam (the currently-deployed hardware, a Logitech C270) works out of the box with no extra configuration. A CSI module needs `libcamera` bridging (e.g. `rpicam-vid` piped through `v4l2loopback`) to appear as V4L2 at all — prefer a USB webcam if you have the choice.

Reboot if anything changed: `sudo reboot`.

### 1.3 Service account and target layout

For quick iteration you can run everything as your own login user first (§2) and switch to a dedicated service account later, right before setting up systemd. **The tested, currently-deployed layout is `/home/ccid/ccid_automation`** — use that unless you have a specific reason to deviate (see §0's note on `/opt/ccid` never having been exercised):

```bash
sudo useradd --system --create-home --home-dir /home/ccid --shell /usr/sbin/nologin ccid
sudo mkdir -p /home/ccid/ccid_automation
sudo chown ccid:ccid /home/ccid/ccid_automation
```

### 1.4 Group membership and udev rules (scope, camera, GPIO)

The Pi needs three kinds of device access. Do this for whichever user will run the code (your login user for now, `ccid` later for the service):

```bash
sudo usermod -a -G gpio,plugdev,video "$USER"
```

- `gpio` — lets `gpiozero`/`lgpio` drive the contactor GPIO lines without root.
- `plugdev` — matches the udev rule below, granting USB access to the Keysight scope.
- `video` — lets OpenCV open `/dev/videoN` for the webcam.

Install **both** udev rules (the scope rule alone is not sufficient — the camera rule is what creates the stable `/dev/ccid_camera` symlink §0 depends on):

```bash
sudo cp deploy/99-keysight-usbtmc.rules deploy/99-c270-camera.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug/replug the camera (or reboot) and confirm the stable symlink exists before relying on it in `config.yaml`:

```bash
ls -la /dev/ccid_camera
```

`config.yaml`'s `camera.device_index` must point at this path, not a raw numeric index — a real incident had the camera re-enumerate from `video0` to `video1`/`video2` mid-campaign. **Do not** deploy `device_index: /dev/ccid_camera` before this symlink exists; use a plain int index (e.g. `0`) until the udev rule is confirmed working.

**Log out and back in (or reboot)** for the new group memberships to take effect — this is the single most common cause of "permission denied" on GPIO/USB/camera on a fresh setup.

### 1.5 Clone the repo and install Python dependencies

```bash
git clone <your-repo-url> ccid_automation   # or copy it over if already local
cd ccid_automation
git checkout development
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

The pinned dependencies (`PyYAML`, `numpy`, `gpiozero`, `pyvisa`, `pyvisa-py`, `pyusb`, `opencv-python-headless`) install with this one command. `gpiozero` uses its `lgpio` backend on modern Raspberry Pi OS; if it complains about the pin factory:

```bash
sudo apt install -y python3-lgpio
```

If a virtualenv still can't see `lgpio` after that (it happened once — a real, non-obvious bug, not hypothetical: `python3-lgpio` installed system-wide but invisible to the isolated venv, silently falling back to GPIO Zero's experimental `NativeFactory`), symlink the system package's `lgpio.py` and its compiled ARM64 extension into the venv's site-packages rather than assuming the apt install alone was sufficient. Confirm which factory got selected if you're ever unsure — "it works, just not on the backend you think it's using" is exactly the kind of failure that only shows up if someone checks.

### 1.6 Which GPIO pin is which contactor?

Locked in `config.yaml` (`gpio.k1`/`k2`/`k3`, BCM numbering), used by `ccid/hal/gpio_real.py` and `tools/gpio_selftest.py`:

| Contactor | Role | BCM GPIO | Physical pin |
|---|---|---|---|
| K1 | L1 mains | GPIO17 | Pin 11 |
| K2 | L2 mains | GPIO27 | Pin 13 |
| K3 | Leakage injection | GPIO22 | Pin 15 |

Confirm without touching hardware:

```bash
python3 -m tools.gpio_selftest --config config.yaml show-pins
```

Any ground pin on the header works as the common return for driver-board inputs (physical pins 9, 14, 20, 25, 30, 34, or 39).

**Signal polarity**: every output initializes inactive and drives **active-high** (`gpiozero.DigitalOutputDevice(active_high=True, initial_value=False)`) — GPIO idles at 0 V, goes to 3.3 V when a contactor is commanded closed. Wire driver-board trigger polarity to match.

**Driver board note — known, unaddressed hardware gap** (hardware/wiring concern, not software-checkable): the ZX-517-style opto-driver boards used in this project have **no onboard flyback diode**, confirmed by inspection, **and no external flyback protection has been added** — this remains the single highest-priority hardware gap in the whole design. A MOSFET driving a coil without flyback suppression avalanche-stresses on turn-off and typically fails *shorted*, and a shorted K3 driver means leakage injection stuck permanently closed, defeating the normally-open contactor and the software 300 ms backstop simultaneously, with no software remedy once it's happened. Separately, **no gate pulldown resistors are installed on any MOSFET input**, so a GPIO pin not yet under `gpiozero`'s control (e.g. during early boot) is left floating rather than reliably held low, instead of defaulting the associated contactor open. Both gaps are tracked as future hardware work, not completed mitigations — see `docs/build-and-commissioning-issue-log.md` §9 item 1. If either is added later: flyback diodes go cathode to `OUT+`, anode to `OUT-` per coil (reversed polarity is a dead short on power-up); a typical gate pulldown value for this class of driver board is on the order of 10 kΩ. All three driver boards' `GND` signal pins must bond to Pi ground at a single star point (the boards aren't optoisolated — MOSFET gates reference supply negative, not Pi ground, without this bond). Never PWM a contactor coil — DC on/off only.

### 1.7 Finding the oscilloscope's VISA resource string

The real scope driver (`ccid/hal/scope_real.py`) needs a VISA resource string via `CCID_SCOPE_RESOURCE`. With the scope connected over USB and powered on:

```bash
python3 -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
```

This prints something like `('USB0::2391::6040::MY58100795::0::INSTR',)` — `2391` is Keysight's vendor ID in decimal (`0x0957`), `6040` is this scope model's product ID in decimal (`0x1798` — **not** `6296`; that figure appeared in an earlier draft of this instruction and was simply an arithmetic error), followed by the instrument's own serial number. **Always copy the exact string your own Pi prints** — the serial number is specific to one physical instrument, so a worked example from documentation should never be pasted in verbatim:

```bash
export CCID_SCOPE_RESOURCE="USB0::2391::6040::MY58100795::0::INSTR"
```

No ARM build of NI-VISA/Keysight IO Libraries exists — this project uses `pyvisa`+`pyvisa-py`+`pyusb` on the Pi (validated separately on Windows with NI-VISA first, during initial bring-up, before confirming the pure-Python stack). Put the `export` in a local shell profile for interactive testing (never commit it); for the systemd path it goes in `/etc/default/ccid-automation` instead (§1.9).

### 1.8 Camera device path

```bash
ls /dev/video*
```

A single USB webcam normally shows up as `/dev/video0` — but don't hardcode that: install the udev rule (§1.4) and use the resulting `/dev/ccid_camera` symlink in `config.yaml` instead, since raw index assignment isn't stable across USB reconnects.

### 1.9 Secrets and environment injection

Create `/etc/default/ccid-automation` (never commit this file):

```
CCID_SCOPE_RESOURCE=USB0::2391::6040::MY58100795::0::INSTR
CCID_CRONITOR_URL=https://cronitor.link/p/your-api-key/your-monitor-key
CCID_NTFY_TOPIC_URL=https://ntfy.sh/your-topic   # optional
```

Both monitoring variables are best-effort — a failure here is logged and never halts a campaign (`ccid/main.py`). `CCID_NTFY_TOPIC_URL` is optional; the other two are required for a monitored real campaign. Never put either secret in git, chat, or shell history that gets logged.

### 1.10 systemd watchdog and persistent journald

Regardless of which run pattern (§3.12 transient vs. §3.13 persistent) you end up using, set up the hardware watchdog and persistent logging once:

```
# /etc/systemd/system.conf
RuntimeWatchdogSec=10
RebootWatchdogSec=60
```

**Persistent journald is required before any unattended endurance campaign.** By default journald keeps logs only in a small volatile ring buffer (`/run/log/journal`), lost on reboot or power loss — this is exactly what happened during campaign `5800_v3_real_20260813T175531Z`: the Pi became unreachable after halting at cycle 38, and the original traceback for `halt_reason=controller:unexpected:ValueError` was lost with the reboot, leaving only a code-review theory instead of a confirmed root cause.

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
```

```
# /etc/systemd/journald.conf
Storage=persistent
SystemMaxUse=500M
```

```bash
sudo systemctl restart systemd-journald
```

Verify persistence survives a reboot before trusting it on a real campaign:

```bash
sudo reboot
journalctl --list-boots   # more than one boot should be listed
```

This is a mitigation, not a substitute for durable per-cycle diagnostics: `Sequencer._capture_controller_exception_diagnostics()` writes directly under `runs/<run_id>/diagnostics/<cycle_index>/controller_exception.json` for any unexpected controller exception — that artifact survives even if journald itself is lost, since it's fsync'd to the run directory as part of the normal halt path, never routed through the system logger.

---

## 2. Software validation ladder (before real hardware, and before any long campaign after a significant change)

Work through these in order; each step should fully pass before the next. Assumes the repo root with the virtualenv activated.

**Step 1 — full test suite on the Pi itself.**

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: `OK`, **364 tests, 2 intentional skips** (the two skips are documented, expected gaps — see `docs/test-suite-guide.md` — not failures). Anything else is a code or environment problem; resolve it before going further, since nothing past this point is trustworthy otherwise. Use a non-`/tmp` temp directory (the Pi's `/tmp` is a 1 GB tmpfs while the project's own disk-space guard requires 2 GB free — every sequencer test will otherwise halt on `persistence:insufficient_disk_space` even with plenty of real disk free):

```bash
mkdir -p ~/ccid_test_tmp
TMPDIR="$HOME/ccid_test_tmp" env -u CCID_SCOPE_RESOURCE python3 -m unittest discover -s tests -p 'test_*.py'
```

(`env -u CCID_SCOPE_RESOURCE` hides any ambient scope resource variable so a test run can't be accidentally influenced by session state.)

**Step 2 — config loads and hashes cleanly.**

```bash
python3 -c "from ccid.config import load_config; c = load_config('config.yaml'); print(c.canonical_hash())"
```

No error, a hex hash printed. Editing `config.yaml` later changes the hash — expected, and how the system prevents resuming a run against a silently different configuration.

**Step 3 — full simulated campaign, zero hardware.**

```bash
python3 -m tools.simulate --config config.yaml campaign --run-root /tmp/sim_check --cycles 20
python3 -m tools.simulate --config config.yaml crash-resume --run-root /tmp/sim_check --cycles 5 --crash-cycle 3 --crash-checkpoint after_csv
python3 -m tools.simulate --config config.yaml sticky-halt-check --run-root /tmp/sim_check --cycles 5
```

Check `"terminal": "COMPLETE"` on the first, `"ok": true` on the second and third — builds confidence the *logic* is correct before any real contactor, scope, or camera is involved.

**Step 4 — real GPIO check (contactors only).** First step that actually energizes hardware.

```bash
python3 -m tools.gpio_selftest --config config.yaml show-pins
python3 -m tools.gpio_selftest --config config.yaml exercise --contactor K1 --real --i-understand-this-energizes-hardware --pulses 1 --hold-s 1 --cooldown-s 1
python3 -m tools.gpio_selftest --config config.yaml exercise --contactor K2 --real --i-understand-this-energizes-hardware --pulses 1
python3 -m tools.gpio_selftest --config config.yaml exercise --contactor K3 --real --i-understand-this-energizes-hardware --pulses 1
python3 -m tools.gpio_selftest --config config.yaml mismatch-test --real --i-understand-this-energizes-hardware --stagger-ms 0
```

`K3` exercise automatically closes K1/K2 first (the interlock doesn't allow otherwise); everything ends de-energized (`safe_off`) even on a partial failure.

**Step 5 — real scope check.** Requires `CCID_SCOPE_RESOURCE` (§1.7).

```bash
python3 -m tools.scope_bench identify --real
python3 -m tools.scope_bench configure --real
python3 -m tools.scope_bench arm-check --real --timeout-s 5
python3 -m tools.scope_bench capture-bench --real --timeout-s 10 --out-dir /tmp/scope_bench_out
```

`identify`/`configure`/`arm-check` should succeed with no real leakage injection present. `capture-bench` needs a real trigger event to complete — timing out on acquisition here (nothing injecting current yet) is expected at this stage and still confirms connect/configure/arm all work.

**Step 6 — camera calibration.** Capture still images of the LED in each state (off, blue/ready, green/charging, red/faulted, and the multi-color booting sequence), one directory per state.

```bash
python3 -m tools.calibrate_camera show-roi --frames-dir calib/off --out roi.json
python3 -m tools.calibrate_camera propose-hsv --off calib/off --blue calib/blue --green calib/green --red calib/red --roi-file roi.json --out hsv_ranges.json
python3 -m tools.calibrate_camera verify --off calib/off --blue calib/blue --green calib/green --red calib/red --booting calib/booting --roi-file roi.json --out verify_report.json
python3 -m tools.calibrate_camera build-replay --off calib/off --booting calib/booting --blue calib/blue --green calib/green --red calib/red --out replay_footage.json
```

Check `verify_report.json` for `"matched": true` on every color — this is a real calibration step for your specific LED/camera/lighting, not a skippable formality.

**Step 7 — flip HAL modes to real, one at a time.** Edit `config.yaml`'s `modes:` section, changing one mode per short campaign rather than all three at once:

```bash
python3 -m ccid.main --config config.yaml start --target-cycles 1 --run-id gpio_real_check
```

Inspect `runs/gpio_real_check/cycles.csv` and the console log. Repeat for `scope_mode: real` (with `CCID_SCOPE_RESOURCE` set), then `camera_mode: real`, each with a fresh `--run-id`.

**Step 8 — one fully hand-validated real cycle.** All three modes real:

```bash
python3 -m ccid.main --config config.yaml start --target-cycles 1 --run-id stage4_hand_check
```

Manually inspect every artifact: `waveforms/1.npz`, `images/1_scope.png`, `images/1_green.jpg`, `cycles/1.json`, the `cycles.csv` row. Confirm verdict and `trip_time_s` look correct for what actually happened electrically.

**Step 9 — small real campaign including a forced crash/resume.**

```bash
python3 -m ccid.main --config config.yaml start --target-cycles 10 --run-id stage5_ten
```

Partway through, kill the process (`Ctrl-C` or `kill <pid>`) to simulate a crash. Confirm from the log that the rig was safely de-energized before exit, then:

```bash
python3 -m ccid.main --config config.yaml resume --run-id stage5_ten --latest
```

Confirm the interrupted cycle is redone (not skipped, not duplicated) via contiguous `cycles.csv` indices. If this passes, repeat with `--target-cycles 100`.

Only after Steps 1-9 all pass are you ready for §3's per-campaign checklist and the unattended-run patterns in §3.12/§3.13.

---

## 3. Operator Preflight and Run Checklist (every real-hardware campaign)

### 3.1 Choose the run type

**A. Supervised SSH run** — use for the first cycle after hardware changes, scope/camera recommissioning, new analysis-version validation, or short debugging campaigns. The SSH terminal must stay open unless the run is inside `systemd` or `tmux`.

**B. Autonomous transient `systemd-run`** — use only after a supervised real-hardware run has passed, the current code passed the full test suite, camera and scope preflight passed, the service environment is configured, and a short real `systemd` campaign has already passed. Continues if SSH disconnects. This is the tested pattern — see §3.12.

### 3.2 Physical safety preflight

Complete with EVSE mains and all three 12 V contactor supplies off:

- [ ] Qualified person inspected the setup
- [ ] Protective-earth connections are intact
- [ ] K1 is wired as the L1 mains contactor
- [ ] K2 is wired as the L2 mains contactor
- [ ] K3 is wired only as the leakage-injection contactor
- [ ] K1, K2, K3 driver channels are labeled correctly
- [ ] Known gap acknowledged: no flyback diodes and no gate pulldown resistors are installed on any driver board (§1.6) — inspect for physical driver-board damage accordingly, since this failure mode has no hardware backstop
- [ ] Probe tip is on the approved measurement node
- [ ] Scope reference is connected only to the approved reference point
- [ ] Probe attenuation switch is set to 10x
- [ ] Camera is mechanically secure and aimed at the EVSE LED
- [ ] No loose conductors, damaged insulation, or mounting damage is visible
- [ ] Emergency disconnect is accessible
- [ ] Test area is restricted during energized operation

If the rig was moved, drilled, remounted, rewired, or disconnected, repeat the complete recommissioning sequence before a multi-cycle campaign.

### 3.3 Pi and repository preflight

```bash
cd ~/ccid_automation
source venv/bin/activate
git status --short
git log -3 --oneline --decorate
```

Expected: local `development` matches `origin/development`; only known temporary files (e.g. `camera_gate_check.yaml`) are untracked; no unexplained modified source files.

```bash
pgrep -af "python.*ccid.main" || echo "No active CCID process"
```

Do not start another campaign if one is active.

```bash
df -h "$HOME"
```

Required: more than 2 GB free; for long campaigns, confirm projected artifact storage plus backup margin.

### 3.4 Software test gate

```bash
mkdir -p ~/ccid_test_tmp
TMPDIR="$HOME/ccid_test_tmp" env -u CCID_SCOPE_RESOURCE python -m unittest discover -s tests -p 'test_*.py'
```

- [ ] Test suite reports `OK`
- [ ] Only the two known intentional skips appear
- [ ] No disk-space, analysis, recorder, scope, camera, or sequencer failures

Do not proceed with energized hardware after a failing test suite.

### 3.5 Analysis-version gate

```bash
grep -n "algorithm_version" config.yaml
```

Required for current campaigns: `algorithm_version: v3`. When the analysis algorithm changes:

- [ ] Use a new explicit version (never edit V1/V2/V3 in place — `docs/trip-time-analysis-algorithm.md`)
- [ ] Do not overwrite historical results
- [ ] Replay archived waveforms under the new version (`tools/replay_waveform.py`)
- [ ] Validate the version on a fresh short real-hardware campaign

### 3.6 Camera preflight

```bash
v4l2-ctl --list-devices
```

Required: Logitech C270 appears (at `/dev/video0` if not yet using the udev symlink; `/dev/ccid_camera` once §1.4's rule is installed).

```bash
v4l2-ctl --device=/dev/video0 --set-ctrl=auto_exposure=1,exposure_time_absolute=60
v4l2-ctl --device=/dev/video0 --get-ctrl=auto_exposure,exposure_time_absolute
v4l2-ctl --device=/dev/video0 --get-fmt-video
```

Expected: manual exposure mode, exposure absolute = **60** (not 30 — see §0), 640x480, YUYV format. Auto white balance cannot actually be disabled on this camera despite the command appearing to succeed — control the physical lighting environment rather than fighting it further.

Capture a warmed frame if the camera was moved (the C270 needs several seconds of active frame reads, not just a sleep, before exposure/gain settle — every early frame is black otherwise):

```bash
timeout 10s ffmpeg -hide_banner -loglevel error -y -f v4l2 -video_size 640x480 -input_format yuyv422 -i /dev/video0 -ss 3 -frames:v 1 -update 1 ~/camera_preflight_warmed.jpg
```

- [ ] Entire EVSE LED area is visible
- [ ] LED is near the center of the frame and inside the configured ROI
- [ ] Image is not black or severely blurred
- [ ] Exposure does not wash out the green LED

**Camera-only classifier validation after camera movement:** physically disable K3's 12 V coil supply → use real K1/K2, real camera, simulated scope via `camera_gate_check.yaml` → confirm the gate waits through boot and grants only on sustained flashing green (this normally takes ~45-50 seconds — that's the real EVSE boot sequence plus the required sustained-green qualification, not a bug) → de-energize → restore K3's coil supply. The resulting simulated PASS is camera evidence only, never an electrical trip result — this is exactly why `camera_gate_check.yaml` stays untracked and out of every commit.

### 3.7 Contactor preflight after wiring or mounting changes

Keep EVSE mains off; power only the 12 V coil supplies.

```bash
python -m tools.gpio_selftest exercise --contactor K1 --pulses 1 --hold-s 0.5 --cooldown-s 1 --real --i-understand-this-energizes-hardware
python -m tools.gpio_selftest exercise --contactor K2 --pulses 1 --hold-s 0.5 --cooldown-s 1 --real --i-understand-this-energizes-hardware
python -m tools.gpio_selftest exercise --contactor K3 --pulses 1 --hold-s 0.2 --cooldown-s 1 --real --i-understand-this-energizes-hardware
```

- [ ] Each contactor pulls in once and releases cleanly
- [ ] No buzzing or sticking
- [ ] Final safe-off completes

The software proves *commanded* state only — auxiliary-contact GPIO feedback confirming physical state is still deferred (`docs/legacy-documentation-audit.md` §4 item 1).

### 3.8 Oscilloscope preflight

```bash
lsusb | grep -i "0957:1798"
```

Expected: `0957:1798 Agilent Technologies, Inc. MSO-X 2014A`. If absent: confirm scope power, reseat the USB cable, try a known-good cable, try another Pi USB port, re-run `lsusb`.

```bash
export CCID_SCOPE_RESOURCE="USB0::2391::6040::MY58100795::0::INSTR"
timeout 15s python -m tools.scope_bench identify --real
```

Expected identity: Model MSO-X 2014A, Serial MY58100795.

```bash
timeout 10s python -c 'import os,pyvisa; rm=pyvisa.ResourceManager("@py"); s=rm.open_resource(os.environ["CCID_SCOPE_RESOURCE"]); [print(s.query(":SYSTem:ERRor?").strip()) for _ in range(5)]; s.close(); rm.close()'
```

All final responses must be `+0,"No error"`.

```bash
timeout 30s python -m tools.scope_bench configure --real
```

Expected: CH1 source, +20 V positive edge trigger, 50 ms/div, center reference, RAW waveform mode, BYTE waveform format, configuration completes without a `ScopeConfigurationError`.

**Probe compensation after hardware changes:** connect the same CH1 probe to the scope compensation output → confirm a stable square wave → restore the probe to the approved measurement node → re-run automated scope configuration.

### 3.9 Network preflight for autonomous runs

For Verizon USB tethering, verify `usb0`:

```bash
ip -brief address show usb0
ip route get 8.8.8.8
ping -c 3 8.8.8.8
```

Expected: previously-deployed address was `192.168.1.121/24`; the route uses `dev usb0` via the Verizon hotspot gateway. Verify remote access separately: `ssh ccid@192.168.1.121`. `usb0` (network tether) and `/dev/video0` (camera) are unrelated devices that happen to look similar in shorthand — don't confuse them.

A dedicated USB-tethered hotspot is used specifically because a phone hotspot isn't viable for a multi-day unattended run (the phone has to stay connected and available the whole time).

### 3.10 Monitoring preflight

Optional for short supervised tests, **required** before a long unattended campaign.

- The previous Cronitor monitor was deleted at one point and is known to auto-recreate itself on the next heartbeat — monitoring lifecycle (pause/resume, schedule, grace, alert behavior) must be revalidated before every long run, not assumed still-correct from a prior campaign.

```bash
sudo sed -E 's/=.*/=<configured>/' /etc/default/ccid-automation
```

Expected variables present: `CCID_SCOPE_RESOURCE=<configured>`, `CCID_CRONITOR_URL=<configured>`.

Before a long unattended run:

- [ ] Create a dedicated final monitor key (e.g. `ccid-endurance-6000`)
- [ ] Verify a normal heartbeat, a failure heartbeat, and recovery behavior
- [ ] Configure expected interval and grace; attach correct alert recipients
- [ ] Disable notification on every successful heartbeat
- [ ] Define how the monitor is paused when no campaign is expected

Never place private URLs or API keys in git.

### 3.11 Supervised SSH run procedure

Use only when the SSH session stays open for the whole run.

```bash
cd ~/ccid_automation
source venv/bin/activate
export CCID_SCOPE_RESOURCE="USB0::2391::6040::MY58100795::0::INSTR"
v4l2-ctl --device=/dev/video0 --set-ctrl=auto_exposure=1,exposure_time_absolute=60

RUN_ID="real_v3_supervised_$(date -u +%Y%m%dT%H%M%SZ)"
echo "RUN_ID=$RUN_ID"
python -m ccid.main --config config.yaml start --target-cycles 5 --run-id "$RUN_ID"
```

- [ ] Never reuse a run ID, even for an aborted attempt that never completed a cycle
- [ ] Do not press scope or camera controls during the campaign
- [ ] Do not resume automatically after a halt (halts are sticky by design — a device that already failed a real test shouldn't be silently re-tested)
- [ ] Use the emergency disconnect for unexpected hardware behavior
- [ ] Fully de-energize before reviewing artifacts

### 3.12 Autonomous run procedure — transient `systemd-run` (the tested pattern)

Use only after all preflight gates in this section pass.

```bash
RUN_ID="real_v3_systemd_$(date -u +%Y%m%dT%H%M%SZ)" && echo "RUN_ID=$RUN_ID" && sudo systemd-run --unit=ccid-real-v3-campaign --uid=ccid --gid=ccid --working-directory=/home/ccid/ccid_automation --property=EnvironmentFile=/etc/default/ccid-automation --setenv=TMPDIR=/home/ccid/ccid_test_tmp --collect /home/ccid/ccid_automation/venv/bin/python -m ccid.main --config config.yaml start --target-cycles 10 --run-id "$RUN_ID"
```

Change only the run ID prefix, `--target-cycles`, and the unit name if needed. Confirm active status before disconnecting:

```bash
systemctl is-active ccid-real-v3-campaign.service   # expect: active
```

Safely disconnect with `exit` — **never `Ctrl+C`** to disconnect (that can propagate to the transient unit depending on session setup; `exit` cleanly detaches).

Check progress after reconnecting:

```bash
journalctl -u ccid-real-v3-campaign.service --no-pager -n 50
systemctl is-active ccid-real-v3-campaign.service
```

If `systemctl status` reports the unit missing after completion, that's expected with `--collect` (completed transient units are removed automatically) — use `journalctl -u UNIT_NAME` and inspect the run directory instead.

**Never reuse a run ID.** Do not resume automatically after a halt — halts are sticky on purpose.

### 3.13 Alternative: persistent, `enable`d systemd service (documented, not validated)

`deploy/ccid-automation.service` describes installing a persistent service that auto-runs `resume --latest` and restarts on failure across reboots forever:

```
[Service]
Type=notify
WorkingDirectory=/opt/ccid
ExecStart=/opt/ccid/venv/bin/python -m ccid.main --config /opt/ccid/config.yaml resume --latest
Restart=on-failure
RestartSec=5
WatchdogSec=10
```

**As committed, this unit's paths (`/opt/ccid`) do not match the tested deployment layout (`/home/ccid/ccid_automation`, §1.3) — this pattern has never actually been exercised against a real campaign** (§0). If you want to use it: correct `WorkingDirectory`/`ExecStart` to the real path first, and be aware the service always runs `resume --latest`, which requires an existing run directory — enabling it before any run exists will crash-loop. Start the first run manually before ever enabling the unit:

```bash
/home/ccid/ccid_automation/venv/bin/python -m ccid.main --config /home/ccid/ccid_automation/config.yaml start
```

Then, only after that run directory exists:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ccid-automation.service
```

Until this path is actually validated on real hardware, prefer §3.12's transient pattern for real campaigns.

### 3.14 Post-run verification

Fully de-energize mains and all three 12 V supplies before reviewing results.

```bash
python -m ccid.main --config config.yaml status --run-id RUN_ID
```

- [ ] `last_completed_cycle` matches target or the expected halt point
- [ ] `target_cycles` is correct
- [ ] `halt_reason` is null for a completed campaign
- [ ] PASS and FAIL counts are plausible

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

```bash
find runs/RUN_ID -maxdepth 3 -type f -printf '%p %s bytes\n' | sort
```

Expected per completed cycle: `cycles/<n>.json`, `waveforms/<n>.npz`, `images/<n>_scope.png`, `images/<n>_green.jpg`; also `config.yaml`, `cycles.csv`, `runstate.json` at the run root. **Do not delete invalid or failed records** — preserve them for replay and debugging.

### 3.15 Common troubleshooting

**Camera remains BOOTING while LED flashes green:** check `v4l2-ctl --list-devices`, `--get-fmt-video`, `--get-ctrl=auto_exposure,exposure_time_absolute`; capture a warmed image and inspect framing. Reposition the camera before changing classifier thresholds.

**Camera frame is black:** first frame was captured before C270 warm-up. Capture after a three-second warmed stream instead.

**Scope not found:** check `lsusb | grep -i "0957:1798"`. If absent: reseat USB, replace cable, try another port, confirm scope power. Do not debug VISA until `lsusb` sees the scope.

**Scope commands time out after an interrupted transfer:** stop retrying immediately. Keep the rig de-energized, disconnect scope USB, **remove scope AC power for 60 seconds** (a USB reconnect, scope power cycle, or Pi reboot alone will not recover it — this was confirmed the hard way during commissioning), boot fully, reconnect USB, verify `*IDN?`.

**Scope configure reports `Query INTERRUPTED` or `Query UNTERMINATED`:** drain the stale error queue, retry clean configuration once, do not ignore newly generated configuration errors.

**Scope screenshot is zero bytes:** scalar diagnostics may still be valid; treat the screenshot as unavailable, don't try to interpret an empty PNG.

**Trip time is `0.0`:** do not accept the PASS at face value. Inspect `t0`, `t_end`, and every sanity check; preserve the waveform for replay. First-cycle zero-time behavior is a known, still-unresolved issue — treat it as unresolved, not as a confirmed real trip, until it's fixed (`docs/build-and-commissioning-issue-log.md` §5).

**`collapse_is_clean` is false:** do not accept a numerical PASS. Preserve the raw waveform, replay under the configured analysis version, investigate endpoint extraction or waveform shape.

**A transient systemd unit "could not be found":** expected with `--collect` once completed. Use `journalctl -u UNIT_NAME` and inspect the run directory / `runstate.json` directly.

**Systemd says failed after a safe CCID halt:** read the actual application halt reason in the journal — do not infer hardware danger from the generic systemd status alone, and do not auto-resume.

**Cronitor monitor reappears after deletion:** the configured heartbeat URL auto-provisions the monitor again on its next event. Remove or change `CCID_CRONITOR_URL` only when no campaign is active.

### 3.16 Safe shutdown

After the campaign finishes and all hardware supplies are off:

```bash
sudo poweroff
```

Wait at least 20 seconds and until the Pi's activity light stops flashing before removing power. Campaign data is saved before shutdown if the run reached its commit points — maintain an off-Pi backup for important runs regardless (no automatic off-Pi backup exists yet; see `docs/build-and-commissioning-issue-log.md` §9 item 7).

### 3.17 Long-campaign gate

Before a 150-cycle or 6,000-cycle campaign, require all of the following, with explicit evidence for each — not authorized by time pressure alone:

- [ ] Current code committed and synchronized
- [ ] Full suite passes (364 tests, 2 intentional skips)
- [ ] Analysis V3 selected
- [ ] Archived pre-V3 campaign data replayed under V3 where required
- [ ] First-cycle zero-time behavior resolved or automatically rejected
- [ ] Camera framing and classifier validated
- [ ] Contactors checked after any hardware changes
- [ ] Flyback-diode/gate-pulldown gap (§1.6) acknowledged for this campaign — still an open future task, not resolved by this checklist
- [ ] Scope USB cable and port stable; error queue clean
- [ ] Real supervised validation campaign passed
- [ ] Real transient-systemd campaign passed without SSH
- [ ] Network route validated
- [ ] External monitoring recreated and tested
- [ ] Monitoring pause/resume lifecycle defined
- [ ] Storage projection checked; off-Pi backup process ready
- [ ] Watchdog, reboot, and sticky-halt resume tested
- [ ] Auxiliary-contact feedback decision revisited
- [ ] Campaign-level acceptance criteria defined (deliberately never inferred by the software — an offline, human decision; see `docs/campaign-results-index.md` §4)
- [ ] Operator understands how to inspect the journal, run state, and artifacts

---

## 4. Quick reference: commands that never touch hardware

Safe to run at any time, on any machine, without the rig connected:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m tools.gpio_selftest --config config.yaml show-pins
python3 -m tools.simulate --config config.yaml campaign --run-root /tmp/x --cycles 5
python3 -m ccid.main --config config.yaml status --latest
```
