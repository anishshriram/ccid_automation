"""Oscilloscope commissioning/bench tool (Phase 11).

Defaults to the deterministic scope simulator; real hardware requires
`--real` plus a VISA resource string (via `--resource` or the
`CCID_SCOPE_RESOURCE` environment variable, matching `ccid.main`). Every
SCPI command stays inside `ccid.hal.scope_real.ScopeReal` / the
`ScopeInterface` contract; this tool only calls the public HAL methods, the
same ones the sequencer uses, so bench results reflect what the sequencer
will actually see.

Note on transfer timing: `ScopeInterface.capture_after_acquire()` bundles the
BYTE waveform, preamble, and PNG transfer into one call by contract. This
tool times that call as a whole and reports each artifact's size; splitting
BYTE-transfer time from PNG-transfer time would require extending
`ScopeInterface`, which is out of scope here, so the two are not reported
separately.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import time
import zipfile

from ccid.analysis import load_waveform
from ccid.hal.base import ScopeInterface, ScopeSettings, WaveformCapture
from ccid.hal.scope_real import ScopeReal
from ccid.hal.scope_sim import ScopeSim, ScopeSimScenario

LOGGER = logging.getLogger("tools.scope_bench")

DEFAULT_SCOPE_RESOURCE_ENV = "CCID_SCOPE_RESOURCE"


def build_scope(*, real: bool, resource: str | None, monotonic_now) -> ScopeInterface:
    if not real:
        return ScopeSim(
            scenario=ScopeSimScenario(sample_rate_hz=200_000.0, sample_count=1_000_000),
            monotonic_now=monotonic_now,
        )
    if not resource:
        raise SystemExit(
            f"--real requires a VISA resource: pass --resource or set {DEFAULT_SCOPE_RESOURCE_ENV}"
        )
    return ScopeReal(resource=resource, monotonic_now=monotonic_now)


def identify_scope(scope: ScopeInterface) -> dict[str, object]:
    scope.connect()
    idn = scope.identify()
    return {"idn": idn}


def apply_and_readback(scope: ScopeInterface, settings: ScopeSettings) -> dict[str, object]:
    scope.connect()
    scope.configure_for_cycle(settings)
    readback = dict(scope.readback_settings())
    applied = {
        "timebase_scale_s_per_div": settings.timebase_scale_s_per_div,
        "trigger_level_v": settings.trigger_level_v,
        "waveform_points_mode": settings.waveform_points_mode,
        "waveform_points": settings.waveform_points,
        "waveform_format": settings.waveform_format,
        "waveform_source": settings.waveform_source,
    }
    return {"applied": applied, "readback": readback}


def verify_arm_polling(
    scope: ScopeInterface,
    *,
    timeout_s: float,
    monotonic_now,
    sleep,
    poll_interval_s: float = 0.01,
) -> dict[str, object]:
    scope.connect()
    scope.configure_for_cycle(ScopeSettings())
    start_s = monotonic_now()
    scope.arm_single()
    armed = False
    while monotonic_now() - start_s <= timeout_s:
        if scope.wait_until_armed(timeout_s=timeout_s, now_monotonic_s=monotonic_now()):
            armed = True
            break
        sleep(poll_interval_s)
    elapsed_s = monotonic_now() - start_s
    return {"armed": armed, "elapsed_s": elapsed_s, "timeout_s": timeout_s}


def query_memory_depth(scope: ScopeInterface) -> dict[str, object]:
    """Report the configured/reported memory-depth settings via the readback contract.

    `ScopeInterface` deliberately exposes domain settings rather than raw
    SCPI, so this reflects `:WAVeform:POINts[:MODE]?` as read back through
    `readback_settings()`, not a separate `:ACQuire:POINts?`-style maximum
    query (adding one would mean extending the interface, which this tool
    does not do).
    """

    scope.connect()
    scope.configure_for_cycle(ScopeSettings())
    readback = dict(scope.readback_settings())
    return {
        "waveform_points_mode": readback.get("waveform_points_mode"),
        "waveform_points": readback.get("waveform_points"),
    }


def time_capture(
    scope: ScopeInterface,
    *,
    monotonic_now,
) -> tuple[WaveformCapture, dict[str, object]]:
    start_s = monotonic_now()
    capture = scope.capture_after_acquire()
    elapsed_s = monotonic_now() - start_s
    return capture, {
        "elapsed_s": elapsed_s,
        "waveform_bytes": len(capture.samples),
        "png_bytes": len(capture.scope_png),
    }


def save_and_validate_capture(capture: WaveformCapture, out_dir, *, label: str = "bench") -> dict[str, object]:
    from pathlib import Path

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    waveform_path = out_path / f"{label}_waveform.npz"
    png_path = out_path / f"{label}_scope.png"
    settings_path = out_path / f"{label}_settings_readback.json"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("samples.bin", capture.samples)
        zf.writestr("preamble.json", json.dumps(dict(capture.preamble), sort_keys=True))
    waveform_bytes = buffer.getvalue()
    waveform_path.write_bytes(waveform_bytes)
    png_path.write_bytes(capture.scope_png)
    settings_path.write_text(
        json.dumps(dict(capture.settings_readback), sort_keys=True, indent=2), encoding="utf-8"
    )

    waveform = load_waveform(waveform_bytes)
    valid = waveform.samples_v.size > 0 and waveform.sample_interval_s > 0.0
    return {
        "waveform_path": str(waveform_path),
        "png_path": str(png_path),
        "settings_path": str(settings_path),
        "sample_count": int(waveform.samples_v.size),
        "sample_interval_s": waveform.sample_interval_s,
        "duration_s": waveform.duration_s,
        "valid": valid,
    }


def cmd_identify(args: argparse.Namespace) -> int:
    scope = build_scope(real=args.real, resource=_resolve_resource(args), monotonic_now=time.monotonic)
    report = identify_scope(scope)
    report["real"] = args.real
    report["resource"] = _resolve_resource(args)
    report["backend"] = "@py" if args.real else "sim"
    print(json.dumps(report, sort_keys=True))
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    scope = build_scope(real=args.real, resource=_resolve_resource(args), monotonic_now=time.monotonic)
    report = apply_and_readback(scope, ScopeSettings())
    print(json.dumps(report, sort_keys=True))
    return 0


def cmd_arm_check(args: argparse.Namespace) -> int:
    scope = build_scope(real=args.real, resource=_resolve_resource(args), monotonic_now=time.monotonic)
    report = verify_arm_polling(
        scope, timeout_s=args.timeout_s, monotonic_now=time.monotonic, sleep=time.sleep
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["armed"] else 1


def cmd_memory_depth(args: argparse.Namespace) -> int:
    scope = build_scope(real=args.real, resource=_resolve_resource(args), monotonic_now=time.monotonic)
    report = query_memory_depth(scope)
    print(json.dumps(report, sort_keys=True))
    return 0


def cmd_capture_bench(args: argparse.Namespace) -> int:
    scope = build_scope(real=args.real, resource=_resolve_resource(args), monotonic_now=time.monotonic)
    scope.connect()
    scope.configure_for_cycle(ScopeSettings())
    scope.arm_single()
    armed = _poll(
        lambda now: scope.wait_until_armed(timeout_s=args.timeout_s, now_monotonic_s=now),
        timeout_s=args.timeout_s,
    )
    if not armed:
        print(json.dumps({"armed": False, "reason": "arm_timeout"}, sort_keys=True))
        return 1
    acquired = _poll(
        lambda now: scope.wait_until_acquisition_complete(timeout_s=args.timeout_s, now_monotonic_s=now),
        timeout_s=args.timeout_s,
    )
    if not acquired:
        print(json.dumps({"armed": True, "acquired": False, "reason": "acquisition_timeout"}, sort_keys=True))
        return 1
    capture, timing = time_capture(scope, monotonic_now=time.monotonic)
    validation = save_and_validate_capture(capture, args.out_dir, label=args.label)
    report = {"armed": True, "acquired": True, "timing": timing, "validation": validation}
    print(json.dumps(report, sort_keys=True))
    return 0 if validation["valid"] else 1


def _poll(check, *, timeout_s: float, poll_interval_s: float = 0.01) -> bool:
    start_s = time.monotonic()
    while time.monotonic() - start_s <= timeout_s:
        if check(time.monotonic()):
            return True
        time.sleep(poll_interval_s)
    return False


def _resolve_resource(args: argparse.Namespace) -> str | None:
    return args.resource or os.environ.get(DEFAULT_SCOPE_RESOURCE_ENV)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--real", action="store_true", help="Drive a real scope instead of the simulator")
    parser.add_argument("--resource", default=None, help=f"VISA resource string (default: {DEFAULT_SCOPE_RESOURCE_ENV})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.scope_bench",
        description=(
            "Oscilloscope commissioning/bench tool. Defaults to the deterministic "
            "scope simulator; --real requires a VISA resource."
        ),
    )
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command", required=True)

    identify = sub.add_parser("identify", help="Connect and run *IDN?")
    _add_common_args(identify)
    identify.set_defaults(func=cmd_identify)

    configure = sub.add_parser("configure", help="Apply and read back the full per-cycle scope configuration")
    _add_common_args(configure)
    configure.set_defaults(func=cmd_configure)

    arm_check = sub.add_parser("arm-check", help="Issue :SINGle and verify armed-state polling")
    _add_common_args(arm_check)
    arm_check.add_argument("--timeout-s", type=float, default=2.0)
    arm_check.set_defaults(func=cmd_arm_check)

    memory_depth = sub.add_parser("memory-depth", help="Report the configured/reported waveform memory depth")
    _add_common_args(memory_depth)
    memory_depth.set_defaults(func=cmd_memory_depth)

    capture_bench = sub.add_parser(
        "capture-bench", help="Arm, acquire, time the transfer, and save+validate a bench capture"
    )
    _add_common_args(capture_bench)
    capture_bench.add_argument("--timeout-s", type=float, default=5.0)
    capture_bench.add_argument("--out-dir", default="./scope_bench_out")
    capture_bench.add_argument("--label", default="bench")
    capture_bench.set_defaults(func=cmd_capture_bench)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
