from __future__ import annotations

from datetime import datetime, timezone
import logging
import unittest

import numpy as np

from ccid.classify import (
    DEFAULT_OPTICAL_CONFIG,
    DEGRADED_FLAG_CAMERA_UNAVAILABLE,
    GateTimeoutAction,
    HueRange,
    LedClassifier,
    LedColor,
    LedOpticalConfig,
    RegionOfInterest,
    apply_dropped_frames,
    await_charging_gate,
    center_roi,
    frame_to_rgb_array,
    frames_to_bgr_bytes,
    gate_timeout_action,
    led_state_for_color,
    make_blinking_sequence,
    make_booting_sequence,
    make_exposure_ramp,
    make_led_frame,
    make_solid_frame,
    rgb_to_hsv,
)
from ccid.errors import VisionFrameError
from ccid.hal.base import CameraFrame, CameraHealth, CameraInterface, CameraStateSample
from ccid.states import LedState


TEST_CONFIG = LedOpticalConfig(window_s=1.0, frame_rate_hz=15.0)

# Vision degradation is expected in several tests; keep its logging out of test output.
_VISION_LOGGER = logging.getLogger("ccid.classify")
_VISION_LOGGER.addHandler(logging.NullHandler())
_VISION_LOGGER.propagate = False


class _FakeClock:
    """Monotonic clock advanced only by simulated sleeps."""

    def __init__(self, start_s: float = 1000.0) -> None:
        self.now_s = start_s
        self.slept_s: list[float] = []

    def monotonic(self) -> float:
        return self.now_s

    def sleep(self, seconds: float) -> None:
        self.slept_s.append(seconds)
        self.now_s += seconds

    @property
    def total_slept_s(self) -> float:
        return sum(self.slept_s)


class _FixtureCamera(CameraInterface):
    """Deterministic camera replaying a fixed list of RGB frames.

    `None` entries simulate dropped frames. After the list is exhausted the last
    entry repeats, unless `fail_after_samples` is set, in which case the camera
    reports FAILED health.
    """

    def __init__(
        self,
        frames: list[np.ndarray | None],
        fail_after_samples: int | None = None,
        raise_after_samples: int | None = None,
    ) -> None:
        if not frames:
            raise ValueError("frames must not be empty")
        self._frames = frames
        self._fail_after_samples = fail_after_samples
        self._raise_after_samples = raise_after_samples
        self._cursor = 0
        self.sample_calls = 0
        self.started = False
        self._latest: CameraFrame | None = None

    def start(self) -> None:
        self.started = True
        self._cursor = 0
        self.sample_calls = 0

    def stop(self) -> None:
        self.started = False

    def sample_state(self, now_monotonic_s: float) -> CameraStateSample:
        self.sample_calls += 1
        if self._raise_after_samples is not None and self.sample_calls > self._raise_after_samples:
            raise RuntimeError("simulated camera transport failure")
        if self._fail_after_samples is not None and self.sample_calls > self._fail_after_samples:
            return CameraStateSample(
                led_state=LedState.CAMERA_UNAVAILABLE,
                observed_at_monotonic_s=now_monotonic_s,
                health=CameraHealth.FAILED,
                frame=None,
            )
        frame_rgb = self._frames[self._cursor]
        self._cursor = min(self._cursor + 1, len(self._frames) - 1)
        if frame_rgb is None:
            return CameraStateSample(
                led_state=LedState.OFF_OR_UNKNOWN,
                observed_at_monotonic_s=now_monotonic_s,
                health=CameraHealth.STALE,
                frame=None,
            )
        camera_frame = CameraFrame(
            frame_bgr=frames_to_bgr_bytes(frame_rgb),
            width=frame_rgb.shape[1],
            height=frame_rgb.shape[0],
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=now_monotonic_s,
            metadata={"source": "fixture", "fixture_note": "deterministic_test_fixture"},
        )
        self._latest = camera_frame
        return CameraStateSample(
            led_state=LedState.OFF_OR_UNKNOWN,
            observed_at_monotonic_s=now_monotonic_s,
            health=CameraHealth.HEALTHY,
            frame=camera_frame,
        )

    def await_charging_gate(self, cycle_index, timeout_s, now_monotonic_s):  # pragma: no cover
        raise NotImplementedError("classification is performed by ccid.classify")

    def latest_frame(self) -> CameraFrame | None:
        return self._latest


