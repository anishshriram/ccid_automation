"""Guarded single-contactor commissioning tool (Phase 11).

Defaults to the simulated contactor controller and never touches real GPIO
unless the operator passes both `--real` and
`--i-understand-this-energizes-hardware`. All outputs start and end
de-energized; `close_k3` still requires K1 and K2 to already be commanded
closed and a fresh charging-gate token, so this tool cannot be used to bypass
the interlocks enforced in `ccid.hal.gpio_real` / `ccid.hal.gpio_sim` — it can
only drive them through their public, interlocked API.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

from ccid.config import AppConfig, load_config
from ccid.hal.base import ChargingGateToken, ContactorInterface, ContactorName
from ccid.hal.gpio_real import GpioRealContactorController
from ccid.hal.gpio_sim import GpioSimContactorController
from ccid.safety import safe_off

LOGGER = logging.getLogger("tools.gpio_selftest")

# Standard Raspberry Pi 40-pin header: BCM GPIO number -> physical pin number.
BCM_TO_PHYSICAL_PIN: dict[int, int] = {
    2: 3, 3: 5, 4: 7, 14: 8, 15: 10, 17: 11, 18: 12, 27: 13, 22: 15, 23: 16,
    24: 18, 10: 19, 9: 21, 25: 22, 11: 23, 8: 24, 7: 26, 0: 27, 1: 28, 5: 29,
    6: 31, 12: 32, 13: 33, 19: 35, 16: 36, 26: 37, 20: 38, 21: 40,
}


class WallClock:
    """Real monotonic clock and real sleeping, for driving actual hardware."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        time.sleep(seconds)


def gpio_pin_for(config: AppConfig, contactor: ContactorName) -> int:
    mapping = {ContactorName.K1: config.gpio.k1, ContactorName.K2: config.gpio.k2, ContactorName.K3: config.gpio.k3}
    return mapping[contactor]


def pin_info(config: AppConfig, contactor: ContactorName) -> dict[str, object]:
    bcm = gpio_pin_for(config, contactor)
    return {
        "contactor": contactor.value,
        "bcm_gpio": bcm,
        "physical_pin": BCM_TO_PHYSICAL_PIN.get(bcm),
    }


def build_contactors(
    *, config: AppConfig, real: bool, clock, output_factory=None
) -> ContactorInterface:
    if not real:
        return GpioSimContactorController(monotonic_now=clock.now)
    return GpioRealContactorController(
        gpio_k1=config.gpio.k1,
        gpio_k2=config.gpio.k2,
        gpio_k3=config.gpio.k3,
        monotonic_now=clock.now,
        output_factory=output_factory,
    )


def _close(contactors: ContactorInterface, contactor: ContactorName, gate: ChargingGateToken) -> None:
    if contactor is ContactorName.K1:
        contactors.close_k1()
    elif contactor is ContactorName.K2:
        contactors.close_k2()
    else:
        contactors.close_k3(gate)


def _open(contactors: ContactorInterface, contactor: ContactorName) -> None:
    if contactor is ContactorName.K1:
        contactors.open_k1()
    elif contactor is ContactorName.K2:
        contactors.open_k2()
    else:
        contactors.open_k3()


def exercise_contactor(
    contactors: ContactorInterface,
    contactor: ContactorName,
    *,
    pulses: int,
    hold_s: float,
    cooldown_s: float,
    clock,
) -> list[dict[str, object]]:
    """Close/open one contactor `pulses` times, always ending de-energized.

    Closing K3 requires K1 and K2 already commanded closed; this helper
    closes K1 and K2 first when `contactor is K3` and leaves them closed for
    the whole exercise, so the exercise engages the same interlock a real
    cycle would rather than bypassing it. K1/K2 exercises never touch K3.
    """

    if pulses < 1:
        raise ValueError("pulses must be >= 1")
    if hold_s < 0 or cooldown_s < 0:
        raise ValueError("hold_s and cooldown_s must be >= 0")

    events: list[dict[str, object]] = []
    safe_off(contactors)
    events.append({"action": "initial_safe_off", "at_monotonic_s": clock.now()})
    try:
        if contactor is ContactorName.K3:
            contactors.close_k1()
            events.append({"action": "close_k1_prereq", "at_monotonic_s": clock.now()})
            contactors.close_k2()
            events.append({"action": "close_k2_prereq", "at_monotonic_s": clock.now()})

        for pulse_index in range(1, pulses + 1):
            gate = ChargingGateToken(cycle_index=pulse_index, granted_at_monotonic_s=clock.now())
            _close(contactors, contactor, gate)
            events.append({"action": "close", "pulse": pulse_index, "at_monotonic_s": clock.now()})
            clock.sleep(hold_s)

            _open(contactors, contactor)
            events.append({"action": "open", "pulse": pulse_index, "at_monotonic_s": clock.now()})
            if pulse_index < pulses:
                clock.sleep(cooldown_s)
    finally:
        safe_off(contactors)
        events.append({"action": "final_safe_off", "at_monotonic_s": clock.now()})
    return events


