from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from ccid.classify import LedColor, frames_to_bgr_bytes, make_led_frame
from ccid.config import load_config
from ccid.hal.base import CameraFrame, CameraHealth, CameraStateSample
from ccid.hal.gpio_sim import GpioSimContactorController
from ccid.hal.scope_sim import ScopeSim, ScopeSimScenario
from ccid.main import (
    HttpNotifier,
    StopRequested,
    SystemdNotifier,
    _cmd_resume,
    _cmd_start,
    _cmd_status,
    _run_campaign_with_auto_retry,
    build_hal_bundle,
    latest_run_dir,
)
from ccid.recorder import RunRecorder
from ccid.sequencer import Sequencer
from ccid.states import LedState, Terminal


class _NoopLifecycle:
    def check(self) -> None:
        return None


class _NoopWatchdog:
    def sleep(self, seconds: float, *, stop_check=None) -> None:
        del seconds
        if stop_check is not None:
            stop_check()


class _FakeNotifier:
    def __init__(self) -> None:
        self.start_calls: list[tuple[str, int, int]] = []
        self.resume_calls: list[tuple[str, int, int]] = []

    def notify_start(self, run_id: str, cycle_start: int, cycle_target: int) -> None:
        self.start_calls.append((run_id, cycle_start, cycle_target))

    def notify_resume(self, run_id: str, last_completed_cycle: int, cycle_target: int) -> None:
        self.resume_calls.append((run_id, last_completed_cycle, cycle_target))

    def notify_fault(self, run_id: str, cycle_index: int, reason: str) -> None:
        del run_id, cycle_index, reason

    def notify_complete(self, run_id: str, cycle_target: int) -> None:
        del run_id, cycle_target

    def heartbeat(self, run_id: str, last_completed_cycle: int) -> None:
        del run_id, last_completed_cycle

    def heartbeat_fail(self, run_id: str, last_completed_cycle: int, reason: str) -> None:
        del run_id, last_completed_cycle, reason


class _ManualClock:
    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s

    def now(self) -> float:
        return self.now_s

    def sleep(self, seconds: float) -> None:
        self.now_s += max(0.0, seconds)


class _AlwaysChargingCamera:
    """Real, classifier-compatible green frames every call - the sequencer
    runs the actual HSV classifier on whatever the camera hands it, so a
    tiny dark placeholder frame (as opposed to a real green one) would
    never actually grant the charging gate."""

    def __init__(self) -> None:
        self._latest_frame: CameraFrame | None = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def sample_state(self, now_monotonic_s: float) -> CameraStateSample:
        rgb = make_led_frame(LedColor.GREEN, width=16, height=16)
        bgr = frames_to_bgr_bytes(rgb)
        frame = CameraFrame(
            frame_bgr=bgr,
            width=16,
            height=16,
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=now_monotonic_s,
            metadata={"source": "test"},
        )
        self._latest_frame = frame
        return CameraStateSample(
            led_state=LedState.CHARGING,
            observed_at_monotonic_s=now_monotonic_s,
            health=CameraHealth.HEALTHY,
            frame=frame,
        )

    def await_charging_gate(self, cycle_index: int, timeout_s: float, now_monotonic_s: float):
        raise NotImplementedError("Sequencer uses classify.await_charging_gate")

    def latest_frame(self) -> CameraFrame | None:
        return self._latest_frame


class _ScriptedScenarioScope(ScopeSim):
    """Behaves like `scenarios[i]` on the i-th configure_for_cycle call
    (0-indexed), holding at the final entry once the list is exhausted -
    lets a single scope fake script an arbitrary sequence of per-cycle
    outcomes (fail, fail, succeed, ...) across multiple sequencer.run()
    invocations, which is what auto-retry actually drives."""

    def __init__(self, *, clock: _ManualClock, scenarios: list[ScopeSimScenario]) -> None:
        super().__init__(scenario=scenarios[0], monotonic_now=clock.now)
        self._scenarios = scenarios
        self.configure_calls = 0

    def configure_for_cycle(self, settings) -> None:
        index = min(self.configure_calls, len(self._scenarios) - 1)
        self._scenario = self._scenarios[index]
        self.configure_calls += 1
        super().configure_for_cycle(settings)


