from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from ccid.config import load_config
from ccid.main import (
    HttpNotifier,
    SystemdNotifier,
    _cmd_resume,
    _cmd_start,
    _cmd_status,
    build_hal_bundle,
    latest_run_dir,
)
from ccid.recorder import RunRecorder


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


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write_config(self, config_hash_tag: str = "runs") -> Path:
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
                  boot_timeout_s: 0.5
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
                  heartbeat_url_env: CCID_HEALTHCHECKS_URL
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

    def test_http_notifier_heartbeat_fail_uses_fail_endpoint(self) -> None:
        calls: list[str] = []

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def opener(request, timeout=5):
            del timeout
            calls.append(request.full_url)
            return _Resp()

        notifier = HttpNotifier(
            heartbeat_url="https://hc-ping.example/abc",
            ntfy_topic_url=None,
            opener=opener,
        )
        notifier.heartbeat_fail("run1", 10, "fault")
        self.assertEqual(calls, ["https://hc-ping.example/abc/fail"])

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


if __name__ == "__main__":
    unittest.main()
