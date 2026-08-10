from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Callable, Mapping

from ccid.errors import HardwareInterfaceError
from ccid.hal.base import (
    ScopeInterface,
    ScopeSettings,
    ScopeStatus,
    ScopeTimeoutDiagnostics,
    WaveformCapture,
)

# Read-only settings queried for a timeout-diagnostics snapshot. Each maps a
# result key to the exact `?`-suffixed SCPI query - none of these mutate
# scope state, unlike the write commands in `configure_for_cycle`.
#
# Deliberately excludes `:WAVeform:POINts?`: on a real dry run it took over
# a second to respond while every other query here answered immediately
# (see SCOPE_TRIGGER_DEBUG_LOG.md Entry 6). It isn't essential -
# `waveform_points_mode`/`waveform_format`/`waveform_source` already say
# how the waveform subsystem is configured - so it isn't worth the risk of
# being the one query that stalls or further destabilizes the transport.
_DIAGNOSTIC_SETTINGS_QUERIES: tuple[tuple[str, str], ...] = (
    ("ch1_display", ":CHANnel1:DISPlay?"),
    ("ch1_coupling", ":CHANnel1:COUPling?"),
    ("ch1_scale", ":CHANnel1:SCALe?"),
    ("ch1_offset", ":CHANnel1:OFFSet?"),
    ("ch1_probe_ratio", ":CHANnel1:PROBe?"),
    ("ch1_bandwidth_limit", ":CHANnel1:BWLimit?"),
    ("ch1_invert", ":CHANnel1:INVert?"),
    ("trigger_mode", ":TRIGger:MODE?"),
    ("trigger_sweep", ":TRIGger:SWEep?"),
    ("trigger_edge_source", ":TRIGger:EDGE:SOURce?"),
    ("trigger_edge_slope", ":TRIGger:EDGE:SLOPe?"),
    ("trigger_edge_level", ":TRIGger:EDGE:LEVel?"),
    ("timebase_scale", ":TIMebase:SCALe?"),
    ("timebase_reference", ":TIMebase:REFerence?"),
    ("acquire_type", ":ACQuire:TYPE?"),
    ("waveform_source", ":WAVeform:SOURce?"),
    ("waveform_format", ":WAVeform:FORMat?"),
    ("waveform_points_mode", ":WAVeform:POINts:MODE?"),
)

_DIAGNOSTIC_ERROR_QUEUE_MAX_READS = 20