def mismatch_probe(
    contactors: ContactorInterface,
    *,
    stagger_ms: int,
    clock,
    settle_margin_s: float = 0.01,
) -> dict[str, object]:
    """Close K1 only and confirm the configured stagger window is honored.

    The detector must not flag a mismatch before `stagger_ms` has elapsed
    (unless `stagger_ms` is 0, where it must flag immediately), and must flag
    one once the window is exceeded.
    """

    if stagger_ms < 0:
        raise ValueError("stagger_ms must be >= 0")
    try:
        contactors.close_k1()
        immediate = contactors.detect_mains_command_mismatch(
            allowed_stagger_ms=stagger_ms, now_monotonic_s=clock.now()
        )
        clock.sleep((stagger_ms / 1000.0) + settle_margin_s)
        after_window = contactors.detect_mains_command_mismatch(
            allowed_stagger_ms=stagger_ms, now_monotonic_s=clock.now()
        )
    finally:
        safe_off(contactors)
    ok = after_window and (immediate == (stagger_ms == 0))
    return {
        "stagger_ms": stagger_ms,
        "mismatch_detected_immediately": immediate,
        "mismatch_detected_after_window": after_window,
        "ok": ok,
    }


def _require_hardware_ack(args: argparse.Namespace) -> None:
    if args.real and not args.i_understand_this_energizes_hardware:
        raise SystemExit(
            "--real requires --i-understand-this-energizes-hardware: this will "
            "physically energize a mains or leakage-injection contactor"
        )


def cmd_show_pins(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    info = [pin_info(config, name) for name in ContactorName]
    print(json.dumps(info, sort_keys=True))
    return 0


def cmd_exercise(args: argparse.Namespace) -> int:
    _require_hardware_ack(args)
    config = load_config(args.config)
    contactor = ContactorName(args.contactor)
    clock = WallClock()
    contactors = build_contactors(config=config, real=args.real, clock=clock)
    print(json.dumps({"selected": pin_info(config, contactor), "real": args.real}, sort_keys=True))
    events = exercise_contactor(
        contactors,
        contactor,
        pulses=args.pulses,
        hold_s=args.hold_s,
        cooldown_s=args.cooldown_s,
        clock=clock,
    )
    print(json.dumps({"events": events}, sort_keys=True))
    return 0


def cmd_mismatch_test(args: argparse.Namespace) -> int:
    _require_hardware_ack(args)
    config = load_config(args.config)
    clock = WallClock()
    contactors = build_contactors(config=config, real=args.real, clock=clock)
    stagger_ms = args.stagger_ms if args.stagger_ms is not None else config.timing.mains_stagger_ms
    report = mismatch_probe(contactors, stagger_ms=stagger_ms, clock=clock)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.gpio_selftest",
        description=(
            "Guarded single-contactor exercise tool. Defaults to the simulated "
            "contactor controller; --real requires an explicit hardware acknowledgement."
        ),
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command", required=True)

    show_pins = sub.add_parser("show-pins", help="Print BCM GPIO and physical pin for K1/K2/K3")
    show_pins.set_defaults(func=cmd_show_pins)

    exercise = sub.add_parser("exercise", help="Close/open one contactor a bounded number of times")
    exercise.add_argument("--contactor", choices=[c.value for c in ContactorName], required=True)
    exercise.add_argument("--pulses", type=int, default=1)
    exercise.add_argument("--hold-s", type=float, default=1.0)
    exercise.add_argument("--cooldown-s", type=float, default=1.0)
    exercise.add_argument("--real", action="store_true", help="Drive real GPIO instead of the simulator")
    exercise.add_argument(
        "--i-understand-this-energizes-hardware",
        action="store_true",
        help="Required alongside --real to acknowledge physical hardware will be energized",
    )
    exercise.set_defaults(func=cmd_exercise)

    mismatch = sub.add_parser(
        "mismatch-test", help="Close K1 only and verify the mains_stagger_ms mismatch detector"
    )
    mismatch.add_argument("--stagger-ms", type=int, default=None, help="Default: config.yaml timing.mains_stagger_ms")
    mismatch.add_argument("--real", action="store_true", help="Drive real GPIO instead of the simulator")
    mismatch.add_argument(
        "--i-understand-this-energizes-hardware",
        action="store_true",
        help="Required alongside --real to acknowledge physical hardware will be energized",
    )
    mismatch.set_defaults(func=cmd_mismatch_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
