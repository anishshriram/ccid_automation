from __future__ import annotations

from dataclasses import dataclass

from ccid.errors import CcidError
from ccid.hal.base import ContactorInterface


@dataclass(frozen=True)
class SafeOffStepFailure:
    operation: str
    error: Exception


class SafeOffExecutionError(CcidError):
    def __init__(self, failures: list[SafeOffStepFailure]) -> None:
        self.failures = tuple(failures)
        summary = ", ".join(f"{failure.operation}: {failure.error}" for failure in failures)
        super().__init__(f"SafeOff encountered failures: {summary}")


def safe_off(contactors: ContactorInterface) -> None:
    """Attempt full de-energization in strict K3->K2->K1 order.

    This routine is idempotent and failure-resilient: every open command is attempted
    even if earlier commands fail, and all failures are returned in one aggregate error.
    """

    failures: list[SafeOffStepFailure] = []
    for operation, action in (
        ("open_k3", contactors.open_k3),
        ("open_k2", contactors.open_k2),
        ("open_k1", contactors.open_k1),
    ):
        try:
            action()
        except Exception as exc:
            failures.append(SafeOffStepFailure(operation=operation, error=exc))

    if failures:
        raise SafeOffExecutionError(failures)

