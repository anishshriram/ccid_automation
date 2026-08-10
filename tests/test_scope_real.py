from __future__ import annotations

from datetime import datetime, timezone
import unittest

from ccid.hal.base import ScopeSettings, ScopeStatus
from ccid.hal.scope_real import ScopeReal, _parse_keysight_preamble


class _FakeInstrument:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False
        self.run_bit_sequence = [8, 8, 0]
        self.fail_commands: set[str] = set()
        self.always_error_queue = False
        self._error_queue_calls = 0
        self.fail_png = False

    def write(self, command: str) -> None:
        self.commands.append(command)

    def query(self, command: str) -> str:
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

    def test_timeout_diagnostics_partial_query_failure_still_returns_bundle(self) -> None:
        rm = _FakeRM()
        rm.inst.fail_commands = {":CHANnel1:COUPling?"}
        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=lambda: 5.0,
            resource_manager_factory=lambda backend: rm,
        )
        scope.connect()

        diagnostics = scope.capture_timeout_diagnostics()

        self.assertIn("query failed", str(diagnostics.settings["ch1_coupling"]))
        self.assertEqual(diagnostics.settings["trigger_mode"], "EDGE")

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
