from __future__ import annotations

import unittest

from ccid.errors import SafetyViolationError
from ccid.hal.base import ChargingGateToken, ContactorName
from ccid.hal.gpio_sim import DeterministicCommandError, GpioSimContactorController
from ccid.safety import SafeOffExecutionError, safe_off


class _ManualClock:
    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s

    def now(self) -> float:
        return self.now_s

    def advance(self, delta_s: float) -> None:
        self.now_s += delta_s


class SafetyLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _ManualClock()
        self.gpio = GpioSimContactorController(monotonic_now=self.clock.now)

    def test_k3_requires_both_mains_closed(self) -> None:
        with self.assertRaises(SafetyViolationError):
            self.gpio.close_k3(ChargingGateToken(cycle_index=1, granted_at_monotonic_s=self.clock.now()))

        self.gpio.close_k1()
        with self.assertRaises(SafetyViolationError):
            self.gpio.close_k3(ChargingGateToken(cycle_index=1, granted_at_monotonic_s=self.clock.now()))

        self.gpio.close_k2()
        self.gpio.close_k3(ChargingGateToken(cycle_index=1, granted_at_monotonic_s=self.clock.now()))
        self.assertTrue(self.gpio.snapshot().commanded_closed[ContactorName.K3])

    def test_gate_token_is_single_use_per_cycle(self) -> None:
        token = ChargingGateToken(cycle_index=10, granted_at_monotonic_s=self.clock.now())
        self.gpio.close_k1()
        self.gpio.close_k2()
        self.gpio.close_k3(token)
        self.gpio.open_k3()
        with self.assertRaises(SafetyViolationError):
            self.gpio.close_k3(token)

    def test_mains_open_blocked_while_k3_closed(self) -> None:
        self.gpio.close_k1()
        self.gpio.close_k2()
        self.gpio.close_k3(ChargingGateToken(cycle_index=2, granted_at_monotonic_s=self.clock.now()))

        with self.assertRaises(SafetyViolationError):
            self.gpio.open_k1()
        with self.assertRaises(SafetyViolationError):
            self.gpio.open_k2()

    def test_deterministic_failure_injection(self) -> None:
        self.gpio.inject_failure("close_k1", count=1)
        with self.assertRaises(DeterministicCommandError):
            self.gpio.close_k1()
        self.gpio.close_k1()
        self.assertTrue(self.gpio.snapshot().commanded_closed[ContactorName.K1])

    def test_safe_off_attempts_all_steps_and_aggregates_failures(self) -> None:
        self.gpio.close_k1()
        self.gpio.close_k2()
        self.gpio.close_k3(ChargingGateToken(cycle_index=3, granted_at_monotonic_s=self.clock.now()))
        self.gpio.inject_failure("open_k3", count=1)
        self.gpio.inject_failure("open_k2", count=1)

        with self.assertRaises(SafeOffExecutionError) as context:
            safe_off(self.gpio)

        self.assertEqual(len(context.exception.failures), 3)
        self.assertEqual(context.exception.failures[0].operation, "open_k3")
        self.assertEqual(context.exception.failures[1].operation, "open_k2")
        self.assertEqual(context.exception.failures[2].operation, "open_k1")

    def test_safe_off_is_idempotent_and_orders_opens(self) -> None:
        self.gpio.close_k1()
        self.gpio.close_k2()
        self.gpio.close_k3(ChargingGateToken(cycle_index=4, granted_at_monotonic_s=self.clock.now()))

        safe_off(self.gpio)
        safe_off(self.gpio)

        snapshot = self.gpio.snapshot().commanded_closed
        self.assertFalse(snapshot[ContactorName.K1])
        self.assertFalse(snapshot[ContactorName.K2])
        self.assertFalse(snapshot[ContactorName.K3])
        self.assertEqual(
            self.gpio.recent_open_order(count=3),
            (ContactorName.K3, ContactorName.K2, ContactorName.K1),
        )

    def test_mains_mismatch_detector_honors_stagger_window(self) -> None:
        self.gpio.close_k1()
        self.assertFalse(
            self.gpio.detect_mains_command_mismatch(allowed_stagger_ms=200, now_monotonic_s=self.clock.now())
        )
        self.clock.advance(0.15)
        self.assertFalse(
            self.gpio.detect_mains_command_mismatch(allowed_stagger_ms=200, now_monotonic_s=self.clock.now())
        )
        self.clock.advance(0.06)
        self.assertTrue(
            self.gpio.detect_mains_command_mismatch(allowed_stagger_ms=200, now_monotonic_s=self.clock.now())
        )
        self.gpio.close_k2()
        self.assertFalse(
            self.gpio.detect_mains_command_mismatch(allowed_stagger_ms=200, now_monotonic_s=self.clock.now())
        )


if __name__ == "__main__":
    unittest.main()
