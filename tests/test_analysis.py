from __future__ import annotations

from dataclasses import replace
from enum import Enum
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

from ccid.analysis import (
    ALL_SANITY_CHECKS,
    CURRENT_ANALYSIS_VERSION,
    DEFAULT_ANALYSIS_CONFIG,
    DEFAULT_ENDPOINT_DEFINITION,
    SANITY_BURST_STARTS_NEAR_T0,
    SANITY_COLLAPSE_IS_CLEAN,
    SANITY_NO_PRETRIGGER_LEAKAGE,
    SANITY_NO_TRIP_PERSISTENT,
    SANITY_RECORD_SPANS_NO_TRIP_LIMIT,
    SANITY_SIGNAL_PRESENT,
    AnalysisConfig,
    AnalysisVersion,
    TripResult,
    Verdict,
    analyze_samples,
    analyze_waveform,
    analyze_waveform_file,
    check_no_pretrigger_leakage,
    check_signal_present,
    extract_envelope,
    load_waveform,
    pack_waveform_npz,
    reference_amplitude,
    resolve_analysis_config,
    rms,
    sliding_max,
    synthesize_burst_samples,
    synthesize_waveform_npz,
    V1_ENDPOINT_DEFINITION,
    V2_ENDPOINT_DEFINITION,
)
from ccid.config import load_config
from ccid.errors import WaveformFormatError

# Sanity-check failures are expected in several tests and are logged by design;
# keep that noise out of the test output.
_ANALYSIS_LOGGER = logging.getLogger("ccid.analysis")
_ANALYSIS_LOGGER.addHandler(logging.NullHandler())
_ANALYSIS_LOGGER.propagate = False

CONFIG = DEFAULT_ANALYSIS_CONFIG
PASS_LIMIT_S = 0.02497
NO_TRIP_LIMIT_S = 0.100
HALF_CYCLE_S = 1.0 / 120.0
# Endpoint resolution of the v1 definition on clean synthetic data.
CLEAN_TOLERANCE_S = 2e-5


def waveform_bytes(**kwargs) -> bytes:
    kwargs.setdefault("record_after_t0_s", 0.180)
    return synthesize_waveform_npz(**kwargs)


class SlidingMaxTests(unittest.TestCase):
    def test_leading_window_falls_on_the_sample_after_the_burst(self) -> None:
        values = np.array([0.0, 3.0, 1.0, 4.0, 0.0, 0.0, 0.0], dtype=np.float64)
        result = sliding_max(values, 3, align="leading")
        # index 2 sees [1, 4, 0] -> 4; index 4 onwards sees only zeros.
        self.assertEqual(list(result), [3.0, 4.0, 4.0, 4.0, 0.0, 0.0, 0.0])

    def test_trailing_window_rises_on_the_first_sample_of_the_burst(self) -> None:
        values = np.array([0.0, 0.0, 5.0, 1.0, 0.0, 0.0], dtype=np.float64)
        result = sliding_max(values, 3, align="trailing")
        self.assertEqual(list(result), [0.0, 0.0, 5.0, 5.0, 5.0, 1.0])

    def test_window_larger_than_array_and_degenerate_windows(self) -> None:
        values = np.array([1.0, 7.0, 2.0], dtype=np.float64)
        self.assertEqual(list(sliding_max(values, 99, align="leading")), [7.0, 7.0, 2.0])
        self.assertEqual(list(sliding_max(values, 1, align="leading")), [1.0, 7.0, 2.0])
        self.assertEqual(list(sliding_max(np.array([]), 4)), [])

    def test_matches_a_brute_force_sliding_maximum(self) -> None:
        rng = np.random.default_rng(7)
        values = np.abs(rng.normal(size=257))
        window = 13
        expected = [float(values[i : i + window].max()) for i in range(values.size)]
        np.testing.assert_allclose(sliding_max(values, window, align="leading"), expected)

    def test_rejects_unknown_alignment(self) -> None:
        with self.assertRaises(ValueError):
            sliding_max(np.array([1.0, 2.0]), 2, align="centred")


