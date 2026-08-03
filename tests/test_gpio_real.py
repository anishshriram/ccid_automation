from __future__ import annotations

import unittest

from ccid.errors import SafetyViolationError
from ccid.hal.base import ChargingGateToken, ContactorName
from ccid.hal.gpio_real import GpioRealContactorController


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


class GpioRealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now_s = 0.0
        self.gpio = GpioRealContactorController(
            gpio_k1=17,
            gpio_k2=27,
            gpio_k3=22,
            monotonic_now=lambda: self.now_s,
            output_factory=_FakeOutput,
        )

    def test_k3_interlock_matches_sim_behavior(self) -> None:
        with self.assertRaises(SafetyViolationError):
            self.gpio.close_k3(ChargingGateToken(cycle_index=1, granted_at_monotonic_s=0.0))
        self.gpio.close_k1()
        with self.assertRaises(SafetyViolationError):
            self.gpio.close_k3(ChargingGateToken(cycle_index=1, granted_at_monotonic_s=0.0))
        self.gpio.close_k2()
        self.gpio.close_k3(ChargingGateToken(cycle_index=1, granted_at_monotonic_s=0.0))
        self.assertTrue(self.gpio.snapshot().commanded_closed[ContactorName.K3])

    def test_mains_open_blocked_while_k3_closed(self) -> None:
        self.gpio.close_k1()
        self.gpio.close_k2()
        self.gpio.close_k3(ChargingGateToken(cycle_index=2, granted_at_monotonic_s=0.0))
        with self.assertRaises(SafetyViolationError):
            self.gpio.open_k1()
        with self.assertRaises(SafetyViolationError):
            self.gpio.open_k2()

    def test_mismatch_detector_tracks_stagger_window(self) -> None:
        self.gpio.close_k1()
        self.assertFalse(self.gpio.detect_mains_command_mismatch(allowed_stagger_ms=100, now_monotonic_s=0.0))
        self.now_s = 0.05
        self.assertFalse(self.gpio.detect_mains_command_mismatch(allowed_stagger_ms=100, now_monotonic_s=0.05))
        self.now_s = 0.11
        self.assertTrue(self.gpio.detect_mains_command_mismatch(allowed_stagger_ms=100, now_monotonic_s=0.11))


if __name__ == "__main__":
    unittest.main()
