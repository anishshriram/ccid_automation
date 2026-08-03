"""Vision classification of the EVSE status LED.

Locked behavior (handoff section 8):

- LED states: off (semi-transparent grey), blue, green, red. Blinking through all
  colours = booting. Solid or blinking blue = ready. Blinking green = charging.
  Blinking red = faulted.
- Classification uses HSV hue presence over an approximately three-second window at
  ~15 fps, over a single fixed ROI. Blink *rate* is deliberately ignored.
- A state is only declared after a configurable number of consecutive agreeing
  window classifications.
- Vision must never be able to kill the run. If the camera fails, the gate degrades
  to a fixed 60 s wait and the run continues in logged degraded mode.
- Vision may grant the charging gate and may record red as secondary evidence.
  It must never calculate trip time.

All optical thresholds live in `LedOpticalConfig`; there are no scattered magic
constants in the classification path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import logging
import math
import time
from typing import Callable, Iterable, MutableSequence, NamedTuple, Sequence

import numpy as np

from ccid.errors import VisionFrameError
from ccid.hal.base import CameraFrame, CameraHealth, CameraInterface
from ccid.states import LedState

_LOGGER = logging.getLogger(__name__)

_EPSILON = 1e-12

DEGRADED_FLAG_CAMERA_UNAVAILABLE = "vision_camera_unavailable_fixed_wait"


class LedColor(str, Enum):
    """Raw optical classification of the LED, before domain interpretation."""

    OFF = "off"
    BLUE = "blue"
    GREEN = "green"
    RED = "red"
    BOOTING = "booting"
    UNKNOWN = "unknown"


class GateTimeoutAction(str, Enum):
    """Sequencer response required for a vision-gate timeout (handoff section 8)."""

    RETRY_EXTENDED_COOLDOWN = "RETRY_EXTENDED_COOLDOWN"
    HALT = "HALT"
    DEGRADED_FIXED_WAIT = "DEGRADED_FIXED_WAIT"


_COLOR_TO_LED_STATE: dict[LedColor, LedState] = {
    LedColor.OFF: LedState.OFF_OR_UNKNOWN,
    LedColor.UNKNOWN: LedState.OFF_OR_UNKNOWN,
    LedColor.BLUE: LedState.READY,
    LedColor.GREEN: LedState.CHARGING,
    LedColor.RED: LedState.FAULTED,
    LedColor.BOOTING: LedState.BOOTING,
}


def led_state_for_color(color: LedColor) -> LedState:
    """Map an optical colour classification onto the domain LED state."""

    return _COLOR_TO_LED_STATE[color]


@dataclass(frozen=True)
class HueRange:
    """Inclusive hue band in degrees. `low_deg > high_deg` means the band wraps 360."""

    low_deg: float
    high_deg: float

    def __post_init__(self) -> None:
        for name, value in (("low_deg", self.low_deg), ("high_deg", self.high_deg)):
            if not math.isfinite(value) or not 0.0 <= value < 360.0:
                raise ValueError(f"HueRange.{name} must be in [0, 360): {value}")

    @property
    def wraps(self) -> bool:
        return self.low_deg > self.high_deg

    def mask(self, hue_deg: np.ndarray) -> np.ndarray:
        if self.wraps:
            return (hue_deg >= self.low_deg) | (hue_deg <= self.high_deg)
        return (hue_deg >= self.low_deg) & (hue_deg <= self.high_deg)


@dataclass(frozen=True)
class RegionOfInterest:
    """Fixed pixel window over the LED. Origin is top-left."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError("RegionOfInterest origin must be non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("RegionOfInterest width and height must be > 0")

    def crop(self, frame: np.ndarray) -> np.ndarray:
        frame_height, frame_width = frame.shape[0], frame.shape[1]
        if self.y + self.height > frame_height or self.x + self.width > frame_width:
            raise VisionFrameError(
                f"ROI {self} does not fit frame of size {frame_width}x{frame_height}"
            )
        return frame[self.y : self.y + self.height, self.x : self.x + self.width]


def center_roi(width: int, height: int, fraction: float = 0.5) -> RegionOfInterest:
    """Fallback ROI: centred box covering `fraction` of each frame dimension."""

    if width <= 0 or height <= 0:
        raise ValueError("Frame width and height must be > 0")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    roi_width = max(1, int(round(width * fraction)))
    roi_height = max(1, int(round(height * fraction)))
    return RegionOfInterest(
        x=(width - roi_width) // 2,
        y=(height - roi_height) // 2,
        width=roi_width,
        height=roi_height,
    )


