# Vision & Charging-Gate Classification

**Source file:** `ccid/classify.py` (978 lines — the largest single module in the codebase)
**Tests:** `tests/test_classify.py` (820 lines, 8 test classes, ~50 tests)

This is everything that turns a raw camera frame into "is the EVSE charging yet." It's deliberately two **independent** decision layers stacked on the same per-frame HSV classification, not one — that split is the single most important thing to understand about this file, and it's easy to miss on a skim.

---

## 1. Locked behavior (from the module docstring)

- LED states: off (semi-transparent grey), blue, green, red. Blinking through all colors = booting. Solid or blinking blue = ready. Blinking green = charging. Blinking red = faulted.
- Classification uses HSV **hue presence** over an approximately three-second window at ~15fps, over a single fixed ROI. **Blink rate is deliberately ignored** — this is why every test fixture that simulates blinking uses an arbitrary on/off period and the tests explicitly assert the result doesn't depend on it.
- A state is only declared after a configurable number of consecutive agreeing window classifications.
- **Vision must never be able to kill the run.** Camera failure degrades to a fixed 60s wait, run continues in logged degraded mode.
- Vision may grant the charging gate and may record red as secondary evidence (fault detection). **It must never calculate trip time** — that's `analysis.py`'s job, entirely separate, and nothing in this file touches a waveform.

---

## 2. The two independent decision layers

This is the architecture to hold in your head before reading anything else:

```
raw RGB frame
   │
   ▼
classify_frame_detailed()  ──────►  FrameClassification (per-frame: color, confidence, hues_present)
   │
   ├──► LedClassifier.observe()  ──► window_classification()  ──► consecutive-agreement  ──► stable_color
   │      (a ~3s rolling window, agreement-gated "what state is the LED actually in right now")
   │      Used for: reporting LED state at gate timeout (picks the sequencer's retry/halt branch),
   │                 diagnostics, `observed_state()`.
   │
   └──► ChargingGatePolicy.record()  ──► sustained-green-evidence check  ──► grant / no grant
          (a separate recent-green-timestamps deque, NOT the same window/agreement machinery)
          Used for: the actual charging-gate GRANT decision that lets the cycle proceed to K3.
```

The in-code comment on `LedOpticalConfig`'s charging-gate fields says this explicitly: the grant policy is "deliberately independent of the window-classifier's consecutive-agreement machinery." Why two systems instead of one: `LedClassifier`'s window requires the *entire* ~3s window to agree before declaring any stable state — good for confidently reporting "what state is this," bad for granting a gate on a real EVSE's charging LED, which **flashes** (green, dark, green, dark) rather than staying solid green continuously. `ChargingGatePolicy` instead accumulates green-frame *timestamps* directly, tolerating dark gaps between flashes, and grants based on count + elapsed span rather than window agreement. Using the wrong one for the wrong job would either (a) never grant on a real flashing charging LED, or (b) grant too eagerly on a passing green frame during boot.

---

## 3. HSV conversion and hue geometry

### `rgb_to_hsv(frame_rgb)`
A vectorized (pure numpy, no per-pixel Python loop) RGB→HSV conversion returning `(hue_degrees, saturation, value)` arrays, each the same shape as the input frame's first two dimensions. Standard formula: `value = max(r,g,b)`, `saturation = delta/max` (0 where max is ~0), and hue computed piecewise depending on which channel is the max, using boolean masks (`red_max`, `green_max`, `blue_max`) rather than a per-pixel branch. Accepts either `[0,255]` uint8 or already-normalized `[0,1]` float input.

### `HueRange` — inclusive band that can wrap 360°
```python
low_deg: float
high_deg: float
wraps = low_deg > high_deg   # e.g. red_hue = HueRange(345.0, 15.0) wraps through 0°
```
`.mask(hue_array)` returns the boolean membership mask, handling the wrap case (`hue >= low OR hue <= high`) vs the normal case (`hue >= low AND hue <= high`) transparently. Red is the one color that needs this — its hue sits right across the 0°/360° boundary.

Default bands: `red_hue = (345°, 15°)`, `green_hue = (85°, 165°)`, `blue_hue = (185°, 265°)` — each roughly 80-120° wide, leaving real gaps between them (not a partition of the full circle) so an ambiguous or off-color pixel doesn't get forced into the nearest band.