class EnvelopeTests(unittest.TestCase):
    def test_envelope_bridges_the_zero_crossings_of_a_live_burst(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=0.02497, record_after_t0_s=0.180
        )
        dt = preamble["x_increment"]
        start = int(round(0.020 / dt))
        end = start + int(round(0.02497 / dt))
        envelope = extract_envelope(samples, sample_interval_s=dt, config=CONFIG)
        window = int(round(HALF_CYCLE_S / dt))

        # The raw signal touches zero four times inside the burst ...
        interior = np.abs(samples[start:end])
        self.assertGreater(int(np.sum(interior < 1.0)), 3)
        # ... but the envelope never collapses while a full half cycle of the
        # burst is still ahead of it.
        self.assertGreater(float(envelope[start : end - window].min()), 0.5 * 170.0)

    def test_envelope_collapses_after_the_burst(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=0.010, record_after_t0_s=0.180
        )
        dt = preamble["x_increment"]
        envelope = extract_envelope(samples, sample_interval_s=dt, config=CONFIG)
        after = int(round((0.020 + 0.010) / dt)) + 1
        self.assertEqual(float(envelope[after:].max()), 0.0)

    def test_reference_amplitude_ignores_a_lone_spike_and_post_trip_silence(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=0.010, record_after_t0_s=0.180
        )
        magnitude = np.abs(samples)
        magnitude[-5] = 5000.0
        window = int(round(HALF_CYCLE_S / preamble["x_increment"]))
        self.assertAlmostEqual(reference_amplitude(magnitude, window), 170.0, delta=1.0)

    def test_rms_helper(self) -> None:
        self.assertAlmostEqual(rms(np.array([3.0, -4.0])), 3.5355339, places=6)
        self.assertEqual(rms(np.array([])), 0.0)


class VerdictBoundaryTests(unittest.TestCase):
    def _analyze(self, **kwargs) -> TripResult:
        return analyze_waveform(waveform_bytes(**kwargs), CONFIG)

    def test_ten_millisecond_trip_passes(self) -> None:
        result = self._analyze(trip_time_s=0.010)
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertAlmostEqual(result.trip_time_s, 0.010, delta=CLEAN_TOLERANCE_S)
        self.assertFalse(result.verdict.halts_run)
        self.assertTrue(result.sanity_ok)

    def test_trip_exactly_at_the_pass_limit_passes(self) -> None:
        result = self._analyze(trip_time_s=PASS_LIMIT_S)
        self.assertAlmostEqual(result.trip_time_s, PASS_LIMIT_S, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.PASS)

    def test_trip_just_above_the_pass_limit_fails_but_continues(self) -> None:
        result = self._analyze(trip_time_s=0.02503)
        self.assertAlmostEqual(result.trip_time_s, 0.02503, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertFalse(result.verdict.halts_run)

    def test_mid_band_trip_fails(self) -> None:
        result = self._analyze(trip_time_s=0.050)
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertAlmostEqual(result.trip_time_s, 0.050, delta=CLEAN_TOLERANCE_S)

    def test_trip_at_the_no_trip_limit_halts(self) -> None:
        result = self._analyze(trip_time_s=NO_TRIP_LIMIT_S)
        self.assertEqual(result.verdict, Verdict.NO_TRIP)
        self.assertTrue(result.verdict.halts_run)
        self.assertAlmostEqual(result.trip_time_s, NO_TRIP_LIMIT_S, delta=2e-4)

    def test_trip_beyond_the_no_trip_limit_halts(self) -> None:
        result = self._analyze(trip_time_s=0.120)
        self.assertEqual(result.verdict, Verdict.NO_TRIP)
        self.assertAlmostEqual(result.trip_time_s, 0.120, delta=CLEAN_TOLERANCE_S)

    def test_no_trip_at_all_reports_no_measurement(self) -> None:
        result = self._analyze(trip_time_s=None)
        self.assertIsNone(result.trip_time_s)
        self.assertEqual(result.verdict, Verdict.NO_TRIP)
        self.assertTrue(result.sanity_checks[SANITY_NO_TRIP_PERSISTENT])

    def test_verdict_table_is_monotonic_across_the_band(self) -> None:
        expected = {
            0.001: Verdict.PASS,
            0.015: Verdict.PASS,
            PASS_LIMIT_S: Verdict.PASS,
            0.030: Verdict.FAIL,
            0.075: Verdict.FAIL,
            0.120: Verdict.NO_TRIP,
        }
        for trip_time_s, verdict in expected.items():
            with self.subTest(trip_time_s=trip_time_s):
                self.assertEqual(self._analyze(trip_time_s=trip_time_s).verdict, verdict)


class HalfCycleTrapTests(unittest.TestCase):
    def test_burst_is_measured_not_a_single_half_cycle(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=PASS_LIMIT_S), CONFIG)
        self.assertAlmostEqual(result.trip_time_s, PASS_LIMIT_S, delta=CLEAN_TOLERANCE_S)
        # The trap value is one half cycle; the burst is three of them.
        self.assertGreater(result.trip_time_s, 2.5 * HALF_CYCLE_S)

    def test_naive_threshold_detector_would_report_one_half_cycle(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=PASS_LIMIT_S, record_after_t0_s=0.180
        )
        dt = preamble["x_increment"]
        start = int(round(0.020 / dt))
        # A naive detector: first sample above threshold, then first sample back
        # below it. This is exactly what a scope width measurement does.
        magnitude = np.abs(samples[start:])
        above = np.flatnonzero(magnitude > 20.0)
        first = int(above[0])
        below_after = np.flatnonzero(magnitude[first:] <= 20.0)
        naive_s = float(below_after[0]) * dt
        self.assertLess(naive_s, HALF_CYCLE_S + 1e-4)

        envelope_result = analyze_waveform(
            pack_waveform_npz(samples, preamble), CONFIG
        )
        self.assertGreater(envelope_result.trip_time_s, 2.5 * naive_s)

    def test_measurement_is_independent_of_injection_phase(self) -> None:
        measured = []
        for phase_rad in np.linspace(0.0, 2.0 * np.pi, 9):
            result = analyze_waveform(
                waveform_bytes(trip_time_s=0.020, phase_rad=float(phase_rad)), CONFIG
            )
            self.assertEqual(result.verdict, Verdict.PASS)
            measured.append(result.trip_time_s)
        self.assertLess(max(measured) - min(measured), CLEAN_TOLERANCE_S)


