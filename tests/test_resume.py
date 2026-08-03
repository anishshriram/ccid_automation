from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from ccid.errors import ConfigHashMismatchError, ResumeBlockedError
from ccid.recorder import CycleArtifacts, CycleCsvRow, RunRecorder


class ResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    @staticmethod
    def _row(cycle_index: int) -> CycleCsvRow:
        return CycleCsvRow.from_values(
            cycle_index=cycle_index,
            run_id="20260803_140000",
            monotonic_start=200.0 + cycle_index,
            trip_time_s=0.02,
            verdict="PASS",
            analysis_version="v0-provisional",
            led_state_at_gate="CHARGING",
        )

    @staticmethod
    def _artifacts() -> CycleArtifacts:
        return CycleArtifacts(
            waveform_samples=b"\x01\x02",
            waveform_preamble={"x_increment": 1e-7, "points": 2},
            scope_png=b"png",
            gate_jpg=b"jpg",
            fault_jpg_burst=(),
            cycle_sidecar={},
        )

    def test_resume_blocks_on_halt_without_override(self) -> None:
        recorder = RunRecorder(self.root)
        run_dir = recorder.initialize_run(
            run_id="20260803_140000",
            target_cycles=6000,
            config_hash="cfg",
            frozen_config_yaml="schema_version: 1\n",
        )
        state = recorder.load_run_state(run_dir, expected_config_hash="cfg", allow_halted_resume=True)
        recorder.record_cycle(
            run_dir=run_dir,
            state=state,
            csv_row=self._row(1),
            artifacts=self._artifacts(),
            halt_reason="rig_fault",
        )
        with self.assertRaises(ResumeBlockedError):
            recorder.load_run_state(run_dir, expected_config_hash="cfg", allow_halted_resume=False)

    def test_resume_blocks_on_config_hash_mismatch(self) -> None:
        recorder = RunRecorder(self.root)
        run_dir = recorder.initialize_run(
            run_id="20260803_140000",
            target_cycles=6000,
            config_hash="cfg",
            frozen_config_yaml="schema_version: 1\n",
        )
        with self.assertRaises(ConfigHashMismatchError):
            recorder.load_run_state(run_dir, expected_config_hash="different", allow_halted_resume=True)

    def test_crash_after_csv_keeps_last_completed_and_reconcile_removes_orphans(self) -> None:
        def crash_injector(step: str) -> None:
            if step == "after_csv":
                raise RuntimeError("simulated crash")

        recorder = RunRecorder(self.root, crash_injector=crash_injector)
        run_dir = recorder.initialize_run(
            run_id="20260803_140000",
            target_cycles=6000,
            config_hash="cfg",
            frozen_config_yaml="schema_version: 1\n",
        )
        state = recorder.load_run_state(run_dir, expected_config_hash="cfg", allow_halted_resume=True)

        with self.assertRaises(RuntimeError):
            recorder.record_cycle(
                run_dir=run_dir,
                state=state,
                csv_row=self._row(1),
                artifacts=self._artifacts(),
            )

        state_after_crash = recorder.load_run_state(
            run_dir,
            expected_config_hash="cfg",
            allow_halted_resume=True,
        )
        self.assertEqual(state_after_crash.last_completed_cycle, 0)
        self.assertTrue((run_dir / "cycles" / "1.json").exists())
        self.assertTrue((run_dir / "waveforms" / "1.npz").exists())

        recorder.reconcile_orphans(run_dir, state_after_crash)
        self.assertFalse((run_dir / "cycles" / "1.json").exists())
        self.assertFalse((run_dir / "waveforms" / "1.npz").exists())
        self.assertFalse((run_dir / "images" / "1_scope.png").exists())
        self.assertFalse((run_dir / "images" / "1_green.jpg").exists())

        with (run_dir / "cycles.csv").open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows, [])

    def test_crash_after_runstate_keeps_committed_cycle(self) -> None:
        def crash_injector(step: str) -> None:
            if step == "after_runstate":
                raise RuntimeError("simulated crash after durable state")

        recorder = RunRecorder(self.root, crash_injector=crash_injector)
        run_dir = recorder.initialize_run(
            run_id="20260803_140000",
            target_cycles=6000,
            config_hash="cfg",
            frozen_config_yaml="schema_version: 1\n",
        )
        state = recorder.load_run_state(run_dir, expected_config_hash="cfg", allow_halted_resume=True)

        with self.assertRaises(RuntimeError):
            recorder.record_cycle(
                run_dir=run_dir,
                state=state,
                csv_row=self._row(1),
                artifacts=self._artifacts(),
            )

        state_after_crash = recorder.load_run_state(
            run_dir,
            expected_config_hash="cfg",
            allow_halted_resume=True,
        )
        self.assertEqual(state_after_crash.last_completed_cycle, 1)


if __name__ == "__main__":
    unittest.main()