class _StopAfterNChecks:
    """Lifecycle fake whose check() raises StopRequested starting on the
    (n+1)-th call - simulates an operator stop request arriving during a
    retry cooldown rather than before the first attempt."""

    def __init__(self, n: int) -> None:
        self._remaining = n

    def check(self) -> None:
        if self._remaining <= 0:
            raise StopRequested("test_stop")
        self._remaining -= 1


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write_config(self, config_hash_tag: str = "runs", *, boot_timeout_s: float = 0.5) -> Path:
        config_path = self.root / f"{config_hash_tag}-config.yaml"
        config_path.write_text(
            textwrap.dedent(
                f"""
                schema_version: 1
                gpio:
                  k1: 17
                  k2: 27
                  k3: 22
                vision:
                  roi_x: 0
                  roi_y: 0
                  roi_width: 16
                  roi_height: 16
                  charging_green_window_s: 6.0
                  charging_green_required_frames: 3
                  charging_green_min_span_s: 3.5
                camera:
                  device_index: 0
                timing:
                  cooldown_s: 0.01
                  cooldown_retry_s: 0.02
                  boot_timeout_s: {boot_timeout_s}
                  scope_arm_timeout_s: 0.2
                  scope_acquisition_timeout_s: 0.2
                  k3_backstop_s: 0.3
                  pass_limit_s: 0.02497
                  no_trip_limit_s: 0.1
                  heartbeat_grace_s: 300
                  mains_stagger_ms: 0
                modes:
                  gpio_mode: sim
                  scope_mode: sim
                  camera_mode: sim
                paths:
                  run_root: {self.root / 'runs'}
                  output_root: {self.root / 'runs'}
                  min_free_disk_gb: 2
                monitoring:
                  cronitor_url_env: CCID_CRONITOR_URL
                """
            ),
            encoding="utf-8",
        )
        return config_path

    def test_build_hal_bundle_selects_sim_backends(self) -> None:
        config = load_config(self._write_config())
        bundle = build_hal_bundle(config, scope_resource=None, monotonic_now=lambda: 0.0)
        self.assertEqual(type(bundle.contactors).__name__, "GpioSimContactorController")
        self.assertEqual(type(bundle.scope).__name__, "ScopeSim")
        self.assertEqual(type(bundle.camera).__name__, "CameraSim")

    def test_latest_run_dir_picks_highest_name(self) -> None:
        run_root = self.root / "runs"
        (run_root / "20260803_120000").mkdir(parents=True)
        (run_root / "20260803_130000").mkdir(parents=True)
        self.assertEqual(latest_run_dir(run_root).name, "20260803_130000")

    def test_systemd_notifier_splits_sleep_and_pings(self) -> None:
        sent: list[str] = []

        def sender(address: str, payload: bytes) -> None:
            sent.append(payload.decode("utf-8"))

        notifier = SystemdNotifier(
            environ={"NOTIFY_SOCKET": "/tmp/fake", "WATCHDOG_USEC": "4000000"},
            sender=sender,
        )
        with patch("ccid.main.time.sleep", lambda seconds: None):
            notifier.sleep(5.0)
        self.assertGreaterEqual(sent.count("WATCHDOG=1"), 2)

    def test_http_notifier_heartbeat_pings_cronitor_with_no_state(self) -> None:
        calls: list = []

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def opener(request, timeout=5):
            del timeout
            calls.append(request)
            return _Resp()

        notifier = HttpNotifier(
            cronitor_url="https://cronitor.link/p/key/ccid-endurance",
            ntfy_topic_url=None,
            opener=opener,
        )
        notifier.heartbeat("run1", 10)

        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(
            request.full_url.startswith("https://cronitor.link/p/key/ccid-endurance?")
        )
        self.assertNotIn("state=", request.full_url)
        self.assertIn("run_id%3Drun1", request.full_url)
        self.assertIn("last_completed_cycle%3D10", request.full_url)

    def test_http_notifier_heartbeat_fail_sets_cronitor_fail_state(self) -> None:
        calls: list = []

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def opener(request, timeout=5):
            del timeout
            calls.append(request)
            return _Resp()

        notifier = HttpNotifier(
            cronitor_url="https://cronitor.link/p/key/ccid-endurance",
            ntfy_topic_url=None,
            opener=opener,
        )
        notifier.heartbeat_fail("run1", 10, "fault")

        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(
            request.full_url.startswith("https://cronitor.link/p/key/ccid-endurance?")
        )
        self.assertIn("state=fail", request.full_url)
        self.assertIn("reason%3Dfault", request.full_url)

    def test_http_notifier_skips_cronitor_when_unconfigured(self) -> None:
        calls: list = []

        def opener(request, timeout=5):
            del request, timeout
            calls.append(1)

        notifier = HttpNotifier(cronitor_url=None, ntfy_topic_url=None, opener=opener)
        notifier.heartbeat("run1", 1)
        notifier.heartbeat_fail("run1", 1, "fault")
        self.assertEqual(calls, [])

    def test_cmd_start_initializes_run_then_executes(self) -> None:
        config_path = self._write_config()
        config = load_config(config_path)
        recorder = RunRecorder(config.paths.run_root)
        notifier = _FakeNotifier()
        args = argparse.Namespace(config=str(config_path), target_cycles=3, run_id="20260803_190000")
        captured = {}

        def fake_execute(**kwargs):
            captured.update(kwargs)
            return 0

        with patch("ccid.main._execute_campaign", fake_execute):
            rc = _cmd_start(args, config, recorder, notifier, _NoopLifecycle(), _NoopWatchdog())
        self.assertEqual(rc, 0)
        self.assertEqual(notifier.start_calls, [("20260803_190000", 1, 3)])
        self.assertTrue((config.paths.run_root / "20260803_190000" / "runstate.json").exists())
        self.assertEqual(captured["state"].target_cycles, 3)

    def test_cmd_resume_allows_config_hash_override(self) -> None:
        config_path_a = self._write_config("a")
        config_a = load_config(config_path_a)
        recorder = RunRecorder(config_a.paths.run_root)
        run_dir = recorder.initialize_run(
            run_id="20260803_191000",
            target_cycles=2,
            config_hash="old-hash",
            frozen_config_yaml=Path(config_path_a).read_text(encoding="utf-8"),
        )

        config_path_b = self._write_config("b")
        config_b = load_config(config_path_b)
        notifier = _FakeNotifier()
        args = argparse.Namespace(
            run_id="20260803_191000",
            latest=False,
            allow_config_hash_override=True,
            allow_halted_resume=False,
        )
        captured = {}

        def fake_execute(**kwargs):
            captured.update(kwargs)
            return 0

        with patch("ccid.main._execute_campaign", fake_execute):
            rc = _cmd_resume(args, config_b, recorder, notifier, _NoopLifecycle(), _NoopWatchdog())
        self.assertEqual(rc, 0)
        self.assertEqual(captured["state"].config_hash, "old-hash")
        self.assertEqual(notifier.resume_calls, [("20260803_191000", 0, 2)])
        self.assertEqual(run_dir.name, "20260803_191000")

    def test_cmd_status_is_safe_and_reports_runstate(self) -> None:
        config_path = self._write_config()
        config = load_config(config_path)
        recorder = RunRecorder(config.paths.run_root)
        recorder.initialize_run(
            run_id="20260803_192000",
            target_cycles=5,
            config_hash="cfg",
            frozen_config_yaml=Path(config_path).read_text(encoding="utf-8"),
        )
        args = argparse.Namespace(run_id="20260803_192000", latest=False)
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            rc = _cmd_status(args, config, recorder)
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["run_state"]["run_id"], "20260803_192000")

    def _make_run_and_sequencer(self, *, target_cycles: int, scope, clock: _ManualClock):
        # boot_timeout_s must comfortably exceed the vision policy's
        # charging_green_min_span_s (3.5s) or the charging gate itself
        # times out before the scope is ever reached, regardless of what
        # the scope fake does.
        config = load_config(self._write_config(boot_timeout_s=6.0))
        recorder = RunRecorder(config.paths.run_root)
        run_dir = recorder.initialize_run(
            run_id="20260817_120000",
            target_cycles=target_cycles,
            config_hash=config.canonical_hash(),
            frozen_config_yaml="schema_version: 1\n",
        )
        state = recorder.load_run_state(
            run_dir,
            expected_config_hash=config.canonical_hash(),
            allow_halted_resume=True,
        )
        sequencer = Sequencer(
            config=config,
            contactors=GpioSimContactorController(monotonic_now=clock.now),
            scope=scope,
            camera=_AlwaysChargingCamera(),
            recorder=recorder,
            monotonic_now=clock.now,
            sleep=clock.sleep,
        )
        return run_dir, state, sequencer

    @staticmethod
    def _scope_scenario(**kwargs) -> ScopeSimScenario:
        # Defaults give a 2 ms record (20_000 samples @ 10 MHz) - nowhere
        # near long enough to span the 100 ms no-trip window, which fails
        # SANITY_RECORD_SPANS_NO_TRIP_LIMIT and produces a RIG_FAULT
        # instead of the intended verdict. A longer, slower record avoids
        # that for every scenario used here, matching test_sequencer.py's
        # own helper.
        defaults = {"sample_rate_hz": 200_000.0, "sample_count": 50_000, "pretrigger_s": 0.020}
        defaults.update(kwargs)
        return ScopeSimScenario(**defaults)

    def test_auto_retry_recovers_after_transient_rig_faults(self) -> None:
        clock = _ManualClock()
        scope = _ScriptedScenarioScope(
            clock=clock,
            scenarios=[
                self._scope_scenario(never_triggered=True),
                self._scope_scenario(never_triggered=True),
                self._scope_scenario(trip_time_s=0.010),
            ],
        )
        run_dir, state, sequencer = self._make_run_and_sequencer(target_cycles=3, scope=scope, clock=clock)

        result = _run_campaign_with_auto_retry(
            sequencer=sequencer,
            run_dir=run_dir,
            state=state,
            notifier=_FakeNotifier(),
            watchdog=_NoopWatchdog(),
            lifecycle=_NoopLifecycle(),
            cooldown_retry_s=0.01,
        )

        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertEqual(scope.configure_calls, 3)
        self.assertEqual(result.state.last_completed_cycle, 3)

    def test_auto_retry_gives_up_after_five_consecutive_rig_faults(self) -> None:
        clock = _ManualClock()
        scope = ScopeSim(scenario=self._scope_scenario(never_triggered=True), monotonic_now=clock.now)
        run_dir, state, sequencer = self._make_run_and_sequencer(target_cycles=100, scope=scope, clock=clock)

        result = _run_campaign_with_auto_retry(
            sequencer=sequencer,
            run_dir=run_dir,
            state=state,
            notifier=_FakeNotifier(),
            watchdog=_NoopWatchdog(),
            lifecycle=_NoopLifecycle(),
            cooldown_retry_s=0.01,
        )

        self.assertEqual(result.terminal, Terminal.RIG_FAULT)
        # "scope never triggered" halts before there's a waveform to
        # analyze, so it never commits a cycle - last_completed_cycle
        # legitimately stays at 4, one behind the cycle_index actually
        # being attempted when the 5th consecutive halt gave up. The
        # attempted cycle_index (tracked via result.cycles, appended for
        # every attempt whether or not it committed) is the correct signal
        # that exactly 5 attempts happened, one per streak count.
        self.assertEqual(result.state.last_completed_cycle, 4)
        self.assertEqual(result.cycles[-1].cycle_index, 5)

    def test_auto_retry_gives_up_after_three_consecutive_no_trips(self) -> None:
        clock = _ManualClock()
        scope = ScopeSim(scenario=self._scope_scenario(no_trip=True), monotonic_now=clock.now)
        run_dir, state, sequencer = self._make_run_and_sequencer(target_cycles=100, scope=scope, clock=clock)

        result = _run_campaign_with_auto_retry(
            sequencer=sequencer,
            run_dir=run_dir,
            state=state,
            notifier=_FakeNotifier(),
            watchdog=_NoopWatchdog(),
            lifecycle=_NoopLifecycle(),
            cooldown_retry_s=0.01,
        )

        self.assertEqual(result.terminal, Terminal.NO_TRIP)
        # Unlike "never triggered," a NO_TRIP verdict has a real analyzed
        # waveform and does commit through the normal path - last_completed_cycle
        # genuinely advances by one per attempt here.
        self.assertEqual(result.state.last_completed_cycle, 3)

    def test_auto_retry_streak_resets_on_a_completed_cycle(self) -> None:
        # 4 RIG_FAULTs, then a PASS (resets the streak), then 4 more
        # RIG_FAULTs, then a PASS - never 5 consecutive failures, so this
        # must reach COMPLETE rather than giving up. A broken (non-
        # resetting) counter would give up partway through, since 4+4=8
        # total failures is well past the limit of 5 if they were summed
        # instead of tracked as a streak.
        clock = _ManualClock()
        fail = self._scope_scenario(never_triggered=True)
        succeed = self._scope_scenario(trip_time_s=0.010)
        scope = _ScriptedScenarioScope(
            clock=clock,
            scenarios=[fail, fail, fail, fail, succeed, fail, fail, fail, fail, succeed],
        )
        run_dir, state, sequencer = self._make_run_and_sequencer(target_cycles=10, scope=scope, clock=clock)

        result = _run_campaign_with_auto_retry(
            sequencer=sequencer,
            run_dir=run_dir,
            state=state,
            notifier=_FakeNotifier(),
            watchdog=_NoopWatchdog(),
            lifecycle=_NoopLifecycle(),
            cooldown_retry_s=0.01,
        )

        self.assertEqual(result.terminal, Terminal.COMPLETE)
        self.assertEqual(scope.configure_calls, 10)

    def test_stop_requested_during_retry_cooldown_propagates(self) -> None:
        clock = _ManualClock()
        scope = ScopeSim(scenario=ScopeSimScenario(never_triggered=True), monotonic_now=clock.now)
        run_dir, state, sequencer = self._make_run_and_sequencer(target_cycles=100, scope=scope, clock=clock)

        with self.assertRaises(StopRequested):
            _run_campaign_with_auto_retry(
                sequencer=sequencer,
                run_dir=run_dir,
                state=state,
                notifier=_FakeNotifier(),
                watchdog=_NoopWatchdog(),
                lifecycle=_StopAfterNChecks(1),
                cooldown_retry_s=0.01,
            )


if __name__ == "__main__":
    unittest.main()