class SanityCheckTests(unittest.TestCase):
    def test_all_checks_are_reported_on_every_result(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.010), CONFIG)
        self.assertEqual(set(result.sanity_checks), set(ALL_SANITY_CHECKS))

    def test_signal_present_on_a_normal_capture(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.010), CONFIG)
        self.assertTrue(result.sanity_checks[SANITY_SIGNAL_PRESENT])

    def test_zero_signal_is_flagged_and_yields_no_measurement(self) -> None:
        samples, preamble = synthesize_burst_samples(trip_time_s=0.010)
        samples[:] = 0.0
        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)
        self.assertFalse(result.sanity_checks[SANITY_SIGNAL_PRESENT])
        self.assertIsNone(result.trip_time_s)
        self.assertEqual(result.verdict, Verdict.NO_TRIP)
        self.assertIn("no signal captured", result.notes)

    def test_weak_signal_is_flagged_as_absent(self) -> None:
        result = analyze_waveform(
            waveform_bytes(trip_time_s=0.010, amplitude_v=0.3), CONFIG
        )
        self.assertFalse(result.sanity_checks[SANITY_SIGNAL_PRESENT])
        self.assertIsNone(result.trip_time_s)

    def test_signal_present_helper_thresholds_on_rms(self) -> None:
        self.assertTrue(check_signal_present(np.full(100, 5.0), CONFIG))
        self.assertFalse(check_signal_present(np.zeros(100), CONFIG))

    def test_pretrigger_leakage_is_flagged_but_never_vetoes_the_verdict(self) -> None:
        payload = waveform_bytes(trip_time_s=0.015, pretrigger_leakage=True)
        result = analyze_waveform(payload, CONFIG, injection_time_s=0.0)
        self.assertFalse(result.sanity_checks[SANITY_NO_PRETRIGGER_LEAKAGE])
        # The measurement and the verdict still stand: recording both is the point.
        self.assertAlmostEqual(result.trip_time_s, 0.015, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.PASS)

    def test_pretrigger_leakage_is_flagged_without_a_sidecar_timestamp(self) -> None:
        payload = waveform_bytes(trip_time_s=0.015, pretrigger_leakage=True)
        result = analyze_waveform(payload, CONFIG)
        self.assertFalse(result.sanity_checks[SANITY_NO_PRETRIGGER_LEAKAGE])

    def test_clean_pretrigger_region_passes_the_leakage_check(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.015), CONFIG)
        self.assertTrue(result.sanity_checks[SANITY_NO_PRETRIGGER_LEAKAGE])

    def test_quiet_record_start_before_early_burst_is_not_pretrigger_leakage(self) -> None:
        samples, preamble = synthesize_burst_samples(
            pretrigger_s=0.020,
            record_after_t0_s=0.180,
            trip_time_s=0.015,
        )

        dt = float(preamble["x_increment"])
        times = float(preamble["x_origin"]) + np.arange(samples.size) * dt
        burst = 170.0 * np.sin(2.0 * np.pi * 60.0 * times)
        samples = np.where(
            (times >= -0.015) & (times <= 0.0),
            burst,
            0.0,
        )

        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)

        self.assertTrue(result.sanity_checks[SANITY_NO_PRETRIGGER_LEAKAGE])
        self.assertIn("t0_source=detected_onset", result.notes)
        self.assertNotIn("t0_s=-0.020000000", result.notes)
        self.assertAlmostEqual(result.trip_time_s, 0.015, delta=CLEAN_TOLERANCE_S)

    def test_leakage_helper_scales_with_the_burst_amplitude(self) -> None:
        probe_noise = np.full(1000, 2.0)
        self.assertFalse(check_no_pretrigger_leakage(probe_noise, CONFIG))
        self.assertTrue(
            check_no_pretrigger_leakage(probe_noise, CONFIG, reference_amplitude_v=170.0)
        )
        self.assertTrue(check_no_pretrigger_leakage(np.array([]), CONFIG))

    def test_short_record_cannot_prove_a_no_trip(self) -> None:
        result = analyze_waveform(
            synthesize_waveform_npz(trip_time_s=None, record_after_t0_s=0.050), CONFIG
        )
        self.assertFalse(result.sanity_checks[SANITY_RECORD_SPANS_NO_TRIP_LIMIT])
        self.assertEqual(result.verdict, Verdict.NO_TRIP)

    def test_full_length_record_satisfies_the_span_check(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=None), CONFIG)
        self.assertTrue(result.sanity_checks[SANITY_RECORD_SPANS_NO_TRIP_LIMIT])

    def test_intermittent_conduction_fails_the_no_trip_persistence_check(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=None, record_after_t0_s=0.180
        )
        dt = preamble["x_increment"]
        # Punch 14 ms gaps every 30 ms: shorter than the collapse persistence, so
        # no trip is declared, but the conduction is plainly not continuous.
        start = int(round(0.020 / dt))
        gap = int(round(0.014 / dt))
        for offset_s in (0.030, 0.060, 0.090, 0.120):
            begin = start + int(round(offset_s / dt))
            samples[begin : begin + gap] = 0.0
        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)
        self.assertIsNone(result.trip_time_s)
        self.assertEqual(result.verdict, Verdict.NO_TRIP)
        self.assertFalse(result.sanity_checks[SANITY_NO_TRIP_PERSISTENT])

    def test_reignition_after_the_collapse_is_flagged(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=0.015, record_after_t0_s=0.180
        )
        dt = preamble["x_increment"]
        restart = int(round((0.020 + 0.100) / dt))
        times = -0.020 + np.arange(samples.size) * dt
        samples[restart:] = 170.0 * np.sin(2.0 * np.pi * 60.0 * times[restart:])
        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)
        self.assertAlmostEqual(result.trip_time_s, 0.015, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertFalse(result.sanity_checks[SANITY_COLLAPSE_IS_CLEAN])

    def test_burst_starting_far_after_t0_is_flagged(self) -> None:
        payload = waveform_bytes(trip_time_s=0.020, pretrigger_s=0.060)
        result = analyze_waveform(payload, CONFIG, injection_time_s=-0.040)
        self.assertFalse(result.sanity_checks[SANITY_BURST_STARTS_NEAR_T0])
        self.assertAlmostEqual(result.trip_time_s, 0.060, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.FAIL)

    def test_sanity_failures_are_logged_but_the_verdict_is_unchanged(self) -> None:
        payload = waveform_bytes(trip_time_s=0.015, pretrigger_leakage=True)
        logger = logging.getLogger("ccid.analysis")
        previous_propagate = logger.propagate
        logger.propagate = True
        try:
            with self.assertLogs("ccid.analysis", level="WARNING") as captured:
                result = analyze_waveform(payload, CONFIG, injection_time_s=0.0)
        finally:
            logger.propagate = previous_propagate
        self.assertIn(SANITY_NO_PRETRIGGER_LEAKAGE, "\n".join(captured.output))
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertIn("sanity_failed", result.notes)


class NoiseAndEdgeCaseTests(unittest.TestCase):
    def test_moderate_probe_noise_does_not_change_the_verdict(self) -> None:
        for noise_v in (0.5, 1.0, 2.0, 5.0):
            with self.subTest(noise_v=noise_v):
                result = analyze_waveform(
                    waveform_bytes(trip_time_s=0.015, noise_v=noise_v), CONFIG
                )
                self.assertEqual(result.verdict, Verdict.PASS)
                self.assertAlmostEqual(result.trip_time_s, 0.015, delta=5e-4)

    def test_noise_on_a_no_trip_record_still_reads_as_a_no_trip(self) -> None:
        result = analyze_waveform(
            waveform_bytes(trip_time_s=None, noise_v=1.0), CONFIG
        )
        self.assertIsNone(result.trip_time_s)
        self.assertEqual(result.verdict, Verdict.NO_TRIP)
        self.assertTrue(result.sanity_checks[SANITY_NO_TRIP_PERSISTENT])

    def test_noise_only_record_reports_no_signal(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=0.010, amplitude_v=0.0, noise_v=0.2
        )
        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)
        self.assertFalse(result.sanity_checks[SANITY_SIGNAL_PRESENT])
        self.assertEqual(result.verdict, Verdict.NO_TRIP)
        self.assertIsNone(result.trip_time_s)

    def test_very_fast_trip_is_still_measured(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.002), CONFIG)
        self.assertAlmostEqual(result.trip_time_s, 0.002, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.PASS)

    def test_full_depth_record_is_analysed_at_scale(self) -> None:
        payload = synthesize_waveform_npz(
            sample_rate_hz=5_000_000.0,
            pretrigger_s=0.020,
            record_after_t0_s=0.180,
            trip_time_s=0.020,
        )
        waveform = load_waveform(payload)
        self.assertEqual(waveform.samples_v.size, 1_000_000)
        result = analyze_samples(waveform, CONFIG)
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertAlmostEqual(result.trip_time_s, 0.020, delta=CLEAN_TOLERANCE_S)