# PyVISA-Py raises this for `.clear()` on backends/resource types that don't
# implement a device clear at all (confirmed on a real de-energized dry
# run - see SCOPE_TRIGGER_DEBUG_LOG.md Entry 5). That is not evidence the
# connection is unhealthy, unlike every other clear failure, so it is the
# one case where diagnostics proceeds to the bounded, fail-fast queries
# instead of aborting.
_VI_ERROR_NSUP_OPER_MARKER = "VI_ERROR_NSUP_OPER"


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
        diagnostics_query_timeout_s: float = 1.0,
        diagnostics_total_budget_s: float = 5.0,
    ) -> None:
        self._resource_name = resource
        self._backend = backend
        self._now = monotonic_now or time.monotonic
        self._resource_manager_factory = resource_manager_factory
        self._reconnect_attempts = reconnect_attempts
        self._diagnostics_query_timeout_s = diagnostics_query_timeout_s
        self._diagnostics_total_budget_s = diagnostics_total_budget_s
        self._rm = None
        self._inst = None
        self._status = ScopeStatus.DISCONNECTED
        self._settings = ScopeSettings()
        # Set once a diagnostics query fails and can't be trusted to have
        # left the transport in a known-good state (see
        # SCOPE_TRIGGER_DEBUG_LOG.md Entry 6 - a background-thread-based
        # timeout previously left an abandoned libusb call racing with the
        # main thread's next operation, causing a segfault). Once set, no
        # further operation is attempted against this connection for the
        # rest of the process, including `disconnect()`'s own `.close()`.
        self._connection_unusable = False

    def connect(self) -> None:
        if self._connection_unusable:
            raise ScopeRealError(
                "Scope connection was marked unusable after an unrecoverable timeout; "
                "refusing to reconnect for the remainder of this process"
            )
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
        if self._connection_unusable:
            # Do not issue another operation against a connection whose
            # transport state is unknown after an unrecoverable diagnostics
            # timeout - not even `.close()`. Drop the references and let
            # process exit reclaim the file descriptor.
            self._inst = None
            self._rm = None
            self._status = ScopeStatus.DISCONNECTED
            return
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
            # :CHANnel:PROBe must be set before :CHANnel:SCALe/:OFFSet - those
            # are interpreted "at the probe tip" using whatever probe ratio is
            # already configured, so setting them first applies against a
            # stale ratio and only gets silently reinterpreted once :PROBe
            # lands - readback looks correct while the actual digitized
            # range is off by the probe factor.
            f":CHANnel1:PROBe {settings.channel1_probe_ratio}",
            f":CHANnel1:SCALe {settings.channel1_scale_v_per_div}",
            f":CHANnel1:OFFSet {settings.channel1_offset_v}",
            f":CHANnel1:COUPling {settings.channel1_coupling}",
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

    def capture_timeout_diagnostics(self) -> ScopeTimeoutDiagnostics:
        # Deliberately bypasses `_query`/`_query_binary`/`_retry_io`: a scope
        # that just failed to report acquisition-complete is plausibly
        # wedged, and `_retry_io`'s reconnect-on-failure could add many
        # seconds of blocking reconnect attempts across ~18 queries.
        #
        # Every call here is synchronous, in this thread, bounded by
        # PyVISA's native per-resource `.timeout` (set once, up front) -
        # NOT by a supervising thread. A prior version used a daemon thread
        # to enforce a wall-clock bound on top of an otherwise-unbounded
        # call; on real hardware that abandoned thread stayed blocked
        # inside libusb after the caller gave up waiting, and a subsequent
        # operation from the main thread raced with it and segfaulted the
        # process (SCOPE_TRIGGER_DEBUG_LOG.md Entry 6). The native VISA
        # timeout is safe because it bounds the actual transport call
        # itself (down at the libusb/kernel level), not just how long we
        # wait for it from Python.
        #
        # Fail-fast, not best-effort-per-field: SCOPE_TRIGGER_DEBUG_LOG.md
        # Entry 3 shows that continuing to send further queries after one
        # has already failed can cascade a single slow/failed query into a
        # fully wedged USBTMC session (a stale unread response left in the
        # instrument's output buffer desyncs every subsequent write/read
        # pair) - recoverable, in that incident, only by physically
        # power-cycling the scope. So the first failure here aborts the
        # whole capture immediately and marks the connection unusable for
        # the rest of the process (see `__init__`) rather than pushing more
        # queries, or a later `disconnect()`, into a connection that may
        # already be unhealthy - except a device clear that fails with
        # VI_ERROR_NSUP_OPER, which just means this backend doesn't
        # implement clear at all (confirmed on real hardware, see Entry 5)
        # and is not evidence of an unhealthy connection.
        if self._inst is None or self._connection_unusable:
            return ScopeTimeoutDiagnostics(
                captured_at_utc=datetime.now(tz=timezone.utc),
                captured_at_monotonic_s=self._now(),
                operation_condition=-1,
                hal_status=self._status.value,
                settings={
                    "connection": "<not connected>" if self._inst is None else "<unusable after prior timeout>"
                },
                error_queue=(),
                scope_png=b"",
            )

        settings: dict[str, object] = {}
        operation_condition = -1
        error_queue: tuple[str, ...] = ()
        scope_png = b""
        deadline = self._now() + self._diagnostics_total_budget_s

        try:
            self._inst.timeout = self._diagnostics_query_timeout_s * 1000.0
        except Exception as exc:
            settings["diagnostics_aborted"] = f"could not set diagnostics timeout, aborting: {exc}"
            self._connection_unusable = True
            return ScopeTimeoutDiagnostics(
                captured_at_utc=datetime.now(tz=timezone.utc),
                captured_at_monotonic_s=self._now(),
                operation_condition=-1,
                hal_status=self._status.value,
                settings=settings,
                error_queue=(),
                scope_png=b"",
            )

        try:
            self._inst.clear()
        except Exception as exc:
            error = str(exc)
            if _VI_ERROR_NSUP_OPER_MARKER in error:
                settings["device_clear"] = f"unsupported by this VISA backend, skipped: {error}"
            else:
                settings["diagnostics_aborted"] = f"device clear failed, aborting: {error}"
                self._connection_unusable = True
                return ScopeTimeoutDiagnostics(
                    captured_at_utc=datetime.now(tz=timezone.utc),
                    captured_at_monotonic_s=self._now(),
                    operation_condition=-1,
                    hal_status=self._status.value,
                    settings=settings,
                    error_queue=(),
                    scope_png=b"",
                )

        aborted = False
        for key, command in _DIAGNOSTIC_SETTINGS_QUERIES:
            if self._now() >= deadline:
                settings["diagnostics_aborted"] = "total time budget exceeded"
                aborted = True
                break
            value, error = self._diag_query(command)
            if error is not None:
                settings[key] = f"<query failed: {error}>"
                settings["diagnostics_aborted"] = f"aborted after failure on {command}: {error}"
                self._connection_unusable = True
                aborted = True
                break
            settings[key] = value

        if not aborted and self._now() < deadline:
            value, error = self._diag_query(":OPERegister:CONDition?")
            if error is not None:
                settings["operation_condition_error"] = error
                settings["diagnostics_aborted"] = "aborted after failure on :OPERegister:CONDition?"
                self._connection_unusable = True
                aborted = True
            else:
                try:
                    operation_condition = int(float(value))
                except ValueError:
                    settings["operation_condition_parse_error"] = value

        if not aborted and self._now() < deadline:
            error_queue, drain_aborted = self._drain_error_queue_bounded(deadline)
            if drain_aborted:
                aborted = True

        if not aborted and self._now() < deadline:
            try:
                raw_png = self._inst.query_binary_values(":DISPlay:DATA? PNG", datatype="B", container=bytes)
                scope_png = bytes(raw_png)
            except Exception as exc:
                settings["scope_png_capture_error"] = f"<query failed: {exc}>"
                self._connection_unusable = True

        return ScopeTimeoutDiagnostics(
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=self._now(),
            operation_condition=operation_condition,
            hal_status=self._status.value,
            settings=settings,
            error_queue=error_queue,
            scope_png=scope_png,
        )

    def _diag_query(self, command: str) -> tuple[str | None, str | None]:
        """One synchronous query bounded by the native VISA timeout already
        set on `self._inst`. Returns (value, None) or (None, error) - never
        raises, and never spawns a thread."""
        try:
            return str(self._inst.query(command)).strip(), None
        except Exception as exc:
            return None, str(exc)

    def _drain_error_queue_bounded(self, deadline: float) -> tuple[tuple[str, ...], bool]:
        errors: list[str] = []
        for _ in range(_DIAGNOSTIC_ERROR_QUEUE_MAX_READS):
            if self._now() >= deadline:
                return tuple(errors), True
            value, error = self._diag_query(":SYSTem:ERRor?")
            if error is not None:
                self._connection_unusable = True
                return tuple(errors), True
            if value.startswith("+0,"):
                break
            errors.append(value)
        return tuple(errors), False

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
        if self._connection_unusable:
            raise ScopeRealError("Scope connection is unusable after an unrecoverable timeout")
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
