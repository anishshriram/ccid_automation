"""LED-status-camera calibration tool (Phase 11).

Computes a fixed ROI and proposed HSV hue ranges from operator-captured
still images of each LED state, verifies temporal classification against
those proposals, and packages captured frames into replay footage for
`ccid.hal.camera_sim.CameraSim`.

This tool never drives hardware; it only reads image files from disk (via
OpenCV, imported lazily so the rest of the tool works without it installed)
and writes JSON/replay artifacts. All classification logic is delegated to
`ccid.classify` so calibration numbers are produced by the same code path the
sequencer uses, not a parallel implementation.

The proposed HSV ranges and the "verified" flag are calibration aids for the
operator to review, not an automatic commit into `config.yaml`; nothing here
edits the frozen configuration or `LedOpticalConfig` defaults.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import json
import logging
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from ccid.classify import (
    DEFAULT_OPTICAL_CONFIG,
    HueRange,
    LedClassifier,
    LedColor,
    LedOpticalConfig,
    RegionOfInterest,
    center_roi,
    frames_to_bgr_bytes,
    rgb_to_hsv,
)
from ccid.hal.camera_sim import CameraSimFrameFixture
from ccid.states import LedState

LOGGER = logging.getLogger("tools.calibrate_camera")

_LED_STATE_FOR_LABEL: dict[str, LedState] = {
    "off": LedState.OFF_OR_UNKNOWN,
    "blue": LedState.READY,
    "green": LedState.CHARGING,
    "red": LedState.FAULTED,
    "booting": LedState.BOOTING,
}
_LED_COLOR_FOR_LABEL: dict[str, LedColor] = {
    "off": LedColor.OFF,
    "blue": LedColor.BLUE,
    "green": LedColor.GREEN,
    "red": LedColor.RED,
    "booting": LedColor.BOOTING,
}


# --------------------------------------------------------------------------
# ROI
# --------------------------------------------------------------------------


def parse_roi_arg(text: str | None) -> RegionOfInterest | None:
    if text is None:
        return None
    parts = text.split(",")
    if len(parts) != 4:
        raise ValueError("--roi must be 'x,y,width,height'")
    x, y, width, height = (int(part) for part in parts)
    return RegionOfInterest(x=x, y=y, width=width, height=height)


def resolve_roi(
    frame_shape: tuple[int, int], explicit: RegionOfInterest | None, fraction: float = 0.5
) -> RegionOfInterest:
    if explicit is not None:
        return explicit
    height, width = frame_shape[0], frame_shape[1]
    return center_roi(width, height, fraction)


def save_roi(roi: RegionOfInterest, path: Path) -> None:
    path.write_text(json.dumps(asdict(roi), sort_keys=True, indent=2), encoding="utf-8")


def load_roi(path: Path) -> RegionOfInterest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RegionOfInterest(
        x=int(payload["x"]), y=int(payload["y"]), width=int(payload["width"]), height=int(payload["height"])
    )


# --------------------------------------------------------------------------
# Proposed HSV hue ranges
# --------------------------------------------------------------------------


def lit_hues(
    frames: Sequence[np.ndarray], roi: RegionOfInterest, config: LedOpticalConfig
) -> np.ndarray:
    """Hue values (degrees) of pixels bright/saturated enough to count as "lit"."""

    collected: list[np.ndarray] = []
    for frame in frames:
        region = roi.crop(np.asarray(frame))
        hue, saturation, value = rgb_to_hsv(region)
        lit = (value >= config.min_value) & (saturation >= config.min_saturation)
        collected.append(hue[lit])
    if not collected:
        return np.array([], dtype=np.float64)
    return np.concatenate(collected)


def _circular_hue_range(hues_deg: np.ndarray, *, low_pct: float, high_pct: float) -> HueRange:
    """Percentile hue band, rotated to avoid a spurious 360/0 wraparound split.

    The circular mean is rotated to 180 deg first so a single-hue cluster
    (e.g. red, which straddles 0/360) does not get its percentile window
    artificially split across the wrap point.
    """

    if hues_deg.size == 0:
        raise ValueError("No lit pixels found; check the ROI, exposure, or min_value/min_saturation")
    radians = np.radians(hues_deg)
    mean_angle_deg = np.degrees(np.arctan2(np.mean(np.sin(radians)), np.mean(np.cos(radians)))) % 360.0
    shift = (180.0 - mean_angle_deg) % 360.0
    shifted = (hues_deg + shift) % 360.0
    low = float(np.percentile(shifted, low_pct))
    high = float(np.percentile(shifted, high_pct))
    return HueRange(low_deg=(low - shift) % 360.0, high_deg=(high - shift) % 360.0)


def propose_hue_range(
    frames: Sequence[np.ndarray],
    roi: RegionOfInterest,
    config: LedOpticalConfig,
    *,
    low_percentile: float = 5.0,
    high_percentile: float = 95.0,
) -> HueRange:
    hues = lit_hues(frames, roi, config)
    return _circular_hue_range(hues, low_pct=low_percentile, high_pct=high_percentile)


def propose_hue_ranges(
    frames_by_color: Mapping[LedColor, Sequence[np.ndarray]],
    roi: RegionOfInterest,
    config: LedOpticalConfig = DEFAULT_OPTICAL_CONFIG,
    **kwargs: float,
) -> dict[LedColor, HueRange]:
    return {color: propose_hue_range(frames, roi, config, **kwargs) for color, frames in frames_by_color.items()}


# --------------------------------------------------------------------------
# Temporal classification verification
# --------------------------------------------------------------------------


def verify_temporal_classification(
    frames_by_expected: Mapping[LedColor, Sequence[np.ndarray]],
    roi: RegionOfInterest,
    config: LedOpticalConfig = DEFAULT_OPTICAL_CONFIG,
) -> dict[str, dict[str, object]]:
    """Feed each captured sequence through `LedClassifier` and check agreement.

    Uses the same classifier the sequencer's `await_charging_gate` uses, so a
    "matched" result here means the real vision-gate path would also declare
    the expected state from this footage.
    """

    report: dict[str, dict[str, object]] = {}
    for expected_color, frames in frames_by_expected.items():
        classifier = LedClassifier(config, roi)
        window = None
        for frame in frames:
            window = classifier.observe(frame)
        stable = classifier.stable_color
        report[expected_color.value] = {
            "expected": expected_color.value,
            "stable_color": stable.value if stable is not None else None,
            "matched": stable == expected_color,
            "frames_observed": classifier.frames_observed,
            "final_window_confidence": window.confidence if window is not None else 0.0,
        }
    return report


# --------------------------------------------------------------------------
# Replay footage for CameraSim
# --------------------------------------------------------------------------


def build_replay_footage(
    sequences: Mapping[LedState, Sequence[np.ndarray]],
) -> list[CameraSimFrameFixture]:
    fixtures: list[CameraSimFrameFixture] = []
    for led_state, frames in sequences.items():
        for frame in frames:
            arr = np.asarray(frame)
            fixtures.append(
                CameraSimFrameFixture(
                    led_state=led_state,
                    frame_bgr=frames_to_bgr_bytes(arr),
                    width=int(arr.shape[1]),
                    height=int(arr.shape[0]),
                )
            )
    if not fixtures:
        raise ValueError("No frames provided to build replay footage from")
    return fixtures


def write_replay_file(fixtures: Sequence[CameraSimFrameFixture], path: Path) -> None:
    """Write footage in the exact schema `CameraSim(replay_file=...)` reads back."""

    payload = [
        {
            "led_state": fixture.led_state.name,
            "frame_bgr_base64": base64.b64encode(fixture.frame_bgr).decode("ascii"),
            "width": fixture.width,
            "height": fixture.height,
        }
        for fixture in fixtures
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------
# Image loading (real captured footage only; lazy OpenCV import)
# --------------------------------------------------------------------------


def load_frames_from_directory(directory: str | Path) -> list[np.ndarray]:
    try:
        import cv2  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency/environment
        raise RuntimeError(
            "opencv-python-headless is required to load frames from image files"
        ) from exc

    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Not a directory: {dir_path}")
    paths = sorted(p for p in dir_path.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"})
    if not paths:
        raise FileNotFoundError(f"No image files found in {dir_path}")
    frames: list[np.ndarray] = []
    for path in paths:
        bgr = cv2.imread(str(path))
        if bgr is None:
            raise RuntimeError(f"Could not read image: {path}")
        frames.append(bgr[..., ::-1].copy())
    return frames


def _load_label_dirs(args: argparse.Namespace, labels: Sequence[str]) -> dict[str, list[np.ndarray]]:
    result: dict[str, list[np.ndarray]] = {}
    for label in labels:
        directory = getattr(args, label, None)
        if directory is None:
            continue
        result[label] = load_frames_from_directory(directory)
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cmd_show_roi(args: argparse.Namespace) -> int:
    frames = load_frames_from_directory(args.frames_dir)
    explicit = parse_roi_arg(args.roi)
    roi = resolve_roi(frames[0].shape[:2], explicit, args.fraction)
    print(json.dumps(asdict(roi), sort_keys=True))
    if args.out:
        save_roi(roi, Path(args.out))
    return 0


def cmd_propose_hsv(args: argparse.Namespace) -> int:
    label_frames = _load_label_dirs(args, ("off", "blue", "green", "red"))
    if not label_frames:
        raise ValueError("At least one of --off/--blue/--green/--red is required")
    any_frames = next(iter(label_frames.values()))
    explicit = parse_roi_arg(args.roi) if args.roi else (load_roi(Path(args.roi_file)) if args.roi_file else None)
    roi = resolve_roi(any_frames[0].shape[:2], explicit, args.fraction)

    frames_by_color = {
        _LED_COLOR_FOR_LABEL[label]: frames for label, frames in label_frames.items() if label != "off"
    }
    proposed = propose_hue_ranges(
        frames_by_color, roi, low_percentile=args.low_percentile, high_percentile=args.high_percentile
    )
    payload = {
        "roi": asdict(roi),
        "hue_ranges": {color.value: asdict(hue_range) for color, hue_range in proposed.items()},
    }
    text = json.dumps(payload, sort_keys=True, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    label_frames = _load_label_dirs(args, ("off", "blue", "green", "red", "booting"))
    if not label_frames:
        raise ValueError("At least one of --off/--blue/--green/--red/--booting is required")
    any_frames = next(iter(label_frames.values()))
    explicit = parse_roi_arg(args.roi) if args.roi else (load_roi(Path(args.roi_file)) if args.roi_file else None)
    roi = resolve_roi(any_frames[0].shape[:2], explicit, args.fraction)

    config = DEFAULT_OPTICAL_CONFIG
    frames_by_expected = {_LED_COLOR_FOR_LABEL[label]: frames for label, frames in label_frames.items()}
    report = verify_temporal_classification(frames_by_expected, roi, config)
    text = json.dumps(report, sort_keys=True, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    all_matched = all(entry["matched"] for entry in report.values())
    return 0 if all_matched else 1


def cmd_build_replay(args: argparse.Namespace) -> int:
    label_frames = _load_label_dirs(args, ("off", "booting", "blue", "green", "red"))
    if not label_frames:
        raise ValueError("At least one state directory is required to build replay footage")
    sequences = {_LED_STATE_FOR_LABEL[label]: frames for label, frames in label_frames.items()}
    fixtures = build_replay_footage(sequences)
    write_replay_file(fixtures, Path(args.out))
    print(json.dumps({"out": args.out, "frame_count": len(fixtures)}, sort_keys=True))
    return 0


def _add_label_dir_args(parser: argparse.ArgumentParser, labels: Sequence[str]) -> None:
    for label in labels:
        parser.add_argument(f"--{label}", default=None, help=f"Directory of captured '{label}' frame images")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.calibrate_camera",
        description=(
            "LED-status-camera calibration: ROI, proposed HSV ranges, temporal "
            "classification verification, and CameraSim replay footage."
        ),
    )
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command", required=True)

    show_roi = sub.add_parser("show-roi", help="Compute/save the fixed ROI from a directory of frames")
    show_roi.add_argument("--frames-dir", required=True)
    show_roi.add_argument("--roi", default=None, help="Explicit 'x,y,width,height' instead of the centred fallback")
    show_roi.add_argument("--fraction", type=float, default=0.5)
    show_roi.add_argument("--out", default=None, help="Path to save roi.json")
    show_roi.set_defaults(func=cmd_show_roi)

    propose = sub.add_parser("propose-hsv", help="Propose HSV hue ranges from captured per-colour frames")
    _add_label_dir_args(propose, ("off", "blue", "green", "red"))
    propose.add_argument("--roi", default=None)
    propose.add_argument("--roi-file", default=None)
    propose.add_argument("--fraction", type=float, default=0.5)
    propose.add_argument("--low-percentile", type=float, default=5.0)
    propose.add_argument("--high-percentile", type=float, default=95.0)
    propose.add_argument("--out", default=None, help="Path to save the proposed hue ranges JSON")
    propose.set_defaults(func=cmd_propose_hsv)

    verify = sub.add_parser("verify", help="Verify temporal classification against captured footage")
    _add_label_dir_args(verify, ("off", "blue", "green", "red", "booting"))
    verify.add_argument("--roi", default=None)
    verify.add_argument("--roi-file", default=None)
    verify.add_argument("--fraction", type=float, default=0.5)
    verify.add_argument("--out", default=None, help="Path to save the verification report JSON")
    verify.set_defaults(func=cmd_verify)

    build_replay = sub.add_parser("build-replay", help="Package captured frames into CameraSim replay footage")
    _add_label_dir_args(build_replay, ("off", "booting", "blue", "green", "red"))
    build_replay.add_argument("--out", required=True, help="Path to write the replay footage JSON")
    build_replay.set_defaults(func=cmd_build_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
