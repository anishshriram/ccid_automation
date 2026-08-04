# Raspberry Pi Bring-Up and Software Test Plan

This document covers two things:

1. **Post-OS Raspberry Pi setup** - everything needed on a freshly flashed Pi before any CCID code will run against real hardware (users, packages, permissions, pin mapping, device discovery).
2. **Step-by-step test plan** - the order to validate the software, starting in pure simulation and working up to real hardware, without needing to debug the physical rig itself (wiring/continuity is assumed correct).

Read part 1 once per Pi. Follow part 2 in order; do not skip ahead if a step fails.

---

## Part 1: Raspberry Pi post-OS setup

Assumes Raspberry Pi OS (Bookworm or newer, 64-bit recommended) already flashed and booted, with either a keyboard/monitor or SSH access.

### 1.1 System update and base packages

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv python3-pip python3-dev build-essential
```

`opencv-python-headless` (used for the real camera) ships as a wheel but occasionally needs a few system shared libraries present on Raspberry Pi OS Lite. If `pip install` later fails with a missing `.so` error, install these and retry:

```bash
sudo apt install -y libatlas-base-dev libjpeg-dev libopenjp2-7 libtiff6 ffmpeg
```

Raspberry Pi OS's pip is pre-configured to pull pre-built ARM wheels from piwheels.org, so `numpy`/`opencv-python-headless` should not need to compile from source.

Confirm Python version is 3.11+ (the code targets 3.11+):

```bash
python3 --version
```

### 1.2 Enable interfaces (`raspi-config`)

```bash
sudo raspi-config
```

- **Interface Options -> SSH**: enable if you're working headless and haven't already.
- **Camera**: only relevant if you are using the Raspberry Pi CSI camera module. The code's real camera driver (`ccid/hal/camera_real.py`) uses OpenCV's `cv2.VideoCapture(device_index)`, which expects a standard V4L2 `/dev/videoN` device. **A USB webcam works out of the box with no extra configuration.** A CSI camera module on recent Raspberry Pi OS uses the `libcamera` stack and does **not** appear as a plain V4L2 device without extra bridging (e.g. `rpicam-vid` piped through `v4l2loopback`). If you have a choice, use a USB webcam - it avoids this entirely.

Reboot if you changed anything: `sudo reboot`.

### 1.3 Create the working user/directory (production layout)

For quick iteration you can run everything as your own login user first (see Part 2) and switch to a dedicated service account later, right before you set up the systemd service. When you're ready for the dedicated account, `DEPLOYMENT.txt` already documents this:

```bash
sudo useradd --system --create-home --home-dir /opt/ccid --shell /usr/sbin/nologin ccid
sudo mkdir -p /opt/ccid
sudo chown ccid:ccid /opt/ccid
```

### 1.4 Group membership (GPIO, USB scope, camera)

The Pi needs three kinds of device access. Do this for whichever user will actually run the code (your login user for now, `ccid` later for the service):

```bash
sudo usermod -a -G gpio,plugdev,video "$USER"
```

- `gpio` - lets `gpiozero`/`lgpio` drive the contactor GPIO lines without root.
- `plugdev` - matches the udev rule below, which grants USB access to the Keysight scope.
- `video` - lets OpenCV open `/dev/videoN` for the webcam.

Install the provided udev rule for the scope (Keysight USB VID `0957`, PID `1798`):

```bash
sudo cp deploy/99-keysight-usbtmc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

**Log out and back in (or reboot)** for the new group memberships to take effect - this is the single most common cause of "permission denied" on GPIO/USB/camera on a fresh setup.

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

The pinned dependencies (`gpiozero`, `pyvisa`, `pyvisa-py`, `pyusb`, `opencv-python-headless`, `numpy`, `PyYAML`) are all installed by this one command. `gpiozero` is used with its `lgpio` backend on modern Raspberry Pi OS; no extra pin-factory configuration is normally required, but if `gpiozero` complains about the pin factory, install `python3-lgpio` via apt as a fallback:

```bash
sudo apt install -y python3-lgpio
```

### 1.6 Which GPIO pin is which contactor?

This is locked in `config.yaml` (`gpio.k1`/`k2`/`k3`, BCM numbering) and is what `ccid/hal/gpio_real.py` and `tools/gpio_selftest.py` both use. On the Pi's 40-pin header:

| Contactor | Role | BCM GPIO | Physical pin |
|---|---|---|---|
| K1 | L1 mains | GPIO17 | Pin 11 |
| K2 | L2 mains | GPIO27 | Pin 13 |
| K3 | Leakage injection | GPIO22 | Pin 15 |

You can confirm this at any time without touching hardware:

```bash
python3 -m tools.gpio_selftest --config config.yaml show-pins
```

Use any ground pin on the header as the common return for your driver board inputs (e.g. physical pins 9, 14, 20, 25, 30, 34, or 39 - all GND on a standard 40-pin Pi header).

