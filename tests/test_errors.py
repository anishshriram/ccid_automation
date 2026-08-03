from __future__ import annotations

import unittest

from ccid.errors import (
    CcidError,
    ConfigHashMismatchError,
    ConfigValidationError,
    PersistenceError,
    ResumeBlockedError,
    SafetyViolationError,
)


class ErrorTests(unittest.TestCase):
    def test_error_hierarchy(self) -> None:
        self.assertTrue(issubclass(ConfigValidationError, CcidError))
        self.assertTrue(issubclass(SafetyViolationError, CcidError))
        self.assertTrue(issubclass(PersistenceError, CcidError))
        self.assertTrue(issubclass(ResumeBlockedError, CcidError))
        self.assertTrue(issubclass(ConfigHashMismatchError, CcidError))


if __name__ == "__main__":
    unittest.main()
