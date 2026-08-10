from __future__ import annotations

from datetime import datetime, timezone
import threading
import unittest

from ccid.hal.base import ScopeSettings, ScopeStatus
from ccid.hal.scope_real import ScopeReal, _parse_keysight_preamble


class _FakeInstrument:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.queries: list[str] = []
        self.closed = False
        self.timeout = 2000  # ms - PyVISA-style native per-resource timeout
        self.run_bit_sequence = [8, 8, 0]
        self.fail_commands: set[str] = set()
        self.always_error_queue = False
        self._error_queue_calls = 0
        self.fail_png = False
        self.fail_clear = False
        self.clear_error_text = "simulated VISA timeout for clear"
        self.clear_calls = 0

    def write(self, command: str) -> None:
        self.commands.append(command)

    def clear(self) -> None:
        self.clear_calls += 1
        if self.fail_clear:
            raise TimeoutError(self.clear_error_text)

    def query(self, command: str) -> str:
        self.queries.append(command)
        if command in self.fail_commands:
            raise TimeoutError(f"simulated VISA timeout for {command}")
        if command == "*IDN?":
            return "FAKE_SCOPE,MODEL,123,1.0"
        if command == ":OPERegister:CONDition?":
            if self.run_bit_sequence:
                return str(self.run_bit_sequence.pop(0))
            return "0"
        if command == ":SYSTem:ERRor?":
            if self.always_error_queue:
                self._error_queue_calls += 1
                return f'-{self._error_queue_calls},"Simulated error {self._error_queue_calls}"'
            return '+0,"No error"'
        responses = {
            ":TIMebase:SCALe?": "0.02",
            ":TIMebase:REFerence?": "LEFT",
            ":TRIGger:EDGE:LEVel?": "20.0",
            ":TRIGger:MODE?": "EDGE",
            ":TRIGger:SWEep?": "NORMal",
            ":TRIGger:COUPling?": "DC",
            ":TRIGger:NREJect?": "0",
            ":TER?": "0",
            ":TRIGger:EDGE:SOURce?": "CHAN1",
            ":TRIGger:EDGE:SLOPe?": "POS",
            ":CHANnel1:DISPlay?": "1",
            ":CHANnel1:COUPling?": "AC",
            ":CHANnel1:SCALe?": "50.0",
            ":CHANnel1:OFFSet?": "0.0",
            ":CHANnel1:PROBe?": "10",
            ":CHANnel1:BWLimit?": "0",
            ":CHANnel1:INVert?": "0",
            ":ACQuire:TYPE?": "NORMal",
            ":WAVeform:POINts:MODE?": "RAW",
            ":WAVeform:POINts?": "1000000",
            ":WAVeform:FORMat?": "BYTE",
            ":WAVeform:SOURce?": "CHANnel1",
            ":WAVeform:PREamble?": "0,0,1000,1,1e-7,-0.02,0,1,-128,0",
        }
        return responses.get(command, "0")

    def query_binary_values(self, command: str, datatype: str = "B", container=bytes):
        del datatype
        if command == ":DISPlay:DATA? PNG" and self.fail_png:
            raise TimeoutError("simulated VISA timeout for :DISPlay:DATA? PNG")
        if command == ":WAVeform:DATA?":
            return container(b"\x80\x81\x82")
        if command == ":DISPlay:DATA? PNG":
            return container(b"\x89PNG")
        return container(b"")

    def close(self) -> None:
        self.closed = True


class _FakeRM:
    def __init__(self) -> None:
        self.inst = _FakeInstrument()
        self.open_resource_calls = 0

    def open_resource(self, resource: str):
        del resource
        self.open_resource_calls += 1
        return self.inst

    def close(self) -> None:
        return None


