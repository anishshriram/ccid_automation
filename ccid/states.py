from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class CycleState(str, Enum):
    SAFE_OFF = "SAFE_OFF"
    MAINS_CLOSING = "MAINS_CLOSING"
    WAITING_FOR_CHARGING = "WAITING_FOR_CHARGING"
    SCOPE_CONFIGURING = "SCOPE_CONFIGURING"
    SCOPE_ARMING = "SCOPE_ARMING"
    SCOPE_ARMED = "SCOPE_ARMED"
    INJECTING = "INJECTING"
    ACQUIRING = "ACQUIRING"
    INJECTION_OPENING = "INJECTION_OPENING"
    TRANSFERRING = "TRANSFERRING"
    COMMITTING = "COMMITTING"
    MAINS_OPENING = "MAINS_OPENING"
    COOLDOWN = "COOLDOWN"
    RETRY_COOLDOWN = "RETRY_COOLDOWN"
    DEGRADED_FIXED_WAIT = "DEGRADED_FIXED_WAIT"
    HALTED = "HALTED"
    COMPLETE = "COMPLETE"


class Terminal(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NO_TRIP = "NO_TRIP"
    RIG_FAULT = "RIG_FAULT"
    HALTED = "HALTED"
    COMPLETE = "COMPLETE"


class LedState(str, Enum):
    BOOTING = "BOOTING"
    READY = "READY"
    CHARGING = "CHARGING"
    FAULTED = "FAULTED"
    OFF_OR_UNKNOWN = "OFF_OR_UNKNOWN"
    CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"


@dataclass(frozen=True)
class CycleDecision:
    terminal: Terminal
    notes: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