def _steady_sequence(color: LedColor, count: int) -> list[np.ndarray]:
    return [make_led_frame(color) for _ in range(count)]


class HsvConversionTests(unittest.TestCase):
    def test_hue_saturation_value_for_primaries(self) -> None:
        frame = np.array(
            [[[255, 0, 0], [0, 255, 0], [0, 0, 255], [0, 0, 0], [255, 255, 255]]],
            dtype=np.uint8,
        )
        hue, saturation, value = rgb_to_hsv(frame)
        self.assertAlmostEqual(float(hue[0, 0]), 0.0, places=6)
        self.assertAlmostEqual(float(hue[0, 1]), 120.0, places=6)
        self.assertAlmostEqual(float(hue[0, 2]), 240.0, places=6)
        self.assertAlmostEqual(float(saturation[0, 3]), 0.0, places=6)
        self.assertAlmostEqual(float(value[0, 3]), 0.0, places=6)
        self.assertAlmostEqual(float(saturation[0, 4]), 0.0, places=6)
        self.assertAlmostEqual(float(value[0, 4]), 1.0, places=6)

    def test_rejects_non_rgb_frame(self) -> None:
        with self.assertRaises(VisionFrameError):
            rgb_to_hsv(np.zeros((4, 4), dtype=np.uint8))

    def test_wrapping_hue_range(self) -> None:
        wrapping = HueRange(345.0, 15.0)
        self.assertTrue(wrapping.wraps)
        mask = wrapping.mask(np.array([350.0, 5.0, 120.0]))
        self.assertTrue(bool(mask[0]))
        self.assertTrue(bool(mask[1]))
        self.assertFalse(bool(mask[2]))


class FrameClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = LedClassifier(TEST_CONFIG)

    def test_detects_each_single_led_state(self) -> None:
        expectations = {
            LedColor.OFF: LedState.OFF_OR_UNKNOWN,
            LedColor.BLUE: LedState.READY,
            LedColor.GREEN: LedState.CHARGING,
            LedColor.RED: LedState.FAULTED,
        }
        for color, expected_state in expectations.items():
            with self.subTest(color=color):
                state, confidence = self.classifier.classify_frame(make_led_frame(color))
                self.assertEqual(state, color)
                self.assertEqual(led_state_for_color(state), expected_state)
                self.assertGreater(confidence, 0.5)

    def test_multiple_hues_in_one_frame_classify_as_booting(self) -> None:
        frame = np.concatenate(
            [make_led_frame(LedColor.BLUE), make_led_frame(LedColor.RED)], axis=1
        )
        state, confidence = self.classifier.classify_frame(frame)
        self.assertEqual(state, LedColor.BOOTING)
        self.assertGreater(confidence, 0.5)

    def test_unrecognized_hue_is_unknown_with_zero_confidence(self) -> None:
        # Saturated magenta: lit, but outside every configured LED hue band.
        state, confidence = self.classifier.classify_frame(make_solid_frame((230, 20, 220)))
        self.assertEqual(state, LedColor.UNKNOWN)
        self.assertEqual(confidence, 0.0)

    def test_grey_led_is_off_not_unknown(self) -> None:
        state, confidence = self.classifier.classify_frame(make_solid_frame((120, 120, 122)))
        self.assertEqual(state, LedColor.OFF)
        self.assertGreater(confidence, 0.9)

    def test_confidence_scoring_orders_pure_above_desaturated_and_dim(self) -> None:
        pure_conf = self.classifier.classify_frame_detailed(make_led_frame(LedColor.BLUE)).confidence
        desaturated = self.classifier.classify_frame_detailed(
            make_solid_frame((120, 140, 235))
        ).confidence
        dim = self.classifier.classify_frame_detailed(
            make_led_frame(LedColor.BLUE, brightness=0.4)
        ).confidence
        self.assertGreater(pure_conf, desaturated)
        self.assertGreater(pure_conf, dim)
        self.assertLessEqual(pure_conf, 1.0)
        self.assertGreaterEqual(desaturated, 0.0)

    def test_sensor_noise_does_not_change_classification(self) -> None:
        for color in (LedColor.BLUE, LedColor.GREEN, LedColor.RED, LedColor.OFF):
            with self.subTest(color=color):
                frame = make_led_frame(color, noise_sigma=8.0, seed=7)
                state, _ = self.classifier.classify_frame(frame)
                self.assertEqual(state, color)

    def test_roi_restricts_classification_region(self) -> None:
        frame = np.concatenate(
            [make_led_frame(LedColor.GREEN), make_led_frame(LedColor.RED)], axis=1
        )
        left = LedClassifier(TEST_CONFIG, RegionOfInterest(0, 0, 16, 16))
        right = LedClassifier(TEST_CONFIG, RegionOfInterest(16, 0, 16, 16))
        self.assertEqual(left.classify_frame(frame)[0], LedColor.GREEN)
        self.assertEqual(right.classify_frame(frame)[0], LedColor.RED)

    def test_roi_outside_frame_is_rejected(self) -> None:
        classifier = LedClassifier(TEST_CONFIG, RegionOfInterest(0, 0, 64, 64))
        with self.assertRaises(VisionFrameError):
            classifier.classify_frame(make_led_frame(LedColor.BLUE))

    def test_center_roi_fallback(self) -> None:
        roi = center_roi(32, 20, fraction=0.5)
        self.assertEqual((roi.x, roi.y, roi.width, roi.height), (8, 5, 16, 10))


class TemporalWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = LedClassifier(TEST_CONFIG)

    def _feed(self, frames: list[np.ndarray | None]) -> None:
        for frame in frames:
            self.classifier.observe(frame)

    def test_default_config_uses_three_second_window_and_five_frame_agreement(self) -> None:
        self.assertEqual(DEFAULT_OPTICAL_CONFIG.consecutive_agreement_frames, 5)
        self.assertAlmostEqual(DEFAULT_OPTICAL_CONFIG.window_s, 3.0)
        self.assertAlmostEqual(DEFAULT_OPTICAL_CONFIG.frame_rate_hz, 15.0)
        self.assertEqual(DEFAULT_OPTICAL_CONFIG.window_frames, 45)

    def test_state_not_declared_before_five_agreeing_windows(self) -> None:
        window_frames = TEST_CONFIG.window_frames
        self._feed(_steady_sequence(LedColor.GREEN, window_frames - 1))
        # Partial windows never count towards agreement.
        self.assertEqual(self.classifier.agreement_count, 0)
        self.assertIsNone(self.classifier.stable_color)
        for expected_agreement in range(1, TEST_CONFIG.consecutive_agreement_frames + 1):
            self.classifier.observe(make_led_frame(LedColor.GREEN))
            self.assertEqual(self.classifier.agreement_count, expected_agreement)
            if expected_agreement < TEST_CONFIG.consecutive_agreement_frames:
                self.assertIsNone(self.classifier.stable_color)
        self.assertEqual(self.classifier.agreement_count, 5)
        self.assertEqual(self.classifier.stable_color, LedColor.GREEN)
        self.assertEqual(self.classifier.stable_state, LedState.CHARGING)

    def test_blinking_green_is_charging_regardless_of_blink_rate(self) -> None:
        for on_frames, off_frames in ((7, 7), (2, 2), (1, 4)):
            with self.subTest(on=on_frames, off=off_frames):
                classifier = LedClassifier(TEST_CONFIG)
                frames = make_blinking_sequence(
                    LedColor.GREEN,
                    frame_count=TEST_CONFIG.window_frames + 10,
                    on_frames=on_frames,
                    off_frames=off_frames,
                )
                for frame in frames:
                    classifier.observe(frame)
                self.assertEqual(classifier.stable_state, LedState.CHARGING)

    def test_booting_sequence_classifies_as_booting(self) -> None:
        frames = make_booting_sequence(TEST_CONFIG.window_frames + 10)
        for frame in frames:
            self.classifier.observe(frame)
        self.assertEqual(self.classifier.stable_color, LedColor.BOOTING)
        self.assertEqual(self.classifier.stable_state, LedState.BOOTING)

    def test_solid_blue_window_is_ready(self) -> None:
        self._feed(_steady_sequence(LedColor.BLUE, TEST_CONFIG.window_frames + 10))
        self.assertEqual(self.classifier.stable_state, LedState.READY)

    def test_blinking_red_window_is_faulted(self) -> None:
        frames = make_blinking_sequence(
            LedColor.RED, frame_count=TEST_CONFIG.window_frames + 10, on_frames=3, off_frames=3
        )
        for frame in frames:
            self.classifier.observe(frame)
        self.assertEqual(self.classifier.stable_state, LedState.FAULTED)

    def test_all_off_window_is_off_or_unknown(self) -> None:
        self._feed(_steady_sequence(LedColor.OFF, TEST_CONFIG.window_frames + 10))
        self.assertEqual(self.classifier.stable_color, LedColor.OFF)
        self.assertEqual(self.classifier.stable_state, LedState.OFF_OR_UNKNOWN)

    def test_transition_requires_full_clean_window(self) -> None:
        self._feed(_steady_sequence(LedColor.BLUE, TEST_CONFIG.window_frames + 10))
        self.assertEqual(self.classifier.stable_state, LedState.READY)
        # Blue lingers in the window, so green alone is not declared immediately.
        self.classifier.observe(make_led_frame(LedColor.GREEN))
        self.assertEqual(self.classifier.stable_state, LedState.READY)
        # A window holding both hues reads as booting during the transition.
        self._feed(_steady_sequence(LedColor.GREEN, TEST_CONFIG.consecutive_agreement_frames + 1))
        self.assertEqual(self.classifier.stable_state, LedState.BOOTING)
        self._feed(
            _steady_sequence(
                LedColor.GREEN,
                TEST_CONFIG.window_frames + TEST_CONFIG.consecutive_agreement_frames,
            )
        )
        self.assertEqual(self.classifier.stable_state, LedState.CHARGING)

    def test_single_spurious_hue_frame_does_not_flip_window(self) -> None:
        self._feed(_steady_sequence(LedColor.GREEN, TEST_CONFIG.window_frames + 10))
        self.assertEqual(self.classifier.stable_state, LedState.CHARGING)
        self.classifier.observe(make_led_frame(LedColor.RED))
        window = self.classifier.window_classification()
        self.assertEqual(window.color, LedColor.GREEN)

    def test_exposure_variation_darkening_ends_in_off(self) -> None:
        frames = make_exposure_ramp(LedColor.BLUE, frame_count=20, start_brightness=1.0,
                                    end_brightness=0.05)
        confidences: list[float] = []
        colors: list[LedColor] = []
        for frame in frames:
            detail = self.classifier.classify_frame_detailed(frame)
            colors.append(detail.color)
            if detail.color == LedColor.BLUE:
                confidences.append(detail.confidence)
        self.assertEqual(colors[0], LedColor.BLUE)
        self.assertEqual(colors[-1], LedColor.OFF)
        for earlier, later in zip(confidences, confidences[1:]):
            self.assertLessEqual(later, earlier + 0.01)
        self.assertLess(confidences[-1], confidences[0] - 0.3)

    def test_exposure_variation_brightening_still_classifies_blue(self) -> None:
        frames = make_exposure_ramp(
            LedColor.BLUE, frame_count=10, start_brightness=0.35, end_brightness=1.0
        )
        for index, frame in enumerate(frames):
            with self.subTest(index=index):
                self.assertEqual(self.classifier.classify_frame(frame)[0], LedColor.BLUE)

    def test_dropped_frames_do_not_reset_agreement(self) -> None:
        frames = apply_dropped_frames(
            _steady_sequence(LedColor.GREEN, TEST_CONFIG.window_frames + 10),
            drop_indices=(3, 4, 9, 17),
        )
        for frame in frames:
            self.classifier.observe(frame)
        self.assertEqual(self.classifier.dropped_frame_count, 4)
        self.assertEqual(self.classifier.consecutive_dropped_frames, 0)
        self.assertEqual(self.classifier.stable_state, LedState.CHARGING)
        self.assertFalse(self.classifier.camera_failed)

    def test_consecutive_dropped_frames_flag_camera_failure(self) -> None:
        for _ in range(TEST_CONFIG.max_consecutive_dropped_frames):
            self.classifier.observe(None)
        self.assertTrue(self.classifier.camera_failed)

    def test_reset_clears_all_temporal_state(self) -> None:
        self._feed(_steady_sequence(LedColor.GREEN, TEST_CONFIG.window_frames + 10))
        self.classifier.reset()
        self.assertIsNone(self.classifier.stable_color)
        self.assertEqual(self.classifier.agreement_count, 0)
        self.assertEqual(self.classifier.frames_observed, 0)
        self.assertEqual(self.classifier.window_classification().frames_in_window, 0)

    def test_fixtures_are_deterministic(self) -> None:
        first = make_led_frame(LedColor.RED, noise_sigma=12.0, seed=3)
        second = make_led_frame(LedColor.RED, noise_sigma=12.0, seed=3)
        third = make_led_frame(LedColor.RED, noise_sigma=12.0, seed=4)
        self.assertTrue(np.array_equal(first, second))
        self.assertFalse(np.array_equal(first, third))
        self.assertEqual(
            [f.tobytes() for f in make_booting_sequence(6)],
            [f.tobytes() for f in make_booting_sequence(6)],
        )


