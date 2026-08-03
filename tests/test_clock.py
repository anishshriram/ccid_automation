from __future__ import annotations

from datetime import timezone
import unittest

from ccid.clock import elapsed_s, make_deadline, utc_now


class ClockTests(unittest.TestCase):
    def test_utc_now_is_timezone_aware_utc(self) -> None:
        now = utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.tzinfo, timezone.utc)

    def test_elapsed_s_uses_monotonic_inputs(self) -> None:
        self.assertAlmostEqual(elapsed_s(10.0, 12.5), 2.5)

    def test_deadline_expiry_and_remaining(self) -> None:
        deadline = make_deadline(timeout_s=2.0, now_s=100.0)
        self.assertFalse(deadline.is_expired(now_s=101.0))
        self.assertTrue(deadline.is_expired(now_s=102.0))
        self.assertAlmostEqual(deadline.remaining_s(now_s=101.25), 0.75)


if __name__ == "__main__":
    unittest.main()

