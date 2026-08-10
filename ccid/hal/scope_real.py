from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Callable, Mapping

from ccid.errors import HardwareInterfaceError
from ccid.hal.base import ScopeInterface, ScopeSettings, ScopeStatus, WaveformCapture


class ScopeRealError(HardwareInterfaceError):
    pass


def _as_float(text: str) -> float:
    return float(text.strip())


class ScopeReal(ScopeInterface):
    """Keysight/USBTMC real scope implementation via PyVISA."""

    def __init__(
        self,
        *,
        resource: str,
        backend: str = "@py",
        monotonic_now: Callable[[], float] | None = None,
        resource_manager_factory=None,
        reconnect_attempts: int = 3,
    ) -> None:
        self._resource_name = resource
        self._backend = backend
        self._now = monotonic_now or time.monotonic
        self._resource_manager_factory = resource_manager_factory
        self._reconnect_attempts = reconnect_attempts
        self._rm = None
        self._inst = None
        self._status = ScopeStatus.DISCONNECTED
        self._settings = ScopeSettings()

    def connect(self) -> None:
        if self._inst is not None:
            return
        try:
            rm_factory = self._resource_manager_factory
            if rm_factory is None:
                import pyvisa  # type: ignore

                rm_factory = pyvisa.ResourceManager
            self._rm = rm_factory(self._backend)
            self._inst = self._rm.open_resource(self._resource_name)
            self._status = ScopeStatus.CONNECTED
        except Exception as exc:
            self._status = ScopeStatus.DISCONNECTED
            raise ScopeRealError(f"Could not connect to scope resource {self._resource_name}") from exc

    def disconnect(self) -> None:
        try:
            if self._inst is not None:
                self._inst.close()
        finally:
            self._inst = None
            if self._rm is not None:
                try:
                    self._rm.close()
                except Exception:
                    pass
                self._rm = None
            self._status = ScopeStatus.DISCONNECTED

    def identify(self) -> str:
        return self._query("*IDN?")

    def configure_for_cycle(self, settings: ScopeSettings) -> None:
        self._require_connected()
        self._settings = settings
        self._write(":STOP")
        if not self._wait_until_stopped(timeout_s=1.0):
            raise ScopeRealError(
                "Scope did not reach stopped state before cycle configuration"
            )

        commands = [
            f":TIMebase:SCALe {settings.timebase_scale_s_per_div}",
            f":TIMebase:REFerence {settings.timebase_reference}",
            f":CHANnel1:SCALe {settings.channel1_scale_v_per_div}",
            f":CHANnel1:OFFSet {settings.channel1_offset_v}",
            f":CHANnel1:COUPling {settings.channel1_coupling}",
            f":CHANnel1:PROBe {settings.channel1_probe_ratio}",
            f":TRIGger:SWEep {settings.trigger_sweep}",
            # :TRIGger:EDGE:* parameters are inert unless :TRIGger:MODE is
            # explicitly EDGE - the scope keeps triggering on whatever mode
            # (Pattern, Glitch, etc.) it was last left on via the front panel.
            ":TRIGger:MODE EDGE",
            f":TRIGger:EDGE:SOURce {settings.trigger_source}",
            f":TRIGger:EDGE:LEVel {settings.trigger_level_v}",
            f":TRIGger:EDGE:SLOPe {settings.trigger_slope}",
            f":ACQuire:TYPE {settings.acquire_type}",
            f":WAVeform:SOURce {settings.waveform_source}",
            f":WAVeform:FORMat {settings.waveform_format}",
            f":WAVeform:POINts {settings.waveform_points}",
            f":WAVeform:POINts:MODE {settings.waveform_points_mode}",
        ]
        for cmd in commands:
            self._write(cmd)
        self._status = ScopeStatus.CONFIGURED

    def readback_settings(self) -> Mapping[str, str]:
        self._require_connected()
        return {
            "timebase_scale": self._query(":TIMebase:SCALe?"),
            "timebase_reference": self._query(":TIMebase:REFerence?"),
            "trigger_level_v": self._query(":TRIGger:EDGE:LEVel?"),
            "waveform_points_mode": self._query(":WAVeform:POINts:MODE?"),
            "waveform_points": self._query(":WAVeform:POINts?"),
            "waveform_format": self._query(":WAVeform:FORMat?"),
            "waveform_source": self._query(":WAVeform:SOURce?"),
        }

    def arm_single(self) -> None:
        self._write(":SINGle")
        self._status = ScopeStatus.ARMING

    def wait_until_armed(self, timeout_s: float, now_monotonic_s: float) -> bool:
        self._require_connected()
        if timeout_s <= 0.0:
            raise ValueError("timeout_s must be > 0")
        deadline = now_monotonic_s + timeout_s
        while self._now() <= deadline:
            run_bit = self._run_bit_set()
            if run_bit:
                self._status = ScopeStatus.ARMED
                return True
            time.sleep(0.01)
        return False

    def wait_until_acquisition_complete(self, timeout_s: float, now_monotonic_s: float) -> bool:
        self._require_connected()
        if timeout_s <= 0.0:
            raise ValueError("timeout_s must be > 0")
        deadline = now_monotonic_s + timeout_s
        self._status = ScopeStatus.ACQUIRING
        while self._now() <= deadline:
            if not self._run_bit_set():
                self._status = ScopeStatus.COMPLETE
                return True
            time.sleep(0.01)
        return False

    def capture_after_acquire(self) -> WaveformCapture:
        self._require_connected()
        samples = self._query_binary(":WAVeform:DATA?")
        preamble_text = self._query(":WAVeform:PREamble?")
        preamble = _parse_keysight_preamble(preamble_text)
        png = self._query_binary(":DISPlay:DATA? PNG")
        return WaveformCapture(
            samples=samples,
            preamble=preamble,
            settings_readback=dict(self.readback_settings()),
            scope_png=png,
            captured_at_utc=datetime.now(tz=timezone.utc),
        )

    def status(self) -> ScopeStatus:
        return self._status

    def _wait_until_stopped(self, timeout_s: float) -> bool:
        """Wait until the operation-register run bit is definitely clear."""

        if timeout_s <= 0.0:
            raise ValueError("timeout_s must be > 0")

        deadline = self._now() + timeout_s
        while self._now() <= deadline:
            if not self._run_bit_set():
                return True
            time.sleep(0.01)

        return False

    def _run_bit_set(self) -> bool:
        condition = int(float(self._query(":OPERegister:CONDition?")))
        return bool(condition & (1 << 3))

    def _require_connected(self) -> None:
        if self._inst is None:
            raise ScopeRealError("Scope is not connected")

    def _write(self, command: str) -> None:
        self._require_connected()
        self._retry_io(lambda: self._inst.write(command), f"write {command}")

    def _query(self, command: str) -> str:
        self._require_connected()
        response = self._retry_io(lambda: self._inst.query(command), f"query {command}")
        return str(response).strip()

    def _query_binary(self, command: str) -> bytes:
        self._require_connected()
        response = self._retry_io(
            lambda: self._inst.query_binary_values(command, datatype="B", container=bytes),
            f"binary query {command}",
        )
        return bytes(response)

    def _retry_io(self, action, label: str):
        attempts = max(1, self._reconnect_attempts)
        last_exc: Exception | None = None
        for _ in range(attempts):
            try:
                return action()
            except Exception as exc:
                last_exc = exc
                self._reconnect_once()
        raise ScopeRealError(f"Scope communication failed after {attempts} attempts: {label}") from last_exc

    def _reconnect_once(self) -> None:
        try:
            if self._inst is not None:
                self._inst.close()
        except Exception:
            pass
        self._inst = None
        if self._rm is None:
            return
        self._inst = self._rm.open_resource(self._resource_name)
        self._status = ScopeStatus.CONNECTED


def _parse_keysight_preamble(preamble: str) -> dict[str, float | int | str]:
    parts = [p.strip() for p in preamble.split(",")]
    if len(parts) < 10:
        raise ScopeRealError("Unexpected preamble format from scope")
    return {
        "format": int(float(parts[0])),
        "type": int(float(parts[1])),
        "points": int(float(parts[2])),
        "count": int(float(parts[3])),
        "x_increment": _as_float(parts[4]),
        "x_origin": _as_float(parts[5]),
        "x_reference": int(float(parts[6])),
        "y_increment": _as_float(parts[7]),
        "y_origin": _as_float(parts[8]),
        "y_reference": int(float(parts[9])),
    }
