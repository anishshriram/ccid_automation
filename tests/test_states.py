from __future__ import annotations

import dataclasses
import unittest

from ccid.states import CycleDecision, CycleState, Terminal


class StatesTests(unittest.TestCase):
    def test_cycle_state_contains_required_values(self) -> None:
        self.assertEqual(CycleState.SAFE_OFF.value, "SAFE_OFF")
        self.assertEqual(CycleState.HALTED.value, "HALTED")

    def test_cycle_decision_is_immutable(self) -> None:
        decision = CycleDecision(terminal=Terminal.PASS, notes="ok")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.notes = "mutated"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

