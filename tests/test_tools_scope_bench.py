from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ccid.hal.base import ScopeSettings
from ccid.hal.scope_sim import ScopeSim, ScopeSimScenario
from tools import scope_bench


def _fast_scope(**overrides) -> ScopeSim:
    kwargs = dict(sample_rate_hz=200_000.0, sample_count=2_000, trip_time_s=0.005)
    kwargs.update(overrides)
    return ScopeSim(scenario=ScopeSimScenario(**kwargs), monotonic_now=lambda: 0.0)


class ScopeBenchToolTests(unittest.TestCase):
    def test_build_scope_defaults_to_sim(self) -> None:
        scope = scope_bench.build_scope(real=False, resource=None, monotonic_now=lambda: 0.0)
        self.assertIsInstance(scope, ScopeSim)

    def test_build_scope_real_without_resource_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            scope_bench.build_scope(real=True, resource=None, monotonic_now=lambda: 0.0)

    def test_identify_scope_returns_idn(self) -> None:
        scope = _fast_scope()
        report = scope_bench.identify_scope(scope)
        self.assertIn("idn", report)
        self.assertIn("SCOPE_SIM", report["idn"])

    def test_apply_and_readback_reports_both(self) -> None:
        scope = _fast_scope()
        report = scope_bench.apply_and_readback(scope, ScopeSettings())
        self.assertIn("applied", report)
        self.assertIn("readback", report)
        self.assertEqual(report["readback"]["waveform_format"], "BYTE")

    def test_verify_arm_polling_succeeds_with_manual_clock(self) -> None:
        now = {"t": 0.0}

        def monotonic_now() -> float:
            return now["t"]

        def sleep(seconds: float) -> None:
            now["t"] += seconds

        scope = ScopeSim(
            scenario=ScopeSimScenario(sample_rate_hz=200_000.0, sample_count=2_000),
            monotonic_now=monotonic_now,
        )
        report = scope_bench.verify_arm_polling(
            scope, timeout_s=1.0, monotonic_now=monotonic_now, sleep=sleep
        )
        self.assertTrue(report["armed"])

    def test_query_memory_depth_reports_configured_points(self) -> None:
        scope = _fast_scope()
        report = scope_bench.query_memory_depth(scope)
        self.assertEqual(report["waveform_points_mode"], "RAW")
        self.assertEqual(report["waveform_points"], "MAXimum")

    def test_time_capture_and_save_and_validate_round_trip(self) -> None:
        now = {"t": 0.0}

        def monotonic_now() -> float:
            now["t"] += 0.001
            return now["t"]

        scope = ScopeSim(
            scenario=ScopeSimScenario(sample_rate_hz=200_000.0, sample_count=2_000, trip_time_s=0.005),
            monotonic_now=monotonic_now,
        )
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        scope.arm_single()
        self.assertTrue(scope.wait_until_armed(timeout_s=1.0, now_monotonic_s=monotonic_now()))
        self.assertTrue(scope.wait_until_acquisition_complete(timeout_s=1.0, now_monotonic_s=monotonic_now()))

        capture, timing = scope_bench.time_capture(scope, monotonic_now=monotonic_now)
        self.assertGreaterEqual(timing["elapsed_s"], 0.0)
        self.assertEqual(timing["waveform_bytes"], 2_000)
        self.assertGreater(timing["png_bytes"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "bench_out"
            validation = scope_bench.save_and_validate_capture(capture, out_dir, label="t")
            self.assertTrue(validation["valid"])
            self.assertEqual(validation["sample_count"], 2_000)
            self.assertTrue((out_dir / "t_waveform.npz").exists())
            self.assertTrue((out_dir / "t_scope.png").exists())
            self.assertTrue((out_dir / "t_settings_readback.json").exists())

    def test_cli_capture_bench_end_to_end_sim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                "capture-bench",
                "--out-dir",
                str(Path(tmp) / "out"),
                "--label",
                "cli",
                "--timeout-s",
                "2.0",
            ]
            code = scope_bench.main(argv)
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "out" / "cli_waveform.npz").exists())

    def test_cli_identify_sim(self) -> None:
        code = scope_bench.main(["identify"])
        self.assertEqual(code, 0)

    def test_cli_arm_check_sim(self) -> None:
        code = scope_bench.main(["arm-check", "--timeout-s", "1.0"])
        self.assertEqual(code, 0)

    def test_cli_memory_depth_sim(self) -> None:
        code = scope_bench.main(["memory-depth"])
        self.assertEqual(code, 0)

    def test_cli_real_without_resource_is_refused(self) -> None:
        argv = ["identify", "--real"]
        with self.assertRaises(SystemExit):
            scope_bench.main(argv)


if __name__ == "__main__":
    unittest.main()
