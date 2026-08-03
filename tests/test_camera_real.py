from __future__ import annotations

import unittest
import time

import numpy as np

from ccid.hal.base import CameraHealth
from ccid.hal.camera_real import CameraReal, CameraRealConfig
from ccid.states import LedState


class _FakeCapture:
    def __init__(self, frames: list[np.ndarray], fail_after: int | None = None) -> None:
        self._frames = frames
        self._index = 0
        self._fail_after = fail_after
        self._reads = 0

    def isOpened(self) -> bool:
        return True

    def read(self):
        self._reads += 1
        if self._fail_after is not None and self._reads > self._fail_after:
            return False, None
        if self._index >= len(self._frames):
            return True, self._frames[-1]
        frame = self._frames[self._index]
        self._index += 1
        return True, frame

    def release(self) -> None:
        return None

    def set(self, prop: float, value: float) -> None:
        del prop, value


class CameraRealTests(unittest.TestCase):
    def test_sample_state_reports_healthy_frame(self) -> None:
        now = [0.0]

        def monotonic() -> float:
            now[0] += 0.02
            return now[0]

        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        frame[..., 1] = 200
        capture = _FakeCapture([frame] * 5)
        camera = CameraReal(
            config=CameraRealConfig(fps=30, stale_after_s=0.5, read_fail_limit=5),
            monotonic_now=monotonic,
            capture_factory=lambda index: capture,
            state_classifier=lambda _bgr, _w, _h: LedState.CHARGING,
        )
        camera.start()
        try:
            # allow reader thread to fetch at least one frame
            for _ in range(10):
                sample = camera.sample_state(monotonic())
                if sample.health == CameraHealth.HEALTHY:
                    break
            self.assertEqual(sample.health, CameraHealth.HEALTHY)
            self.assertEqual(sample.led_state, LedState.CHARGING)
            self.assertIsNotNone(sample.frame)
        finally:
            camera.stop()

    def test_reader_failure_marks_camera_failed(self) -> None:
        now = [0.0]

        def monotonic() -> float:
            now[0] += 0.01
            return now[0]

        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        capture = _FakeCapture([frame], fail_after=1)
        camera = CameraReal(
            config=CameraRealConfig(fps=30, stale_after_s=0.2, read_fail_limit=2),
            monotonic_now=monotonic,
            capture_factory=lambda index: capture,
            state_classifier=lambda _bgr, _w, _h: LedState.OFF_OR_UNKNOWN,
        )
        camera.start()
        try:
            failed = None
            for _ in range(50):
                sample = camera.sample_state(monotonic())
                if sample.health == CameraHealth.FAILED:
                    failed = sample
                    break
                time.sleep(0.005)
            self.assertIsNotNone(failed)
            self.assertEqual(failed.led_state, LedState.CAMERA_UNAVAILABLE)
        finally:
            camera.stop()


if __name__ == "__main__":
    unittest.main()
