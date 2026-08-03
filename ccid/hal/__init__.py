"""HAL interface exports."""

from ccid.hal.base import (
    CameraHealth,
    CameraInterface,
    ChargingGateToken,
    ContactorInterface,
    ContactorName,
    ContactorSnapshot,
    NotificationInterface,
    ScopeInterface,
    ScopeSettings,
    ScopeStatus,
    WaveformCapture,
)
from ccid.hal.gpio_sim import DeterministicCommandError, GpioSimContactorController, SimContactorEvent

__all__ = [
    "CameraHealth",
    "CameraInterface",
    "ChargingGateToken",
    "ContactorInterface",
    "ContactorName",
    "ContactorSnapshot",
    "NotificationInterface",
    "ScopeInterface",
    "ScopeSettings",
    "ScopeStatus",
    "WaveformCapture",
    "DeterministicCommandError",
    "GpioSimContactorController",
    "SimContactorEvent",
]