class OnsetNoiseRobustnessTests(unittest.TestCase):
    """Regression coverage for a real-hardware defect (25-cycle campaign,
    cycles 1 and 17): scattered near-floor noise samples in the pre-trigger
    buffer dragged the detected onset far ahead of the true burst, because
    the forward-looking leading envelope cannot distinguish a lone noisy
    sample from genuine sustained conduction -- both look identical for one
    window's length. `_refine_start_index` must confirm a candidate against
    the raw samples themselves before trusting it.
    """

    def test_scattered_pretrigger_noise_blips_do_not_drag_the_onset_backward(self) -> None:
        samples, preamble = synthesize_burst_samples(
            trip_time_s=0.020, pretrigger_s=0.030, record_after_t0_s=0.180
        )
        dt = preamble["x_increment"]
        # Single-sample near-floor blips, spaced 4 ms apart (closer together
        # than the 8.33 ms envelope window, so their forward shadows chain
        # into one continuous "elevated" stretch reaching the real burst) --
        # this is the exact pattern found in the real cycle 1 and 17 waveforms.
        for blip_time_s in np.arange(-0.020, -0.003, 0.004):
            index = int(round((blip_time_s - preamble["x_origin"]) / dt))
            samples[index] = 5.0

        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)

        self.assertAlmostEqual(result.trip_time_s, 0.020, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertTrue(result.sanity_checks[SANITY_COLLAPSE_IS_CLEAN])

    def test_genuine_sub_threshold_ramp_before_t0_is_still_recovered(self) -> None:
        samples, preamble = synthesize_burst_samples(
            pretrigger_s=0.020, record_after_t0_s=0.180, trip_time_s=0.015
        )
        dt = float(preamble["x_increment"])
        times = float(preamble["x_origin"]) + np.arange(samples.size) * dt
        # Genuine conduction (real sine, not an isolated sample) starting
        # 5 ms before the nominal t0 and running through the burst.
        burst = 170.0 * np.sin(2.0 * np.pi * 60.0 * times)
        samples = np.where((times >= -0.005) & (times <= 0.015), burst, 0.0)

        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)

        self.assertIn("t0_source=detected_onset", result.notes)
        self.assertAlmostEqual(result.trip_time_s, 0.020, delta=CLEAN_TOLERANCE_S)
        self.assertEqual(result.verdict, Verdict.PASS)


