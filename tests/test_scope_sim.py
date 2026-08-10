from __future__ import annotations

import unittest

from ccid.hal.base import ScopeSettings, ScopeStatus
from ccid.hal.scope_sim import ScopeSim, ScopeSimCommunicationError, ScopeSimScenario


class _ManualClock:
    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s

    def now(self) -> float:
        return self.now_s

    def advance(self, delta_s: float) -> None:
        self.now_s += delta_s


class ScopeSimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _ManualClock()

    def test_normal_capture_path(self) -> None:
        scope = ScopeSim(monotonic_now=self.clock.now)
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        scope.arm_single()
        self.assertTrue(scope.wait_until_armed(timeout_s=2.0, now_monotonic_s=self.clock.now()))
        self.assertTrue(scope.wait_until_acquisition_complete(timeout_s=5.0, now_monotonic_s=self.clock.now()))
        capture = scope.capture_after_acquire()
        self.assertGreater(len(capture.samples), 1000)
        self.assertEqual(capture.preamble["pretrigger_s"], 0.02)
        self.assertEqual(scope.status(), ScopeStatus.COMPLETE)

    def test_never_triggered_acquisition(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(never_triggered=True),
            monotonic_now=self.clock.now,
        )
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        scope.arm_single()
        self.assertTrue(scope.wait_until_armed(timeout_s=2.0, now_monotonic_s=self.clock.now()))
        self.clock.advance(10.0)
        self.assertFalse(scope.wait_until_acquisition_complete(timeout_s=5.0, now_monotonic_s=self.clock.now()))
        self.assertEqual(scope.status(), ScopeStatus.ARMED)

    def test_trigger_event_register_clear_by_default(self) -> None:
        scope = ScopeSim(monotonic_now=self.clock.now)
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        self.assertFalse(scope.read_trigger_event_register())
        scope.arm_single()
        self.assertFalse(scope.read_trigger_event_register())

    def test_trigger_event_register_latched_stale_before_arm(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(trigger_event_latched_at_configure=True),
            monotonic_now=self.clock.now,
        )
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        self.assertTrue(scope.read_trigger_event_register())
        # Read-and-clear: a second immediate read must not still be latched.
        self.assertFalse(scope.read_trigger_event_register())

    def test_trigger_event_register_latched_before_injection(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(trigger_event_latched_before_injection=True),
            monotonic_now=self.clock.now,
        )
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        self.assertFalse(scope.read_trigger_event_register())
        scope.arm_single()
        self.assertTrue(scope.read_trigger_event_register())
        self.assertFalse(scope.read_trigger_event_register())

    def test_pretrigger_leakage_and_no_trip_flags(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(pretrigger_leakage=True, no_trip=True, sample_count=4000),
            monotonic_now=self.clock.now,
        )
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        scope.arm_single()
        scope.wait_until_armed(timeout_s=2.0, now_monotonic_s=self.clock.now())
        scope.wait_until_acquisition_complete(timeout_s=5.0, now_monotonic_s=self.clock.now())
        capture = scope.capture_after_acquire()
        self.assertEqual(capture.preamble["no_trip"], 1)
        self.assertNotEqual(capture.samples[0], 128)

    def test_transfer_truncation(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(sample_count=2000, transfer_truncated=True),
            monotonic_now=self.clock.now,
        )
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        scope.arm_single()
        scope.wait_until_armed(timeout_s=2.0, now_monotonic_s=self.clock.now())
        scope.wait_until_acquisition_complete(timeout_s=5.0, now_monotonic_s=self.clock.now())
        capture = scope.capture_after_acquire()
        self.assertLess(len(capture.samples), 2000)
        self.assertEqual(capture.preamble["points"], 2000)

    def test_invalid_and_missing_preamble_fields(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(
                invalid_preamble=True,
                missing_preamble_fields=("y_increment", "x_origin"),
            ),
            monotonic_now=self.clock.now,
        )
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        scope.arm_single()
        scope.wait_until_armed(timeout_s=2.0, now_monotonic_s=self.clock.now())
        scope.wait_until_acquisition_complete(timeout_s=5.0, now_monotonic_s=self.clock.now())
        capture = scope.capture_after_acquire()
        self.assertEqual(capture.preamble["x_increment"], "INVALID")
        self.assertNotIn("y_increment", capture.preamble)
        self.assertNotIn("x_origin", capture.preamble)

    def test_comm_error_injection(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(force_comm_errors=frozenset({"connect"})),
            monotonic_now=self.clock.now,
        )
        with self.assertRaises(ScopeSimCommunicationError):
            scope.connect()

    def test_timeout_diagnostics_returns_deterministic_bundle(self) -> None:
        scope = ScopeSim(monotonic_now=self.clock.now)
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(diagnostics.operation_condition, 0)
        self.assertEqual(diagnostics.settings["ch1_coupling"], "AC")
        self.assertEqual(diagnostics.settings["trigger_coupling"], "DC")
        self.assertEqual(diagnostics.error_queue, ())
        self.assertTrue(diagnostics.scope_png.startswith(b"\x89PNG"))

    def test_timeout_diagnostics_respects_scenario_overrides(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(
                diagnostics_operation_condition=32,
                diagnostics_settings_overrides={"trigger_mode": "PATTern"},
                diagnostics_error_queue=('-410,"Query INTERRUPTED"',),
                diagnostics_scope_png=b"\x89PNG\r\n\x1a\nCUSTOM",
            ),
            monotonic_now=self.clock.now,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(diagnostics.operation_condition, 32)
        self.assertEqual(diagnostics.settings["trigger_mode"], "PATTern")
        self.assertEqual(diagnostics.error_queue, ('-410,"Query INTERRUPTED"',))
        self.assertEqual(diagnostics.scope_png, b"\x89PNG\r\n\x1a\nCUSTOM")

    def test_timeout_diagnostics_comm_error_injection(self) -> None:
        scope = ScopeSim(
            scenario=ScopeSimScenario(force_comm_errors=frozenset({"timeout_diagnostics"})),
            monotonic_now=self.clock.now,
        )
        scope.connect()
        with self.assertRaises(ScopeSimCommunicationError):
            scope.capture_timeout_diagnostics()

    def test_timeout_diagnostics_requires_connected(self) -> None:
        scope = ScopeSim(monotonic_now=self.clock.now)
        with self.assertRaises(ScopeSimCommunicationError):
            scope.capture_timeout_diagnostics()


if __name__ == "__main__":
    unittest.main()

