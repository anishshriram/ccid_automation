from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ccid.errors import HardwareInterfaceError, SafetyViolationError
from ccid.hal.base import ChargingGateToken, ContactorInterface, ContactorName, ContactorSnapshot


@dataclass(frozen=True)
class SimContactorEvent:
    operation: str
    contactor: ContactorName
    commanded_closed: bool
    monotonic_s: float
    success: bool
    detail: str = ""


class DeterministicCommandError(HardwareInterfaceError):
    pass


class GpioSimContactorController(ContactorInterface):
    """Simulation-first contactor controller with hard interlocks and event logs."""

    def __init__(self, monotonic_now: Callable[[], float]) -> None:
        self._monotonic_now = monotonic_now
        self._commanded = {
            ContactorName.K1: False,
            ContactorName.K2: False,
            ContactorName.K3: False,
        }
        self._events: list[SimContactorEvent] = []
        self._failure_budget: dict[str, int] = {}
        self._used_gate_cycles: set[int] = set()
        self._last_change_s = {
            ContactorName.K1: self._monotonic_now(),
            ContactorName.K2: self._monotonic_now(),
            ContactorName.K3: self._monotonic_now(),
        }
        self._mismatch_started_s: float | None = None

    def inject_failure(self, operation: str, count: int = 1) -> None:
        if count <= 0:
            raise ValueError("count must be positive")
        self._failure_budget[operation] = self._failure_budget.get(operation, 0) + count

    def clear_failures(self) -> None:
        self._failure_budget.clear()

    def events(self) -> tuple[SimContactorEvent, ...]:
        return tuple(self._events)

    def close_k1(self) -> None:
        self._apply_command("close_k1", ContactorName.K1, True)

    def close_k2(self) -> None:
        self._apply_command("close_k2", ContactorName.K2, True)

    def close_k3(self, gate: ChargingGateToken) -> None:
        if gate.cycle_index in self._used_gate_cycles:
            raise SafetyViolationError(
                f"Charging gate token for cycle {gate.cycle_index} has already been used"
            )
        if not self._commanded[ContactorName.K1] or not self._commanded[ContactorName.K2]:
            raise SafetyViolationError("K3 may close only when both K1 and K2 are commanded closed")
        self._apply_command("close_k3", ContactorName.K3, True)
        self._used_gate_cycles.add(gate.cycle_index)

    def open_k1(self) -> None:
        if self._commanded[ContactorName.K3]:
            raise SafetyViolationError("K1 may not open while K3 is commanded closed")
        self._apply_command("open_k1", ContactorName.K1, False)

    def open_k2(self) -> None:
        if self._commanded[ContactorName.K3]:
            raise SafetyViolationError("K2 may not open while K3 is commanded closed")
        self._apply_command("open_k2", ContactorName.K2, False)

    def open_k3(self) -> None:
        self._apply_command("open_k3", ContactorName.K3, False)

    def safe_open_all(self) -> None:
        self.open_k3()
        self.open_k2()
        self.open_k1()

    def snapshot(self) -> ContactorSnapshot:
        return ContactorSnapshot(
            commanded_closed=dict(self._commanded),
            captured_at_monotonic_s=self._monotonic_now(),
        )

    def detect_mains_command_mismatch(self, allowed_stagger_ms: int, now_monotonic_s: float) -> bool:
        if allowed_stagger_ms < 0:
            raise ValueError("allowed_stagger_ms must be >= 0")

        k1_closed = self._commanded[ContactorName.K1]
        k2_closed = self._commanded[ContactorName.K2]
        mismatch = k1_closed != k2_closed
        if not mismatch:
            self._mismatch_started_s = None
            return False

        if self._mismatch_started_s is None:
            self._mismatch_started_s = now_monotonic_s
            return allowed_stagger_ms == 0

        elapsed_ms = (now_monotonic_s - self._mismatch_started_s) * 1000.0
        return elapsed_ms > float(allowed_stagger_ms)

    def recent_open_order(self, count: int = 3) -> tuple[ContactorName, ...]:
        opens = [
            event.contactor
            for event in self._events
            if event.success and event.operation.startswith("open_")
        ]
        if count <= 0:
            return tuple()
        return tuple(opens[-count:])

    def _apply_command(self, operation: str, contactor: ContactorName, commanded_closed: bool) -> None:
        self._consume_failure_budget(operation=operation, contactor=contactor, state=commanded_closed)
        self._commanded[contactor] = commanded_closed
        now_s = self._monotonic_now()
        self._last_change_s[contactor] = now_s
        self._events.append(
            SimContactorEvent(
                operation=operation,
                contactor=contactor,
                commanded_closed=commanded_closed,
                monotonic_s=now_s,
                success=True,
            )
        )

    def _consume_failure_budget(self, operation: str, contactor: ContactorName, state: bool) -> None:
        remaining = self._failure_budget.get(operation, 0)
        if remaining <= 0:
            return
        self._failure_budget[operation] = remaining - 1
        self._events.append(
            SimContactorEvent(
                operation=operation,
                contactor=contactor,
                commanded_closed=state,
                monotonic_s=self._monotonic_now(),
                success=False,
                detail="injected_failure",
            )
        )
        raise DeterministicCommandError(f"Injected command failure for operation {operation}")