class TimeBaseTests(unittest.TestCase):
    def test_sidecar_injection_time_shifts_the_measurement(self) -> None:
        payload = waveform_bytes(trip_time_s=0.020)
        shifted = analyze_waveform(payload, CONFIG, injection_time_s=-0.005)
        self.assertAlmostEqual(shifted.trip_time_s, 0.025, delta=CLEAN_TOLERANCE_S)
        self.assertIn("t0_source=sidecar", shifted.notes)

    def test_preamble_injection_time_is_used_when_no_sidecar_value_is_given(self) -> None:
        samples, preamble = synthesize_burst_samples(trip_time_s=0.020)
        preamble["k3_close_time_s"] = -0.005
        result = analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)
        self.assertAlmostEqual(result.trip_time_s, 0.025, delta=CLEAN_TOLERANCE_S)
        self.assertIn("t0_source=preamble", result.notes)

    def test_t0_falls_back_to_the_detected_conduction_onset(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.020), CONFIG)
        self.assertIn("t0_source=detected_onset", result.notes)
        self.assertAlmostEqual(result.trip_time_s, 0.020, delta=CLEAN_TOLERANCE_S)

    def test_time_base_is_recovered_from_the_preamble(self) -> None:
        payload = waveform_bytes(trip_time_s=0.020)
        waveform = load_waveform(payload)
        self.assertAlmostEqual(waveform.sample_rate_hz, 1_000_000.0)
        self.assertAlmostEqual(waveform.first_sample_time_s, -0.020)
        self.assertEqual(waveform.index_of_time(0.0), 20_000)
        self.assertAlmostEqual(waveform.time_of_index(20_000), 0.0)

    def test_sample_rate_only_preamble_is_accepted(self) -> None:
        samples, preamble = synthesize_burst_samples(trip_time_s=0.020)
        del preamble["x_increment"]
        del preamble["x_origin"]
        waveform = load_waveform(pack_waveform_npz(samples, preamble))
        self.assertAlmostEqual(waveform.sample_interval_s, 1e-6)
        self.assertAlmostEqual(waveform.first_sample_time_s, -0.020)


