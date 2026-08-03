from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from ccid.errors import HardwareInterfaceError, SafetyViolationError
from ccid.hal.base import ChargingGateToken, ContactorInterface, ContactorName, ContactorSnapshot


class GpioRealError(HardwareInterfaceError):
    pass


@dataclass(frozen=True)
class RealContactorEvent:
    operation: str
    contactor: ContactorName
    commanded_closed: bool
    monotonic_s: float
    success: bool
    detail: str = ""


class GpioRealContactorController(ContactorInterface):
    """Real GPIO contactor controller using gpiozero/lgpio.

    Interlock behavior intentionally mirrors `GpioSimContactorController`.
    """

    def __init__(
        self,
        *,
        gpio_k1: int,
        gpio_k2: int,
        gpio_k3: int,
        monotonic_now: Callable[[], float] | None = None,
        output_factory=None,
        active_high: bool = True,
        initial_value: bool = False,
    ) -> None:
        if len({gpio_k1, gpio_k2, gpio_k3}) != 3:
            raise ValueError("GPIO numbers must be unique for K1/K2/K3")
        self._now = monotonic_now or time.monotonic
        self._events: list[RealContactorEvent] = []
        self._used_gate_cycles: set[int] = set()
        self._mismatch_started_s: float | None = None
        self._commanded = {
            ContactorName.K1: False,
            ContactorName.K2: False,
            ContactorName.K3: False,
        }

        if output_factory is None:
            try:
                from gpiozero import DigitalOutputDevice  # type: ignore
            except Exception as exc:  # pragma: no cover - dependency/environment
                raise GpioRealError(
                    "gpiozero is required for gpio_real; install requirements on target Pi"
                ) from exc
            output_factory = DigitalOutputDevice

        try:
            self._k1 = output_factory(gpio_k1, active_high=active_high, initial_value=initial_value)
            self._k2 = output_factory(gpio_k2, active_high=active_high, initial_value=initial_value)
            self._k3 = output_factory(gpio_k3, active_high=active_high, initial_value=initial_value)
        except Exception as exc:
            raise GpioRealError("Failed to initialize GPIO outputs") from exc

        # Safety default: all outputs inactive on startup.
        self.safe_open_all()

    def close_k1(self) -> None:
        self._apply("close_k1", ContactorName.K1, True)

    def close_k2(self) -> None:
        self._apply("close_k2", ContactorName.K2, True)

    def close_k3(self, gate: ChargingGateToken) -> None:
        if gate.cycle_index in self._used_gate_cycles:
            raise SafetyViolationError(
                f"Charging gate token for cycle {gate.cycle_index} has already been used"
            )
        if not self._commanded[ContactorName.K1] or not self._commanded[ContactorName.K2]:
            raise SafetyViolationError("K3 may close only when both K1 and K2 are commanded closed")
        self._apply("close_k3", ContactorName.K3, True)
        self._used_gate_cycles.add(gate.cycle_index)

    def open_k1(self) -> None:
        if self._commanded[ContactorName.K3]:
            raise SafetyViolationError("K1 may not open while K3 is commanded closed")
        self._apply("open_k1", ContactorName.K1, False)

    def open_k2(self) -> None:
        if self._commanded[ContactorName.K3]:
            raise SafetyViolationError("K2 may not open while K3 is commanded closed")
        self._apply("open_k2", ContactorName.K2, False)

    def open_k3(self) -> None:
        self._apply("open_k3", ContactorName.K3, False)

    def safe_open_all(self) -> None:
        failures: list[Exception] = []
        for name, action in (("open_k3", self._open_raw_k3), ("open_k2", self._open_raw_k2), ("open_k1", self._open_raw_k1)):
            try:
                action()
            except Exception as exc:  # pragma: no cover - hardware path
                failures.append(exc)
                self._events.append(
                    RealContactorEvent(
                        operation=name,
                        contactor=ContactorName[name.split("_")[1].upper()],
                        commanded_closed=False,
                        monotonic_s=self._now(),
                        success=False,
                        detail=str(exc),
                    )
                )
        if failures:
            raise GpioRealError("safe_open_all encountered hardware errors")

    def snapshot(self) -> ContactorSnapshot:
        return ContactorSnapshot(commanded_closed=dict(self._commanded), captured_at_monotonic_s=self._now())

    def detect_mains_command_mismatch(self, allowed_stagger_ms: int, now_monotonic_s: float) -> bool:
        if allowed_stagger_ms < 0:
            raise ValueError("allowed_stagger_ms must be >= 0")
        mismatch = self._commanded[ContactorName.K1] != self._commanded[ContactorName.K2]
        if not mismatch:
            self._mismatch_started_s = None
            return False
        if self._mismatch_started_s is None:
            self._mismatch_started_s = now_monotonic_s
            return allowed_stagger_ms == 0
        elapsed_ms = (now_monotonic_s - self._mismatch_started_s) * 1000.0
        return elapsed_ms > float(allowed_stagger_ms)

    def events(self) -> tuple[RealContactorEvent, ...]:
        return tuple(self._events)

    def close(self) -> None:
        for device in (self._k1, self._k2, self._k3):
            try:
                device.close()
            except Exception:
                pass

    def _open_raw_k1(self) -> None:
        self._k1.off()
        self._commanded[ContactorName.K1] = False

    def _open_raw_k2(self) -> None:
        self._k2.off()
        self._commanded[ContactorName.K2] = False

    def _open_raw_k3(self) -> None:
        self._k3.off()
        self._commanded[ContactorName.K3] = False

    def _device_for(self, contactor: ContactorName):
        if contactor is ContactorName.K1:
            return self._k1
        if contactor is ContactorName.K2:
            return self._k2
        return self._k3

    def _apply(self, operation: str, contactor: ContactorName, commanded_closed: bool) -> None:
        device = self._device_for(contactor)
        try:
            if commanded_closed:
                device.on()
            else:
                device.off()
            self._commanded[contactor] = commanded_closed
            self._events.append(
                RealContactorEvent(
                    operation=operation,
                    contactor=contactor,
                    commanded_closed=commanded_closed,
                    monotonic_s=self._now(),
                    success=True,
                )
            )
        except Exception as exc:
            self._events.append(
                RealContactorEvent(
                    operation=operation,
                    contactor=contactor,
                    commanded_closed=commanded_closed,
                    monotonic_s=self._now(),
                    success=False,
                    detail=str(exc),
                )
            )
            raise GpioRealError(f"GPIO operation failed: {operation}") from exc
