from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ccid.config import load_config
from ccid.recorder import RunRecorder
from ccid.states import Terminal
from tools import simulate


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


def _argv(command: str, *rest: str) -> list[str]:
    return ["--config", str(_CONFIG_PATH), command, *rest]


class SimulateToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_root = Path(self._tmp.name)

    def test_campaign_completes_with_default_scenario(self) -> None:
        argv = _argv(
            "campaign",
            "--run-root",
            str(self.run_root),
            "--run-id",
            "campaign_ok",
            "--cycles",
            "3",
        )
        code = simulate.main(argv)
        self.assertEqual(code, 0)

    def test_campaign_halts_on_never_triggered_scope_fault(self) -> None:
        argv = _argv(
            "campaign",
            "--run-root",
            str(self.run_root),
            "--run-id",
            "campaign_fault",
            "--cycles",
            "3",
            "--scope-fault",
            "never_triggered",
        )
        code = simulate.main(argv)
        self.assertEqual(code, 1)

    def test_campaign_reports_opening_order_safe(self) -> None:
        config = load_config(_CONFIG_PATH)

        recorder = RunRecorder(self.run_root)
        run_dir = recorder.initialize_run(
            run_id="order_check",
            target_cycles=2,
            config_hash=config.canonical_hash(),
            frozen_config_yaml=_CONFIG_PATH.read_text(encoding="utf-8"),
        )
        state = recorder.load_run_state(
            run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True
        )
        result, contactors = simulate.run_campaign(
            config=config,
            recorder=recorder,
            run_dir=run_dir,
            state=state,
            clock=simulate.ManualClock(),
            scope_scenario=simulate.default_scope_scenario(),
        )
        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertTrue(simulate.opening_order_is_safe(contactors))

    def test_crash_resume_redoes_crashed_cycle_without_skip_or_duplicate(self) -> None:
        argv = _argv(
            "crash-resume",
            "--run-root",
            str(self.run_root),
            "--run-id",
            "crash_case",
            "--cycles",
            "3",
            "--crash-cycle",
            "2",
            "--crash-checkpoint",
            "after_csv",
        )
        code = simulate.main(argv)
        self.assertEqual(code, 0)

        run_dir = self.run_root / "crash_case"
        self.assertTrue(simulate.no_skipped_cycles(run_dir))

    def test_crash_resume_reports_failure_when_injection_never_triggers(self) -> None:
        argv = _argv(
            "crash-resume",
            "--run-root",
            str(self.run_root),
            "--run-id",
            "crash_never_fires",
            "--cycles",
            "1",
            "--crash-cycle",
            "5",
            "--crash-checkpoint",
            "after_csv",
        )
        code = simulate.main(argv)
        self.assertEqual(code, 1)

    def test_sticky_halt_check_confirms_resume_is_blocked(self) -> None:
        argv = _argv(
            "sticky-halt-check",
            "--run-root",
            str(self.run_root),
            "--run-id",
            "sticky_halt",
            "--cycles",
            "2",
        )
        code = simulate.main(argv)
        self.assertEqual(code, 0)

    def test_crash_injector_fires_once_at_target_cycle_and_checkpoint(self) -> None:
        injector = simulate.CrashInjector(target_cycle=2, target_checkpoint="after_csv")
        injector("after_artifacts")  # cycle 1 starts
        injector("after_csv")  # cycle 1's after_csv: should not trigger
        injector("after_runstate")
        injector("after_heartbeat")
        injector("after_artifacts")  # cycle 2 starts
        with self.assertRaises(simulate.SimulatedCrash):
            injector("after_csv")
        self.assertTrue(injector.triggered)

    def test_gpio_fail_parsing_rejects_malformed_spec(self) -> None:
        with self.assertRaises(ValueError):
            simulate._parse_gpio_fail(["close_k1"])

    def test_gpio_fail_parsing_accepts_operation_count(self) -> None:
        parsed = simulate._parse_gpio_fail(["close_k1:2", "open_k3:1"])
        self.assertEqual(parsed, {"close_k1": 2, "open_k3": 1})


if __name__ == "__main__":
    unittest.main()
