"""Consolidated fault-matrix tests (coding_instructions.txt section 7).

Individual scenarios are already exercised at a basic (terminal/halt_reason)
level in `test_sequencer.py`. This file is the canonical fault-matrix
reference: for every row that is genuinely testable without real hardware, it
asserts the *full* required property set (fault classification, retry/
degrade/continue/halt decision, final commanded contactor states and opening
order, runstate contents, whether artifacts were committed, whether the next
cycle is permitted) rather than duplicating the lighter existing coverage.

Rows the spec itself says cannot be locally unit-tested (K1/K2 physically
stuck closed - an explicitly undetectable known gap; a missing external
heartbeat - external-service behavior) are present as explicitly skipped
stubs, so the matrix is visibly complete rather than silently absent.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import types
import unittest
from urllib.error import URLError

from ccid.classify import LedColor, frames_to_bgr_bytes, make_led_frame
from ccid.config import AppConfig, TimingConfig, VisionConfig, load_config
from ccid.errors import ResumeBlockedError
from ccid.hal.base import CameraFrame, CameraHealth, CameraStateSample, ContactorName
from ccid.hal.gpio_sim import GpioSimContactorController
from ccid.hal.scope_real import ScopeReal, ScopeRealError
from ccid.hal.scope_sim import ScopeSim, ScopeSimScenario
from ccid.main import HttpNotifier
from ccid.recorder import RunRecorder
from ccid.sequencer import FaultCategory, Sequencer
from ccid.states import LedState, Terminal
from tools.simulate import (
    CrashInjector,
    ManualClock,
    default_scope_scenario,
    no_skipped_cycles,
    opening_order_is_safe,
    run_campaign,
)


class _ManualClock:
    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s

    def now(self) -> float:
        return self.now_s

    def sleep(self, seconds: float) -> None:
        self.now_s += max(0.0, seconds)


class _ScriptedCamera:
    def __init__(self, states: list[LedState], *, fail_after: int | None = None) -> None:
        self._states = states
        self._fail_after = fail_after
        self._calls = 0
        self._latest_frame: CameraFrame | None = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def sample_state(self, now_monotonic_s: float) -> CameraStateSample:
        self._calls += 1
        if self._fail_after is not None and self._calls > self._fail_after:
            return CameraStateSample(
                led_state=LedState.CAMERA_UNAVAILABLE,
                observed_at_monotonic_s=now_monotonic_s,
                health=CameraHealth.FAILED,
                frame=self._latest_frame,
            )
        if self._calls - 1 < len(self._states):
            led_state = self._states[self._calls - 1]
        else:
            led_state = self._states[-1]

        frame = self._frame_for_state(led_state, now_monotonic_s)
        self._latest_frame = frame
        return CameraStateSample(
            led_state=led_state,
            observed_at_monotonic_s=now_monotonic_s,
            health=CameraHealth.HEALTHY,
            frame=frame,
        )

    def await_charging_gate(self, cycle_index: int, timeout_s: float, now_monotonic_s: float):
        raise NotImplementedError("Sequencer uses classify.await_charging_gate")

    def latest_frame(self) -> CameraFrame | None:
        return self._latest_frame

    @staticmethod
    def _frame_for_state(led_state: LedState, now_monotonic_s: float) -> CameraFrame:
        mapping = {
            LedState.READY: LedColor.BLUE,
            LedState.CHARGING: LedColor.GREEN,
            LedState.FAULTED: LedColor.RED,
            LedState.OFF_OR_UNKNOWN: LedColor.OFF,
            LedState.BOOTING: LedColor.BLUE,
            LedState.CAMERA_UNAVAILABLE: LedColor.OFF,
        }
        rgb = make_led_frame(mapping[led_state], width=8, height=8)
        bgr = frames_to_bgr_bytes(rgb)
        return CameraFrame(
            frame_bgr=bgr,
            width=8,
            height=8,
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=now_monotonic_s,
            metadata={"source": "test"},
        )


class _RaisingHttpOpener:
    """Stands in for `urllib.request.urlopen`, always failing the transport."""

    def __call__(self, request, timeout=5):
        del request, timeout
        raise URLError("simulated network failure")


class _FlakyInstrument:
    """A fake VISA instrument that raises for its first `fail_count` calls
    (across write/query combined), then behaves like a healthy scope."""

    def __init__(self, fail_count: int) -> None:
        self._remaining_failures = fail_count
        self.closed = False
        self.run_bit_sequence = [8, 8, 0]

    def _maybe_fail(self) -> None:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ConnectionError("simulated USBTMC comms drop")

    def write(self, command: str) -> None:
        del command
        self._maybe_fail()

    def query(self, command: str) -> str:
        self._maybe_fail()
        if command == "*IDN?":
            return "FAKE_SCOPE,MODEL,123,1.0"
        return "0"

    def close(self) -> None:
        self.closed = True


class _FlakyResourceManager:
    """Hands out the same underlying flaky link on every reconnect (a real
    reconnect creates a new VISA session object, but the thing making it
    flaky - e.g. a transient USB glitch - doesn't reset just because
    `ScopeReal._reconnect_once` asked for a new session)."""

    def __init__(self, fail_count: int) -> None:
        self._instrument = _FlakyInstrument(fail_count)

    def open_resource(self, resource: str):
        del resource
        return self._instrument

    def close(self) -> None:
        return None


class FaultMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _ManualClock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recorder = RunRecorder(self.root)
        self.base_config = self._small_config()

    def _small_config(self) -> AppConfig:
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
        timing = TimingConfig(
            cooldown_s=0.05,
            cooldown_retry_s=0.10,
            boot_timeout_s=4.0,
            scope_arm_timeout_s=0.5,
            scope_acquisition_timeout_s=0.5,
            k3_backstop_s=0.30,
            pass_limit_s=cfg.timing.pass_limit_s,
            no_trip_limit_s=cfg.timing.no_trip_limit_s,
            heartbeat_grace_s=cfg.timing.heartbeat_grace_s,
            mains_stagger_ms=cfg.timing.mains_stagger_ms,
        )
        vision = VisionConfig(
            roi_x=0,
            roi_y=0,
            roi_width=8,
            roi_height=8,
            charging_green_window_s=2.0,
            charging_green_required_frames=3,
        )
        return replace(cfg, timing=timing, vision=vision)

    def _initialize(self, target_cycles: int = 1):
        run_id = "20260805_000000"
        run_dir = self.recorder.initialize_run(
            run_id=run_id,
            target_cycles=target_cycles,
            config_hash=self.base_config.canonical_hash(),
            frozen_config_yaml="schema_version: 1\n",
        )
        state = self.recorder.load_run_state(
            run_dir, expected_config_hash=self.base_config.canonical_hash(), allow_halted_resume=True
        )
        return run_dir, state

    def _make_sequencer(
        self,
        *,
        camera,
        scope_scenario: ScopeSimScenario,
        contactors=None,
        scope=None,
        disk_usage=None,
    ) -> tuple[Sequencer, GpioSimContactorController]:
        if contactors is None:
            contactors = GpioSimContactorController(monotonic_now=self.clock.now)
        if scope is None:
            scope = ScopeSim(scenario=scope_scenario, monotonic_now=self.clock.now)
        kwargs = {}
        if disk_usage is not None:
            kwargs["disk_usage"] = disk_usage
        sequencer = Sequencer(
            config=self.base_config,
            contactors=contactors,
            scope=scope,
            camera=camera,
            recorder=self.recorder,
            monotonic_now=self.clock.now,
            sleep=self.clock.sleep,
            **kwargs,
        )
        return sequencer, contactors

    @staticmethod
    def _scope_scenario(**kwargs) -> ScopeSimScenario:
        defaults = {"sample_rate_hz": 200_000.0, "sample_count": 50_000, "pretrigger_s": 0.020}
        defaults.update(kwargs)
        return ScopeSimScenario(**defaults)

    def _assert_safe_and_ordered(self, contactors: GpioSimContactorController) -> None:
        snapshot = contactors.snapshot().commanded_closed
        self.assertFalse(snapshot[ContactorName.K1])
        self.assertFalse(snapshot[ContactorName.K2])
        self.assertFalse(snapshot[ContactorName.K3])
        self.assertTrue(opening_order_is_safe(contactors))

    def _assert_runstate(self, run_dir, *, halt_reason_present: bool) -> None:
        runstate = self.recorder.read_run_state_unchecked(run_dir)
        if halt_reason_present:
            self.assertIsNotNone(runstate.halt_reason)
            with self.assertRaises(ResumeBlockedError):
                self.recorder.load_run_state(
                    run_dir, expected_config_hash=self.base_config.canonical_hash(), allow_halted_resume=False
                )
        else:
            self.assertIsNone(runstate.halt_reason)

    @staticmethod
    def _artifact_committed(run_dir, cycle_index: int = 1) -> bool:
        return (run_dir / "waveforms" / f"{cycle_index}.npz").exists()

    # -- DUT faults --------------------------------------------------------

    def test_dut_no_trip_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(no_trip=True)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.NO_TRIP)
        self.assertEqual(result.fault_category, FaultCategory.DUT)
        self.assertIn("dut_no_trip", result.halt_reason or "")
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertTrue(self._artifact_committed(run_dir))  # DUT result is real data, kept.

    def test_late_trip_fails_and_campaign_continues_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(trip_time_s=0.05)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertEqual(result.cycles[0].terminal, Terminal.FAIL)
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=False)  # next cycle permitted
        self.assertTrue(self._artifact_committed(run_dir))

    # -- Rig faults ----------------------------------------------------------

    def test_scope_never_triggered_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(never_triggered=True)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        self.assertEqual(result.fault_category, FaultCategory.RIG)
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertFalse(self._artifact_committed(run_dir))  # halted before any capture.

    def test_k3_pretrigger_leakage_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(pretrigger_leakage=True, no_trip=True)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        self.assertIn("k3_pretrigger_current_detected", result.halt_reason or "")
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertTrue(self._artifact_committed(run_dir))  # captured before the sanity check failed.

    def test_persistent_k1_k2_mismatch_row(self) -> None:
        class _AlwaysMismatchContactors(GpioSimContactorController):
            def detect_mains_command_mismatch(self, allowed_stagger_ms: int, now_monotonic_s: float) -> bool:
                return True

        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        contactors = _AlwaysMismatchContactors(monotonic_now=self.clock.now)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(), contactors=contactors
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        self.assertIn("k1_k2_command_mismatch", result.halt_reason or "")
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertFalse(self._artifact_committed(run_dir))

    def test_k1_k2_physically_stuck_closed_row(self) -> None:
        self.skipTest(
            "Explicitly undetectable known gap (coding_instructions.txt sec.7): "
            "software tracks commanded state only, with no auxiliary-contact or "
            "voltage readback. Cannot be validated by a unit test without real "
            "hardware readback wiring that does not exist."
        )

    # -- Vision-gate timeout branches (opening-order/artifact dimension not
    #    already covered by test_sequencer.py's terminal/reason-focused tests) --

    def test_red_vision_timeout_retry_exhausted_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.FAULTED] * 500)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(trip_time_s=0.015)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.HALTED)
        self.assertIn("retry_exhausted", result.halt_reason or "")
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertFalse(self._artifact_committed(run_dir))

    def test_blue_vision_timeout_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.READY] * 500)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(trip_time_s=0.015)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.HALTED)
        self.assertIn("vision_gate_timeout_ready_no_charging_state", result.halt_reason or "")
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertFalse(self._artifact_committed(run_dir))

    def test_off_vision_timeout_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.OFF_OR_UNKNOWN] * 500)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(trip_time_s=0.015)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.HALTED)
        self.assertIn("vision_gate_timeout_led_off_or_unknown", result.halt_reason or "")
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertFalse(self._artifact_committed(run_dir))

    # -- Peripheral faults -----------------------------------------------

    def test_camera_failure_degraded_wait_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 4, fail_after=2)
        sequencer, contactors = self._make_sequencer(
            camera=camera, scope_scenario=self._scope_scenario(trip_time_s=0.012)
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertIn("vision_camera_unavailable_fixed_wait", result.cycles[0].degraded_flags)
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=False)  # degrade continues, next cycle permitted.
        self.assertTrue(self._artifact_committed(run_dir))

    def test_scope_comms_drop_then_reconnect_succeeds_row(self) -> None:
        """Bounded reconnect: fails twice (within the 3-attempt budget), then
        the third attempt against a fresh connection succeeds."""

        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=self.clock.now,
            resource_manager_factory=lambda backend: _FlakyResourceManager(fail_count=2),
        )
        scope.connect()
        self.assertIn("FAKE_SCOPE", scope.identify())

    def test_scope_comms_drop_exhausts_reconnect_attempts_row(self) -> None:
        """Failing on every attempt (more than the 3-attempt budget) surfaces
        as a typed HAL error rather than hanging or silently continuing."""

        scope = ScopeReal(
            resource="USB::FAKE",
            monotonic_now=self.clock.now,
            resource_manager_factory=lambda backend: _FlakyResourceManager(fail_count=100),
        )
        scope.connect()
        with self.assertRaises(ScopeRealError):
            scope.identify()

    def test_healthcheck_request_failure_is_swallowed_row(self) -> None:
        """A raising HTTP transport must be logged and swallowed, never
        propagated - monitoring can never halt the campaign."""

        notifier = HttpNotifier(
            heartbeat_url="https://hc-ping.com/fake-uuid",
            ntfy_topic_url="https://ntfy.sh/fake-topic",
            opener=_RaisingHttpOpener(),
        )
        notifier.heartbeat("run-1", 3)
        notifier.heartbeat_fail("run-1", 3, "rig:some_fault")
        notifier.notify_fault("run-1", 3, "rig:some_fault")
        notifier.notify_complete("run-1", 10)
        # No exception above means the row passes: transport failures are
        # logged, never raised.

    # -- Persistence faults --------------------------------------------------

    def test_disk_below_threshold_halts_before_energizing_row(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)

        def near_full_disk(_path: str) -> types.SimpleNamespace:
            return types.SimpleNamespace(free=1)

        sequencer, contactors = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.010),
            disk_usage=near_full_disk,
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        self.assertEqual(result.terminal, Terminal.HALTED)
        self.assertIn("insufficient_disk_space", result.halt_reason or "")
        self.assertEqual(result.fault_category, FaultCategory.PERSISTENCE)
        self._assert_safe_and_ordered(contactors)
        self._assert_runstate(run_dir, halt_reason_present=True)
        self.assertFalse(self._artifact_committed(run_dir))
        # Caught before mains were ever commanded closed - not merely opened
        # again afterward.
        close_ops = [event.operation for event in contactors.events() if event.success]
        self.assertNotIn("close_k1", close_ops)
        self.assertNotIn("close_k2", close_ops)
        self.assertNotIn("close_k3", close_ops)

    def test_missing_external_heartbeat_row(self) -> None:
        self.skipTest(
            "External-service behavior (coding_instructions.txt sec.7): a missing "
            "heartbeat is detected by healthchecks.io's own grace-period timer, "
            "not by this codebase, so it is documented rather than falsely "
            "unit-tested locally."
        )

    def test_power_loss_and_safe_resume_row(self) -> None:
        """Software-crash proxy (not real power loss - see
        tools/simulate.py's module docstring for that distinction): a crash
        mid-persistence must not skip or duplicate a cycle on resume, and the
        rig must end up safely open both before and after."""

        run_root = self.root / "power_loss_sim"
        injector = CrashInjector(target_cycle=1, target_checkpoint="after_csv")
        crashing_recorder = RunRecorder(run_root, crash_injector=injector)
        config = self.base_config
        run_dir = crashing_recorder.initialize_run(
            run_id="power_loss_row",
            target_cycles=2,
            config_hash=config.canonical_hash(),
            frozen_config_yaml="schema_version: 1\n",
        )
        state = crashing_recorder.load_run_state(
            run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True
        )
        try:
            run_campaign(
                config=config,
                recorder=crashing_recorder,
                run_dir=run_dir,
                state=state,
                clock=ManualClock(),
                scope_scenario=default_scope_scenario(),
            )
        except Exception:
            pass
        self.assertTrue(injector.triggered)

        clean_recorder = RunRecorder(run_root)
        pre_resume_state = clean_recorder.read_run_state_unchecked(run_dir)
        clean_recorder.reconcile_orphans(run_dir, pre_resume_state)
        resumed_state = clean_recorder.load_run_state(
            run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True
        )
        result, contactors = run_campaign(
            config=config,
            recorder=clean_recorder,
            run_dir=run_dir,
            state=resumed_state,
            clock=ManualClock(),
            scope_scenario=default_scope_scenario(),
        )

        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertTrue(no_skipped_cycles(run_dir))
        self.assertTrue(opening_order_is_safe(contactors))


if __name__ == "__main__":
    unittest.main()
