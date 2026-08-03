from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()

