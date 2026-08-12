"""CLI/lifecycle entrypoint (coding_instructions.txt Phase 10).

`start`/`resume`/`status`/`simulate` subcommands, signal-safe shutdown
(SIGINT/SIGTERM request a safe stop rather than abandoning the run), systemd
watchdog/notify integration, and best-effort outbound monitoring (ntfy +
Cronitor) that can never itself halt a campaign - every outbound call
in `HttpNotifier` logs and swallows failures rather than raising. `SafeOff` is
invoked on every lifecycle exit via `_execute_campaign`'s `finally` block,
independent of how the campaign ended.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import socket
import time
from typing import Callable, Sequence
from urllib import parse as urlparse
from urllib import request as urlrequest

from ccid.config import AppConfig, load_config
from ccid.hal import CameraReal, CameraRealConfig, CameraSim, GpioRealContactorController, GpioSimContactorController, ScopeReal, ScopeSim
from ccid.recorder import RunRecorder, RunState
from ccid.safety import safe_off
from ccid.sequencer import Sequencer, SequencerRunResult
from ccid.states import Terminal

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_TARGET_CYCLES = 6000
DEFAULT_SCOPE_RESOURCE_ENV = "CCID_SCOPE_RESOURCE"
DEFAULT_NTFY_TOPIC_URL_ENV = "CCID_NTFY_TOPIC_URL"


class StopRequested(RuntimeError):
    pass


@dataclass(frozen=True)
class HalBundle:
    contactors: object
    scope: object
    camera: object


class LifecycleSignals:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or LOGGER
        self._requested: str | None = None
        self._previous: dict[int, object] = {}

    @property
    def requested(self) -> str | None:
        return self._requested

    def install(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._previous[sig] = signal.getsignal(sig)
            signal.signal(sig, self._handle)

    def restore(self) -> None:
        for sig, previous in self._previous.items():
            signal.signal(sig, previous)
        self._previous.clear()

    def check(self) -> None:
        if self._requested is not None:
            raise StopRequested(self._requested)

    def _handle(self, signum: int, _frame) -> None:
        name = signal.Signals(signum).name
        self._requested = name
        self._logger.warning("Received signal %s; will stop safely", name)


class SystemdNotifier:
    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        sender: Callable[[str, bytes], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._env = environ if environ is not None else os.environ
        self._logger = logger or LOGGER
        self._sender = sender or self._send_unix_datagram
        self._notify_socket = self._env.get("NOTIFY_SOCKET")
        watchdog_usec = self._env.get("WATCHDOG_USEC")
        self._watchdog_interval_s = None
        if watchdog_usec:
            try:
                # Ping at half of systemd's configured WatchdogSec, per
                # systemd's own documented convention - pinging at the full
                # interval leaves no margin if a single ping is ever late.
                self._watchdog_interval_s = max(1.0, int(watchdog_usec) / 1_000_000.0 / 2.0)
            except ValueError:
                self._logger.warning("Invalid WATCHDOG_USEC value; watchdog disabled")

    def ready(self) -> None:
        self._notify("READY=1")

    def stopping(self) -> None:
        self._notify("STOPPING=1")

    def watchdog_ping(self) -> None:
        if self._watchdog_interval_s is None:
            return
        self._notify("WATCHDOG=1")

    def sleep(self, seconds: float, *, stop_check: Callable[[], None] | None = None) -> None:
        remaining = max(0.0, seconds)
        if self._watchdog_interval_s is None:
            if stop_check is not None:
                stop_check()
            time.sleep(remaining)
            if stop_check is not None:
                stop_check()
            return
        step = self._watchdog_interval_s
        while remaining > 0.0:
            if stop_check is not None:
                stop_check()
            chunk = min(step, remaining)
            time.sleep(chunk)
            self.watchdog_ping()
            remaining -= chunk
        if stop_check is not None:
            stop_check()

    def _notify(self, payload: str) -> None:
        if not self._notify_socket:
            return
        try:
            self._sender(self._notify_socket, payload.encode("utf-8"))
        except Exception as exc:  # pragma: no cover - environment dependent
            self._logger.warning("systemd notify failed: %s", exc)

    @staticmethod
    def _send_unix_datagram(address: str, payload: bytes) -> None:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(payload)


class HttpNotifier:
    """`cronitor_url` is a Cronitor telemetry ping URL

    (`https://cronitor.link/p/<API_KEY>/<monitor-key>`, see
    https://cronitor.io/docs/heartbeat-monitoring). A bare ping means "still
    alive"; Cronitor's own configured expected-frequency alerting is the
    dead-man's-switch (matches this project's prior healthchecks.io usage).
    `?state=fail` is Cronitor's explicit failure signal. Cronitor discards
    POST bodies, so all context travels in the query string via `message=`.
    """

    def __init__(
        self,
        *,
        cronitor_url: str | None,
        ntfy_topic_url: str | None,
        opener: Callable[..., object] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._cronitor_url = cronitor_url
        self._ntfy_topic_url = ntfy_topic_url
        self._opener = opener or urlrequest.urlopen
        self._logger = logger or LOGGER

    def notify_start(self, run_id: str, cycle_start: int, cycle_target: int) -> None:
        self._notify_ntfy("CCID start", f"run={run_id} cycle={cycle_start}/{cycle_target}")

    def notify_resume(self, run_id: str, last_completed_cycle: int, cycle_target: int) -> None:
        self._notify_ntfy("CCID resume", f"run={run_id} last_completed={last_completed_cycle}/{cycle_target}")

    def notify_fault(self, run_id: str, cycle_index: int, reason: str) -> None:
        self._notify_ntfy("CCID fault", f"run={run_id} cycle={cycle_index} reason={reason}")

    def notify_complete(self, run_id: str, cycle_target: int) -> None:
        self._notify_ntfy("CCID complete", f"run={run_id} cycles={cycle_target}")

    def heartbeat(self, run_id: str, last_completed_cycle: int) -> None:
        if not self._cronitor_url:
            return
        query = urlparse.urlencode(
            {"message": f"run_id={run_id} last_completed_cycle={last_completed_cycle}"}
        )
        self._http_request(
            f"{self._cronitor_url}?{query}",
            method="GET",
            failure_is_warning=True,
        )

    def heartbeat_fail(self, run_id: str, last_completed_cycle: int, reason: str) -> None:
        if not self._cronitor_url:
            return
        query = urlparse.urlencode(
            {
                "state": "fail",
                "message": (
                    f"run_id={run_id} last_completed_cycle={last_completed_cycle} reason={reason}"
                ),
            }
        )
        self._http_request(
            f"{self._cronitor_url}?{query}",
            method="GET",
            failure_is_warning=True,
        )

    def _notify_ntfy(self, title: str, body: str) -> None:
        if not self._ntfy_topic_url:
            return
        self._http_request(
            self._ntfy_topic_url,
            data=body.encode("utf-8"),
            method="POST",
            headers={"Title": title},
            failure_is_warning=True,
        )

    def _http_request(
        self,
        url: str,
        *,
        data: bytes | None = None,
        method: str,
        headers: dict[str, str] | None = None,
        failure_is_warning: bool,
    ) -> None:
        req = urlrequest.Request(url, data=data, method=method, headers=headers or {})
        try:
            with self._opener(req, timeout=5):
                return
        except Exception as exc:
            if failure_is_warning:
                self._logger.warning("Outbound monitoring/notification failed: %s", type(exc).__name__)
                return
            raise


def build_hal_bundle(
    config: AppConfig,
    *,
    scope_resource: str | None,
    monotonic_now: Callable[[], float],
    scope_sim_scenario=None,
    camera_sim_kwargs: dict | None = None,
    gpio_output_factory=None,
    resource_manager_factory=None,
    capture_factory=None,
    camera_classifier=None,
) -> HalBundle:
    from ccid.hal.scope_sim import ScopeSimScenario

    if config.modes.gpio_mode == "sim":
        contactors = GpioSimContactorController(monotonic_now=monotonic_now)
    else:
        contactors = GpioRealContactorController(
            gpio_k1=config.gpio.k1,
            gpio_k2=config.gpio.k2,
            gpio_k3=config.gpio.k3,
            monotonic_now=monotonic_now,
            output_factory=gpio_output_factory,
        )

    if config.modes.scope_mode == "sim":
        scenario = scope_sim_scenario or ScopeSimScenario(sample_rate_hz=200_000.0, sample_count=50_000)
        scope = ScopeSim(scenario=scenario, monotonic_now=monotonic_now)
    else:
        if not scope_resource:
            raise ValueError(f"Environment variable {DEFAULT_SCOPE_RESOURCE_ENV} is required for real scope mode")
        scope = ScopeReal(
            resource=scope_resource,
            monotonic_now=monotonic_now,
            resource_manager_factory=resource_manager_factory,
        )

    if config.modes.camera_mode == "sim":
        camera = CameraSim(monotonic_now=monotonic_now, **(camera_sim_kwargs or {}))
    else:
        camera = CameraReal(
            config=CameraRealConfig(device_index=config.camera.device_index),
            monotonic_now=monotonic_now,
            capture_factory=capture_factory,
            state_classifier=camera_classifier,
        )
    return HalBundle(contactors=contactors, scope=scope, camera=camera)


def latest_run_dir(run_root: Path) -> Path | None:
    if not run_root.exists():
        return None
    candidates = [child for child in run_root.iterdir() if child.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.name)[-1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccid")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--log-level", default="INFO")

    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--target-cycles", type=int, default=DEFAULT_TARGET_CYCLES)
    start.add_argument("--run-id")

    resume = sub.add_parser("resume")
    resume.add_argument("--run-id")
    resume.add_argument("--latest", action="store_true")
    resume.add_argument("--allow-config-hash-override", action="store_true")
    resume.add_argument("--allow-halted-resume", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--run-id")
    status.add_argument("--latest", action="store_true")

    simulate = sub.add_parser("simulate")
    simulate.add_argument("--target-cycles", type=int, default=10)
    simulate.add_argument("--run-id")
    simulate.add_argument(
        "--scope-fault",
        choices=["none", "never_triggered", "no_trip", "pretrigger_leakage"],
        default="none",
    )
    simulate.add_argument("--camera-fail-after", type=int)

    return parser


def configure_logging(level_text: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_text.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def generate_run_id(now: Callable[[], datetime] | None = None) -> str:
    clock = now or (lambda: datetime.now(tz=timezone.utc))
    return clock().strftime("%Y%m%d_%H%M%S")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    config = load_config(args.config)
    lifecycle = LifecycleSignals()
    watchdog = SystemdNotifier()
    notifier = HttpNotifier(
        cronitor_url=config.resolve_cronitor_url(),
        ntfy_topic_url=os.environ.get(DEFAULT_NTFY_TOPIC_URL_ENV),
    )
    recorder = RunRecorder(config.paths.run_root, heartbeat_sender=notifier.heartbeat)

    if args.command == "status":
        return _cmd_status(args, config, recorder)

    lifecycle.install()
    watchdog.ready()
    try:
        if args.command == "start":
            return _cmd_start(args, config, recorder, notifier, lifecycle, watchdog)
        if args.command == "resume":
            return _cmd_resume(args, config, recorder, notifier, lifecycle, watchdog)
        if args.command == "simulate":
            return _cmd_simulate(args, config, recorder, notifier, lifecycle, watchdog)
        raise ValueError(f"Unknown command {args.command}")
    finally:
        watchdog.stopping()
        lifecycle.restore()


def _cmd_start(args, config: AppConfig, recorder: RunRecorder, notifier: HttpNotifier, lifecycle: LifecycleSignals, watchdog: SystemdNotifier) -> int:
    run_id = args.run_id or generate_run_id()
    config_text = Path(args.config).read_text(encoding="utf-8")
    run_dir = recorder.initialize_run(
        run_id=run_id,
        target_cycles=args.target_cycles,
        config_hash=config.canonical_hash(),
        frozen_config_yaml=config_text,
    )
    state = recorder.load_run_state(run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True)
    notifier.notify_start(run_id, state.last_completed_cycle + 1, state.target_cycles)
    return _execute_campaign(
        config=config,
        recorder=recorder,
        notifier=notifier,
        lifecycle=lifecycle,
        watchdog=watchdog,
        run_dir=run_dir,
        state=state,
    )


def _cmd_resume(args, config: AppConfig, recorder: RunRecorder, notifier: HttpNotifier, lifecycle: LifecycleSignals, watchdog: SystemdNotifier) -> int:
    run_dir = _resolve_run_dir(config.paths.run_root, run_id=args.run_id, latest=args.latest)
    if run_dir is None:
        raise FileNotFoundError("No run directory found to resume")
    if args.allow_config_hash_override:
        state = recorder.read_run_state_unchecked(run_dir)
    else:
        state = recorder.load_run_state(
            run_dir,
            expected_config_hash=config.canonical_hash(),
            allow_halted_resume=args.allow_halted_resume,
        )
    recorder.reconcile_orphans(run_dir, state)
    notifier.notify_resume(state.run_id, state.last_completed_cycle, state.target_cycles)
    return _execute_campaign(
        config=config,
        recorder=recorder,
        notifier=notifier,
        lifecycle=lifecycle,
        watchdog=watchdog,
        run_dir=run_dir,
        state=state,
    )


def _cmd_simulate(args, config: AppConfig, recorder: RunRecorder, notifier: HttpNotifier, lifecycle: LifecycleSignals, watchdog: SystemdNotifier) -> int:
    if not (config.modes.gpio_mode == config.modes.scope_mode == config.modes.camera_mode == "sim"):
        raise ValueError("simulate requires gpio_mode, scope_mode, and camera_mode all set to sim")
    from ccid.hal.scope_sim import ScopeSimScenario

    scenario_kwargs = {}
    if args.scope_fault == "never_triggered":
        scenario_kwargs["never_triggered"] = True
    elif args.scope_fault == "no_trip":
        scenario_kwargs["no_trip"] = True
    elif args.scope_fault == "pretrigger_leakage":
        scenario_kwargs["pretrigger_leakage"] = True
        scenario_kwargs["no_trip"] = True
    run_id = args.run_id or generate_run_id()
    config_text = Path(args.config).read_text(encoding="utf-8")
    run_dir = recorder.initialize_run(
        run_id=run_id,
        target_cycles=args.target_cycles,
        config_hash=config.canonical_hash(),
        frozen_config_yaml=config_text,
    )
    state = recorder.load_run_state(run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True)
    notifier.notify_start(run_id, state.last_completed_cycle + 1, state.target_cycles)
    return _execute_campaign(
        config=config,
        recorder=recorder,
        notifier=notifier,
        lifecycle=lifecycle,
        watchdog=watchdog,
        run_dir=run_dir,
        state=state,
        scope_sim_scenario=ScopeSimScenario(**scenario_kwargs),
        camera_sim_kwargs={"fail_after_samples": args.camera_fail_after} if args.camera_fail_after is not None else None,
    )


def _execute_campaign(
    *,
    config: AppConfig,
    recorder: RunRecorder,
    notifier: HttpNotifier,
    lifecycle: LifecycleSignals,
    watchdog: SystemdNotifier,
    run_dir: Path,
    state: RunState,
    scope_sim_scenario=None,
    camera_sim_kwargs: dict | None = None,
) -> int:
    scope_resource = os.environ.get(DEFAULT_SCOPE_RESOURCE_ENV)
    bundle = build_hal_bundle(
        config,
        scope_resource=scope_resource,
        monotonic_now=time.monotonic,
        scope_sim_scenario=scope_sim_scenario,
        camera_sim_kwargs=camera_sim_kwargs,
    )
    sequencer = Sequencer(
        config=config,
        contactors=bundle.contactors,
        scope=bundle.scope,
        camera=bundle.camera,
        recorder=recorder,
        monotonic_now=time.monotonic,
        sleep=lambda seconds: watchdog.sleep(seconds, stop_check=lifecycle.check),
    )

    try:
        bundle.camera.start()
        result = sequencer.run(run_dir=run_dir, state=state)
    except StopRequested as exc:
        safe_off(bundle.contactors)
        notifier.notify_fault(state.run_id, state.last_completed_cycle, f"controller_stop:{exc}")
        return 130
    finally:
        try:
            bundle.camera.stop()
        finally:
            try:
                safe_off(bundle.contactors)
            except Exception:
                pass

    return _finalize_result(notifier, result)


def _finalize_result(notifier: HttpNotifier, result: SequencerRunResult) -> int:
    # Exit code contract for the systemd unit / operator scripts:
    #   0 = campaign completed its target cycle count
    #   2 = DUT no-trip halt
    #   3 = rig fault halt
    #   4 = any other halt (persistence/controller/peripheral fault)
    #   130 = stopped by SIGINT/SIGTERM (see StopRequested in _execute_campaign)
    run_id = result.state.run_id
    if result.terminal is Terminal.COMPLETE:
        notifier.notify_complete(run_id, result.state.target_cycles)
        return 0
    notifier.notify_fault(run_id, result.state.last_completed_cycle, result.halt_reason or "halted")
    notifier.heartbeat_fail(run_id, result.state.last_completed_cycle, result.halt_reason or "halted")
    if result.terminal is Terminal.NO_TRIP:
        return 2
    if result.terminal is Terminal.RIG_FAULT:
        return 3
    return 4


def _cmd_status(args, config: AppConfig, recorder: RunRecorder) -> int:
    run_dir = _resolve_run_dir(config.paths.run_root, run_id=args.run_id, latest=args.latest)
    if run_dir is None:
        print(json.dumps({"status": "no_run_found"}))
        return 1
    state = recorder.read_run_state_unchecked(run_dir)
    payload = {
        "run_dir": str(run_dir),
        "run_state": asdict(state),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _resolve_run_dir(run_root: Path, *, run_id: str | None, latest: bool) -> Path | None:
    if run_id:
        candidate = run_root / run_id
        return candidate if candidate.exists() else None
    if latest:
        return latest_run_dir(run_root)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