class WaveformFormatTests(unittest.TestCase):
    def test_recorder_bundle_with_byte_samples_is_analysable(self) -> None:
        samples, preamble = synthesize_burst_samples(trip_time_s=0.020)
        codes = np.clip(np.round(128.0 + (samples / 200.0) * 127.0), 0, 255).astype(np.uint8)
        preamble = dict(preamble)
        preamble.update({"format": "BYTE", "y_increment": 1.0, "y_origin": -128.0, "y_reference": 0})

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("samples.bin", codes.tobytes())
            archive.writestr("preamble.json", json.dumps(preamble, sort_keys=True))

        result = analyze_waveform(buffer.getvalue(), CONFIG)
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertAlmostEqual(result.trip_time_s, 0.020, delta=1e-4)

    def test_pickled_preamble_requires_an_explicit_opt_in(self) -> None:
        samples, preamble = synthesize_burst_samples(trip_time_s=0.020)
        buffer = io.BytesIO()
        np.savez(buffer, samples=samples, preamble=np.array(preamble, dtype=object))
        payload = buffer.getvalue()
        with self.assertRaises(WaveformFormatError):
            analyze_waveform(payload, CONFIG)
        result = analyze_waveform(payload, CONFIG, allow_pickle=True)
        self.assertEqual(result.verdict, Verdict.PASS)

    def test_missing_preamble_is_rejected(self) -> None:
        samples, _ = synthesize_burst_samples(trip_time_s=0.020)
        buffer = io.BytesIO()
        np.savez(buffer, samples=samples)
        with self.assertRaises(WaveformFormatError):
            analyze_waveform(buffer.getvalue(), CONFIG)

    def test_missing_time_base_is_rejected(self) -> None:
        samples, preamble = synthesize_burst_samples(trip_time_s=0.020)
        del preamble["x_increment"]
        del preamble["sample_rate_hz"]
        with self.assertRaises(WaveformFormatError):
            analyze_waveform(pack_waveform_npz(samples, preamble), CONFIG)

    def test_integer_samples_without_scaling_values_are_rejected(self) -> None:
        samples, preamble = synthesize_burst_samples(trip_time_s=0.020)
        for key in ("y_increment", "y_origin", "y_reference"):
            preamble.pop(key, None)
        codes = np.zeros(samples.size, dtype=np.uint8)
        with self.assertRaises(WaveformFormatError):
            analyze_waveform(pack_waveform_npz(codes, preamble), CONFIG)

    def test_empty_and_non_container_payloads_are_rejected(self) -> None:
        with self.assertRaises(WaveformFormatError):
            analyze_waveform(b"", CONFIG)
        with self.assertRaises(WaveformFormatError):
            analyze_waveform(b"not a waveform", CONFIG)
        with self.assertRaises(WaveformFormatError):
            analyze_waveform("not bytes", CONFIG)  # type: ignore[arg-type]

    def test_analyze_waveform_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "7.npz"
            path.write_bytes(waveform_bytes(trip_time_s=0.010))
            result = analyze_waveform_file(path, CONFIG)
            self.assertEqual(result.verdict, Verdict.PASS)
            with self.assertRaises(WaveformFormatError):
                analyze_waveform_file(Path(temp_dir) / "missing.npz", CONFIG)


