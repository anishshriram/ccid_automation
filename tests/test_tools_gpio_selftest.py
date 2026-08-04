from __future__ import annotations

from pathlib import Path
import unittest

from ccid.hal.base import ContactorName
from ccid.hal.gpio_real import GpioRealContactorController
from ccid.hal.gpio_sim import GpioSimContactorController
from tools import gpio_selftest
from tools.simulate import ManualClock


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class _FakeOutput:
    def __init__(self, pin: int, active_high: bool = True, initial_value: bool = False) -> None:
        del active_high
        self.pin = pin
        self.state = bool(initial_value)
        self.closed = False

    def on(self) -> None:
        self.state = True

    def off(self) -> None:
        self.state = False

    def close(self) -> None:
        self.closed = True


class GpioSelftestToolTests(unittest.TestCase):
    def setUp(self) -> None:
        from ccid.config import load_config

        self.config = load_config(_CONFIG_PATH)

    def test_pin_info_matches_standard_40_pin_header(self) -> None:
        self.assertEqual(
            gpio_selftest.pin_info(self.config, ContactorName.K1),
            {"contactor": "K1", "bcm_gpio": 17, "physical_pin": 11},
        )
        self.assertEqual(
            gpio_selftest.pin_info(self.config, ContactorName.K2),
            {"contactor": "K2", "bcm_gpio": 27, "physical_pin": 13},
        )
        self.assertEqual(
            gpio_selftest.pin_info(self.config, ContactorName.K3),
            {"contactor": "K3", "bcm_gpio": 22, "physical_pin": 15},
        )

    def test_build_contactors_defaults_to_sim(self) -> None:
        contactors = gpio_selftest.build_contactors(config=self.config, real=False, clock=ManualClock())
        self.assertIsInstance(contactors, GpioSimContactorController)

    def test_build_contactors_real_uses_fake_output_factory(self) -> None:
        contactors = gpio_selftest.build_contactors(
            config=self.config, real=True, clock=ManualClock(), output_factory=_FakeOutput
        )
        self.assertIsInstance(contactors, GpioRealContactorController)

    def test_exercise_k1_two_pulses_ends_deenergized(self) -> None:
        clock = ManualClock()
        contactors = gpio_selftest.build_contactors(config=self.config, real=False, clock=clock)
        events = gpio_selftest.exercise_contactor(
            contactors, ContactorName.K1, pulses=2, hold_s=0.0, cooldown_s=0.0, clock=clock
        )
        actions = [event["action"] for event in events]
        self.assertEqual(
            actions,
            [
                "initial_safe_off",
                "close",
                "open",
                "close",
                "open",
                "final_safe_off",
            ],
        )
        self.assertFalse(contactors.snapshot().commanded_closed[ContactorName.K1])

    def test_exercise_k3_closes_k1_k2_prerequisites_first(self) -> None:
        clock = ManualClock()
        contactors = gpio_selftest.build_contactors(config=self.config, real=False, clock=clock)
        events = gpio_selftest.exercise_contactor(
            contactors, ContactorName.K3, pulses=1, hold_s=0.0, cooldown_s=0.0, clock=clock
        )
        actions = [event["action"] for event in events]
        self.assertEqual(
            actions,
            [
                "initial_safe_off",
                "close_k1_prereq",
                "close_k2_prereq",
                "close",
                "open",
                "final_safe_off",
            ],
        )
        snapshot = contactors.snapshot().commanded_closed
        self.assertFalse(snapshot[ContactorName.K1])
        self.assertFalse(snapshot[ContactorName.K2])
        self.assertFalse(snapshot[ContactorName.K3])

    def test_exercise_rejects_invalid_pulses_and_durations(self) -> None:
        clock = ManualClock()
        contactors = gpio_selftest.build_contactors(config=self.config, real=False, clock=clock)
        with self.assertRaises(ValueError):
            gpio_selftest.exercise_contactor(
                contactors, ContactorName.K1, pulses=0, hold_s=0.0, cooldown_s=0.0, clock=clock
            )
        with self.assertRaises(ValueError):
            gpio_selftest.exercise_contactor(
                contactors, ContactorName.K1, pulses=1, hold_s=-1.0, cooldown_s=0.0, clock=clock
            )

    def test_mismatch_probe_zero_stagger_flags_immediately(self) -> None:
        clock = ManualClock()
        contactors = gpio_selftest.build_contactors(config=self.config, real=False, clock=clock)
        report = gpio_selftest.mismatch_probe(contactors, stagger_ms=0, clock=clock)
        self.assertTrue(report["mismatch_detected_immediately"])
        self.assertTrue(report["mismatch_detected_after_window"])
        self.assertTrue(report["ok"])

    def test_mismatch_probe_positive_stagger_waits_before_flagging(self) -> None:
        clock = ManualClock()
        contactors = gpio_selftest.build_contactors(config=self.config, real=False, clock=clock)
        report = gpio_selftest.mismatch_probe(contactors, stagger_ms=100, clock=clock)
        self.assertFalse(report["mismatch_detected_immediately"])
        self.assertTrue(report["mismatch_detected_after_window"])
        self.assertTrue(report["ok"])

    def test_cli_show_pins(self) -> None:
        code = gpio_selftest.main(["--config", str(_CONFIG_PATH), "show-pins"])
        self.assertEqual(code, 0)

    def test_cli_exercise_sim_default(self) -> None:
        argv = [
            "--config",
            str(_CONFIG_PATH),
            "exercise",
            "--contactor",
            "K2",
            "--pulses",
            "1",
            "--hold-s",
            "0",
            "--cooldown-s",
            "0",
        ]
        code = gpio_selftest.main(argv)
        self.assertEqual(code, 0)

    def test_cli_mismatch_test_sim_default(self) -> None:
        argv = ["--config", str(_CONFIG_PATH), "mismatch-test", "--stagger-ms", "0"]
        code = gpio_selftest.main(argv)
        self.assertEqual(code, 0)

    def test_cli_real_without_acknowledgement_is_refused(self) -> None:
        argv = [
            "--config",
            str(_CONFIG_PATH),
            "exercise",
            "--contactor",
            "K1",
            "--real",
        ]
        with self.assertRaises(SystemExit):
            gpio_selftest.main(argv)


if __name__ == "__main__":
    unittest.main()
