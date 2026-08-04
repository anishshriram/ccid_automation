from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from ccid.analysis import AnalysisVersion
from ccid.config import load_config
from ccid.recorder import RunRecorder
from ccid.states import Terminal
from tools import replay_waveform, simulate


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class ReplayWaveformToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_root = Path(self._tmp.name)

        config = load_config(_CONFIG_PATH)
        recorder = RunRecorder(self.run_root)
        self.run_dir = recorder.initialize_run(
            run_id="replay_fixture",
            target_cycles=3,
            config_hash=config.canonical_hash(),
            frozen_config_yaml=_CONFIG_PATH.read_text(encoding="utf-8"),
        )
        state = recorder.load_run_state(
            self.run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True
        )
        result, _contactors = simulate.run_campaign(
            config=config,
            recorder=recorder,
            run_dir=self.run_dir,
            state=state,
            clock=simulate.ManualClock(),
            scope_scenario=simulate.default_scope_scenario(),
        )
        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertEqual(result.state.last_completed_cycle, 3)

    def test_replay_single_waveform_file_matches_inline_result(self) -> None:
        waveform_path = self.run_dir / "waveforms" / "1.npz"
        original_bytes = waveform_path.read_bytes()

        analysis_config = replay_waveform.build_analysis_config(_CONFIG_PATH, None)
        result = replay_waveform.replay_single_waveform(waveform_path, analysis_config)

        original = replay_waveform.load_original_trip_result(self.run_dir, 1)
        self.assertIsNotNone(original)
        self.assertEqual(result.verdict, original.verdict)
        self.assertAlmostEqual(result.trip_time_s, original.trip_time_s, places=6)

        # Raw data must never be touched by a replay.
        self.assertEqual(waveform_path.read_bytes(), original_bytes)

    def test_replay_run_writes_report_without_touching_originals(self) -> None:
        original_csv = (self.run_dir / "cycles.csv").read_bytes()
        original_waveforms = {
            n: (self.run_dir / "waveforms" / f"{n}.npz").read_bytes() for n in (1, 2, 3)
        }
        original_sidecars = {
            n: (self.run_dir / "cycles" / f"{n}.json").read_bytes() for n in (1, 2, 3)
        }

        analysis_config = replay_waveform.build_analysis_config(_CONFIG_PATH, None)
        cycle_indices = replay_waveform.resolve_cycle_range(
            self.run_dir, cycle=None, from_cycle=None, to_cycle=None
        )
        self.assertEqual(cycle_indices, [1, 2, 3])

        out_dir, rows = replay_waveform.replay_run(
            run_dir=self.run_dir,
            analysis_config=analysis_config,
            cycle_indices=cycle_indices,
        )

        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertFalse(row["verdict_changed"])

        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["cycles_replayed"], [1, 2, 3])
        self.assertEqual(manifest["cycles_with_verdict_change"], [])

        report_path = out_dir / "change_report.csv"
        with report_path.open("r", encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        self.assertEqual(len(csv_rows), 3)
        self.assertEqual(csv_rows[0]["verdict_changed"], "False")

        for n in (1, 2, 3):
            payload = json.loads((out_dir / "cycles" / f"{n}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "replay")
            self.assertEqual(payload["cycle_index"], n)

        # Original artifacts must be byte-for-byte unchanged.
        self.assertEqual((self.run_dir / "cycles.csv").read_bytes(), original_csv)
        for n in (1, 2, 3):
            self.assertEqual(
                (self.run_dir / "waveforms" / f"{n}.npz").read_bytes(), original_waveforms[n]
            )
            self.assertEqual(
                (self.run_dir / "cycles" / f"{n}.json").read_bytes(), original_sidecars[n]
            )

    def test_replay_run_single_cycle(self) -> None:
        analysis_config = replay_waveform.build_analysis_config(_CONFIG_PATH, None)
        out_dir, rows = replay_waveform.replay_run(
            run_dir=self.run_dir,
            analysis_config=analysis_config,
            cycle_indices=[2],
        )
        self.assertEqual([row["cycle_index"] for row in rows], [2])
        self.assertTrue((out_dir / "cycles" / "2.json").exists())
        self.assertFalse((out_dir / "cycles" / "1.json").exists())

    def test_algorithm_version_override_is_recorded_in_report(self) -> None:
        analysis_config = replay_waveform.build_analysis_config(_CONFIG_PATH, "v1")
        self.assertEqual(analysis_config.algorithm_version, AnalysisVersion.V1)
        out_dir, rows = replay_waveform.replay_run(
            run_dir=self.run_dir,
            analysis_config=analysis_config,
            cycle_indices=[1],
        )
        self.assertEqual(rows[0]["replay_algorithm_version"], "v1")
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["algorithm_version"], "v1")

    def test_unsupported_algorithm_version_rejected(self) -> None:
        with self.assertRaises(ValueError):
            replay_waveform.build_analysis_config(_CONFIG_PATH, "v99-does-not-exist")

    def test_cycle_range_rejects_conflicting_options(self) -> None:
        with self.assertRaises(ValueError):
            replay_waveform.resolve_cycle_range(self.run_dir, cycle=1, from_cycle=1, to_cycle=2)
        with self.assertRaises(ValueError):
            replay_waveform.resolve_cycle_range(self.run_dir, cycle=None, from_cycle=2, to_cycle=None)

    def test_cli_run_subcommand_end_to_end(self) -> None:
        out_dir = self.run_root / "custom_reanalysis"
        argv = [
            "--config",
            str(_CONFIG_PATH),
            "run",
            str(self.run_dir),
            "--from",
            "1",
            "--to",
            "2",
            "--out-dir",
            str(out_dir),
        ]
        code = replay_waveform.main(argv)
        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "cycles" / "1.json").exists())
        self.assertTrue((out_dir / "cycles" / "2.json").exists())
        self.assertFalse((out_dir / "cycles" / "3.json").exists())

    def test_cli_waveform_subcommand_writes_optional_output(self) -> None:
        waveform_path = self.run_dir / "waveforms" / "1.npz"
        out_path = self.run_root / "single_result.json"
        argv = [
            "--config",
            str(_CONFIG_PATH),
            "waveform",
            str(waveform_path),
            "--out",
            str(out_path),
        ]
        code = replay_waveform.main(argv)
        self.assertEqual(code, 0)
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source"], "replay")
        self.assertIn("verdict", payload)

    def test_missing_waveform_for_cycle_is_skipped_not_fatal(self) -> None:
        analysis_config = replay_waveform.build_analysis_config(_CONFIG_PATH, None)
        out_dir, rows = replay_waveform.replay_run(
            run_dir=self.run_dir,
            analysis_config=analysis_config,
            cycle_indices=[1, 999],
        )
        self.assertEqual([row["cycle_index"] for row in rows], [1])


if __name__ == "__main__":
    unittest.main()
