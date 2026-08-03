from __future__ import annotations

import unittest

from ccid.errors import CcidError, ConfigValidationError, SafetyViolationError


class ErrorTests(unittest.TestCase):
    def test_error_hierarchy(self) -> None:
        self.assertTrue(issubclass(ConfigValidationError, CcidError))
        self.assertTrue(issubclass(SafetyViolationError, CcidError))


if __name__ == "__main__":
    unittest.main()

