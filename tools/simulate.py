"""Accelerated simulated-campaign runner for commissioning validation.

This tool always uses the simulated GPIO/scope/camera HALs; it never touches
real hardware regardless of what `config.yaml` says. It exists to exercise the
full `Sequencer` + `RunRecorder` stack at accelerated (non-real) time so fault
handling, crash-safe persistence, and safe-off opening order can be checked
before or alongside the unit test suite.

The `crash-resume` subcommand injects a single Python exception at a named
`RunRecorder` commit checkpoint. That models a software crash mid-persistence
(the property `tests/test_resume.py` already covers at the recorder level);
it is not a substitute for testing power loss or `kill -9` against the real
process, which is called out explicitly in `coding_instructions.txt` as a
deployment-validation item, not something a unit test can prove.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from ccid.classify import LedColor, frames_to_bgr_bytes, make_blinking_sequence
from ccid.config import AppConfig, load_config
from ccid.errors import ResumeBlockedError
from ccid.hal.base import ContactorName
from ccid.hal.camera_sim import CameraSim, CameraSimFrameFixture
from ccid.hal.gpio_sim import GpioSimContactorController
from ccid.hal.scope_sim import ScopeSim, ScopeSimScenario
from ccid.recorder import RunRecorder, RunState
from ccid.sequencer import Sequencer, SequencerRunResult
from ccid.states import LedState, Terminal

LOGGER = logging.getLogger("tools.simulate")


class SimulatedCrash(RuntimeError):
    """Raised by CrashInjector to emulate a software crash mid-commit."""


class ManualClock:
    """Deterministic, instantly-advancing monotonic clock for accelerated runs."""

    def __init__(self, start_s: float = 0.0) -> None:
        self._now_s = start_s

    def now(self) -> float:
        return self._now_s

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        self._now_s += seconds


class CrashInjector:
    """Raises `SimulatedCrash` the first time cycle `target_cycle` reaches
    `target_checkpoint` during `RunRecorder.record_cycle`.

    Cycles are counted by observing the "after_artifacts" checkpoint, which is
    the first checkpoint reached on every `record_cycle` call, so
    `target_cycle` lines up with the 1-based cycle index being committed.
    """

    def __init__(self, *, target_cycle: int, target_checkpoint: str) -> None:
        if target_cycle < 1:
            raise ValueError("target_cycle must be >= 1")
        self._target_cycle = target_cycle
        self._target_checkpoint = target_checkpoint
        self._commits_started = 0
        self._triggered = False

    def __call__(self, step_name: str) -> None:
        if step_name == "after_artifacts":
            self._commits_started += 1
        if (
            not self._triggered
            and self._commits_started == self._target_cycle
            and step_name == self._target_checkpoint
        ):
            self._triggered = True
            raise SimulatedCrash(
                f"injected crash at cycle {self._target_cycle}, checkpoint {step_name}"
            )

    @property
    def triggered(self) -> bool:
        return self._triggered


def default_camera_fixtures(frame_count: int = 240) -> list[CameraSimFrameFixture]:
    """Blinking-green (charging) frames long enough to satisfy the vision
    gate's ~3 s agreement window well within `boot_timeout_s`.

    `CameraSim`'s default fixtures use raw near-black bytes that read as
    LED "off" once actually HSV-classified (the fixture's `led_state` label
    is not consulted by `classify.await_charging_gate`), so a campaign run
    with plain defaults never observes charging and always halts on a vision
    gate timeout. Real hue-bearing frames are required to exercise the normal
    cycle path.
    """

    frames = make_blinking_sequence(LedColor.GREEN, frame_count, on_frames=7, off_frames=7)
    return [
        CameraSimFrameFixture(
            led_state=LedState.CHARGING,
            frame_bgr=frames_to_bgr_bytes(frame),
            width=frame.shape[1],
            height=frame.shape[0],
        )
        for frame in frames
    ]


# Matches ccid.main.build_hal_bundle's default scenario override: the
# ScopeSimScenario dataclass default (20_000 samples at 10 MS/s) is a 2 ms
# record, shorter than the scenario's own 20 ms pretrigger window. A record
# long enough to hold a full no-trip window (100 ms) after t=0 is required
# for the analysis sanity checks to pass on a normal, non-faulted cycle.
_DEFAULT_SCOPE_SAMPLE_RATE_HZ = 200_000.0
_DEFAULT_SCOPE_SAMPLE_COUNT = 50_000


def default_scope_scenario(**overrides: object) -> ScopeSimScenario:
    return ScopeSimScenario(
        sample_rate_hz=_DEFAULT_SCOPE_SAMPLE_RATE_HZ,
        sample_count=_DEFAULT_SCOPE_SAMPLE_COUNT,
        **overrides,
    )


def build_sim_bundle(
    *,
    clock: ManualClock,
    scope_scenario: ScopeSimScenario | None = None,
    camera_fail_after: int | None = None,
    gpio_fail_operations: dict[str, int] | None = None,
) -> tuple[GpioSimContactorController, ScopeSim, CameraSim]:
    contactors = GpioSimContactorController(monotonic_now=clock.now)
    for operation, count in (gpio_fail_operations or {}).items():
        contactors.inject_failure(operation, count)
    scope = ScopeSim(scenario=scope_scenario or default_scope_scenario(), monotonic_now=clock.now)
    camera = CameraSim(
        monotonic_now=clock.now,
        fixture_sequence=default_camera_fixtures(),
        fail_after_samples=camera_fail_after,
    )
    return contactors, scope, camera


def run_campaign(
    *,
    config: AppConfig,
    recorder: RunRecorder,
    run_dir: Path,
    state: RunState,
    clock: ManualClock,
    scope_scenario: ScopeSimScenario | None = None,
    camera_fail_after: int | None = None,
    gpio_fail_operations: dict[str, int] | None = None,
) -> tuple[SequencerRunResult, GpioSimContactorController]:
    contactors, scope, camera = build_sim_bundle(
        clock=clock,
        scope_scenario=scope_scenario,
        camera_fail_after=camera_fail_after,
        gpio_fail_operations=gpio_fail_operations,
    )
    sequencer = Sequencer(
        config=config,
        contactors=contactors,
        scope=scope,
        camera=camera,
        recorder=recorder,
        monotonic_now=clock.now,
        sleep=clock.sleep,
    )
    camera.start()
    try:
        result = sequencer.run(run_dir=run_dir, state=state)
    finally:
        camera.stop()
    return result, contactors


def opening_order_is_safe(contactors: GpioSimContactorController) -> bool:
    """True if the most recent commanded-open events respect K3, then K2/K1."""

    order = contactors.recent_open_order(count=3)
    if not order:
        return True
    positions = {name: idx for idx, name in enumerate(order)}
    pairs = (
        (ContactorName.K3, ContactorName.K2),
        (ContactorName.K3, ContactorName.K1),
        (ContactorName.K2, ContactorName.K1),
    )
    for first, second in pairs:
        if first in positions and second in positions and positions[first] > positions[second]:
            return False
    return True


def no_skipped_cycles(run_dir: Path) -> bool:
    """True if `cycles.csv` contains exactly the contiguous run 1..N with no gaps or dupes."""

    csv_path = run_dir / "cycles.csv"
    if not csv_path.exists():
        return True
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        indices = sorted(int(row["cycle_index"]) for row in csv.DictReader(handle))
    return indices == list(range(1, len(indices) + 1))


def _scope_scenario_from_args(args: argparse.Namespace) -> ScopeSimScenario:
    fault = getattr(args, "scope_fault", "none")
    kwargs: dict[str, bool] = {}
    if fault == "never_triggered":
        kwargs["never_triggered"] = True
    elif fault == "no_trip":
        kwargs["no_trip"] = True
    elif fault == "pretrigger_leakage":
        kwargs["pretrigger_leakage"] = True
        kwargs["no_trip"] = True
    return default_scope_scenario(**kwargs)


def _parse_gpio_fail(specs: list[str] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for spec in specs or []:
        if ":" not in spec:
            raise ValueError(f"--gpio-fail must be OPERATION:COUNT, got {spec!r}")
        operation, count_text = spec.split(":", 1)
        result[operation] = int(count_text)
    return result


def cmd_campaign(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    recorder = RunRecorder(Path(args.run_root))
    run_id = args.run_id or "sim_campaign"
    config_text = Path(args.config).read_text(encoding="utf-8")
    run_dir = recorder.initialize_run(
        run_id=run_id,
        target_cycles=args.cycles,
        config_hash=config.canonical_hash(),
        frozen_config_yaml=config_text,
    )
    state = recorder.load_run_state(
        run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True
    )
    result, contactors = run_campaign(
        config=config,
        recorder=recorder,
        run_dir=run_dir,
        state=state,
        clock=ManualClock(),
        scope_scenario=_scope_scenario_from_args(args),
        camera_fail_after=args.camera_fail_after,
        gpio_fail_operations=_parse_gpio_fail(args.gpio_fail),
    )
    report = {
        "run_dir": str(run_dir),
        "terminal": result.terminal.value,
        "halt_reason": result.halt_reason,
        "cycles_completed": result.state.last_completed_cycle,
        "pass_count": result.state.pass_count,
        "fail_count": result.state.fail_count,
        "latch_slow_clear_count": result.latch_slow_clear_count,
        "opening_order_safe": opening_order_is_safe(contactors),
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if result.terminal == Terminal.COMPLETE else 1


def cmd_crash_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_root = Path(args.run_root)
    run_id = args.run_id or "sim_crash_resume"
    config_text = Path(args.config).read_text(encoding="utf-8")
    scope_scenario = _scope_scenario_from_args(args)

    injector = CrashInjector(target_cycle=args.crash_cycle, target_checkpoint=args.crash_checkpoint)
    crashing_recorder = RunRecorder(run_root, crash_injector=injector)
    run_dir = crashing_recorder.initialize_run(
        run_id=run_id,
        target_cycles=args.cycles,
        config_hash=config.canonical_hash(),
        frozen_config_yaml=config_text,
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
            scope_scenario=scope_scenario,
        )
    except SimulatedCrash as exc:
        LOGGER.info("Simulated crash occurred as requested: %s", exc)

    if not injector.triggered:
        print(json.dumps({"ok": False, "reason": "crash_injection_did_not_trigger"}, sort_keys=True))
        return 1

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
        scope_scenario=scope_scenario,
    )
    report = {
        "ok": no_skipped_cycles(run_dir) and opening_order_is_safe(contactors),
        "crash_cycle": args.crash_cycle,
        "crash_checkpoint": args.crash_checkpoint,
        "last_completed_cycle_before_crash": pre_resume_state.last_completed_cycle,
        "last_completed_cycle_after_resume": result.state.last_completed_cycle,
        "terminal": result.terminal.value,
        "opening_order_safe": opening_order_is_safe(contactors),
        "no_skipped_cycles": no_skipped_cycles(run_dir),
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


def cmd_sticky_halt_check(args: argparse.Namespace) -> int:
    """Force a rig-fault halt (scope never triggers) and confirm resume is refused."""

    config = load_config(args.config)
    recorder = RunRecorder(Path(args.run_root))
    run_id = args.run_id or "sim_sticky_halt"
    config_text = Path(args.config).read_text(encoding="utf-8")
    run_dir = recorder.initialize_run(
        run_id=run_id,
        target_cycles=args.cycles,
        config_hash=config.canonical_hash(),
        frozen_config_yaml=config_text,
    )
    state = recorder.load_run_state(
        run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=True
    )
    result, _contactors = run_campaign(
        config=config,
        recorder=recorder,
        run_dir=run_dir,
        state=state,
        clock=ManualClock(),
        scope_scenario=ScopeSimScenario(never_triggered=True),
    )
    blocked = False
    try:
        recorder.load_run_state(
            run_dir, expected_config_hash=config.canonical_hash(), allow_halted_resume=False
        )
    except ResumeBlockedError:
        blocked = True
    report = {
        "ok": blocked and result.terminal != Terminal.COMPLETE,
        "terminal": result.terminal.value,
        "halt_reason": result.halt_reason,
        "resume_without_override_blocked": blocked,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.simulate",
        description=(
            "Accelerated simulated-campaign runner for commissioning validation. "
            "Always uses simulated GPIO/scope/camera HALs; never touches real hardware."
        ),
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command", required=True)

    campaign = sub.add_parser("campaign", help="Run an accelerated simulated campaign")
    campaign.add_argument("--run-root", required=True)
    campaign.add_argument("--run-id")
    campaign.add_argument("--cycles", type=int, default=10)
    campaign.add_argument(
        "--scope-fault",
        choices=["none", "never_triggered", "no_trip", "pretrigger_leakage"],
        default="none",
    )
    campaign.add_argument("--camera-fail-after", type=int)
    campaign.add_argument("--gpio-fail", action="append", metavar="OPERATION:COUNT")
    campaign.set_defaults(func=cmd_campaign)

    crash_resume = sub.add_parser(
        "crash-resume",
        help="Run a campaign that crashes at a chosen commit checkpoint, then verify resume",
    )
    crash_resume.add_argument("--run-root", required=True)
    crash_resume.add_argument("--run-id")
    crash_resume.add_argument("--cycles", type=int, default=5)
    crash_resume.add_argument("--crash-cycle", type=int, required=True)
    crash_resume.add_argument(
        "--crash-checkpoint",
        choices=["after_artifacts", "after_csv", "after_runstate", "after_heartbeat"],
        required=True,
    )
    crash_resume.add_argument(
        "--scope-fault",
        choices=["none", "never_triggered", "no_trip", "pretrigger_leakage"],
        default="none",
    )
    crash_resume.set_defaults(func=cmd_crash_resume)

    sticky_halt = sub.add_parser(
        "sticky-halt-check",
        help="Force a rig-fault halt and verify resume is refused without an explicit override",
    )
    sticky_halt.add_argument("--run-root", required=True)
    sticky_halt.add_argument("--run-id")
    sticky_halt.add_argument("--cycles", type=int, default=5)
    sticky_halt.set_defaults(func=cmd_sticky_halt_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
