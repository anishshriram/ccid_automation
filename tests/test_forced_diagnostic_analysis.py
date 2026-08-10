from __future__ import annotations

import math
import unittest

from ccid.analysis import synthesize_waveform_npz
from ccid.forced_diagnostic_analysis import (
    ForcedDiagnosticWaveformSummary,
    analyze_forced_diagnostic_waveform,
)


class ForcedDiagnosticAnalysisTests(unittest.TestCase):
    def test_identifies_burst_quiet_baseline_onset_and_collapse(self) -> None:
        # phase_rad=pi/2 starts the sine at full amplitude (cos-like), so
        # onset/collapse are both abrupt - easy to assert precisely.
        blob = synthesize_waveform_npz(
            amplitude_v=150.0,
            phase_rad=math.pi / 2,
            pretrigger_s=0.005,
            record_after_t0_s=0.015,
            trip_time_s=0.010,
            sample_rate_hz=200_000.0,
        )

        summary = analyze_forced_diagnostic_waveform(blob)

        self.assertIsInstance(summary, ForcedDiagnosticWaveformSummary)
        self.assertAlmostEqual(summary.max_v, 150.0, delta=1.0)
        self.assertAlmostEqual(summary.min_v, -150.0, delta=1.0)
        self.assertGreater(summary.rms_v, 50.0)

        self.assertIsNotNone(summary.sustained_onset_s)
        self.assertAlmostEqual(summary.sustained_onset_s, 0.0, delta=1e-4)

        self.assertIsNotNone(summary.quiet_baseline_duration_s)
        self.assertAlmostEqual(summary.quiet_baseline_duration_s, 0.005, delta=1e-4)
        self.assertAlmostEqual(summary.quiet_baseline_rms_v, 0.0, delta=1e-6)

        self.assertIsNotNone(summary.collapse_s)
        self.assertAlmostEqual(summary.collapse_s, 0.010, delta=1e-3)

        self.assertIsNotNone(summary.burst_duration_s)
        self.assertAlmostEqual(summary.burst_duration_s, 0.010, delta=1e-3)

        self.assertGreater(summary.positive_crossing_count, 0)
        self.assertGreater(summary.negative_crossing_count, 0)
        self.assertIsNotNone(summary.first_positive_crossing_s)
        self.assertIsNotNone(summary.first_negative_crossing_s)

    def test_quiet_record_reports_no_onset_or_collapse(self) -> None:
        # trip_time_s=0.0 with no post-t0 record window means every sample
        # is either before t0 (not conducting) or right at t0 with a
        # zero-width conduction window - an entirely quiet record.
        blob = synthesize_waveform_npz(
            amplitude_v=150.0,
            pretrigger_s=0.005,
            record_after_t0_s=0.0,
            trip_time_s=0.0,
            sample_rate_hz=200_000.0,
        )

        summary = analyze_forced_diagnostic_waveform(blob)

        self.assertIsNone(summary.sustained_onset_s)
        self.assertIsNone(summary.collapse_s)
        self.assertIsNone(summary.burst_duration_s)
        self.assertEqual(summary.positive_crossing_count, 0)
        self.assertEqual(summary.negative_crossing_count, 0)
        self.assertAlmostEqual(summary.quiet_baseline_rms_v, 0.0, delta=1e-6)

    def test_never_used_for_pass_fail(self) -> None:
        # The diagnostic summary must not resemble or be mistakable for a
        # TripResult/verdict payload.
        blob = synthesize_waveform_npz(
            amplitude_v=150.0, phase_rad=math.pi / 2, trip_time_s=0.010, sample_rate_hz=200_000.0
        )
        payload = analyze_forced_diagnostic_waveform(blob).to_dict()
        self.assertNotIn("verdict", payload)
        self.assertNotIn("trip_time_s", payload)
        self.assertNotIn("sanity_checks", payload)
        self.assertIn("never used for PASS/FAIL", payload["notes"])
