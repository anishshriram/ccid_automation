from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from ccid.classify import LedColor, frames_to_bgr_bytes, make_led_frame
from ccid.config import AppConfig, TimingConfig, VisionConfig, load_config
from ccid.hal.base import CameraFrame, CameraHealth, CameraStateSample, ContactorName
from ccid.hal.gpio_sim import GpioSimContactorController
from ccid.hal.scope_sim import ScopeSim, ScopeSimScenario
from ccid.recorder import RunRecorder
from ccid.sequencer import FaultCategory, Sequencer
from ccid.states import LedState, Terminal


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

class _BlockingNeverTriggeredScope(ScopeSim):
    def __init__(self, *, clock: _ManualClock, scenario: ScopeSimScenario) -> None:
        super().__init__(scenario=scenario, monotonic_now=clock.now)
        self._clock = clock

    def wait_until_acquisition_complete(
        self,
        timeout_s: float,
        now_monotonic_s: float,
    ) -> bool:
        self._clock.sleep(timeout_s)
        return False

class _AlwaysMismatchContactors(GpioSimContactorController):
    def detect_mains_command_mismatch(self, allowed_stagger_ms: int, now_monotonic_s: float) -> bool:
        return True


class SequencerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _ManualClock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recorder = RunRecorder(self.root)
        self.base_config = self._small_config()

    def _small_config(self) -> AppConfig:
        cfg = load_config(
            Path(__file__).resolve().parents[1] / "config.yaml"
        )
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
        )
        return replace(cfg, timing=timing, vision=vision)

    def _initialize(self, target_cycles: int = 1):
        run_id = "20260803_180000"
        run_dir = self.recorder.initialize_run(
            run_id=run_id,
            target_cycles=target_cycles,
            config_hash=self.base_config.canonical_hash(),
            frozen_config_yaml="schema_version: 1\n",
        )
        state = self.recorder.load_run_state(
            run_dir,
            expected_config_hash=self.base_config.canonical_hash(),
            allow_halted_resume=True,
        )
        return run_dir, state

    def _make_sequencer(
        self,
        *,
        camera,
        scope_scenario: ScopeSimScenario,
        contactors=None,
        scope=None,
    ) -> Sequencer:
        if contactors is None:
            contactors = GpioSimContactorController(monotonic_now=self.clock.now)
        if scope is None:
            scope = ScopeSim(scenario=scope_scenario, monotonic_now=self.clock.now)
        return Sequencer(
            config=self.base_config,
            contactors=contactors,
            scope=scope,
            camera=camera,
            recorder=self.recorder,
            monotonic_now=self.clock.now,
            sleep=self.clock.sleep,
        )

    @staticmethod
    def _scope_scenario(**kwargs) -> ScopeSimScenario:
        defaults = {
            "sample_rate_hz": 200_000.0,
            "sample_count": 50_000,
            "pretrigger_s": 0.020,
        }
        defaults.update(kwargs)
        return ScopeSimScenario(**defaults)

    def test_normal_pass_cycle_completes(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.010),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertEqual(result.state.last_completed_cycle, 1)
        self.assertEqual(result.cycles[0].terminal, Terminal.PASS)

    def test_red_timeout_retries_once_then_succeeds(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        # First timeout window sees red, second window sees green.
        camera = _ScriptedCamera(([LedState.FAULTED] * 70) + ([LedState.CHARGING] * 180))
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.015),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertEqual(result.latch_slow_clear_count, 1)
        self.assertIn("latch_slow_clear", result.cycles[0].degraded_flags)

    def test_red_timeout_retry_exhausted_halts(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.FAULTED] * 500)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.015),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.HALTED)
        self.assertIn("retry_exhausted", result.halt_reason or "")

    def test_blue_timeout_halts_without_retry(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.READY] * 500)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.015),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.HALTED)
        self.assertIn("vision_gate_timeout_ready_no_charging_state", result.halt_reason or "")

    def test_off_timeout_halts_without_retry(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.OFF_OR_UNKNOWN] * 500)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.015),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.HALTED)
        self.assertIn("vision_gate_timeout_led_off_or_unknown", result.halt_reason or "")

    def test_camera_failure_degrades_and_continues(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 4, fail_after=2)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.012),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertIn("vision_camera_unavailable_fixed_wait", result.cycles[0].degraded_flags)

    def test_scope_never_triggered_halts_as_rig_fault(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(never_triggered=True),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        self.assertEqual(result.fault_category, FaultCategory.RIG)

    def test_k3_backstop_opens_before_blocking_acquisition_timeout(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        contactors = GpioSimContactorController(monotonic_now=self.clock.now)
        scenario = self._scope_scenario(never_triggered=True)
        scope = _BlockingNeverTriggeredScope(clock=self.clock, scenario=scenario)

        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=scenario,
            contactors=contactors,
            scope=scope,
        )

        result = sequencer.run(run_dir=run_dir, state=state)

        events = contactors.events()
        close_k3 = next(event for event in events if event.operation == "close_k3")
        open_k3 = next(event for event in events if event.operation == "open_k3" and event.monotonic_s >= close_k3.monotonic_s)
        k3_duration_s = open_k3.monotonic_s - close_k3.monotonic_s

        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        self.assertLessEqual(
            k3_duration_s,
            self.base_config.timing.k3_backstop_s + 0.02,
        )

    def test_pretrigger_leakage_halts_as_rig_fault(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(pretrigger_leakage=True, no_trip=True),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        self.assertIn("k3_pretrigger_current_detected", result.halt_reason or "")

    def test_no_trip_halts_as_dut_fault(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(no_trip=True),
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.NO_TRIP)
        self.assertEqual(result.fault_category, FaultCategory.DUT)

    def test_mains_mismatch_halts(self) -> None:
        run_dir, state = self._initialize(target_cycles=1)
        camera = _ScriptedCamera([LedState.CHARGING] * 120)
        contactors = _AlwaysMismatchContactors(monotonic_now=self.clock.now)
        sequencer = self._make_sequencer(
            camera=camera,
            scope_scenario=self._scope_scenario(trip_time_s=0.010),
            contactors=contactors,
        )

        result = sequencer.run(run_dir=run_dir, state=state)
        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        self.assertIn("k1_k2_command_mismatch", result.halt_reason or "")
        self.assertFalse(contactors.snapshot().commanded_closed[ContactorName.K1])
        self.assertFalse(contactors.snapshot().commanded_closed[ContactorName.K2])
        self.assertFalse(contactors.snapshot().commanded_closed[ContactorName.K3])


if __name__ == "__main__":
    unittest.main()