**Signal polarity**: the code initializes every output inactive and drives it **active-high** (`gpiozero.DigitalOutputDevice(active_high=True, initial_value=False)`) - GPIO idles at 0 V and goes to 3.3 V when a contactor is commanded closed. Wire your driver board's trigger input polarity to match.

**Driver board note** (carried over from the project handoff, relevant to how you wire these pins, not something the software can check): the ZX-517-style opto-driver boards referenced in this project have no onboard flyback diode. If your relay/contactor coils are inductive, external flyback protection is required on the coil side - this is a hardware/wiring concern, independent of and not verifiable by the software here.

### 1.7 Finding the oscilloscope's VISA resource string

The real scope driver (`ccid/hal/scope_real.py`) needs a VISA resource string, supplied via the `CCID_SCOPE_RESOURCE` environment variable. With the scope connected over USB and powered on:

```bash
python3 -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
```

This prints something like `('USB0::2391::6296::MY58100795::0::INSTR',)` (vendor ID `0957`/`2391` in decimal, product ID `1798`/`6296` in decimal, followed by the instrument's serial number). Copy the exact string, then:

```bash
export CCID_SCOPE_RESOURCE="USB0::2391::6296::MY58100795::0::INSTR"
```

Put this `export` in a local shell profile for interactive testing (do not commit it anywhere). For the eventual systemd service, it goes in `/etc/default/ccid-automation` instead (see `DEPLOYMENT.txt` section 5).

### 1.8 Finding the camera device index

```bash
ls /dev/video*
```

If you only have one USB webcam, it will normally be `/dev/video0`.

**Known gap, read this before wiring up the camera**: `CameraReal` (in `ccid/hal/camera_real.py`) defaults to `device_index=0`, and nothing in `config.yaml` or the CLI currently overrides it - there is no existing config knob for this. If your webcam does *not* show up as `/dev/video0` (e.g. a second camera or a built-in device is also present), the code will either open the wrong camera or fail to open one at all, and this will need a small code change (wiring a `camera.device_index` config value through to `CameraRealConfig`) before it will work. Check `/dev/video*` now so you know whether this applies to you.

### 1.9 Monitoring/notification secrets (optional, real campaigns only)

Only needed once you're running real, unattended campaigns (Part 2, step 10+). Not required for anything earlier in this plan.

```bash
export CCID_HEALTHCHECKS_URL="https://hc-ping.com/your-uuid"     # optional
export CCID_NTFY_TOPIC_URL="https://ntfy.sh/your-topic"          # optional
```

Both are best-effort: failures here are logged and never halt a campaign (this is enforced in `ccid/main.py`).

---

## Part 2: Step-by-step test plan

Work through these in order. Each step should fully pass before moving to the next. All commands assume you're in the repo root with the virtualenv activated (`source venv/bin/activate`).

### Step 1: Run the existing test suite on the Pi itself

Confirms the codebase itself is sound on this specific machine/Python version, before any hardware is involved.

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: `Ran 229 tests ... OK`. If anything fails here, it is a code or environment problem - stop and resolve it before going further; nothing past this point will be trustworthy otherwise.

### Step 2: Confirm the config file loads and hashes cleanly

```bash
python3 -c "from ccid.config import load_config; c = load_config('config.yaml'); print(c.canonical_hash())"
```

No error, and a hex hash is printed. Note the hash - if you edit `config.yaml` later, the hash will change, which is expected and is how the system prevents resuming a run against a silently different configuration.

### Step 3: Full simulated campaign (still zero hardware)

This exercises the entire sequencer state machine, retry/degrade/halt branches, crash-safe persistence, and resume behavior - all in software, using `tools/simulate.py`.

```bash
python3 -m tools.simulate --config config.yaml campaign --run-root /tmp/sim_check --cycles 20
python3 -m tools.simulate --config config.yaml crash-resume --run-root /tmp/sim_check --cycles 5 --crash-cycle 3 --crash-checkpoint after_csv
python3 -m tools.simulate --config config.yaml sticky-halt-check --run-root /tmp/sim_check --cycles 5
```

Each command prints a JSON report. Check for `"terminal": "COMPLETE"` on the first, `"ok": true` on the second and third. This step builds confidence that the *logic* is correct before any real contactor, scope, or camera is involved.

### Step 4: Real GPIO check (contactors only)

This is the first step that actually energizes hardware. Do it with the DUT/mains disconnected if you want an extra margin of safety while confirming wiring polarity, even though wiring itself is out of scope for this plan.

```bash
python3 -m tools.gpio_selftest --config config.yaml show-pins
python3 -m tools.gpio_selftest --config config.yaml exercise --contactor K1 --real --i-understand-this-energizes-hardware --pulses 1 --hold-s 1 --cooldown-s 1
python3 -m tools.gpio_selftest --config config.yaml exercise --contactor K2 --real --i-understand-this-energizes-hardware --pulses 1
python3 -m tools.gpio_selftest --config config.yaml exercise --contactor K3 --real --i-understand-this-energizes-hardware --pulses 1
python3 -m tools.gpio_selftest --config config.yaml mismatch-test --real --i-understand-this-energizes-hardware --stagger-ms 0
```

`K3` exercise automatically closes K1 and K2 first (the interlock does not allow K3 to close otherwise), and everything ends de-energized (`safe_off`) even if a step fails partway through.

### Step 5: Real scope check

Requires `CCID_SCOPE_RESOURCE` set (Part 1.7).

```bash
python3 -m tools.scope_bench identify --real
python3 -m tools.scope_bench configure --real
python3 -m tools.scope_bench arm-check --real --timeout-s 5
python3 -m tools.scope_bench capture-bench --real --timeout-s 10 --out-dir /tmp/scope_bench_out
```

`identify`/`configure`/`arm-check` should all succeed without any real leakage injection present. `capture-bench` needs an actual trigger event (real current flowing past the trigger level) to complete acquisition - if nothing is injecting current yet, it will time out on acquisition, which is expected at this stage. That's fine; it still confirms connect/configure/arm all work.

### Step 6: Camera calibration

Capture a handful of still images of the LED in each state first (off, blue/ready, green/charging, red/faulted, and ideally the multi-color booting sequence), one directory per state, e.g. `calib/off/`, `calib/blue/`, `calib/green/`, `calib/red/`, `calib/booting/`.

```bash
python3 -m tools.calibrate_camera show-roi --frames-dir calib/off --out roi.json
python3 -m tools.calibrate_camera propose-hsv --off calib/off --blue calib/blue --green calib/green --red calib/red --roi-file roi.json --out hsv_ranges.json
python3 -m tools.calibrate_camera verify --off calib/off --blue calib/blue --green calib/green --red calib/red --booting calib/booting --roi-file roi.json --out verify_report.json
python3 -m tools.calibrate_camera build-replay --off calib/off --booting calib/booting --blue calib/blue --green calib/green --red calib/red --out replay_footage.json
```

Check `verify_report.json` for `"matched": true` on every color. If any color doesn't match, the default hue ranges in `LedOpticalConfig` (`ccid/classify.py`) may need adjusting for your specific LED/camera/lighting before proceeding - this is a calibration step, not a pass/fail gate you can skip.

### Step 7: Flip HAL modes to real, one at a time

Edit `config.yaml`'s `modes:` section. Change **one** mode at a time and re-run a tiny campaign after each change, rather than flipping all three simultaneously.

```yaml
modes:
  gpio_mode: real
  scope_mode: sim
  camera_mode: sim
```

```bash
python3 -m ccid.main --config config.yaml start --target-cycles 1 --run-id gpio_real_check
```

Inspect `runs/gpio_real_check/cycles.csv` and the console log for errors. Repeat, changing `scope_mode: real` next (with `CCID_SCOPE_RESOURCE` set), then `camera_mode: real`, each time with a fresh `--run-id` and `--target-cycles 1`.

### Step 8: One fully hand-validated real cycle

All three modes real now:

```yaml
modes:
  gpio_mode: real
  scope_mode: real
  camera_mode: real
```

```bash
python3 -m ccid.main --config config.yaml start --target-cycles 1 --run-id stage4_hand_check
```

Manually inspect every artifact this produces: `runs/stage4_hand_check/waveforms/1.npz`, `runs/stage4_hand_check/images/1_scope.png`, `runs/stage4_hand_check/images/1_green.jpg`, `runs/stage4_hand_check/cycles/1.json`, and the corresponding row in `cycles.csv`. Confirm the verdict and `trip_time_s` look correct for what actually happened electrically.

### Step 9: Small real campaign, including a forced crash/resume

```bash
python3 -m ccid.main --config config.yaml start --target-cycles 10 --run-id stage5_ten
```

Partway through, kill the process (`Ctrl-C`, or `kill <pid>` from another shell) to simulate a crash. Confirm from the log that the rig was safely de-energized before exit. Then resume:

```bash
python3 -m ccid.main --config config.yaml resume --run-id stage5_ten --latest
```

Confirm the interrupted cycle is redone (not skipped, not duplicated) by checking that `cycles.csv` cycle indices are contiguous with no gaps or repeats. If this passes, repeat the whole step with `--target-cycles 100`.

### Step 10: Only after all of the above pass

You're ready for the long/unattended campaign path: the systemd service (`deploy/ccid-automation.service`), the hardware watchdog, and the monitoring/notification environment variables (Part 1.9), all documented in `DEPLOYMENT.txt`. Do not set up unattended/automatic restart via systemd until Steps 1-9 above have all been verified manually at least once.

---

## Quick reference: commands that never touch hardware

Safe to run at any time, on any machine, without the rig connected:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m tools.gpio_selftest --config config.yaml show-pins
python3 -m tools.simulate --config config.yaml campaign --run-root /tmp/x --cycles 5
python3 -m ccid.main --config config.yaml status --latest
```