class CameraFrameDecodingTests(unittest.TestCase):
    def test_round_trip_bgr_bytes(self) -> None:
        rgb = make_led_frame(LedColor.GREEN, width=4, height=3)
        camera_frame = CameraFrame(
            frame_bgr=frames_to_bgr_bytes(rgb),
            width=4,
            height=3,
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=0.0,
        )
        decoded = frame_to_rgb_array(camera_frame)
        self.assertTrue(np.array_equal(decoded, rgb))

    def test_payload_size_mismatch_is_rejected(self) -> None:
        camera_frame = CameraFrame(
            frame_bgr=b"\x00\x01\x02",
            width=4,
            height=3,
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=0.0,
        )
        with self.assertRaises(VisionFrameError):
            frame_to_rgb_array(camera_frame)


class ChargingGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FakeClock()
        self.flags: list[str] = []

    def _run_gate(
        self,
        camera: _FixtureCamera,
        timeout_s: float = 20.0,
        config: LedOpticalConfig = TEST_CONFIG,
    ):
        camera.start()
        return await_charging_gate(
            camera,
            roi=None,
            timeout_s=timeout_s,
            degraded_flag_out=self.flags,
            config=config,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )

    def test_happy_path_grants_gate_on_blinking_green(self) -> None:
        frames = make_blinking_sequence(
            LedColor.GREEN, frame_count=200, on_frames=5, off_frames=5
        )
        camera = _FixtureCamera(list(frames))
        success, led_state, degraded = self._run_gate(camera)
        self.assertTrue(success)
        self.assertEqual(led_state, LedState.CHARGING)
        self.assertFalse(degraded)
        self.assertEqual(self.flags, [])
        self.assertLess(self.clock.total_slept_s, 20.0)

    def test_happy_path_with_default_three_second_window(self) -> None:
        frames = make_blinking_sequence(
            LedColor.GREEN, frame_count=400, on_frames=7, off_frames=7
        )
        camera = _FixtureCamera(list(frames))
        success, led_state, degraded = self._run_gate(
            camera, timeout_s=90.0, config=DEFAULT_OPTICAL_CONFIG
        )
        self.assertTrue(success)
        self.assertEqual(led_state, LedState.CHARGING)
        self.assertFalse(degraded)
        # 3 s window plus 5 agreeing frames at 15 fps.
        self.assertGreaterEqual(self.clock.total_slept_s, 3.0)
        self.assertLess(self.clock.total_slept_s, 6.0)

    def test_timeout_with_blinking_red_reports_faulted_and_requests_retry(self) -> None:
        frames = make_blinking_sequence(LedColor.RED, frame_count=200, on_frames=4, off_frames=4)
        camera = _FixtureCamera(list(frames))
        success, led_state, degraded = self._run_gate(camera, timeout_s=10.0)
        self.assertFalse(success)
        self.assertEqual(led_state, LedState.FAULTED)
        self.assertFalse(degraded)
        action, reason = gate_timeout_action(led_state)
        self.assertEqual(action, GateTimeoutAction.RETRY_EXTENDED_COOLDOWN)
        self.assertIn("latched", reason)

    def test_timeout_with_blue_halts_without_retry(self) -> None:
        camera = _FixtureCamera(_steady_sequence(LedColor.BLUE, 60))
        success, led_state, degraded = self._run_gate(camera, timeout_s=10.0)
        self.assertFalse(success)
        self.assertEqual(led_state, LedState.READY)
        self.assertFalse(degraded)
        self.assertEqual(gate_timeout_action(led_state)[0], GateTimeoutAction.HALT)

    def test_timeout_with_led_off_halts_without_retry(self) -> None:
        camera = _FixtureCamera(_steady_sequence(LedColor.OFF, 60))
        success, led_state, degraded = self._run_gate(camera, timeout_s=10.0)
        self.assertFalse(success)
        self.assertEqual(led_state, LedState.OFF_OR_UNKNOWN)
        self.assertFalse(degraded)
        self.assertEqual(gate_timeout_action(led_state)[0], GateTimeoutAction.HALT)

    def test_timeout_while_still_booting_halts(self) -> None:
        camera = _FixtureCamera(list(make_booting_sequence(200)))
        success, led_state, degraded = self._run_gate(camera, timeout_s=10.0)
        self.assertFalse(success)
        self.assertEqual(led_state, LedState.BOOTING)
        self.assertEqual(gate_timeout_action(led_state)[0], GateTimeoutAction.HALT)

    def test_camera_failure_degrades_to_fixed_sixty_second_wait(self) -> None:
        camera = _FixtureCamera(_steady_sequence(LedColor.BLUE, 10), fail_after_samples=2)
        success, led_state, degraded = self._run_gate(camera, timeout_s=90.0)
        self.assertFalse(success)
        self.assertTrue(degraded)
        self.assertEqual(led_state, LedState.CAMERA_UNAVAILABLE)
        self.assertEqual(self.flags, [DEGRADED_FLAG_CAMERA_UNAVAILABLE])
        self.assertIn(TEST_CONFIG.degraded_fixed_wait_s, self.clock.slept_s)
        self.assertEqual(TEST_CONFIG.degraded_fixed_wait_s, 60.0)
        self.assertEqual(
            gate_timeout_action(led_state)[0], GateTimeoutAction.DEGRADED_FIXED_WAIT
        )

    def test_camera_exception_never_propagates_and_degrades(self) -> None:
        camera = _FixtureCamera(_steady_sequence(LedColor.GREEN, 10), raise_after_samples=1)
        success, led_state, degraded = self._run_gate(camera, timeout_s=90.0)
        self.assertFalse(success)
        self.assertTrue(degraded)
        self.assertEqual(led_state, LedState.CAMERA_UNAVAILABLE)
        self.assertEqual(self.flags, [DEGRADED_FLAG_CAMERA_UNAVAILABLE])
        self.assertIn(60.0, self.clock.slept_s)

    def test_transient_dropped_frames_do_not_degrade_the_gate(self) -> None:
        frames = apply_dropped_frames(_steady_sequence(LedColor.GREEN, 120), drop_indices=(1, 2, 8))
        camera = _FixtureCamera(list(frames))
        success, led_state, degraded = self._run_gate(camera, timeout_s=30.0)
        self.assertTrue(success)
        self.assertFalse(degraded)
        self.assertEqual(led_state, LedState.CHARGING)
        self.assertEqual(self.flags, [])

    def test_gate_is_deterministic_across_repeated_runs(self) -> None:
        results = []
        for _ in range(3):
            self.clock = _FakeClock()
            self.flags = []
            frames = make_blinking_sequence(
                LedColor.GREEN, frame_count=200, on_frames=5, off_frames=5
            )
            camera = _FixtureCamera(list(frames))
            results.append((tuple(self._run_gate(camera)), self.clock.total_slept_s))
        self.assertEqual(len(set(results)), 1)

    def test_rejects_non_positive_timeout(self) -> None:
        camera = _FixtureCamera(_steady_sequence(LedColor.GREEN, 10))
        camera.start()
        with self.assertRaises(ValueError):
            await_charging_gate(
                camera,
                timeout_s=0.0,
                config=TEST_CONFIG,
                monotonic=self.clock.monotonic,
                sleep=self.clock.sleep,
            )


