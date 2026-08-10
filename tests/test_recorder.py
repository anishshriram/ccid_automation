from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from ccid.hal.base import ScopeTimeoutDiagnostics, WaveformCapture
from ccid.recorder import CycleArtifacts, CycleCsvRow, RunRecorder


class RecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.recorder = RunRecorder(self.root)
        self.run_dir = self.recorder.initialize_run(
            run_id="20260803_135500",
            target_cycles=6000,
            config_hash="abc123",
            frozen_config_yaml="schema_version: 1\n",
        )

    def _state(self):
        return self.recorder.load_run_state(
            self.run_dir,
            expected_config_hash="abc123",
            allow_halted_resume=True,
        )

    def _row(self, cycle_index: int, trip_time_s: float | None, verdict: str = "PASS") -> CycleCsvRow:
        return CycleCsvRow.from_values(
            cycle_index=cycle_index,
            run_id="20260803_135500",
            monotonic_start=100.123 + cycle_index,
            trip_time_s=trip_time_s,
            verdict=verdict,
            analysis_version="v0-provisional",
            led_state_at_gate="CHARGING",
            degraded_flags="",
            notes="",
        )

    @staticmethod
    def _artifacts() -> CycleArtifacts:
        return CycleArtifacts(
            waveform_samples=b"\x01\x02\x03\x04",
            waveform_preamble={"x_increment": 1e-7, "points": 4},
            scope_png=b"png-bytes",
            gate_jpg=b"jpg-bytes",
            fault_jpg_burst=(b"fault-1", b"fault-2"),
            cycle_sidecar={"scope_settings": {"waveform_points_mode": "RAW"}},
        )

    def test_record_cycle_writes_artifacts_csv_and_runstate(self) -> None:
        next_state = self.recorder.record_cycle(
            run_dir=self.run_dir,
            state=self._state(),
            csv_row=self._row(cycle_index=1, trip_time_s=0.024969),
            artifacts=self._artifacts(),
        )
        self.assertEqual(next_state.last_completed_cycle, 1)

        self.assertTrue((self.run_dir / "waveforms" / "1.npz").exists())
        self.assertTrue((self.run_dir / "images" / "1_scope.png").exists())
        self.assertTrue((self.run_dir / "images" / "1_green.jpg").exists())
        self.assertTrue((self.run_dir / "images" / "1_fault_1.jpg").exists())
        self.assertTrue((self.run_dir / "images" / "1_fault_2.jpg").exists())
        self.assertTrue((self.run_dir / "cycles" / "1.json").exists())

        sidecar = json.loads((self.run_dir / "cycles" / "1.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["cycle_index"], 1)
        self.assertEqual(sidecar["verdict"], "PASS")

    def test_cycles_csv_keeps_raw_trip_float_separate_from_verdict(self) -> None:
        self.recorder.record_cycle(
            run_dir=self.run_dir,
            state=self._state(),
            csv_row=self._row(cycle_index=1, trip_time_s=0.0249701, verdict="FAIL"),
            artifacts=self._artifacts(),
        )
        with (self.run_dir / "cycles.csv").open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "FAIL")
        self.assertEqual(float(rows[0]["trip_time_s"]), 0.0249701)

    def test_halt_reason_is_sticky_in_runstate(self) -> None:
        next_state = self.recorder.record_cycle(
            run_dir=self.run_dir,
            state=self._state(),
            csv_row=self._row(cycle_index=1, trip_time_s=None, verdict="NO_TRIP"),
            artifacts=self._artifacts(),
            halt_reason="dut_no_trip",
        )
        self.assertEqual(next_state.halt_reason, "dut_no_trip")

    @staticmethod
    def _diagnostics(error_queue: tuple[str, ...] = ()) -> ScopeTimeoutDiagnostics:
        return ScopeTimeoutDiagnostics(
            captured_at_utc=datetime(2026, 8, 10, tzinfo=timezone.utc),
            captured_at_monotonic_s=873.9,
            operation_condition=32,
            hal_status="ARMED",
            settings={"trigger_mode": "EDGE", "ch1_coupling": "AC"},
            error_queue=error_queue,
            scope_png=b"\x89PNG\r\n\x1a\ntimeout",
        )

    def test_write_timeout_diagnostics_writes_expected_files(self) -> None:
        self.recorder.write_timeout_diagnostics(
            run_dir=self.run_dir,
            run_id="20260803_135500",
            cycle_index=1,
            diagnostics=self._diagnostics(error_queue=('-410,"Query INTERRUPTED"',)),
            k3_closed_monotonic_s=873.9,
            k3_open_monotonic_s=874.2,
            k3_open_reason="backstop",
            primary_halt_reason="rig:scope_never_triggered_or_acquire_timeout",
        )

        diag_dir = self.run_dir / "diagnostics" / "1"
        self.assertTrue((diag_dir / "scope_timeout.png").exists())
        self.assertEqual((diag_dir / "scope_timeout.png").read_bytes(), b"\x89PNG\r\n\x1a\ntimeout")

        state = json.loads((diag_dir / "scope_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["run_id"], "20260803_135500")
        self.assertEqual(state["cycle_index"], 1)
        self.assertEqual(state["operation_condition"], 32)
        self.assertEqual(state["settings"]["trigger_mode"], "EDGE")
        self.assertEqual(state["k3_open_reason"], "backstop")
        self.assertAlmostEqual(state["k3_duration_s"], 0.3, places=6)
        self.assertEqual(state["primary_halt_reason"], "rig:scope_never_triggered_or_acquire_timeout")

        errors_text = (diag_dir / "scope_errors.txt").read_text(encoding="utf-8")
        self.assertIn("Query INTERRUPTED", errors_text)

    def test_write_timeout_diagnostics_does_not_touch_normal_artifacts_or_runstate(self) -> None:
        runstate_before = (self.run_dir / "runstate.json").read_bytes()

        self.recorder.write_timeout_diagnostics(
            run_dir=self.run_dir,
            run_id="20260803_135500",
            cycle_index=1,
            diagnostics=self._diagnostics(),
            k3_closed_monotonic_s=None,
            k3_open_monotonic_s=None,
            k3_open_reason=None,
            primary_halt_reason="rig:scope_never_triggered_or_acquire_timeout",
        )

        self.assertEqual((self.run_dir / "runstate.json").read_bytes(), runstate_before)
        self.assertFalse((self.run_dir / "waveforms" / "1.npz").exists())
        self.assertFalse((self.run_dir / "images" / "1_scope.png").exists())
        self.assertFalse((self.run_dir / "cycles" / "1.json").exists())

    def test_write_timeout_diagnostics_no_errors_marker(self) -> None:
        self.recorder.write_timeout_diagnostics(
            run_dir=self.run_dir,
            run_id="20260803_135500",
            cycle_index=1,
            diagnostics=self._diagnostics(error_queue=()),
            k3_closed_monotonic_s=None,
            k3_open_monotonic_s=None,
            k3_open_reason=None,
            primary_halt_reason="rig:scope_never_triggered_or_acquire_timeout",
        )

        errors_text = (self.run_dir / "diagnostics" / "1" / "scope_errors.txt").read_text(encoding="utf-8")
        self.assertEqual(errors_text, "no errors\n")

    @staticmethod
    def _forced_capture() -> WaveformCapture:
        return WaveformCapture(
            samples=b"\x01\x02\x03\x04",
            preamble={"x_increment": 1e-7, "points": 4},
            settings_readback={"waveform_points_mode": "RAW"},
            scope_png=b"\x89PNG\r\n\x1a\nforced",
            captured_at_utc=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )

    def test_write_forced_diagnostic_capture_writes_expected_files(self) -> None:
        self.recorder.write_forced_diagnostic_capture(
            run_dir=self.run_dir,
            run_id="20260803_135500",
            cycle_index=1,
            capture=self._forced_capture(),
            force_command_start_monotonic_s=100.10,
            force_command_return_monotonic_s=100.101,
            forced_acquisition_completion_monotonic_s=100.102,
            k3_closed_monotonic_s=100.0,
            diagnostic_timeline=[
                {"stage": "k3_close", "monotonic_s": 100.0, "operation_condition": 40,
                 "trigger_event_register": None, "hal_status": "ARMED"},
                {"stage": "force_command_start", "monotonic_s": 100.10, "operation_condition": 40,
                 "trigger_event_register": None, "hal_status": "ACQUIRING"},
            ],
            waveform_analysis={"min_v": -167.0, "max_v": 141.0, "rms_v": 80.0},
        )

        diag_dir = self.run_dir / "diagnostics" / "1"
        self.assertTrue((diag_dir / "forced_diagnostic_waveform.npz").exists())
        self.assertEqual((diag_dir / "forced_diagnostic_scope.png").read_bytes(), b"\x89PNG\r\n\x1a\nforced")

        state = json.loads((diag_dir / "forced_diagnostic_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["capture_type"], "forced_diagnostic_non_measurement")
        timing = state["pi_side_timing"]
        self.assertEqual(timing["force_command_start_monotonic_s"], 100.10)
        self.assertEqual(timing["force_command_return_monotonic_s"], 100.101)
        self.assertEqual(timing["forced_acquisition_completion_monotonic_s"], 100.102)
        self.assertEqual(timing["k3_closed_monotonic_s"], 100.0)
        self.assertEqual(len(state["diagnostic_timeline"]), 2)
        self.assertEqual(state["diagnostic_timeline"][0]["stage"], "k3_close")
        self.assertEqual(state["waveform_analysis"]["min_v"], -167.0)
        # The old single-timestamp fields this replaces must be gone, not
        # just unused - their presence is exactly what enabled the wrong
        # conclusion in SCOPE_TRIGGER_DEBUG_LOG.md Entry 13.
        self.assertNotIn("forced_at_monotonic_s", state)
        self.assertNotIn("elapsed_since_k3_closed_s", state)

    def test_write_forced_diagnostic_capture_does_not_touch_normal_artifacts_or_runstate(self) -> None:
        runstate_before = (self.run_dir / "runstate.json").read_bytes()

        self.recorder.write_forced_diagnostic_capture(
            run_dir=self.run_dir,
            run_id="20260803_135500",
            cycle_index=1,
            capture=self._forced_capture(),
            force_command_start_monotonic_s=100.10,
            force_command_return_monotonic_s=100.101,
            forced_acquisition_completion_monotonic_s=None,
            k3_closed_monotonic_s=100.0,
            diagnostic_timeline=[],
            waveform_analysis=None,
        )

        self.assertEqual((self.run_dir / "runstate.json").read_bytes(), runstate_before)
        self.assertFalse((self.run_dir / "waveforms" / "1.npz").exists())
        self.assertFalse((self.run_dir / "images" / "1_scope.png").exists())
        self.assertFalse((self.run_dir / "cycles" / "1.json").exists())


if __name__ == "__main__":
    unittest.main()

