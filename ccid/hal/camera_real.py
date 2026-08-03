from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Callable

from ccid.errors import HardwareInterfaceError
from ccid.hal.base import CameraFrame, CameraHealth, CameraInterface, CameraStateSample, ChargingGateToken
from ccid.states import LedState


class CameraRealError(HardwareInterfaceError):
    pass


@dataclass(frozen=True)
class CameraRealConfig:
    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    stale_after_s: float = 1.0
    max_buffer_frames: int = 5
    read_fail_limit: int = 15


class CameraReal(CameraInterface):
    """Bounded reader-thread camera implementation.

    Classification is injected via `state_classifier` so hardware I/O stays
    separate from domain logic.
    """

    def __init__(
        self,
        *,
        config: CameraRealConfig | None = None,
        monotonic_now: Callable[[], float] | None = None,
        capture_factory=None,
        state_classifier: Callable[[bytes, int, int], LedState] | None = None,
    ) -> None:
        self._cfg = config or CameraRealConfig()
        if self._cfg.max_buffer_frames <= 0:
            raise ValueError("max_buffer_frames must be > 0")
        self._now = monotonic_now or time.monotonic
        self._capture_factory = capture_factory
        self._state_classifier = state_classifier or (lambda _bgr, _w, _h: LedState.OFF_OR_UNKNOWN)
        self._capture = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._frames: deque[CameraFrame] = deque(maxlen=self._cfg.max_buffer_frames)
        self._latest: CameraFrame | None = None
        self._last_frame_monotonic_s: float | None = None
        self._consecutive_read_failures = 0

    def start(self) -> None:
        if self._running:
            return
        if self._capture_factory is None:
            try:
                import cv2  # type: ignore
            except Exception as exc:  # pragma: no cover - dependency/environment
                raise CameraRealError(
                    "opencv-python-headless is required for camera_real on target"
                ) from exc
            self._capture_factory = cv2.VideoCapture
            cap = self._capture_factory(self._cfg.device_index)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._cfg.width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._cfg.height))
            cap.set(cv2.CAP_PROP_FPS, float(self._cfg.fps))
        else:
            cap = self._capture_factory(self._cfg.device_index)

        if not cap or not cap.isOpened():
            raise CameraRealError(f"Could not open camera index {self._cfg.device_index}")

        self._capture = cap
        self._running = True
        self._consecutive_read_failures = 0
        self._thread = threading.Thread(target=self._reader_loop, name="camera-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

    def sample_state(self, now_monotonic_s: float) -> CameraStateSample:
        if not self._running:
            raise CameraRealError("Camera is not started")
        with self._lock:
            frame = self._latest
            last_frame_s = self._last_frame_monotonic_s
            failures = self._consecutive_read_failures
        if failures >= self._cfg.read_fail_limit:
            return CameraStateSample(
                led_state=LedState.CAMERA_UNAVAILABLE,
                observed_at_monotonic_s=now_monotonic_s,
                health=CameraHealth.FAILED,
                frame=frame,
            )
        if frame is None or last_frame_s is None:
            return CameraStateSample(
                led_state=LedState.OFF_OR_UNKNOWN,
                observed_at_monotonic_s=now_monotonic_s,
                health=CameraHealth.STALE,
                frame=frame,
            )
        age = now_monotonic_s - last_frame_s
        if age > self._cfg.stale_after_s:
            return CameraStateSample(
                led_state=LedState.OFF_OR_UNKNOWN,
                observed_at_monotonic_s=now_monotonic_s,
                health=CameraHealth.STALE,
                frame=frame,
            )
        led_state = self._state_classifier(frame.frame_bgr, frame.width, frame.height)
        return CameraStateSample(
            led_state=led_state,
            observed_at_monotonic_s=now_monotonic_s,
            health=CameraHealth.HEALTHY,
            frame=frame,
        )

    def await_charging_gate(
        self,
        cycle_index: int,
        timeout_s: float,
        now_monotonic_s: float,
    ) -> tuple[ChargingGateToken | None, CameraStateSample]:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        deadline = now_monotonic_s + timeout_s
        last = self.sample_state(now_monotonic_s)
        while self._now() <= deadline:
            sample = self.sample_state(self._now())
            last = sample
            if sample.led_state == LedState.CHARGING:
                return (
                    ChargingGateToken(cycle_index=cycle_index, granted_at_monotonic_s=sample.observed_at_monotonic_s),
                    sample,
                )
            if sample.health == CameraHealth.FAILED:
                return (None, sample)
            time.sleep(max(0.001, 1.0 / float(self._cfg.fps)))
        return (None, last)

    def latest_frame(self) -> CameraFrame | None:
        with self._lock:
            return self._latest

    def recent_frames(self) -> tuple[CameraFrame, ...]:
        with self._lock:
            return tuple(self._frames)

    def _reader_loop(self) -> None:
        while self._running:
            try:
                ok, frame = self._capture.read()
            except Exception:
                ok = False
                frame = None
            if not ok or frame is None:
                with self._lock:
                    self._consecutive_read_failures += 1
                time.sleep(0.01)
                continue

            bgr_bytes = frame.tobytes()
            h, w = int(frame.shape[0]), int(frame.shape[1])
            now_s = self._now()
            cam_frame = CameraFrame(
                frame_bgr=bgr_bytes,
                width=w,
                height=h,
                captured_at_utc=datetime.now(tz=timezone.utc),
                captured_at_monotonic_s=now_s,
                metadata={"source": "camera_real"},
            )
            with self._lock:
                self._consecutive_read_failures = 0
                self._latest = cam_frame
                self._last_frame_monotonic_s = now_s
                self._frames.append(cam_frame)