class VersioningTests(unittest.TestCase):
    def test_results_carry_the_current_algorithm_version(self) -> None:
        result = analyze_waveform(
            waveform_bytes(trip_time_s=0.010),
            CONFIG,
        )

        self.assertEqual(result.algorithm_version, AnalysisVersion.V3)
        self.assertEqual(CURRENT_ANALYSIS_VERSION, AnalysisVersion.V3)
        self.assertEqual(result.algorithm_version.value, "v3")
        self.assertIn("analysis_version=v3", result.notes)

    def test_an_unimplemented_future_version_refuses_to_analyse(self) -> None:
        class FutureVersion(str, Enum):
            V4 = "v4"

        future_config = replace(CONFIG, algorithm_version=FutureVersion.V4)
        waveform = load_waveform(waveform_bytes(trip_time_s=0.010))
        with self.assertRaises(NotImplementedError):
            analyze_samples(waveform, future_config)

    def test_version_survives_the_sidecar_round_trip(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.010), CONFIG)
        payload = json.loads(json.dumps(result.to_dict()))
        restored = TripResult.from_dict(payload)
        self.assertEqual(restored, result)
        self.assertEqual(restored.algorithm_version, AnalysisVersion.V3)

    def test_historical_v1_remains_replayable(self) -> None:
        v1_config = replace(
            CONFIG,
            algorithm_version=AnalysisVersion.V1,
            endpoint_definition=V1_ENDPOINT_DEFINITION,
        )
        result = analyze_waveform(
            waveform_bytes(trip_time_s=0.010),
            v1_config,
        )

        self.assertEqual(result.algorithm_version, AnalysisVersion.V1)
        self.assertIn("analysis_version=v1", result.notes)
        self.assertIn("endpoints=v1 endpoints:", result.notes)

    def test_historical_v2_remains_replayable_with_its_known_onset_defect(self) -> None:
        # Regression guard for the fix itself: v2 must keep reproducing its own
        # recorded (buggy) numbers on replay, never silently pick up the v3
        # correction. See OnsetNoiseRobustnessTests for the v3 behavior on the
        # same kind of waveform.
        samples, preamble = synthesize_burst_samples(
            trip_time_s=0.020, pretrigger_s=0.030, record_after_t0_s=0.180
        )
        dt = preamble["x_increment"]
        for blip_time_s in np.arange(-0.020, -0.003, 0.004):
            index = int(round((blip_time_s - preamble["x_origin"]) / dt))
            samples[index] = 5.0

        v2_config = replace(
            CONFIG,
            algorithm_version=AnalysisVersion.V2,
            endpoint_definition=V2_ENDPOINT_DEFINITION,
        )
        result = analyze_waveform(pack_waveform_npz(samples, preamble), v2_config)

        self.assertEqual(result.algorithm_version, AnalysisVersion.V2)
        self.assertIn("analysis_version=v2", result.notes)
        # The known v2 defect: the onset is dragged back to the first blip
        # instead of the real burst, inflating the trip time by ~17 ms.
        self.assertAlmostEqual(result.trip_time_s, 0.040, delta=CLEAN_TOLERANCE_S)

    def test_malformed_sidecar_payloads_are_rejected(self) -> None:
        with self.assertRaises(WaveformFormatError):
            TripResult.from_dict({"trip_time_s": 0.01, "verdict": "MAYBE", "analysis_version": "v1"})
        with self.assertRaises(WaveformFormatError):
            TripResult.from_dict({"trip_time_s": 0.01, "verdict": "PASS"})
        with self.assertRaises(WaveformFormatError):
            TripResult.from_dict(
                {
                    "trip_time_s": 0.01,
                    "verdict": "PASS",
                    "analysis_version": "v1",
                    "sanity_checks": ["nope"],
                }
            )


