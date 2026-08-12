"""Offline waveform re-analysis (Phase 11).

Re-runs `ccid.analysis` against already-captured, already-committed raw
waveforms, using a possibly different `AnalysisVersion` or injection-time
override than the inline analysis performed during the run. This is the
supported way to react to a changed trip-time algorithm or a corrected
endpoint definition without touching the frozen campaign data.

Hard rules (handoff sections 4, 9, 11), enforced by construction here:

- Original artifacts (`waveforms/<n>.npz`, `cycles/<n>.json`, `cycles.csv`,
  `runstate.json`) are only ever opened for reading. Every output of this
  tool is written under a fresh `reanalysis/<replay_id>/` directory inside
  the run, so a replay can never overwrite raw data or the original inline
  analysis.
- Every replayed result is tagged with `source: "replay"` plus the replay id
  and timestamp, so it can never be confused with the original inline
  analysis performed during the run.
- A change report is produced alongside the per-cycle results so a verdict
  change is auditable at a glance, not just recoverable by hand-diffing JSON.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from ccid.analysis import (
    AnalysisConfig,
    AnalysisVersion,
    TripResult,
    analyze_waveform_file,
    resolve_analysis_config,
    V1_ENDPOINT_DEFINITION,
    V2_ENDPOINT_DEFINITION,
    V3_ENDPOINT_DEFINITION,
)
from ccid.config import load_config
from ccid.errors import WaveformFormatError
from ccid.recorder import RunRecorder

LOGGER = logging.getLogger("tools.replay_waveform")

_CHANGE_REPORT_COLUMNS = [
    "cycle_index",
    "source_algorithm_version",
    "replay_algorithm_version",
    "source_trip_time_s",
    "replay_trip_time_s",
    "source_verdict",
    "replay_verdict",
    "verdict_changed",
    "trip_time_delta_s",
]


def make_replay_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(tz=timezone.utc)
    return "replay_" + moment.strftime("%Y%m%dT%H%M%SZ")


def resolve_algorithm_version(text: str | None) -> AnalysisVersion | None:
    if text is None:
        return None
    try:
        return AnalysisVersion(text)
    except ValueError as exc:
        supported = sorted(member.value for member in AnalysisVersion)
        raise ValueError(f"Unsupported algorithm_version '{text}'; supported: {supported}") from exc


_ENDPOINT_DEFINITION_BY_VERSION = {
    AnalysisVersion.V1: V1_ENDPOINT_DEFINITION,
    AnalysisVersion.V2: V2_ENDPOINT_DEFINITION,
    AnalysisVersion.V3: V3_ENDPOINT_DEFINITION,
}


def build_analysis_config(config_path: str | Path, algorithm_version: str | None) -> AnalysisConfig:
    app_config = load_config(config_path)
    analysis_config = resolve_analysis_config(app_config)
    version = resolve_algorithm_version(algorithm_version)
    if version is not None:
        analysis_config = replace(
            analysis_config,
            algorithm_version=version,
            endpoint_definition=_ENDPOINT_DEFINITION_BY_VERSION[version],
        )
    return analysis_config


def replay_single_waveform(
    waveform_path: str | Path,
    analysis_config: AnalysisConfig,
    *,
    injection_time_s: float | None = None,
) -> TripResult:
    return analyze_waveform_file(waveform_path, analysis_config, injection_time_s=injection_time_s)


def load_original_trip_result(run_dir: Path, cycle_index: int) -> TripResult | None:
    """Read the inline `TripResult` recorded at run time for one cycle, if any."""

    sidecar_path = run_dir / "cycles" / f"{cycle_index}.json"
    if not sidecar_path.exists():
        return None
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WaveformFormatError(f"Could not read cycle sidecar: {sidecar_path}") from exc
    analysis_payload = payload.get("analysis")
    if not isinstance(analysis_payload, dict):
        return None
    return TripResult.from_dict(analysis_payload)


def _diff_row(
    cycle_index: int, original: TripResult | None, replayed: TripResult
) -> dict[str, Any]:
    original_trip = original.trip_time_s if original is not None else None
    original_verdict = original.verdict.value if original is not None else None
    delta = None
    if original_trip is not None and replayed.trip_time_s is not None:
        delta = replayed.trip_time_s - original_trip
    return {
        "cycle_index": cycle_index,
        "source_algorithm_version": original.algorithm_version.value if original is not None else "",
        "replay_algorithm_version": replayed.algorithm_version.value,
        "source_trip_time_s": original_trip,
        "replay_trip_time_s": replayed.trip_time_s,
        "source_verdict": original_verdict,
        "replay_verdict": replayed.verdict.value,
        "verdict_changed": original_verdict is not None and original_verdict != replayed.verdict.value,
        "trip_time_delta_s": delta,
    }


def replay_cycle(
    *,
    run_dir: Path,
    cycle_index: int,
    analysis_config: AnalysisConfig,
    out_dir: Path,
    replay_id: str,
    injection_time_s: float | None = None,
) -> dict[str, Any]:
    waveform_path = run_dir / "waveforms" / f"{cycle_index}.npz"
    if not waveform_path.exists():
        raise FileNotFoundError(f"No waveform artifact for cycle {cycle_index}: {waveform_path}")

    original = load_original_trip_result(run_dir, cycle_index)
    replayed = replay_single_waveform(
        waveform_path, analysis_config, injection_time_s=injection_time_s
    )

    result_payload = replayed.to_dict()
    result_payload["source"] = "replay"
    result_payload["replay_id"] = replay_id
    result_payload["replayed_at_utc"] = datetime.now(tz=timezone.utc).isoformat()
    result_payload["cycle_index"] = cycle_index
    if injection_time_s is not None:
        result_payload["injection_time_s_override"] = injection_time_s

    out_path = out_dir / "cycles" / f"{cycle_index}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result_payload, sort_keys=True, indent=2), encoding="utf-8")

    return _diff_row(cycle_index, original, replayed)


def resolve_cycle_range(
    run_dir: Path,
    *,
    cycle: int | None,
    from_cycle: int | None,
    to_cycle: int | None,
) -> list[int]:
    if cycle is not None:
        if from_cycle is not None or to_cycle is not None:
            raise ValueError("--cycle cannot be combined with --from/--to")
        return [cycle]

    if from_cycle is not None or to_cycle is not None:
        if from_cycle is None or to_cycle is None:
            raise ValueError("--from and --to must be given together")
        if from_cycle < 1 or to_cycle < from_cycle:
            raise ValueError("Invalid cycle range")
        return list(range(from_cycle, to_cycle + 1))

    recorder = RunRecorder(run_dir)
    state = recorder.read_run_state_unchecked(run_dir)
    return list(range(1, state.last_completed_cycle + 1))


def replay_run(
    *,
    run_dir: Path,
    analysis_config: AnalysisConfig,
    cycle_indices: list[int],
    out_dir: Path | None = None,
    injection_time_s: float | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    replay_id = make_replay_id()
    resolved_out_dir = out_dir or (run_dir / "reanalysis" / replay_id)
    resolved_out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for cycle_index in cycle_indices:
        try:
            rows.append(
                replay_cycle(
                    run_dir=run_dir,
                    cycle_index=cycle_index,
                    analysis_config=analysis_config,
                    out_dir=resolved_out_dir,
                    replay_id=replay_id,
                    injection_time_s=injection_time_s,
                )
            )
        except FileNotFoundError as exc:
            LOGGER.warning("Skipping cycle %d: %s", cycle_index, exc)

    report_path = resolved_out_dir / "change_report.csv"
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CHANGE_REPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    manifest = {
        "replay_id": replay_id,
        "run_dir": str(run_dir),
        "replayed_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "algorithm_version": analysis_config.algorithm_version.value,
        "cycles_replayed": cycle_indices,
        "cycles_with_verdict_change": [row["cycle_index"] for row in rows if row["verdict_changed"]],
    }
    (resolved_out_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
    )
    return resolved_out_dir, rows


def cmd_waveform(args: argparse.Namespace) -> int:
    analysis_config = build_analysis_config(args.config, args.algorithm_version)
    result = replay_single_waveform(
        args.waveform, analysis_config, injection_time_s=args.injection_time_s
    )
    payload = result.to_dict()
    payload["source"] = "replay"
    payload["replayed_at_utc"] = datetime.now(tz=timezone.utc).isoformat()
    text = json.dumps(payload, sort_keys=True, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")
    analysis_config = build_analysis_config(args.config, args.algorithm_version)
    cycle_indices = resolve_cycle_range(
        run_dir, cycle=args.cycle, from_cycle=args.__dict__.get("from"), to_cycle=args.to
    )
    out_dir = Path(args.out_dir) if args.out_dir else None
    resolved_out_dir, rows = replay_run(
        run_dir=run_dir,
        analysis_config=analysis_config,
        cycle_indices=cycle_indices,
        out_dir=out_dir,
        injection_time_s=args.injection_time_s,
    )
    changed = [row for row in rows if row["verdict_changed"]]
    print(
        json.dumps(
            {
                "out_dir": str(resolved_out_dir),
                "cycles_replayed": len(rows),
                "cycles_with_verdict_change": len(changed),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.replay_waveform",
        description=(
            "Offline re-analysis of committed raw waveforms. Never modifies "
            "original run data; always writes to a fresh reanalysis/<id>/ directory."
        ),
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--log-level", default="WARNING")
    parser.add_argument(
        "--algorithm-version",
        default=None,
        help="Override AnalysisConfig.algorithm_version (default: from config.yaml)",
    )
    parser.add_argument(
        "--injection-time-s",
        type=float,
        default=None,
        help="Override t=0 (K3 close instant) in the scope time base for every replayed waveform",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    waveform = sub.add_parser("waveform", help="Replay one stored .npz waveform file")
    waveform.add_argument("waveform", help="Path to a waveforms/<n>.npz file")
    waveform.add_argument("--out", help="Optional path to also write the TripResult JSON")
    waveform.set_defaults(func=cmd_waveform)

    run = sub.add_parser("run", help="Replay a cycle, a cycle range, or an entire run")
    run.add_argument("run_dir", help="Path to a run directory (contains waveforms/, cycles/, cycles.csv)")
    run.add_argument("--cycle", type=int, help="Replay exactly one cycle index")
    run.add_argument("--from", dest="from", type=int, help="First cycle index of a range")
    run.add_argument("--to", type=int, help="Last cycle index of a range (inclusive)")
    run.add_argument(
        "--out-dir",
        help="Output directory (default: <run_dir>/reanalysis/<replay_id>/)",
    )
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