@dataclass(frozen=True)
class LedOpticalConfig:
    """All optical and temporal thresholds for LED classification."""

    red_hue: HueRange = HueRange(345.0, 15.0)
    green_hue: HueRange = HueRange(85.0, 165.0)
    blue_hue: HueRange = HueRange(185.0, 265.0)
    min_saturation: float = 0.30
    min_value: float = 0.22
    off_value_threshold: float = 0.30
    min_pixel_fraction: float = 0.02
    confidence_reference_value: float = 0.60
    window_s: float = 3.0
    frame_rate_hz: float = 15.0
    consecutive_agreement_frames: int = 5
    window_hue_min_frames: int = 2
    max_consecutive_dropped_frames: int = 15
    degraded_fixed_wait_s: float = 60.0
    gate_timeout_s: float = 90.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_saturation <= 1.0:
            raise ValueError("min_saturation must be in [0, 1]")
        if not 0.0 <= self.min_value <= 1.0:
            raise ValueError("min_value must be in [0, 1]")
        if not 0.0 <= self.off_value_threshold <= 1.0:
            raise ValueError("off_value_threshold must be in [0, 1]")
        if not 0.0 < self.min_pixel_fraction <= 1.0:
            raise ValueError("min_pixel_fraction must be in (0, 1]")
        if not 0.0 < self.confidence_reference_value <= 1.0:
            raise ValueError("confidence_reference_value must be in (0, 1]")
        if self.window_s <= 0.0:
            raise ValueError("window_s must be > 0")
        if self.frame_rate_hz <= 0.0:
            raise ValueError("frame_rate_hz must be > 0")
        if self.consecutive_agreement_frames < 1:
            raise ValueError("consecutive_agreement_frames must be >= 1")
        if self.window_hue_min_frames < 1:
            raise ValueError("window_hue_min_frames must be >= 1")
        if self.max_consecutive_dropped_frames < 1:
            raise ValueError("max_consecutive_dropped_frames must be >= 1")
        if self.degraded_fixed_wait_s <= 0.0:
            raise ValueError("degraded_fixed_wait_s must be > 0")
        if self.gate_timeout_s <= 0.0:
            raise ValueError("gate_timeout_s must be > 0")
        if self.window_frames < self.consecutive_agreement_frames:
            raise ValueError("window must hold at least consecutive_agreement_frames frames")

    @property
    def window_frames(self) -> int:
        return max(1, int(round(self.window_s * self.frame_rate_hz)))

    @property
    def frame_interval_s(self) -> float:
        return 1.0 / self.frame_rate_hz

    def hue_ranges(self) -> dict[LedColor, HueRange]:
        return {
            LedColor.RED: self.red_hue,
            LedColor.GREEN: self.green_hue,
            LedColor.BLUE: self.blue_hue,
        }


DEFAULT_OPTICAL_CONFIG = LedOpticalConfig()


@dataclass(frozen=True)
class FrameClassification:
    """Single-frame classification result."""

    color: LedColor
    confidence: float
    hues_present: frozenset[LedColor]
    hue_fractions: dict[LedColor, float]
    lit_fraction: float

    @property
    def led_state(self) -> LedState:
        return led_state_for_color(self.color)


@dataclass(frozen=True)
class WindowClassification:
    """Temporal-window classification result, ignoring blink rate."""

    color: LedColor
    confidence: float
    hues_present: frozenset[LedColor]
    frames_in_window: int
    window_full: bool

    @property
    def led_state(self) -> LedState:
        return led_state_for_color(self.color)


class ChargingGateResult(NamedTuple):
    """`(success, led_state_at_timeout, degraded)` as required by the sequencer."""

    success: bool
    led_state: LedState
    degraded: bool


