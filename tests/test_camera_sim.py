from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest

from ccid.classify import await_charging_gate
from ccid.hal.base import CameraHealth
from ccid.hal.camera_sim import CameraSim, CameraSimError
from ccid.states import LedState


class _ManualClock:
    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s

    def now(self) -> float:
        return self.now_s


class CameraSimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _ManualClock()

    def test_default_fixture_sequence_reaches_charging(self) -> None:
        camera = CameraSim(monotonic_now=self.clock.now)
        camera.start()
        token, sample = camera.await_charging_gate(
            cycle_index=1,
            timeout_s=1.0,
            now_monotonic_s=self.clock.now(),
        )
        self.assertIsNotNone(token)
        self.assertEqual(sample.led_state, LedState.CHARGING)
        self.assertIsNotNone(sample.frame)
        self.assertEqual(sample.frame.metadata.get("source"), "fixture")
     
    def test_default_fixtures_reach_charging_through_optical_gate(self) -> None:
        camera = CameraSim(monotonic_now=self.clock.now)
        camera.start()

        def sleep(duration_s: float) -> None:
            self.clock.now_s += duration_s

        result = await_charging_gate(
            camera,
            timeout_s=5.0,
            monotonic=self.clock.now,
            sleep=sleep,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.led_state, LedState.CHARGING)
        self.assertFalse(result.degraded)

    def test_camera_failure_path(self) -> None:
        camera = CameraSim(monotonic_now=self.clock.now, fail_after_samples=1)
        camera.start()
        sample_1 = camera.sample_state(self.clock.now())
        sample_2 = camera.sample_state(self.clock.now())
        self.assertEqual(sample_1.health, CameraHealth.HEALTHY)
        self.assertEqual(sample_2.health, CameraHealth.FAILED)
        self.assertEqual(sample_2.led_state, LedState.CAMERA_UNAVAILABLE)

    def test_replay_file_loading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            replay_file = Path(tmp) / "replay.json"
            replay = [
                {
                    "led_state": "BOOTING",
                    "frame_bgr_base64": base64.b64encode(b"\x01\x02\x03").decode("ascii"),
                    "width": 1,
                    "height": 1,
                },
                {
                    "led_state": "CHARGING",
                    "frame_bgr_base64": base64.b64encode(b"\x03\x02\x01").decode("ascii"),
                    "width": 1,
                    "height": 1,
                },
            ]
            replay_file.write_text(json.dumps(replay), encoding="utf-8")
            camera = CameraSim(monotonic_now=self.clock.now, replay_file=replay_file)
            camera.start()
            token, sample = camera.await_charging_gate(
                cycle_index=5,
                timeout_s=1.0,
                now_monotonic_s=self.clock.now(),
            )
            self.assertIsNotNone(token)
            self.assertEqual(sample.led_state, LedState.CHARGING)
            self.assertEqual(sample.frame.metadata.get("source"), "replay")

    def test_bad_replay_format_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            replay_file = Path(tmp) / "bad.json"
            replay_file.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(CameraSimError):
                CameraSim(monotonic_now=self.clock.now, replay_file=replay_file)


if __name__ == "__main__":
    unittest.main()