class GateTimeoutActionTests(unittest.TestCase):
    def test_all_timeout_branches_are_mapped(self) -> None:
        expected = {
            LedState.FAULTED: GateTimeoutAction.RETRY_EXTENDED_COOLDOWN,
            LedState.READY: GateTimeoutAction.HALT,
            LedState.OFF_OR_UNKNOWN: GateTimeoutAction.HALT,
            LedState.BOOTING: GateTimeoutAction.HALT,
            LedState.CAMERA_UNAVAILABLE: GateTimeoutAction.DEGRADED_FIXED_WAIT,
            LedState.CHARGING: GateTimeoutAction.HALT,
        }
        for led_state, action in expected.items():
            with self.subTest(led_state=led_state):
                actual_action, reason = gate_timeout_action(led_state)
                self.assertEqual(actual_action, action)
                self.assertTrue(reason)


class ConfigValidationTests(unittest.TestCase):
    def test_rejects_window_shorter_than_agreement_requirement(self) -> None:
        with self.assertRaises(ValueError):
            LedOpticalConfig(window_s=0.2, frame_rate_hz=15.0, consecutive_agreement_frames=5)

    def test_rejects_invalid_thresholds(self) -> None:
        with self.assertRaises(ValueError):
            LedOpticalConfig(min_saturation=1.5)
        with self.assertRaises(ValueError):
            LedOpticalConfig(min_pixel_fraction=0.0)
        with self.assertRaises(ValueError):
            LedOpticalConfig(degraded_fixed_wait_s=0.0)

    def test_rejects_invalid_hue_range(self) -> None:
        with self.assertRaises(ValueError):
            HueRange(-1.0, 30.0)
        with self.assertRaises(ValueError):
            HueRange(10.0, 400.0)

    def test_rejects_invalid_roi(self) -> None:
        with self.assertRaises(ValueError):
            RegionOfInterest(-1, 0, 4, 4)
        with self.assertRaises(ValueError):
            RegionOfInterest(0, 0, 0, 4)


if __name__ == "__main__":
    unittest.main()