class RecordedResultTests(unittest.TestCase):
    def test_trip_time_and_verdict_are_recorded_separately(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.030), CONFIG)
        payload = result.to_dict()
        self.assertIn("trip_time_s", payload)
        self.assertIn("verdict", payload)
        self.assertEqual(payload["verdict"], "FAIL")
        self.assertIsInstance(payload["trip_time_s"], float)

    def test_verdicts_are_rederivable_from_the_stored_trip_time(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.030), CONFIG)
        self.assertEqual(result.verdict, Verdict.FAIL)

        # A later campaign decision moves the pass limit; the stored raw number
        # is enough to re-derive the verdict without re-reading the waveform.
        relaxed = replace(CONFIG, pass_limit_s=0.035)
        rederived = (
            Verdict.PASS
            if result.trip_time_s <= relaxed.pass_limit_s
            else Verdict.FAIL
        )
        self.assertEqual(rederived, Verdict.PASS)

    def test_no_trip_records_a_null_trip_time(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=None), CONFIG)
        self.assertIsNone(result.to_dict()["trip_time_s"])

    def test_notes_capture_the_endpoint_definition_and_thresholds(self) -> None:
        result = analyze_waveform(waveform_bytes(trip_time_s=0.010), CONFIG)
        for key in ("endpoints=", "t0_s=", "t_end_s=", "on_threshold_v=", "off_threshold_v="):
            self.assertIn(key, result.notes)

    def test_failed_sanity_checks_are_listed_in_sorted_order(self) -> None:
        result = TripResult(
            trip_time_s=0.01,
            verdict=Verdict.PASS,
            sanity_checks={"b": False, "a": False, "c": True},
        )
        self.assertEqual(result.failed_sanity_checks, ("a", "b"))
        self.assertFalse(result.sanity_ok)


class AnalysisConfigTests(unittest.TestCase):
    def test_defaults_match_the_locked_limits(self) -> None:
        self.assertAlmostEqual(CONFIG.pass_limit_s, PASS_LIMIT_S)
        self.assertAlmostEqual(CONFIG.no_trip_limit_s, NO_TRIP_LIMIT_S)
        self.assertAlmostEqual(CONFIG.mains_period_s, 1.0 / 60.0)
        self.assertAlmostEqual(CONFIG.envelope_window_s, HALF_CYCLE_S)

    def test_invalid_configurations_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            replace(CONFIG, pass_limit_s=0.2)
        with self.assertRaises(ValueError):
            replace(CONFIG, envelope_off_fraction=0.9)
        with self.assertRaises(ValueError):
            replace(CONFIG, line_frequency_hz=0.0)
        with self.assertRaises(ValueError):
            replace(CONFIG, endpoint_definition="  ")
        with self.assertRaises(ValueError):
            replace(CONFIG, endpoint_uncertainty_s=-1.0)

    def test_resolve_accepts_app_config_analysis_config_mapping_and_none(self) -> None:
        app_config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
        self.assertIsInstance(app_config.analysis, AnalysisConfig)
        self.assertEqual(resolve_analysis_config(app_config), app_config.analysis)
        self.assertEqual(resolve_analysis_config(CONFIG), CONFIG)
        self.assertEqual(resolve_analysis_config(None), DEFAULT_ANALYSIS_CONFIG)
        from_mapping = resolve_analysis_config({"line_frequency_hz": 50.0})
        self.assertAlmostEqual(from_mapping.line_frequency_hz, 50.0)
        with self.assertRaises(TypeError):
            resolve_analysis_config(object())

    def test_config_yaml_freezes_the_endpoint_definition(self) -> None:
        app_config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
        self.assertEqual(app_config.analysis.endpoint_definition, DEFAULT_ENDPOINT_DEFINITION)
        self.assertEqual(app_config.analysis.algorithm_version, AnalysisVersion.V3)
        self.assertAlmostEqual(app_config.analysis.pass_limit_s, app_config.timing.pass_limit_s)
        self.assertAlmostEqual(
            app_config.analysis.no_trip_limit_s, app_config.timing.no_trip_limit_s
        )

    def test_changing_the_endpoint_definition_changes_the_config_hash(self) -> None:
        app_config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
        mutated = replace(
            app_config,
            analysis=replace(app_config.analysis, endpoint_definition="something else"),
        )
        self.assertNotEqual(app_config.canonical_hash(), mutated.canonical_hash())

    def test_analysis_config_is_usable_at_fifty_hertz(self) -> None:
        config_50 = replace(CONFIG, line_frequency_hz=50.0)
        payload = synthesize_waveform_npz(
            line_frequency_hz=50.0, trip_time_s=0.020, record_after_t0_s=0.180
        )
        result = analyze_waveform(payload, config_50)
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertAlmostEqual(result.trip_time_s, 0.020, delta=5e-5)


if __name__ == "__main__":
    unittest.main()
