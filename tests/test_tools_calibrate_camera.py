from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ccid.classify import (
    DEFAULT_OPTICAL_CONFIG,
    LedColor,
    RegionOfInterest,
    make_blinking_sequence,
    make_led_frame,
    make_solid_frame,
)
from ccid.hal.camera_sim import CameraSim
from ccid.states import LedState
from tools import calibrate_camera


class CalibrateCameraToolTests(unittest.TestCase):
    def test_parse_roi_arg_round_trip(self) -> None:
        roi = calibrate_camera.parse_roi_arg("1,2,3,4")
        self.assertEqual(roi, RegionOfInterest(x=1, y=2, width=3, height=4))
        self.assertIsNone(calibrate_camera.parse_roi_arg(None))

    def test_parse_roi_arg_rejects_malformed_input(self) -> None:
        with self.assertRaises(ValueError):
            calibrate_camera.parse_roi_arg("1,2,3")

    def test_resolve_roi_prefers_explicit_over_fallback(self) -> None:
        explicit = RegionOfInterest(x=1, y=1, width=2, height=2)
        resolved = calibrate_camera.resolve_roi((16, 16), explicit)
        self.assertEqual(resolved, explicit)

        fallback = calibrate_camera.resolve_roi((16, 16), None, fraction=0.5)
        self.assertEqual(fallback, RegionOfInterest(x=4, y=4, width=8, height=8))

    def test_save_and_load_roi_round_trip(self) -> None:
        roi = RegionOfInterest(x=2, y=3, width=4, height=5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roi.json"
            calibrate_camera.save_roi(roi, path)
            loaded = calibrate_camera.load_roi(path)
        self.assertEqual(loaded, roi)

    def test_propose_hue_range_recovers_default_blue_band(self) -> None:
        roi = RegionOfInterest(x=0, y=0, width=16, height=16)
        frames = [make_led_frame(LedColor.BLUE, width=16, height=16) for _ in range(5)]
        hue_range = calibrate_camera.propose_hue_range(frames, roi, DEFAULT_OPTICAL_CONFIG)
        # The fixture's fixed blue swatch should land inside the default band.
        default_band = DEFAULT_OPTICAL_CONFIG.blue_hue
        self.assertTrue(default_band.low_deg <= hue_range.low_deg <= default_band.high_deg)
        self.assertTrue(default_band.low_deg <= hue_range.high_deg <= default_band.high_deg)

    def test_propose_hue_ranges_for_all_colors(self) -> None:
        roi = RegionOfInterest(x=0, y=0, width=16, height=16)
        frames_by_color = {
            LedColor.BLUE: [make_led_frame(LedColor.BLUE, width=16, height=16)],
            LedColor.GREEN: [make_led_frame(LedColor.GREEN, width=16, height=16)],
            LedColor.RED: [make_led_frame(LedColor.RED, width=16, height=16)],
        }
        proposed = calibrate_camera.propose_hue_ranges(frames_by_color, roi)
        self.assertEqual(set(proposed.keys()), {LedColor.BLUE, LedColor.GREEN, LedColor.RED})

    def test_propose_hue_range_raises_when_no_lit_pixels(self) -> None:
        roi = RegionOfInterest(x=0, y=0, width=16, height=16)
        dark_frames = [make_solid_frame((0, 0, 0), width=16, height=16)]
        with self.assertRaises(ValueError):
            calibrate_camera.propose_hue_range(dark_frames, roi, DEFAULT_OPTICAL_CONFIG)

    def test_verify_temporal_classification_matches_blinking_sequences(self) -> None:
        roi = RegionOfInterest(x=0, y=0, width=16, height=16)
        frames_by_expected = {
            LedColor.BLUE: make_blinking_sequence(LedColor.BLUE, 120, on_frames=7, off_frames=7, width=16, height=16),
            LedColor.GREEN: make_blinking_sequence(LedColor.GREEN, 120, on_frames=7, off_frames=7, width=16, height=16),
            LedColor.RED: make_blinking_sequence(LedColor.RED, 120, on_frames=7, off_frames=7, width=16, height=16),
        }
        report = calibrate_camera.verify_temporal_classification(frames_by_expected, roi)
        for color in (LedColor.BLUE, LedColor.GREEN, LedColor.RED):
            self.assertTrue(report[color.value]["matched"], report[color.value])

    def test_verify_temporal_classification_reports_mismatch(self) -> None:
        roi = RegionOfInterest(x=0, y=0, width=16, height=16)
        # Label frames as expected-BLUE but actually feed solid green: must not match.
        frames_by_expected = {
            LedColor.BLUE: [make_led_frame(LedColor.GREEN, width=16, height=16) for _ in range(60)],
        }
        report = calibrate_camera.verify_temporal_classification(frames_by_expected, roi)
        self.assertFalse(report[LedColor.BLUE.value]["matched"])

    def test_build_replay_footage_and_write_replay_file_round_trip_through_camera_sim(self) -> None:
        sequences = {
            LedState.CHARGING: make_blinking_sequence(LedColor.GREEN, 10, width=8, height=8),
            LedState.FAULTED: make_blinking_sequence(LedColor.RED, 10, width=8, height=8),
        }
        fixtures = calibrate_camera.build_replay_footage(sequences)
        self.assertEqual(len(fixtures), 20)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            calibrate_camera.write_replay_file(fixtures, path)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 20)
            self.assertIn("led_state", payload[0])
            self.assertIn("frame_bgr_base64", payload[0])

            # Must load back cleanly through the real CameraSim replay loader.
            camera = CameraSim(monotonic_now=lambda: 0.0, replay_file=path)
            camera.start()
            sample = camera.sample_state(0.0)
            self.assertIsNotNone(sample.frame)
            camera.stop()

    def test_build_replay_footage_rejects_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            calibrate_camera.build_replay_footage({})

    def test_load_frames_from_directory_missing_dependency_or_path(self) -> None:
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            calibrate_camera.load_frames_from_directory("/path/does/not/exist")


if __name__ == "__main__":
    unittest.main()
