from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Callable, Mapping

from ccid.errors import HardwareInterfaceError
from ccid.hal.base import (
    ScopeInterface,
    ScopeSettings,
    ScopeStatus,
    ScopeTimeoutDiagnostics,
    WaveformCapture,
)


class ScopeSimCommunicationError(HardwareInterfaceError):
    pass


@dataclass(frozen=True)
class ScopeSimScenario:
    sample_count: int = 20_000
    sample_rate_hz: float = 10_000_000.0
    line_frequency_hz: float = 60.0
    amplitude_v: float = 170.0
    phase_rad: float = 0.0
    pretrigger_s: float = 0.020
    trip_time_s: float | None = 0.020
    no_trip: bool = False
    never_triggered: bool = False
    pretrigger_leakage: bool = False
    arm_delay_s: float = 0.0
    acquisition_delay_s: float = 0.0
    transfer_truncated: bool = False
    invalid_preamble: bool = False
    missing_preamble_fields: tuple[str, ...] = field(default_factory=tuple)
    force_comm_errors: frozenset[str] = field(default_factory=frozenset)
    preamble_overrides: Mapping[str, float | int | str] = field(default_factory=dict)
    diagnostics_operation_condition: int = 0
    diagnostics_settings_overrides: Mapping[str, object] = field(default_factory=dict)
    diagnostics_error_queue: tuple[str, ...] = field(default_factory=tuple)
    diagnostics_scope_png: bytes = b"\x89PNG\r\n\x1a\nSCOPE_SIM"
    # Simulates a stale trigger-event flag already latched when
    # configure_for_cycle completes, before arm_single is ever called.
    trigger_event_latched_at_configure: bool = False
    # Simulates a spurious trigger event occurring between arm_single and
    # the deliberate K3 close (the pre-injection recheck window).
    trigger_event_latched_before_injection: bool = False


