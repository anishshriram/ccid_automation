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
from ccid.hal.camera_real import CameraReal, CameraRealConfig, CameraRealError
from ccid.hal.camera_sim import CameraSim, CameraSimError, CameraSimFrameFixture
from ccid.hal.gpio_real import GpioRealContactorController, GpioRealError, RealContactorEvent
from ccid.hal.gpio_sim import DeterministicCommandError, GpioSimContactorController, SimContactorEvent
from ccid.hal.scope_real import ScopeReal, ScopeRealError
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
    "GpioRealContactorController",
    "GpioRealError",
    "GpioSimContactorController",
    "RealContactorEvent",
    "SimContactorEvent",
    "CameraReal",
    "CameraRealConfig",
    "CameraRealError",
    "CameraSim",
    "CameraSimError",
    "CameraSimFrameFixture",
    "ScopeReal",
    "ScopeRealError",
    "ScopeSim",
    "ScopeSimCommunicationError",
    "ScopeSimScenario",
]
