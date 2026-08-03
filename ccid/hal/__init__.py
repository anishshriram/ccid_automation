"""HAL interface exports."""

from ccid.hal.base import (
    CameraFrame,
    CameraHealth,
    CameraInterface,
    CameraStateSample,
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
from ccid.hal.camera_sim import CameraSim, CameraSimError, CameraSimFrameFixture
from ccid.hal.gpio_sim import DeterministicCommandError, GpioSimContactorController, SimContactorEvent
from ccid.hal.scope_sim import ScopeSim, ScopeSimCommunicationError, ScopeSimScenario

__all__ = [
    "CameraFrame",
    "CameraHealth",
    "CameraInterface",
    "CameraStateSample",
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
    "CameraSim",
    "CameraSimError",
    "CameraSimFrameFixture",
    "ScopeSim",
    "ScopeSimCommunicationError",
    "ScopeSimScenario",
]