def rgb_to_hsv(frame_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert an HxWx3 8-bit RGB array to (hue degrees, saturation, value)."""

    arr = np.asarray(frame_rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise VisionFrameError(f"Expected an HxWx3 RGB frame, got shape {arr.shape}")
    if arr.size == 0:
        raise VisionFrameError("Frame is empty")
    scaled = arr.astype(np.float64)
    if scaled.max(initial=0.0) > 1.0 or arr.dtype == np.uint8:
        scaled = scaled / 255.0
    scaled = np.clip(scaled, 0.0, 1.0)

    red = scaled[..., 0]
    green = scaled[..., 1]
    blue = scaled[..., 2]
    max_c = scaled.max(axis=-1)
    min_c = scaled.min(axis=-1)
    delta = max_c - min_c

    value = max_c
    saturation = np.where(max_c > _EPSILON, delta / np.maximum(max_c, _EPSILON), 0.0)

    hue = np.zeros_like(max_c)
    chromatic = delta > _EPSILON
    red_max = chromatic & (max_c == red)
    green_max = chromatic & (max_c == green) & ~red_max
    blue_max = chromatic & (max_c == blue) & ~red_max & ~green_max

    with np.errstate(invalid="ignore", divide="ignore"):
        hue[red_max] = ((green - blue)[red_max] / delta[red_max]) % 6.0
        hue[green_max] = ((blue - red)[green_max] / delta[green_max]) + 2.0
        hue[blue_max] = ((red - green)[blue_max] / delta[blue_max]) + 4.0
    hue = np.mod(hue * 60.0, 360.0)
    return hue, saturation, value


def frame_to_rgb_array(camera_frame: CameraFrame) -> np.ndarray:
    """Decode a HAL `CameraFrame` (raw BGR bytes) into an HxWx3 RGB array."""

    expected = camera_frame.width * camera_frame.height * 3
    if camera_frame.width <= 0 or camera_frame.height <= 0:
        raise VisionFrameError("CameraFrame dimensions must be positive")
    if len(camera_frame.frame_bgr) != expected:
        raise VisionFrameError(
            "CameraFrame payload size "
            f"{len(camera_frame.frame_bgr)} does not match {camera_frame.width}x"
            f"{camera_frame.height}x3 = {expected}"
        )
    flat = np.frombuffer(camera_frame.frame_bgr, dtype=np.uint8)
    bgr = flat.reshape((camera_frame.height, camera_frame.width, 3))
    return bgr[..., ::-1].copy()


class LedClassifier:
    """HSV LED classifier with a temporal window and consecutive-agreement gating."""

    def __init__(
        self,
        config: LedOpticalConfig | None = None,
        roi: RegionOfInterest | None = None,
    ) -> None:
        self.config = config or DEFAULT_OPTICAL_CONFIG
        self.roi = roi
        self._window: deque[FrameClassification] = deque(maxlen=self.config.window_frames)
        self._pending_color: LedColor | None = None
        self._agreement_count = 0
        self._stable_color: LedColor | None = None
        self._consecutive_dropped = 0
        self._dropped_total = 0
        self._frames_observed = 0

    # -- state accessors -------------------------------------------------

    @property
    def agreement_count(self) -> int:
        return self._agreement_count

    @property
    def stable_color(self) -> LedColor | None:
        return self._stable_color

    @property
    def stable_state(self) -> LedState | None:
        if self._stable_color is None:
            return None
        return led_state_for_color(self._stable_color)

    @property
    def dropped_frame_count(self) -> int:
        return self._dropped_total

    @property
    def consecutive_dropped_frames(self) -> int:
        return self._consecutive_dropped

    @property
    def frames_observed(self) -> int:
        return self._frames_observed

    @property
    def camera_failed(self) -> bool:
        return self._consecutive_dropped >= self.config.max_consecutive_dropped_frames

    def reset(self) -> None:
        self._window.clear()
        self._pending_color = None
        self._agreement_count = 0
        self._stable_color = None
        self._consecutive_dropped = 0
        self._dropped_total = 0
        self._frames_observed = 0

    # -- classification --------------------------------------------------

    def classify_frame(self, rgb_frame: np.ndarray) -> tuple[LedColor, float]:
        """Classify one frame. Returns `(led_color, confidence)`."""

        detail = self.classify_frame_detailed(rgb_frame)
        return detail.color, detail.confidence

    def classify_frame_detailed(self, rgb_frame: np.ndarray) -> FrameClassification:
        arr = np.asarray(rgb_frame)
        if arr.ndim != 3 or arr.shape[2] != 3:
            raise VisionFrameError(f"Expected an HxWx3 RGB frame, got shape {arr.shape}")
        roi = self.roi if self.roi is not None else center_roi(arr.shape[1], arr.shape[0], 1.0)
        region = roi.crop(arr)

        hue, saturation, value = rgb_to_hsv(region)
        lit = (value >= self.config.min_value) & (saturation >= self.config.min_saturation)
        total_pixels = float(hue.size)
        lit_fraction = float(np.count_nonzero(lit)) / total_pixels

        hue_fractions: dict[LedColor, float] = {}
        masks: dict[LedColor, np.ndarray] = {}
        for color, hue_range in self.config.hue_ranges().items():
            mask = lit & hue_range.mask(hue)
            masks[color] = mask
            hue_fractions[color] = float(np.count_nonzero(mask)) / total_pixels

        present = frozenset(
            color
            for color, fraction in hue_fractions.items()
            if fraction >= self.config.min_pixel_fraction
        )

        if len(present) >= 2:
            color = LedColor.BOOTING
            combined = np.zeros_like(lit)
            for hue_color in present:
                combined |= masks[hue_color]
            confidence = self._confidence(combined, lit_fraction, saturation, value)
        elif len(present) == 1:
            color = next(iter(present))
            confidence = self._confidence(masks[color], lit_fraction, saturation, value)
        else:
            mean_value = float(value.mean())
            is_off = (
                lit_fraction < self.config.min_pixel_fraction
                or mean_value < self.config.off_value_threshold
            )
            if is_off:
                color = LedColor.OFF
                unlit_confidence = float(
                    np.clip(1.0 - lit_fraction / self.config.min_pixel_fraction, 0.0, 1.0)
                )
                dark_confidence = float(
                    np.clip(1.0 - mean_value / self.config.off_value_threshold, 0.0, 1.0)
                )
                confidence = max(unlit_confidence, dark_confidence)
            else:
                # Something is lit and bright, but no configured hue band explains it.
                color = LedColor.UNKNOWN
                confidence = 0.0

        return FrameClassification(
            color=color,
            confidence=confidence,
            hues_present=present,
            hue_fractions=hue_fractions,
            lit_fraction=lit_fraction,
        )

    def _confidence(
        self,
        mask: np.ndarray,
        lit_fraction: float,
        saturation: np.ndarray,
        value: np.ndarray,
    ) -> float:
        matched = float(np.count_nonzero(mask))
        if matched <= 0.0 or lit_fraction <= 0.0:
            return 0.0
        matched_fraction = matched / float(mask.size)
        dominance = float(np.clip(matched_fraction / lit_fraction, 0.0, 1.0))
        mean_saturation = float(np.clip(saturation[mask].mean(), 0.0, 1.0))
        mean_value = float(value[mask].mean())
        value_factor = float(
            np.clip(mean_value / self.config.confidence_reference_value, 0.0, 1.0)
        )
        return float(np.clip(dominance * mean_saturation * value_factor, 0.0, 1.0))

    # -- temporal window -------------------------------------------------

    def observe(self, rgb_frame: np.ndarray | None) -> WindowClassification:
        """Feed one frame (or `None` for a dropped frame) and re-evaluate the window."""

        if rgb_frame is None:
            return self.observe_dropped()
        try:
            detail = self.classify_frame_detailed(rgb_frame)
        except VisionFrameError:
            return self.observe_dropped()
        self._consecutive_dropped = 0
        self._frames_observed += 1
        self._window.append(detail)
        return self._update_agreement()

    def observe_dropped(self) -> WindowClassification:
        """Record a dropped/unusable frame without disturbing window agreement."""

        self._consecutive_dropped += 1
        self._dropped_total += 1
        return self.window_classification()

    def window_classification(self) -> WindowClassification:
        frames = list(self._window)
        window_full = len(frames) >= self.config.window_frames
        if not frames:
            return WindowClassification(
                color=LedColor.UNKNOWN,
                confidence=0.0,
                hues_present=frozenset(),
                frames_in_window=0,
                window_full=False,
            )

        counts: dict[LedColor, int] = {LedColor.RED: 0, LedColor.GREEN: 0, LedColor.BLUE: 0}
        for frame in frames:
            for hue_color in frame.hues_present:
                counts[hue_color] += 1
        present = frozenset(
            color
            for color, count in counts.items()
            if count >= min(self.config.window_hue_min_frames, len(frames))
        )

        if len(present) >= 2:
            color = LedColor.BOOTING
        elif LedColor.GREEN in present:
            color = LedColor.GREEN
        elif LedColor.RED in present:
            color = LedColor.RED
        elif LedColor.BLUE in present:
            color = LedColor.BLUE
        else:
            off_frames = sum(1 for frame in frames if frame.color == LedColor.OFF)
            color = LedColor.OFF if off_frames * 2 >= len(frames) else LedColor.UNKNOWN

        confidence = self._window_confidence(frames, color, present)
        return WindowClassification(
            color=color,
            confidence=confidence,
            hues_present=present,
            frames_in_window=len(frames),
            window_full=window_full,
        )

    @staticmethod
    def _window_confidence(
        frames: Sequence[FrameClassification],
        color: LedColor,
        present: frozenset[LedColor],
    ) -> float:
        if color == LedColor.UNKNOWN:
            return 0.0
        if color == LedColor.BOOTING:
            supporting = [f.confidence for f in frames if f.hues_present & present]
        elif color == LedColor.OFF:
            supporting = [f.confidence for f in frames if f.color == LedColor.OFF]
        else:
            supporting = [f.confidence for f in frames if color in f.hues_present]
        if not supporting:
            return 0.0
        return float(sum(supporting) / len(supporting))

    def _update_agreement(self) -> WindowClassification:
        window = self.window_classification()
        if not window.window_full:
            # A partial window cannot support a state declaration; the full
            # ~3 s of hue history is required before agreement is counted.
            self._pending_color = None
            self._agreement_count = 0
            return window
        if self._pending_color == window.color:
            self._agreement_count += 1
        else:
            self._pending_color = window.color
            self._agreement_count = 1
        if (
            self._agreement_count >= self.config.consecutive_agreement_frames
            and self._stable_color != window.color
        ):
            _LOGGER.debug(
                "LED state change declared: %s -> %s after %d agreeing windows",
                self._stable_color,
                window.color,
                self._agreement_count,
            )
            self._stable_color = window.color
        return window

    def observed_state(self) -> LedState:
        """Best available LED state, preferring the declared stable state."""

        if self._stable_color is not None:
            return led_state_for_color(self._stable_color)
        return led_state_for_color(self.window_classification().color)


def gate_timeout_action(led_state: LedState) -> tuple[GateTimeoutAction, str]:
    """Map the LED state observed at gate timeout onto the required response."""

    if led_state == LedState.FAULTED:
        return (
            GateTimeoutAction.RETRY_EXTENDED_COOLDOWN,
            "vision_gate_timeout_faulted_latched_ccid",
        )
    if led_state == LedState.CAMERA_UNAVAILABLE:
        return (GateTimeoutAction.DEGRADED_FIXED_WAIT, "vision_gate_camera_unavailable")
    if led_state == LedState.READY:
        return (GateTimeoutAction.HALT, "vision_gate_timeout_ready_no_charging_state")
    if led_state == LedState.OFF_OR_UNKNOWN:
        return (GateTimeoutAction.HALT, "vision_gate_timeout_led_off_or_unknown")
    if led_state == LedState.BOOTING:
        return (GateTimeoutAction.HALT, "vision_gate_timeout_stuck_booting")
    return (GateTimeoutAction.HALT, f"vision_gate_timeout_unexpected_state_{led_state.value}")


def await_charging_gate(
    camera: CameraInterface,
    roi: RegionOfInterest | None = None,
    timeout_s: float | None = None,
    degraded_flag_out: MutableSequence[str] | None = None,
    config: LedOpticalConfig | None = None,
    classifier: LedClassifier | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
) -> ChargingGateResult:
    """Poll the camera until charging is confirmed, the deadline expires, or vision fails.

    Returns `(success, led_state, degraded)`.

    - success: a stable charging (blinking green) state was declared.
    - led_state: the LED state observed at the moment of timeout, used by the
      sequencer to pick between extended-cooldown retry and immediate halt.
    - degraded: the camera became unavailable, a fixed wait was applied instead,
      and the run continues in logged degraded mode.
    """

    effective_config = config or (classifier.config if classifier is not None else DEFAULT_OPTICAL_CONFIG)
    effective_timeout = effective_config.gate_timeout_s if timeout_s is None else timeout_s
    if effective_timeout <= 0.0:
        raise ValueError("timeout_s must be > 0")
    log = logger or _LOGGER
    active = classifier if classifier is not None else LedClassifier(effective_config, roi)
    if classifier is not None and roi is not None:
        active.roi = roi
    active.reset()

    interval = effective_config.frame_interval_s
    start_s = monotonic()

    while True:
        now_s = monotonic()
        if now_s - start_s >= effective_timeout:
            break

        camera_lost = False
        try:
            sample = camera.sample_state(now_s)
        except Exception as exc:  # noqa: BLE001 - vision must never kill the run
            log.warning("Camera sample raised %s: %s", type(exc).__name__, exc)
            active.observe_dropped()
            camera_lost = True
        else:
            if (
                sample.health == CameraHealth.FAILED
                or sample.led_state == LedState.CAMERA_UNAVAILABLE
                or sample.frame is None
            ):
                active.observe_dropped()
            else:
                try:
                    rgb = frame_to_rgb_array(sample.frame)
                except VisionFrameError as exc:
                    log.warning("Unusable camera frame: %s", exc)
                    active.observe_dropped()
                else:
                    active.observe(rgb)

        if camera_lost or active.camera_failed:
            return _degrade_to_fixed_wait(
                config=effective_config,
                degraded_flag_out=degraded_flag_out,
                sleep=sleep,
                log=log,
            )

        if active.stable_color == LedColor.GREEN:
            log.info("Charging gate granted by vision after %.2f s", monotonic() - start_s)
            return ChargingGateResult(True, LedState.CHARGING, False)

        sleep(interval)

    observed = active.observed_state()
    log.warning(
        "Vision gate timed out after %.1f s with LED state %s",
        effective_timeout,
        observed.value,
    )
    return ChargingGateResult(False, observed, False)


def _degrade_to_fixed_wait(
    config: LedOpticalConfig,
    degraded_flag_out: MutableSequence[str] | None,
    sleep: Callable[[float], None],
    log: logging.Logger,
) -> ChargingGateResult:
    log.error(
        "Camera unavailable; degrading vision gate to a fixed %.0f s wait",
        config.degraded_fixed_wait_s,
    )
    if degraded_flag_out is not None and DEGRADED_FLAG_CAMERA_UNAVAILABLE not in degraded_flag_out:
        degraded_flag_out.append(DEGRADED_FLAG_CAMERA_UNAVAILABLE)
    sleep(config.degraded_fixed_wait_s)
    return ChargingGateResult(False, LedState.CAMERA_UNAVAILABLE, True)


# ---------------------------------------------------------------------------
# Deterministic fixtures
#
# Synthetic frames used by unit tests and by bench checks before real footage
# from `calibrate_camera.py` is available. Real replay footage remains the
# preferred source for `camera_sim`; these fixtures are explicitly synthetic.
# ---------------------------------------------------------------------------

LED_FIXTURE_RGB: dict[LedColor, tuple[int, int, int]] = {
    LedColor.OFF: (40, 40, 42),
    LedColor.BLUE: (20, 60, 235),
    LedColor.GREEN: (30, 210, 60),
    LedColor.RED: (230, 25, 30),
}


def make_solid_frame(
    rgb: tuple[int, int, int],
    width: int = 16,
    height: int = 16,
    brightness: float = 1.0,
    noise_sigma: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Deterministic solid-colour frame with optional brightness scaling and noise."""

    if width <= 0 or height <= 0:
        raise ValueError("width and height must be > 0")
    if brightness < 0.0:
        raise ValueError("brightness must be >= 0")
    if noise_sigma < 0.0:
        raise ValueError("noise_sigma must be >= 0")
    base = np.array(rgb, dtype=np.float64) * brightness
    frame = np.tile(base, (height, width, 1))
    if noise_sigma > 0.0:
        rng = np.random.default_rng(seed)
        frame = frame + rng.normal(0.0, noise_sigma, size=frame.shape)
    return np.clip(np.round(frame), 0, 255).astype(np.uint8)


def make_led_frame(
    color: LedColor,
    width: int = 16,
    height: int = 16,
    brightness: float = 1.0,
    noise_sigma: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Deterministic frame for one LED colour."""

    if color not in LED_FIXTURE_RGB:
        raise ValueError(f"No fixture RGB defined for {color}")
    return make_solid_frame(
        LED_FIXTURE_RGB[color],
        width=width,
        height=height,
        brightness=brightness,
        noise_sigma=noise_sigma,
        seed=seed,
    )


def make_blinking_sequence(
    color: LedColor,
    frame_count: int,
    on_frames: int = 7,
    off_frames: int = 7,
    width: int = 16,
    height: int = 16,
    noise_sigma: float = 0.0,
    seed: int = 0,
) -> list[np.ndarray]:
    """Blinking sequence for one colour; blink rate is irrelevant to classification."""

    if frame_count <= 0:
        raise ValueError("frame_count must be > 0")
    if on_frames <= 0 or off_frames < 0:
        raise ValueError("on_frames must be > 0 and off_frames >= 0")
    period = on_frames + off_frames
    frames: list[np.ndarray] = []
    for index in range(frame_count):
        phase_on = (index % period) < on_frames
        frame_color = color if phase_on else LedColor.OFF
        frames.append(
            make_led_frame(
                frame_color,
                width=width,
                height=height,
                noise_sigma=noise_sigma,
                seed=seed + index,
            )
        )
    return frames


def make_booting_sequence(
    frame_count: int,
    width: int = 16,
    height: int = 16,
    noise_sigma: float = 0.0,
    seed: int = 0,
) -> list[np.ndarray]:
    """Boot sequence: blinking through all colours, which must classify as booting."""

    cycle = [LedColor.BLUE, LedColor.OFF, LedColor.GREEN, LedColor.OFF, LedColor.RED, LedColor.OFF]
    return [
        make_led_frame(
            cycle[index % len(cycle)],
            width=width,
            height=height,
            noise_sigma=noise_sigma,
            seed=seed + index,
        )
        for index in range(frame_count)
    ]


def make_exposure_ramp(
    color: LedColor,
    frame_count: int,
    start_brightness: float = 1.0,
    end_brightness: float = 0.1,
    width: int = 16,
    height: int = 16,
    noise_sigma: float = 0.0,
    seed: int = 0,
) -> list[np.ndarray]:
    """Exposure variation fixture: brightness ramps linearly across the sequence."""

    if frame_count <= 0:
        raise ValueError("frame_count must be > 0")
    if frame_count == 1:
        steps = [start_brightness]
    else:
        span = end_brightness - start_brightness
        steps = [start_brightness + span * (i / (frame_count - 1)) for i in range(frame_count)]
    return [
        make_led_frame(
            color,
            width=width,
            height=height,
            brightness=step,
            noise_sigma=noise_sigma,
            seed=seed + index,
        )
        for index, step in enumerate(steps)
    ]


def apply_dropped_frames(
    frames: Sequence[np.ndarray],
    drop_indices: Iterable[int],
) -> list[np.ndarray | None]:
    """Dropped-frame simulation: replace the given indices with `None`."""

    dropped = set(drop_indices)
    return [None if index in dropped else frame for index, frame in enumerate(frames)]


def frames_to_bgr_bytes(frame_rgb: np.ndarray) -> bytes:
    """Encode an RGB frame as the raw BGR byte payload used by `CameraFrame`."""

    arr = np.asarray(frame_rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise VisionFrameError(f"Expected an HxWx3 RGB frame, got shape {arr.shape}")
    return arr.astype(np.uint8)[..., ::-1].tobytes()


__all__ = [
    "DEFAULT_OPTICAL_CONFIG",
    "DEGRADED_FLAG_CAMERA_UNAVAILABLE",
    "ChargingGateResult",
    "FrameClassification",
    "GateTimeoutAction",
    "HueRange",
    "LED_FIXTURE_RGB",
    "LedClassifier",
    "LedColor",
    "LedOpticalConfig",
    "RegionOfInterest",
    "WindowClassification",
    "apply_dropped_frames",
    "await_charging_gate",
    "center_roi",
    "frame_to_rgb_array",
    "frames_to_bgr_bytes",
    "gate_timeout_action",
    "led_state_for_color",
    "make_blinking_sequence",
    "make_booting_sequence",
    "make_exposure_ramp",
    "make_led_frame",
    "make_solid_frame",
    "rgb_to_hsv",
]