### `RegionOfInterest` / `center_roi`
A fixed pixel rectangle (`x, y, width, height`, origin top-left), validated non-negative/positive at construction. `.crop(frame)` raises `VisionFrameError` if the ROI doesn't fit inside the given frame rather than silently clipping — a misconfigured ROI is a loud failure, not quietly wrong data. `center_roi(width, height, fraction)` is a fallback for when no ROI is configured (rounds the box to at least 1×1 pixel, centers it) — used when `LedClassifier` is invoked without an explicit `roi`.

---

## 4. `LedOpticalConfig` — every threshold, one dataclass

| Field | Default | Meaning |
|---|---|---|
| `red_hue`/`green_hue`/`blue_hue` | see above | Hue bands |
| `min_saturation` | 0.30 | Below this, a pixel isn't "lit" regardless of hue |
| `min_value` | 0.22 | Same, for brightness |
| `off_value_threshold` | 0.30 | Mean frame value below this (when nothing is lit) → OFF |
| `min_pixel_fraction` | 0.02 | A hue band needs at least this fraction of ROI pixels to count as "present" |
| `confidence_reference_value` | 0.60 | Brightness normalization point for the confidence score |
| `window_s` / `frame_rate_hz` | 3.0 / 15.0 | Temporal window depth → `window_frames = round(window_s * frame_rate_hz)` |
| `consecutive_agreement_frames` | 5 | Windows must agree this many times running before a stable-state change is declared |
| `window_hue_min_frames` | 2 | A hue needs to appear in at least this many frames *within* the window to count as "present" for that window |
| `max_consecutive_dropped_frames` | 15 | Threshold for declaring the camera failed |
| `degraded_fixed_wait_s` | 60.0 | Fixed wait when vision degrades |
| `gate_timeout_s` | 90.0 | Default `await_charging_gate` timeout (mirrors `config.yaml`'s `timing.boot_timeout_s`) |
| `charging_green_window_s` | 6.0 | `ChargingGatePolicy`'s own rolling window for recent green timestamps |
| `charging_green_required_frames` | 3 | Minimum green-frame count within that window to even consider granting |
| `charging_green_min_span_s` | 3.5 | Minimum elapsed time between the oldest and newest green timestamp in-window |

`__post_init__` validates all of these (ranges, positivity, and two cross-field checks: `window_frames >= consecutive_agreement_frames`, `charging_green_min_span_s <= charging_green_window_s` — a span requirement that couldn't possibly fit inside its own window would silently never grant).

---

## 5. Per-frame classification — `classify_frame_detailed`

```
region = roi.crop(frame)
hue, saturation, value = rgb_to_hsv(region)
lit = (value >= min_value) & (saturation >= min_saturation)
for each configured hue color: hue_fractions[color] = fraction of pixels that are lit AND in that hue band
present = { colors whose hue_fraction >= min_pixel_fraction }
```

Then, in order:
1. **`len(present) >= 2` → `LedColor.BOOTING`.** Two or more hue bands simultaneously present in one frame is exactly what "blinking through all colors" looks like averaged/aliased within a single exposure, or what a genuine rapid transition between colors produces. Confidence is computed over the *union* of all present hues' pixel masks.
2. **`len(present) == 1` → that color.** Confidence from `_confidence()` (below).
3. **`len(present) == 0`**: check if the frame is dark — `lit_fraction < min_pixel_fraction` OR `mean(value) < off_value_threshold`. If dark → `LedColor.OFF`, with confidence taken as the *max* of two complementary signals (how thoroughly unlit, how thoroughly dark) — either signal alone being strong is enough. If lit but no hue band explains it → `LedColor.UNKNOWN`, confidence `0.0` — "something is lit and bright, but no configured hue band explains it," a real "I don't know what I'm looking at" case, distinct from OFF.

### `_confidence(mask, lit_fraction, saturation, value)`
Three independent factors multiplied together, each in `[0,1]`:
- **`dominance`** — `matched_fraction / lit_fraction`, clipped to 1. How much of the *lit* portion of the frame this specific hue explains (not the whole frame — a small ROI with a lot of dark background around a small LED shouldn't be penalized for the background).
- **mean saturation** of the matched pixels.
- **`value_factor`** — mean value of matched pixels relative to `confidence_reference_value`, clipped to 1 — rewards brighter matches up to that reference point, doesn't reward *over*-bright beyond it.

A pure, fully-saturated, adequately-bright single-color match scores near 1.0; a desaturated or dim match scores lower — this is what `test_confidence_scoring_orders_pure_above_desaturated_and_dim` checks directly.

---

## 6. `LedClassifier` — the temporal window layer

Wraps a fixed-size `deque[FrameClassification]` (`maxlen = window_frames`) plus consecutive-agreement bookkeeping.

**`observe(frame_or_None)`** — the main entry point per tick. `None` (or a frame that raises `VisionFrameError` during classification) routes to `observe_dropped()`: increments both a running total and a *consecutive* dropped-frame counter, and — critically — **does not touch the window or agreement state at all**. A dropped frame is simply absent from consideration, not a vote for any particular color. `camera_failed` becomes `True` once `consecutive_dropped >= max_consecutive_dropped_frames` (15); a single successful frame resets the consecutive counter to 0 (the running total, `dropped_frame_count`, never resets).

**`window_classification()`** — aggregates the current window's frames:
```
for each color in {RED, GREEN, BLUE}: count frames where that color was "present" (from FrameClassification.hues_present)
present_in_window = { colors that appeared in >= window_hue_min_frames frames }
if len(present_in_window) >= 2: BOOTING
elif GREEN present: GREEN
elif RED present: RED
elif BLUE present: BLUE
else: OFF if at least half the frames individually classified as OFF, else UNKNOWN
```
Note the priority order when multiple single colors qualify (shouldn't normally happen given `>= 2` already routes to BOOTING, but the `elif` chain is GREEN > RED > BLUE as a tie-break policy). `window_hue_min_frames` (2) means a single stray hue-present frame within an otherwise-clean window does **not** make that color "present" for the window — this is what protects against one noisy frame flipping a window classification (`test_single_spurious_hue_frame_does_not_flip_window`).

**Consecutive agreement (`_update_agreement`)**: a window classification only *counts* toward a state change if the window is full (`window_full` — fewer than `window_frames` observed yet means no verdict is even attempted, `_pending_color`/`_agreement_count` reset to null/0). Once full: if this window's color matches the previous pending color, `_agreement_count` increments; otherwise it resets to 1 with the new color as pending. `_stable_color` only updates once `_agreement_count >= consecutive_agreement_frames` (5) **and** the color actually differs from the current stable color (no-op logging/reassignment avoided otherwise). This is deliberately slow to change — five consecutive ~3-second-window agreements is a real, sustained observation, not a blip.

`observed_state()` prefers `_stable_color` if one has been declared; otherwise falls back to whatever the current (possibly not-yet-agreed) window classification says. This is what feeds the LED state reported at a gate timeout.

---

## 7. `ChargingGatePolicy` — the actual gate-grant decision

A much smaller, purpose-built state machine: one `deque[float]` of recent green-frame timestamps, nothing else persisted.

```python
def record(now_s, detail: FrameClassification) -> bool:
    drop timestamps older than now_s - charging_green_window_s   # 6s rolling window
    if RED or BLUE in detail.hues_present:
        clear all accumulated green evidence; return False        # non-charging signal resets qualification
    if detail.color != GREEN:
        return False                                              # dark/unknown frames: neutral, no evidence, no reset
    append now_s to the green-timestamp deque
    if len(deque) < charging_green_required_frames:                # need at least 3 green frames in-window
        return False
    span = deque[-1] - deque[0]
    return span >= charging_green_min_span_s                        # and they must span at least 3.5s
```

Three properties worth being precise about, since they're each independently tested:

- **Dark/unknown frames are neutral, not disqualifying.** A charging LED that flashes green-dark-green-dark doesn't lose its accumulated evidence during the dark gaps — only an *actual* red or blue observation clears it (`test_dark_frames_do_not_erase_green_evidence`). This is what makes the policy tolerant of real flashing hardware.
- **Red or blue anywhere in a multi-hue frame clears evidence, even if not the dominant color** — checked via `detail.hues_present`, which can include a hue present but not the frame's single reported `color` (e.g., a `BOOTING` frame with green *and* red present still clears, since red is in `hues_present`) — `test_multi_hue_frame_with_red_present_clears_even_when_not_the_dominant_color`.
- **Both a frame-count floor and a time-span floor are required.** A brief green flash during the EVSE's boot animation (which cycles through all colors, including green, briefly) must not grant the gate — requiring the accumulated green observations to span at least 3.5 real seconds is what rules that out (`test_two_second_green_segment_during_boot_does_not_grant`), independent of how many frames were captured in that time.

---

## 8. `gate_timeout_action(led_state)` — what the sequencer does when the gate never opens

A pure mapping, no state, used by `Sequencer._attempt_cycle` (its own doc, §5.3) only when `await_charging_gate` returns `success=False, degraded=False`:

| LED state at timeout | Action | Reason |
|---|---|---|
| `FAULTED` | `RETRY_EXTENDED_COOLDOWN` | `vision_gate_timeout_faulted_latched_ccid` — the **only** retryable case in the whole sequencer |
| `CAMERA_UNAVAILABLE` | `DEGRADED_FIXED_WAIT` | `vision_gate_camera_unavailable` (in practice this branch of `await_charging_gate` already returns `degraded=True` before this function is reached — see §9) |
| `READY` | `HALT` | `vision_gate_timeout_ready_no_charging_state` |
| `OFF_OR_UNKNOWN` | `HALT` | `vision_gate_timeout_led_off_or_unknown` |
| `BOOTING` | `HALT` | `vision_gate_timeout_stuck_booting` |
| anything else | `HALT` | `vision_gate_timeout_unexpected_state_{value}` — a defensive catch-all, not expected to be reachable given the closed `LedState` enum |

The `FAULTED`/retry case matches the locked-behavior note: a blinking-red (CCID latched/fault) LED is plausibly transient — one retry with an extended cooldown gives it a chance to clear before halting for real.

---

## 9. `await_charging_gate` — the orchestration function

This is what `Sequencer` actually calls (not the `CameraInterface.await_charging_gate` method — see the HAL doc §1 for that distinction). Per-tick loop:

```
loop until elapsed >= timeout:
    try: sample = camera.sample_state(now)
    except Exception: log, observe_dropped(), camera_lost = True   # vision must never kill the run — even an unexpected exception here is swallowed
    else:
        if sample unhealthy / CAMERA_UNAVAILABLE / no frame: observe_dropped()
        else:
            try: rgb = frame_to_rgb_array(sample.frame)
            except VisionFrameError: log, observe_dropped()
            else:
                classifier.observe(rgb)
                granted = gate_policy.record(now, classifier.last_frame_detail)
    if camera_lost or classifier.camera_failed:
        return _degrade_to_fixed_wait(...)     # sleeps degraded_fixed_wait_s, returns (False, CAMERA_UNAVAILABLE, degraded=True)
    if granted:
        return (True, CHARGING, False)
    sleep(frame_interval)
# loop exhausted without a grant or a degrade:
return (False, classifier.observed_state(), False)
```

Every failure surface — a raised exception from `camera.sample_state`, an unhealthy/failed sample, an unusable frame that fails to decode — routes to `observe_dropped()` and, if it accumulates past the consecutive-drop threshold (or happens on the very first sample as an outright exception), to `_degrade_to_fixed_wait`. That function logs at `ERROR`, appends `DEGRADED_FLAG_CAMERA_UNAVAILABLE` to the caller-supplied `degraded_flag_out` list (guarded against duplicate append), sleeps the full fixed wait itself (so the caller doesn't need its own separate degraded-wait logic), and returns — this is the "vision can never kill the run" guarantee made concrete: no matter how badly the camera fails, this function always returns a well-formed `ChargingGateResult` rather than propagating an exception up into the sequencer.

`classifier`/`gate_policy` can both be injected (tests do this to inspect internal state directly); if omitted, fresh ones are constructed from `config` and reset at the start of every call — so a new call to `await_charging_gate` always starts with a clean temporal window and clean green-evidence history, never carrying state over from a previous cycle's gate wait.

---

## 10. Deterministic fixture helpers

A dedicated section of the file (clearly marked off with a comment banner) for synthetic test data, also reused by `tools/calibrate_camera.py`'s bench checks before real footage is available:

- **`LED_FIXTURE_RGB`** — one canonical RGB triple per color.
- **`make_solid_frame(rgb, width, height, brightness, noise_sigma, seed)`** — a uniform-color frame, optionally dimmed and/or given seeded Gaussian noise (deterministic via `np.random.default_rng(seed)` — same seed always produces the same frame, which is what `test_fixtures_are_deterministic` checks).
- **`make_led_frame`** — `make_solid_frame` keyed by `LedColor` via `LED_FIXTURE_RGB`.
- **`make_blinking_sequence(color, frame_count, on_frames, off_frames, ...)`** — alternates between the given color and OFF on a fixed period; the whole point of this fixture existing is that varying `on_frames`/`off_frames` must never change the classification outcome (blink rate is ignored by design).
- **`make_booting_sequence`** — cycles blue→off→green→off→red→off, which must classify as `BOOTING` throughout.
- **`make_exposure_ramp(color, frame_count, start_brightness, end_brightness, ...)`** — linear brightness ramp, used to test the OFF/UNKNOWN boundary and confirm classification survives exposure drift down to some floor.
- **`apply_dropped_frames(frames, drop_indices)`** — replaces frames at given indices with `None`, simulating dropped camera reads within an otherwise-normal sequence.
- **`frames_to_bgr_bytes`** — the inverse of `frame_to_rgb_array`, used to build synthetic `CameraFrame.frame_bgr` payloads for fixtures/tests.

---

## 11. Test coverage map

| Behavior | Test class |
|---|---|
| HSV math correctness, wrapping hue ranges, non-RGB rejection | `HsvConversionTests` |
| Per-frame classification: each single state, BOOTING on multi-hue, UNKNOWN vs OFF distinction, confidence ordering, noise tolerance, ROI restriction/rejection, center-ROI fallback | `FrameClassificationTests` |
| Window depth/agreement defaults, state-not-declared-early, blink-rate independence, booting/ready/faulted/off window outcomes, full-clean-window requirement for a transition, single-spurious-frame tolerance, exposure ramp boundaries, dropped-frame tolerance, camera-failure flagging, `reset()`, fixture determinism | `TemporalWindowTests` |
| `CameraFrame` BGR↔RGB round-trip, payload-size validation | `CameraFrameDecodingTests` |
| Full `await_charging_gate` orchestration: happy path (blinking green), minimum-span enforcement, transient-hue tolerance, red-then-green non-immediate-grant, all timeout/action branches (faulted→retry, blue/off/booting→halt), camera degrade (both explicit failure and raised exception), transient-drop tolerance (doesn't degrade), determinism across repeated runs, timeout validation | `ChargingGateTests` |
| `ChargingGatePolicy` in isolation: continuous vs flashing green, dark-frame tolerance, blue/red/off never granting, boot-animation green segment rejected, below-minimum-frames rejected, red clearing evidence (including via `hues_present` on a non-dominant color), evidence expiry outside the window, `reset()` | `ChargingGatePolicyTests` |
| Every `gate_timeout_action` branch mapped | `GateTimeoutActionTests` |
| `LedOpticalConfig` validation (window/agreement relationship, threshold ranges, hue range validity, ROI validity) | `ConfigValidationTests` |

---

## 12. Things to know if you're about to change this file

- **Don't merge the two decision layers.** If you're tempted to make `ChargingGatePolicy` reuse `LedClassifier`'s window/agreement state instead of its own timestamp deque, remember why they're separate: the grant policy needs to tolerate flashing (dark gaps between green frames), while the window classifier's job is to be *slow and certain* about declaring an overall LED state. Collapsing them risks either never granting on real flashing hardware or granting too eagerly.
- `hues_present` (which colors are present in a frame) and `color` (the single reported classification) are not the same thing and both matter — `ChargingGatePolicy.record` deliberately checks `hues_present` for red/blue (catches a multi-hue frame even when red/blue isn't dominant) but checks `color` for green (requires green to actually be *the* classification, not just present alongside something else).
- Any new `LedColor` or `LedState` value needs an entry in `_COLOR_TO_LED_STATE` and in `gate_timeout_action`'s branch table, or it silently falls through to the generic catch-all halt reason.
- `window_hue_min_frames`, `consecutive_agreement_frames`, `charging_green_required_frames`/`charging_green_min_span_s` are four *different* debounce/persistence knobs across the two layers — know which one you're actually trying to tune before changing a number, since they don't all affect the same decision.