class ScopeSim(ScopeInterface):
    """Deterministic scope simulator for normal and fault branches."""

    def __init__(
        self,
        scenario: ScopeSimScenario | None = None,
        monotonic_now: Callable[[], float] | None = None,
    ) -> None:
        self._scenario = scenario or ScopeSimScenario()
        self._monotonic_now = monotonic_now or __import__("time").monotonic
        self._status = ScopeStatus.DISCONNECTED
        self._connected = False
        self._settings = ScopeSettings()
        self._arm_commanded_s: float | None = None
        self._armed_s: float | None = None
        self._acquire_started_s: float | None = None
        self._trigger_event_latched = False
        self._force_triggered = False

    def connect(self) -> None:
        self._maybe_raise("connect")
        self._connected = True
        self._status = ScopeStatus.CONNECTED

    def disconnect(self) -> None:
        self._maybe_raise("disconnect")
        self._connected = False
        self._status = ScopeStatus.DISCONNECTED

    def identify(self) -> str:
        self._require_connected()
        self._maybe_raise("identify")
        return "SCOPE_SIM,MSOX2014A,MY58100795,SIM"

    def configure_for_cycle(self, settings: ScopeSettings) -> None:
        self._require_connected()
        self._maybe_raise("configure")
        self._settings = settings
        self._status = ScopeStatus.CONFIGURED
        self._arm_commanded_s = None
        self._armed_s = None
        self._acquire_started_s = None
        self._trigger_event_latched = self._scenario.trigger_event_latched_at_configure
        self._force_triggered = False

    def readback_settings(self) -> dict[str, str]:
        self._require_connected()
        self._maybe_raise("readback")
        return {
            "timebase_scale": str(self._settings.timebase_scale_s_per_div),
            "trigger_level_v": str(self._settings.trigger_level_v),
            "waveform_points_mode": self._settings.waveform_points_mode,
            "waveform_points": self._settings.waveform_points,
            "waveform_format": self._settings.waveform_format,
            "waveform_source": self._settings.waveform_source,
        }

    def arm_single(self) -> None:
        self._require_connected()
        self._maybe_raise("arm")
        self._arm_commanded_s = self._monotonic_now()
        self._status = ScopeStatus.ARMING
        self._trigger_event_latched = self._scenario.trigger_event_latched_before_injection

    def wait_until_armed(self, timeout_s: float, now_monotonic_s: float) -> bool:
        self._require_connected()
        self._maybe_raise("wait_armed")
        if self._arm_commanded_s is None:
            raise ScopeSimCommunicationError("arm_single must be called before wait_until_armed")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        elapsed = now_monotonic_s - self._arm_commanded_s
        if elapsed < 0:
            return False
        if elapsed >= self._scenario.arm_delay_s:
            self._armed_s = now_monotonic_s
            self._acquire_started_s = now_monotonic_s
            self._status = ScopeStatus.ARMED
            return True
        return False

    def wait_until_acquisition_complete(self, timeout_s: float, now_monotonic_s: float) -> bool:
        self._require_connected()
        self._maybe_raise("wait_acquire")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if self._armed_s is None or self._acquire_started_s is None:
            return False
        if self._force_triggered:
            # Mirrors real-hardware behavior: :TRIGger:FORCe completes the
            # currently-armed acquisition immediately, indistinguishable
            # here from a genuine trigger - callers that force a trigger
            # must not call this method expecting to tell the difference.
            self._status = ScopeStatus.COMPLETE
            return True
        if self._scenario.never_triggered:
            self._status = ScopeStatus.ARMED
            return False

        elapsed = now_monotonic_s - self._acquire_started_s
        if elapsed < 0:
            return False
        if elapsed >= self._scenario.acquisition_delay_s:
            self._status = ScopeStatus.COMPLETE
            return True
        self._status = ScopeStatus.ACQUIRING
        return False

    def read_trigger_event_register(self) -> bool:
        self._require_connected()
        self._maybe_raise("trigger_event_register")
        latched = self._trigger_event_latched
        self._trigger_event_latched = False
        return latched

    def force_trigger(self) -> None:
        self._require_connected()
        self._maybe_raise("force_trigger")
        self._force_triggered = True

    def capture_after_acquire(self) -> WaveformCapture:
        self._require_connected()
        self._maybe_raise("capture")
        samples = self._build_samples()
        preamble = self._build_preamble(points=len(samples))
        if self._scenario.transfer_truncated:
            samples = samples[: max(1, len(samples) // 2)]
        return WaveformCapture(
            samples=samples,
            preamble=preamble,
            settings_readback=self.readback_settings(),
            scope_png=b"\x89PNG\r\n\x1a\nSCOPE_SIM",
            captured_at_utc=datetime.now(tz=timezone.utc),
        )

    def capture_timeout_diagnostics(self) -> ScopeTimeoutDiagnostics:
        self._require_connected()
        self._maybe_raise("timeout_diagnostics")
        settings: dict[str, object] = {
            "ch1_coupling": self._settings.channel1_coupling,
            "ch1_scale": self._settings.channel1_scale_v_per_div,
            "ch1_offset": self._settings.channel1_offset_v,
            "ch1_probe_ratio": self._settings.channel1_probe_ratio,
            "trigger_sweep": self._settings.trigger_sweep,
            "trigger_coupling": self._settings.trigger_coupling,
            "trigger_edge_source": self._settings.trigger_source,
            "trigger_edge_slope": self._settings.trigger_slope,
            "trigger_edge_level": self._settings.trigger_level_v,
            "timebase_scale": self._settings.timebase_scale_s_per_div,
            "timebase_reference": self._settings.timebase_reference,
            "acquire_type": self._settings.acquire_type,
            "waveform_source": self._settings.waveform_source,
            "waveform_format": self._settings.waveform_format,
            "waveform_points_mode": self._settings.waveform_points_mode,
            "waveform_points": self._settings.waveform_points,
        }
        settings.update(self._scenario.diagnostics_settings_overrides)
        return ScopeTimeoutDiagnostics(
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=self._monotonic_now(),
            operation_condition=self._scenario.diagnostics_operation_condition,
            hal_status=self._status.value,
            settings=settings,
            error_queue=self._scenario.diagnostics_error_queue,
            scope_png=self._scenario.diagnostics_scope_png,
        )

    def status(self) -> ScopeStatus:
        return self._status

    def _build_samples(self) -> bytes:
        points = self._scenario.sample_count
        if points <= 0:
            raise ValueError("sample_count must be > 0")
        sample_rate = self._scenario.sample_rate_hz
        if sample_rate <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        freq = self._scenario.line_frequency_hz
        if freq <= 0:
            raise ValueError("line_frequency_hz must be > 0")

        amplitude = self._scenario.amplitude_v
        phase = self._scenario.phase_rad
        pretrigger = self._scenario.pretrigger_s
        trip_time = self._scenario.trip_time_s
        bytes_out = bytearray(points)
        fullscale_v = 200.0
        for i in range(points):
            t = (i / sample_rate) - pretrigger
            present = False
            if t >= 0.0:
                if self._scenario.no_trip:
                    present = True
                elif trip_time is None:
                    present = False
                else:
                    present = t <= trip_time
            elif self._scenario.pretrigger_leakage:
                present = True

            voltage = amplitude * math.sin((2.0 * math.pi * freq * t) + phase) if present else 0.0
            normalized = (voltage / fullscale_v) * 127.0
            code = int(round(128.0 + normalized))
            if code < 0:
                code = 0
            elif code > 255:
                code = 255
            bytes_out[i] = code
        return bytes(bytes_out)

    def _build_preamble(self, points: int) -> dict[str, float | int | str]:
        preamble: dict[str, float | int | str] = {
            "format": "BYTE",
            "type": "NORMal",
            "points": points,
            "count": 1,
            "x_increment": 1.0 / self._scenario.sample_rate_hz,
            "x_origin": -self._scenario.pretrigger_s,
            "x_reference": 0,
            "y_increment": 1.0,
            "y_origin": -128.0,
            "y_reference": 0,
            "source": self._settings.waveform_source,
            "pretrigger_s": self._scenario.pretrigger_s,
            "trip_time_s": self._scenario.trip_time_s if self._scenario.trip_time_s is not None else -1.0,
            "no_trip": int(self._scenario.no_trip),
            "never_triggered": int(self._scenario.never_triggered),
        }
        for key, value in self._scenario.preamble_overrides.items():
            preamble[key] = value

        if self._scenario.invalid_preamble:
            preamble["x_increment"] = "INVALID"
        for key in self._scenario.missing_preamble_fields:
            preamble.pop(key, None)
        return preamble

    def _require_connected(self) -> None:
        if not self._connected:
            raise ScopeSimCommunicationError("ScopeSim is not connected")

    def _maybe_raise(self, operation: str) -> None:
        if operation in self._scenario.force_comm_errors:
            raise ScopeSimCommunicationError(f"Injected communication error at operation '{operation}'")