class ScopeRealTests(unittest.TestCase):
    def test_scope_real_configure_arm_capture_path(self) -> None:
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [
            8, 8, 0,  # Previous acquisition stops after :STOP.
            8, 8, 0,  # Fresh :SINGle arms, then acquisition completes.
        ]
        now_s = [0.0]

        def monotonic() -> float:
            now_s[0] += 0.02
            return now_s[0]

        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=monotonic,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()
        self.assertEqual(scope.status(), ScopeStatus.CONNECTED)
        self.assertIn("FAKE_SCOPE", scope.identify())

        scope.configure_for_cycle(ScopeSettings())
        self.assertEqual(scope.status(), ScopeStatus.CONFIGURED)

        scope.arm_single()
        self.assertTrue(scope.wait_until_armed(timeout_s=1.0, now_monotonic_s=monotonic()))
        self.assertTrue(scope.wait_until_acquisition_complete(timeout_s=1.0, now_monotonic_s=monotonic()))

        capture = scope.capture_after_acquire()
        self.assertEqual(capture.preamble["points"], 1000)
        self.assertGreater(len(capture.samples), 0)
        self.assertTrue(capture.scope_png.startswith(b"\x89PNG"))

    def test_configure_waits_until_scope_is_stopped(self) -> None:
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [
            8, 8, 0,  # Previous acquisition stops after :STOP.
            8, 8, 0,  # Fresh :SINGle arms, then acquisition completes.
        ]
        rm.inst.run_bit_sequence = [8, 8, 0]
        now_s = [0.0]

        def monotonic() -> float:
            now_s[0] += 0.02
            return now_s[0]

        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=monotonic,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.configure_for_cycle(ScopeSettings())

        self.assertEqual(rm.inst.run_bit_sequence, [])
        self.assertEqual(scope.status(), ScopeStatus.CONFIGURED)

    def test_configure_sets_trigger_mode_edge_before_edge_parameters(self) -> None:
        # Regression: :TRIGger:EDGE:* commands are inert unless :TRIGger:MODE
        # is explicitly EDGE first - the scope otherwise keeps triggering on
        # whatever mode was last left active on the front panel.
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [0]
        now_s = [0.0]

        def monotonic() -> float:
            now_s[0] += 0.02
            return now_s[0]

        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=monotonic,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.configure_for_cycle(ScopeSettings())

        self.assertIn(":TRIGger:MODE EDGE", rm.inst.commands)
        mode_index = rm.inst.commands.index(":TRIGger:MODE EDGE")
        edge_param_commands = [
            cmd for cmd in rm.inst.commands if cmd.startswith(":TRIGger:EDGE:")
        ]
        self.assertTrue(edge_param_commands, "expected :TRIGger:EDGE:* parameter commands")
        for cmd in edge_param_commands:
            self.assertGreater(
                rm.inst.commands.index(cmd),
                mode_index,
                f"{cmd} must be sent after :TRIGger:MODE EDGE",
            )

    def test_configure_sets_probe_ratio_before_scale_and_offset(self) -> None:
        # Regression: :CHANnel:SCALe/:OFFSet are interpreted "at the probe
        # tip" using whatever probe ratio is already configured - setting
        # them before :CHANnel:PROBe applies them against a stale ratio and
        # silently leaves the actual digitized range off by the probe
        # factor, even though readback afterward looks correct.
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [0]
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.configure_for_cycle(ScopeSettings())

        probe_index = rm.inst.commands.index(":CHANnel1:PROBe 10")
        for cmd in (":CHANnel1:SCALe 50.0", ":CHANnel1:OFFSet 0.0"):
            self.assertGreater(
                rm.inst.commands.index(cmd),
                probe_index,
                f"{cmd} must be sent after :CHANnel1:PROBe",
            )

    def test_configure_sets_trigger_coupling_dc_and_disables_noise_reject(self) -> None:
        # DC, not AC: the trigger comparator must see the raw absolute
        # voltage for a one-shot transient against a fixed level. Noise
        # reject is locked off since it adds comparator hysteresis, raising
        # the effective threshold above the configured level.
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [0]
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.configure_for_cycle(ScopeSettings())

        self.assertIn(":TRIGger:COUPling DC", rm.inst.commands)
        self.assertIn(":TRIGger:NREJect OFF", rm.inst.commands)

    def test_configure_sends_opc_sync_barrier_after_commands(self) -> None:
        # arm_single() is called immediately after configure_for_cycle()
        # returns - without this barrier it could race ahead of the scope
        # still internalizing the last few configuration commands.
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [0]
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.configure_for_cycle(ScopeSettings())

        self.assertIn("*OPC?", rm.inst.queries)
        # Every configuration write already happened by the time
        # configure_for_cycle() returns - *OPC? is queried last, after the
        # full command list, not interleaved with it.
        self.assertEqual(rm.inst.commands[-1], ":WAVeform:POINts:MODE RAW")

    def test_timeout_diagnostics_captures_operation_condition_and_settings(self) -> None:
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [0]
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(diagnostics.operation_condition, 0)
        self.assertEqual(diagnostics.settings["trigger_mode"], "EDGE")
        self.assertEqual(diagnostics.settings["ch1_coupling"], "AC")
        self.assertEqual(diagnostics.settings["trigger_coupling"], "DC")
        self.assertEqual(diagnostics.settings["trigger_noise_reject"], "0")
        self.assertEqual(diagnostics.settings["trigger_event_register"], "0")
        self.assertEqual(diagnostics.error_queue, ())
        self.assertTrue(diagnostics.scope_png.startswith(b"\x89PNG"))

    def test_timeout_diagnostics_sends_no_write_commands(self) -> None:
        rm = _FakeRM()
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()
        commands_before = list(rm.inst.commands)

        scope.capture_timeout_diagnostics()

        self.assertEqual(rm.inst.commands, commands_before)

    def test_timeout_diagnostics_aborts_after_first_query_failure(self) -> None:
        # Fail-fast, not best-effort-per-field: SCOPE_TRIGGER_DEBUG_LOG.md
        # Entry 3 showed that continuing to query after one failure can
        # cascade into a fully wedged USBTMC session on real hardware.
        rm = _FakeRM()
        rm.inst.fail_commands = {":CHANnel1:COUPling?"}
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(diagnostics.settings["ch1_display"], "1")  # queried before the failure
        self.assertIn("query failed", str(diagnostics.settings["ch1_coupling"]))  # the failure itself
        self.assertIn("diagnostics_aborted", diagnostics.settings)
        self.assertNotIn("trigger_mode", diagnostics.settings)  # never reached - abort was immediate

    def test_timeout_diagnostics_calls_device_clear_first(self) -> None:
        rm = _FakeRM()
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.capture_timeout_diagnostics()

        self.assertEqual(rm.inst.clear_calls, 1)

    def test_timeout_diagnostics_aborts_immediately_if_device_clear_fails(self) -> None:
        rm = _FakeRM()
        rm.inst.fail_clear = True
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(rm.inst.clear_calls, 1)
        self.assertEqual(set(diagnostics.settings.keys()), {"diagnostics_aborted"})
        self.assertIn("device clear failed", diagnostics.settings["diagnostics_aborted"])
        self.assertEqual(diagnostics.operation_condition, -1)
        self.assertEqual(diagnostics.scope_png, b"")

    def test_timeout_diagnostics_continues_when_device_clear_unsupported(self) -> None:
        # Confirmed on a real de-energized dry run against the MSO-X 2014A:
        # PyVISA-Py raises VI_ERROR_NSUP_OPER for .clear() on this
        # backend/resource type - that's "not implemented," not evidence of
        # an unhealthy connection, so diagnostics should proceed to the
        # normal bounded, fail-fast queries rather than aborting.
        rm = _FakeRM()
        rm.inst.fail_clear = True
        rm.inst.clear_error_text = "VI_ERROR_NSUP_OPER (-1073807360): The specified operation is not supported."
        rm.inst.run_bit_sequence = [0]
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(rm.inst.clear_calls, 1)
        self.assertIn("unsupported by this VISA backend", diagnostics.settings["device_clear"])
        self.assertNotIn("diagnostics_aborted", diagnostics.settings)
        self.assertEqual(diagnostics.settings["trigger_mode"], "EDGE")
        self.assertEqual(diagnostics.operation_condition, 0)
        self.assertTrue(diagnostics.scope_png.startswith(b"\x89PNG"))

    def test_timeout_diagnostics_sets_native_visa_timeout_before_queries(self) -> None:
        # No supervising thread anymore (see SCOPE_TRIGGER_DEBUG_LOG.md
        # Entry 6) - the per-query bound now comes entirely from PyVISA's
        # own native per-resource `.timeout`, set synchronously before any
        # query is issued.
        rm = _FakeRM()
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
            diagnostics_query_timeout_s=0.25,
        )
        scope.connect()

        scope.capture_timeout_diagnostics()

        self.assertEqual(rm.inst.timeout, 250.0)

    def test_timeout_diagnostics_spawns_no_background_threads(self) -> None:
        # Regression for SCOPE_TRIGGER_DEBUG_LOG.md Entry 6: a prior
        # daemon-thread-based timeout gave up waiting on a slow query but
        # left the thread running in the background, still touching the
        # connection - racing with the main thread and segfaulting the
        # process. Every query must now be synchronous, in this thread,
        # with nothing left running afterward, whether it succeeds or fails.
        rm = _FakeRM()
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()
        threads_before = threading.active_count()

        scope.capture_timeout_diagnostics()

        self.assertEqual(threading.active_count(), threads_before)

    def test_timeout_diagnostics_spawns_no_background_threads_on_failure(self) -> None:
        rm = _FakeRM()
        rm.inst.fail_commands = {":CHANnel1:COUPling?"}
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()
        threads_before = threading.active_count()

        scope.capture_timeout_diagnostics()

        self.assertEqual(threading.active_count(), threads_before)

    def test_timeout_diagnostics_marks_connection_unusable_after_query_failure(self) -> None:
        # Once a query fails, the transport's state can't be trusted (Entry
        # 3) - no further operation, including a later disconnect()'s
        # .close(), should touch this connection.
        rm = _FakeRM()
        rm.inst.fail_commands = {":CHANnel1:COUPling?"}
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.capture_timeout_diagnostics()

        with self.assertRaises(Exception):
            scope.identify()

        scope.disconnect()
        self.assertFalse(rm.inst.closed)  # no operation issued against the poisoned connection

        with self.assertRaises(Exception):
            scope.connect()

    def test_timeout_diagnostics_does_not_mark_connection_unusable_on_success(self) -> None:
        rm = _FakeRM()
        rm.inst.run_bit_sequence = [0]
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        scope.capture_timeout_diagnostics()

        self.assertIn("FAKE_SCOPE", scope.identify())  # still usable
        scope.disconnect()
        self.assertTrue(rm.inst.closed)  # normal disconnect still closes the resource

    def test_timeout_diagnostics_respects_total_time_budget(self) -> None:
        rm = _FakeRM()
        clock = [5.0]

        def monotonic() -> float:
            value = clock[0]
            clock[0] += 10.0  # every call jumps well past any reasonable budget
            return value

        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=monotonic,
            resource_manager_factory=lambda backend: rm,
            diagnostics_total_budget_s=1.0,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(diagnostics.settings.get("diagnostics_aborted"), "total time budget exceeded")
        self.assertNotIn("ch1_display", diagnostics.settings)

    def test_timeout_diagnostics_query_failure_does_not_trigger_reconnect(self) -> None:
        rm = _FakeRM()
        rm.inst.fail_commands = {":CHANnel1:COUPling?", ":TRIGger:MODE?"}
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()
        self.assertEqual(rm.open_resource_calls, 1)

        scope.capture_timeout_diagnostics()

        self.assertEqual(rm.open_resource_calls, 1)

    def test_timeout_diagnostics_error_queue_drains_bounded(self) -> None:
        rm = _FakeRM()
        rm.inst.always_error_queue = True
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertLessEqual(len(diagnostics.error_queue), 20)
        self.assertEqual(len(diagnostics.error_queue), 20)

    def test_timeout_diagnostics_when_disconnected_returns_best_effort_bundle(self) -> None:
        scope = ScopeReal(resource="USB::FAKE", monotonic_now=lambda: 5.0)

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(diagnostics.operation_condition, -1)
        self.assertEqual(diagnostics.scope_png, b"")

    def test_timeout_diagnostics_png_query_failure_leaves_scope_png_empty(self) -> None:
        rm = _FakeRM()
        rm.inst.fail_png = True
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertEqual(diagnostics.scope_png, b"")
        self.assertIn("scope_png_capture_error", diagnostics.settings)

    def test_parse_preamble(self) -> None:
        parsed = _parse_keysight_preamble("0,0,1000,1,1e-7,-0.02,0,1,-128,0")
        self.assertEqual(parsed["points"], 1000)
        self.assertAlmostEqual(parsed["x_increment"], 1e-7)


if __name__ == "__main__":
    unittest.main()
