from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping

from ccid.states import LedState


class ContactorName(str, Enum):
    K1 = "K1"
    K2 = "K2"
    K3 = "K3"


class ScopeStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    CONFIGURED = "CONFIGURED"
    ARMING = "ARMING"
    ARMED = "ARMED"
    ACQUIRING = "ACQUIRING"
    COMPLETE = "COMPLETE"


class CameraHealth(str, Enum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ContactorSnapshot:
    commanded_closed: Mapping[ContactorName, bool]
    captured_at_monotonic_s: float


@dataclass(frozen=True)
class ChargingGateToken:
    cycle_index: int
    granted_at_monotonic_s: float


@dataclass(frozen=True)
class ScopeSettings:
    timebase_scale_s_per_div: float = 0.05
    timebase_reference: str = "CENTER"
    channel1_scale_v_per_div: float = 50.0
    channel1_offset_v: float = 0.0
    channel1_coupling: str = "AC"
    channel1_probe_ratio: int = 10
    trigger_sweep: str = "NORMal"
    trigger_source: str = "CHANnel1"
    trigger_level_v: float = 20.0
    trigger_slope: str = "POSitive"
    acquire_type: str = "NORMal"
    waveform_source: str = "CHANnel1"
    waveform_format: str = "BYTE"
    waveform_points_mode: str = "RAW"
    waveform_points: str = "MAXimum"


@dataclass(frozen=True)
class WaveformCapture:
    samples: bytes
    preamble: Mapping[str, float | int | str]
    settings_readback: Mapping[str, str]
    scope_png: bytes
    captured_at_utc: datetime


@dataclass(frozen=True)
class ScopeTimeoutDiagnostics:
    """Best-effort, read-only snapshot of scope state captured on a
    scope-never-triggered-or-acquire-timeout halt. Never represents a
    completed acquisition; a failed individual query is recorded inline
    (e.g. a "<query failed: ...>" string in `settings`) rather than
    aborting the whole capture."""

    captured_at_utc: datetime
    captured_at_monotonic_s: float
    operation_condition: int
    hal_status: str
    settings: Mapping[str, object]
    error_queue: tuple[str, ...]
    scope_png: bytes


@dataclass(frozen=True)
class CameraFrame:
    frame_bgr: bytes
    width: int
    height: int
    captured_at_utc: datetime
    captured_at_monotonic_s: float
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CameraStateSample:
    led_state: LedState
    observed_at_monotonic_s: float
    health: CameraHealth
    frame: CameraFrame | None = None


class ContactorInterface(ABC):
    """Domain-level contactor control contract.

    Preconditions:
    - Implementations start with all outputs inactive.
    - `close_k3` requires a valid charging gate token for the current cycle.
    - `open_k1`/`open_k2` must reject commands while K3 is commanded closed.

    Postconditions:
    - `safe_open_all` leaves all contactors commanded open, applying K3-first order.
    - `snapshot` returns commanded state only; no physical-readback claim.

    Exceptions:
    - Safety violations raise a safety-domain exception in implementation.
    - I/O failures raise a HAL hardware exception in implementation.

    Timeout behavior:
    - Methods are synchronous command operations. Any retry or timeout policy is owned by caller.

    I/O:
    - All command methods may perform hardware I/O.

    Retry safety:
    - Opening methods should be idempotent and safe to retry.
    - Closing methods are not assumed idempotent on failed transport and should be retried cautiously.
    """

    @abstractmethod
    def close_k1(self) -> None:
        pass

    @abstractmethod
    def close_k2(self) -> None:
        pass

    @abstractmethod
    def close_k3(self, gate: ChargingGateToken) -> None:
        pass

    @abstractmethod
    def open_k1(self) -> None:
        pass

    @abstractmethod
    def open_k2(self) -> None:
        pass

    @abstractmethod
    def open_k3(self) -> None:
        pass

    @abstractmethod
    def safe_open_all(self) -> None:
        pass

    @abstractmethod
    def snapshot(self) -> ContactorSnapshot:
        pass

    @abstractmethod
    def detect_mains_command_mismatch(self, allowed_stagger_ms: int, now_monotonic_s: float) -> bool:
        pass


class ScopeInterface(ABC):
    """Oscilloscope control and data-acquisition contract.

    Preconditions:
    - `connect` must complete before configuration/arming/capture calls.
    - `configure_for_cycle` receives a full explicit setting set each cycle.

    Postconditions:
    - `arm_single` requests `:SINGle` behavior.
    - `wait_until_armed` returns true only when scope run/armed state is confirmed.
    - `capture_after_acquire` returns waveform bytes, preamble, settings readback, and PNG.

    Exceptions:
    - Communication/protocol failures raise HAL errors in implementation.

    Timeout behavior:
    - `wait_until_armed` and `wait_until_acquisition_complete` use monotonic deadlines.
    - On timeout they return False; they do not sleep-based synchronize.

    I/O:
    - All methods except `status` perform I/O.

    Retry safety:
    - `disconnect` is safe to retry.
    - `connect`/`configure_for_cycle` may be retried by caller using bounded policy.
    """

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def identify(self) -> str:
        pass

    @abstractmethod
    def configure_for_cycle(self, settings: ScopeSettings) -> None:
        pass

    @abstractmethod
    def readback_settings(self) -> Mapping[str, str]:
        pass

    @abstractmethod
    def arm_single(self) -> None:
        pass

    @abstractmethod
    def wait_until_armed(self, timeout_s: float, now_monotonic_s: float) -> bool:
        pass

    @abstractmethod
    def wait_until_acquisition_complete(self, timeout_s: float, now_monotonic_s: float) -> bool:
        pass

    @abstractmethod
    def capture_after_acquire(self) -> WaveformCapture:
        pass

    @abstractmethod
    def capture_timeout_diagnostics(self) -> ScopeTimeoutDiagnostics:
        """Read-only best-effort snapshot for a scope-timeout halt.

        Must never arm, trigger, run, or reconfigure the scope, and must
        never be treated as a completed acquisition. Only called after the
        caller has already confirmed K3 is commanded open."""
        pass

    @abstractmethod
    def status(self) -> ScopeStatus:
        pass


class CameraInterface(ABC):
    """Camera and charging-gate observation contract.

    Preconditions:
    - Camera capture loop initialized before state sampling.

    Postconditions:
    - `sample_state` returns timestamped state and health.
    - `await_charging_gate` is scoped to one cycle and returns when charging is authorized.

    Exceptions:
    - Initialization/runtime failures raise HAL errors in implementation.

    Timeout behavior:
    - Polling is bounded by caller-supplied monotonic timeout parameters.
    - On timeout, implementation returns latest observed state sample.

    I/O:
    - All methods except pure accessors may perform I/O.

    Retry safety:
    - `stop` should be idempotent and safe to retry.
    """

    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def sample_state(self, now_monotonic_s: float) -> CameraStateSample:
        pass

    @abstractmethod
    def await_charging_gate(
        self,
        cycle_index: int,
        timeout_s: float,
        now_monotonic_s: float,
    ) -> tuple[ChargingGateToken | None, CameraStateSample]:
        pass

    @abstractmethod
    def latest_frame(self) -> CameraFrame | None:
        pass


class NotificationInterface(ABC):
    """Outbound notification and heartbeat contract.

    Preconditions:
    - External endpoint configuration is already validated.

    Postconditions:
    - Start/resume/fault/complete events are sent best-effort by implementation policy.

    Exceptions:
    - Implementations may raise explicit outbound transport exceptions.

    Timeout behavior:
    - Methods should be bounded; caller may isolate failures from safety path.

    I/O:
    - All methods perform outbound I/O.

    Retry safety:
    - Heartbeat and notifications should be safe to retry with duplicate-tolerant payloads.
    """

    @abstractmethod
    def notify_start(self, run_id: str, cycle_start: int, cycle_target: int) -> None:
        pass

    @abstractmethod
    def notify_resume(self, run_id: str, last_completed_cycle: int, cycle_target: int) -> None:
        pass

    @abstractmethod
    def notify_fault(self, run_id: str, cycle_index: int, reason: str) -> None:
        pass

    @abstractmethod
    def notify_complete(self, run_id: str, cycle_target: int) -> None:
        pass

    @abstractmethod
    def heartbeat(self, run_id: str, last_completed_cycle: int) -> None:
        pass

    @abstractmethod
    def heartbeat_fail(self, run_id: str, last_completed_cycle: int, reason: str) -> None:
        pass

