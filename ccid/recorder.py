"""Crash-safe per-cycle persistence.

Locked behavior (coding_instructions.txt Phase 5):

- Commit order is mandatory and is what makes a crash mid-cycle recoverable:
  write and fsync every per-cycle artifact first, then append and fsync the
  CSV row, then atomically replace `runstate.json`, then send the external
  heartbeat. A crash at any point along that order leaves the run in a state
  `reconcile_orphans` can clean up without ever losing or double-counting a
  cycle: artifacts written but `runstate.json` not yet advanced are orphans
  (deleted on resume, since `last_completed_cycle` never claimed them);
  `runstate.json` is never advanced until everything it describes already
  exists on disk.
- `runstate.json` itself is written with a temp-file-write + fsync +
  `os.replace` sequence so a crash mid-write can never leave a torn/partial
  file in its place - `os.replace` is atomic, so a reader always sees either
  the old or the new content, never a mix.
- The heartbeat is sent last, deliberately after every other write has
  succeeded, so an external liveness ping can never certify a cycle that
  is not actually durable yet.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import csv
import io
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Mapping, Sequence
import zipfile

from ccid import __version__
from ccid.errors import ConfigHashMismatchError, PersistenceError, ResumeBlockedError
from ccid.hal.base import ScopeTimeoutDiagnostics, WaveformCapture


@dataclass(frozen=True)
class CycleArtifacts:
    waveform_samples: bytes
    waveform_preamble: Mapping[str, float | int | str]
    scope_png: bytes
    gate_jpg: bytes
    fault_jpg_burst: tuple[bytes, ...] = field(default_factory=tuple)
    cycle_sidecar: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CycleCsvRow:
    cycle_index: int
    run_id: str
    utc_timestamp: str
    monotonic_start: float
    trip_time_s: float | None
    verdict: str
    analysis_version: str
    led_state_at_gate: str
    degraded_flags: str
    notes: str

    @classmethod
    def from_values(
        cls,
        *,
        cycle_index: int,
        run_id: str,
        monotonic_start: float,
        trip_time_s: float | None,
        verdict: str,
        analysis_version: str,
        led_state_at_gate: str,
        degraded_flags: str = "",
        notes: str = "",
    ) -> "CycleCsvRow":
        return cls(
            cycle_index=cycle_index,
            run_id=run_id,
            utc_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            monotonic_start=monotonic_start,
            trip_time_s=trip_time_s,
            verdict=verdict,
            analysis_version=analysis_version,
            led_state_at_gate=led_state_at_gate,
            degraded_flags=degraded_flags,
            notes=notes,
        )


@dataclass(frozen=True)
class RunState:
    run_id: str
    last_completed_cycle: int
    target_cycles: int
    config_hash: str
    pass_count: int
    fail_count: int
    halt_reason: str | None


_CYCLES_CSV_COLUMNS = [
    "cycle_index",
    "run_id",
    "utc_timestamp",
    "monotonic_start",
    "trip_time_s",
    "verdict",
    "analysis_version",
    "led_state_at_gate",
    "degraded_flags",
    "notes",
]


class RunRecorder:
    def __init__(
        self,
        run_root: Path,
        *,
        crash_injector: Callable[[str], None] | None = None,
        heartbeat_sender: Callable[[str, int], None] | None = None,
    ) -> None:
        self._run_root = run_root
        self._crash_injector = crash_injector
        self._heartbeat_sender = heartbeat_sender

    def initialize_run(
        self,
        *,
        run_id: str,
        target_cycles: int,
        config_hash: str,
        frozen_config_yaml: str,
    ) -> Path:
        run_dir = self._run_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_layout(run_dir)

        self._write_bytes_and_fsync(run_dir / "config.yaml", frozen_config_yaml.encode("utf-8"))
        if not (run_dir / "cycles.csv").exists():
            self._write_cycles_csv_header(run_dir / "cycles.csv")

        state = RunState(
            run_id=run_id,
            last_completed_cycle=0,
            target_cycles=target_cycles,
            config_hash=config_hash,
            pass_count=0,
            fail_count=0,
            halt_reason=None,
        )
        self._write_runstate_atomic(run_dir / "runstate.json", state)
        return run_dir

    def load_run_state(
        self,
        run_dir: Path,
        *,
        expected_config_hash: str,
        allow_halted_resume: bool = False,
    ) -> RunState:
        state = self._read_runstate(run_dir / "runstate.json")
        if state.config_hash != expected_config_hash:
            raise ConfigHashMismatchError(
                "Config hash mismatch on resume; explicit override required before continuing"
            )
        if state.halt_reason is not None and not allow_halted_resume:
            raise ResumeBlockedError(
                f"Run is halted with reason '{state.halt_reason}', explicit override required"
            )
        return state

    def read_run_state_unchecked(self, run_dir: Path) -> RunState:
        return self._read_runstate(run_dir / "runstate.json")

    def reconcile_orphans(self, run_dir: Path, state: RunState) -> None:
        self._delete_orphans(run_dir, state.last_completed_cycle)
        self._truncate_cycles_csv(run_dir / "cycles.csv", state.last_completed_cycle)

    def record_cycle(
        self,
        *,
        run_dir: Path,
        state: RunState,
        csv_row: CycleCsvRow,
        artifacts: CycleArtifacts,
        halt_reason: str | None = None,
    ) -> RunState:
        if csv_row.run_id != state.run_id:
            raise PersistenceError("CSV row run_id must match current run state run_id")
        if csv_row.cycle_index != state.last_completed_cycle + 1:
            raise PersistenceError("Cycle index must be exactly last_completed_cycle + 1")

        self._ensure_layout(run_dir)
        cycle_index = csv_row.cycle_index
        cycle_label = str(cycle_index)

        # Commit order is load-bearing for crash safety (module docstring):
        # artifacts -> CSV row -> runstate.json -> heartbeat. Each
        # `_checkpoint()` call is a no-op in production and the hook
        # `tools/simulate.py`'s crash-resume path uses to prove a crash at
        # that exact point can always be recovered from without a skipped or
        # falsely-completed cycle.
        self._write_waveform_npz(
            run_dir / "waveforms" / f"{cycle_label}.npz",
            samples=artifacts.waveform_samples,
            preamble=artifacts.waveform_preamble,
        )
        self._write_bytes_and_fsync(run_dir / "images" / f"{cycle_label}_scope.png", artifacts.scope_png)
        self._write_bytes_and_fsync(run_dir / "images" / f"{cycle_label}_green.jpg", artifacts.gate_jpg)
        for i, fault_jpg in enumerate(artifacts.fault_jpg_burst, start=1):
            self._write_bytes_and_fsync(
                run_dir / "images" / f"{cycle_label}_fault_{i}.jpg",
                fault_jpg,
            )
        sidecar = dict(artifacts.cycle_sidecar)
        sidecar.update(
            {
                "cycle_index": cycle_index,
                "run_id": csv_row.run_id,
                "utc_timestamp": csv_row.utc_timestamp,
                "trip_time_s": csv_row.trip_time_s,
                "verdict": csv_row.verdict,
                "analysis_version": csv_row.analysis_version,
                "led_state_at_gate": csv_row.led_state_at_gate,
                # Preserved per cycle (not just at the run level in
                # runstate.json) so an individually-inspected cycle JSON is
                # self-describing about which software/config produced it.
                "config_hash": state.config_hash,
                "software_version": __version__,
            }
        )
        self._write_json_and_fsync(run_dir / "cycles" / f"{cycle_label}.json", sidecar)
        self._checkpoint("after_artifacts")

        self._append_cycles_csv(run_dir / "cycles.csv", csv_row)
        self._checkpoint("after_csv")

        next_state = RunState(
            run_id=state.run_id,
            last_completed_cycle=cycle_index,
            target_cycles=state.target_cycles,
            config_hash=state.config_hash,
            pass_count=state.pass_count + (1 if csv_row.verdict == "PASS" else 0),
            fail_count=state.fail_count + (0 if csv_row.verdict == "PASS" else 1),
            halt_reason=halt_reason,
        )
        self._write_runstate_atomic(run_dir / "runstate.json", next_state)
        self._checkpoint("after_runstate")

        if self._heartbeat_sender is not None:
            self._heartbeat_sender(next_state.run_id, next_state.last_completed_cycle)
        self._checkpoint("after_heartbeat")
        return next_state

    def write_timeout_diagnostics(
        self,
        *,
        run_dir: Path,
        run_id: str,
        cycle_index: int,
        diagnostics: ScopeTimeoutDiagnostics,
        k3_closed_monotonic_s: float | None,
        k3_open_monotonic_s: float | None,
        k3_open_reason: str | None,
        primary_halt_reason: str,
    ) -> None:
        """Best-effort evidence capture for a scope-timeout halt.

        Deliberately outside the crash-safe commit contract described in
        this module's docstring: takes no `RunState`, never touches
        `runstate.json`, and never advances `last_completed_cycle`. Writes
        under `diagnostics/<cycle_index>/`, a subtree `_ensure_layout` and
        `_delete_orphans` never look at.
        """

        diag_dir = run_dir / "diagnostics" / str(cycle_index)
        k3_duration_s = None
        if k3_closed_monotonic_s is not None and k3_open_monotonic_s is not None:
            k3_duration_s = k3_open_monotonic_s - k3_closed_monotonic_s

        self._write_bytes_and_fsync(diag_dir / "scope_timeout.png", diagnostics.scope_png)
        self._write_json_and_fsync(
            diag_dir / "scope_state.json",
            {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "primary_halt_reason": primary_halt_reason,
                "software_version": __version__,
                "captured_at_utc": diagnostics.captured_at_utc.isoformat(),
                "captured_at_monotonic_s": diagnostics.captured_at_monotonic_s,
                "operation_condition": diagnostics.operation_condition,
                "hal_status": diagnostics.hal_status,
                "settings": dict(diagnostics.settings),
                "k3_closed_monotonic_s": k3_closed_monotonic_s,
                "k3_open_monotonic_s": k3_open_monotonic_s,
                "k3_open_reason": k3_open_reason,
                "k3_duration_s": k3_duration_s,
            },
        )
        errors_text = "\n".join(diagnostics.error_queue) + "\n" if diagnostics.error_queue else "no errors\n"
        self._write_bytes_and_fsync(diag_dir / "scope_errors.txt", errors_text.encode("utf-8"))

    def write_forced_diagnostic_capture(
        self,
        *,
        run_dir: Path,
        run_id: str,
        cycle_index: int,
        capture: WaveformCapture,
        force_command_start_monotonic_s: float | None,
        force_command_return_monotonic_s: float | None,
        forced_acquisition_completion_monotonic_s: float | None,
        k3_closed_monotonic_s: float | None,
        diagnostic_timeline: Sequence[Mapping[str, object]],
        waveform_analysis: Mapping[str, object] | None,
    ) -> None:
        """Best-effort evidence capture for a diagnostic-only forced
        trigger (SCOPE_TRIGGER_DEBUG_LOG.md Entry 11) - a real trigger
        never occurred this cycle, so this is not a measurement.

        `diagnostic_timeline` and `waveform_analysis` intentionally replace
        the single `forced_at_monotonic_s`/`elapsed_since_k3_closed_s`
        fields from the original version of this method (Entry 13): that
        single Pi-side timestamp was incorrectly assumed to correspond to
        the scope waveform's own t=0, which nothing in this system
        actually guarantees. The Pi-side `*_monotonic_s` fields below are
        still recorded (useful for reasoning about the Pi-side sequence of
        events and durations) but must never be mapped onto the waveform's
        own time axis - `waveform_analysis` (from
        `ccid.forced_diagnostic_analysis`) is computed entirely from the
        waveform's own samples/preamble instead, for exactly that reason.

        Written only under diagnostics/<cycle_index>/, never
        waveforms/ or images/ - anything reading the normal per-cycle
        artifact tree (replay, analysis, commit) must never encounter
        this data. Like `write_timeout_diagnostics`, outside the
        crash-safe commit contract: no runstate.json, no
        `last_completed_cycle` advance.
        """

        diag_dir = run_dir / "diagnostics" / str(cycle_index)
        self._write_waveform_npz(
            diag_dir / "forced_diagnostic_waveform.npz",
            samples=capture.samples,
            preamble=capture.preamble,
        )
        self._write_bytes_and_fsync(diag_dir / "forced_diagnostic_scope.png", capture.scope_png)
        self._write_json_and_fsync(
            diag_dir / "forced_diagnostic_state.json",
            {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "capture_type": "forced_diagnostic_non_measurement",
                "note": (
                    "Forced via :TRIGger:FORCe because TER was still 0 ~100 ms "
                    "after K3 closed - not a real trigger event. Must never be "
                    "used for PASS/FAIL or trip-time calculation. The "
                    "*_monotonic_s fields are Pi-side timestamps only - do not "
                    "map them onto the waveform's own time axis (see "
                    "waveform_analysis instead, and SCOPE_TRIGGER_DEBUG_LOG.md "
                    "Entry 13)."
                ),
                "software_version": __version__,
                "pi_side_timing": {
                    "force_command_start_monotonic_s": force_command_start_monotonic_s,
                    "force_command_return_monotonic_s": force_command_return_monotonic_s,
                    "forced_acquisition_completion_monotonic_s": (
                        forced_acquisition_completion_monotonic_s
                    ),
                    "k3_closed_monotonic_s": k3_closed_monotonic_s,
                },
                "diagnostic_timeline": list(diagnostic_timeline),
                "waveform_analysis": dict(waveform_analysis) if waveform_analysis is not None else None,
                "captured_at_utc": capture.captured_at_utc.isoformat(),
                "settings_readback": dict(capture.settings_readback),
            },
        )

    def write_controller_exception_diagnostics(
        self,
        *,
        run_dir: Path,
        run_id: str,
        cycle_index: int,
        exception_type: str,
        exception_message: str,
        traceback_text: str,
        last_state: str | None,
        transitions: Sequence[Mapping[str, object]],
        captured_at_monotonic_s: float,
        cycle_monotonic_start_s: float,
    ) -> None:
        """Best-effort evidence capture for a `controller:unexpected:*` halt.

        Sequencer._run_cycle's defensive catch-all previously recorded only
        `type(exc).__name__` in the halt reason - the exception message and
        traceback went nowhere but process stderr/journald, which is not
        guaranteed durable. That gap is exactly what turned a real defect
        into an unproven theory in campaign `5800_v3_real_20260813T175531Z`:
        the Pi became unreachable after halting at cycle 38, and non-
        persistent journald lost the original traceback when it was power-
        cycled. This method exists so the next unexpected controller
        exception - whatever it turns out to be - leaves durable evidence
        even if the process or the host doesn't survive to report it live.

        Like `write_timeout_diagnostics`/`write_forced_diagnostic_capture`:
        outside the crash-safe commit contract described in this module's
        docstring - writes only under diagnostics/<cycle_index>/, never
        touches runstate.json, cycles.csv, or last_completed_cycle. Must
        never raise into the caller; the caller is itself the last-resort
        exception handler and cannot tolerate a second failure here masking
        the original one.
        """

        diag_dir = run_dir / "diagnostics" / str(cycle_index)
        self._write_json_and_fsync(
            diag_dir / "controller_exception.json",
            {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "software_version": __version__,
                "captured_at_monotonic_s": captured_at_monotonic_s,
                "cycle_monotonic_start_s": cycle_monotonic_start_s,
                "elapsed_in_cycle_s": captured_at_monotonic_s - cycle_monotonic_start_s,
                "exception_type": exception_type,
                "exception_message": exception_message,
                "traceback": traceback_text,
                "last_state": last_state,
                "transitions": list(transitions),
            },
        )

    def _checkpoint(self, step_name: str) -> None:
        if self._crash_injector is not None:
            self._crash_injector(step_name)

    @staticmethod
    def _ensure_layout(run_dir: Path) -> None:
        (run_dir / "cycles").mkdir(parents=True, exist_ok=True)
        (run_dir / "waveforms").mkdir(parents=True, exist_ok=True)
        (run_dir / "images").mkdir(parents=True, exist_ok=True)

    def _write_waveform_npz(
        self,
        path: Path,
        *,
        samples: bytes,
        preamble: Mapping[str, float | int | str],
    ) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("samples.bin", samples)
            zf.writestr("preamble.json", json.dumps(dict(preamble), sort_keys=True))
        self._write_bytes_and_fsync(path, buffer.getvalue())

    @staticmethod
    def _write_bytes_and_fsync(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _write_json_and_fsync(path: Path, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        RunRecorder._write_bytes_and_fsync(path, encoded)

    @staticmethod
    def _write_cycles_csv_header(path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_CYCLES_CSV_COLUMNS)
            writer.writeheader()
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _append_cycles_csv(path: Path, row: CycleCsvRow) -> None:
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_CYCLES_CSV_COLUMNS)
            writer.writerow(asdict(row))
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _write_runstate_atomic(path: Path, state: RunState) -> None:
        # Write-to-temp-file + fsync + os.replace, not an in-place write: a
        # crash mid-write to `path` directly could leave a truncated/torn
        # JSON file that a resume can't even parse. `os.replace` is atomic on
        # POSIX, so any reader always sees either the fully-old or the
        # fully-new file, never a partial one.
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(state), sort_keys=True, separators=(",", ":")).encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(path.parent),
            prefix="runstate.",
            suffix=".tmp",
        ) as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = Path(tmp.name)
        os.replace(tmp_name, path)

    @staticmethod
    def _read_runstate(path: Path) -> RunState:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PersistenceError(f"Failed reading runstate: {path}") from exc
        except json.JSONDecodeError as exc:
            raise PersistenceError(f"Invalid runstate JSON: {path}") from exc

        try:
            return RunState(
                run_id=str(payload["run_id"]),
                last_completed_cycle=int(payload["last_completed_cycle"]),
                target_cycles=int(payload["target_cycles"]),
                config_hash=str(payload["config_hash"]),
                pass_count=int(payload["pass_count"]),
                fail_count=int(payload["fail_count"]),
                halt_reason=payload.get("halt_reason"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PersistenceError(f"Runstate missing required fields: {path}") from exc

    @staticmethod
    def _delete_orphans(run_dir: Path, last_completed_cycle: int) -> None:
        numeric_patterns = [
            (run_dir / "cycles", r"^(\d+)\.json$"),
            (run_dir / "waveforms", r"^(\d+)\.npz$"),
            (run_dir / "images", r"^(\d+)_scope\.png$"),
            (run_dir / "images", r"^(\d+)_green\.jpg$"),
            (run_dir / "images", r"^(\d+)_fault_\d+\.jpg$"),
        ]
        for directory, pattern in numeric_patterns:
            if not directory.exists():
                continue
            regex = re.compile(pattern)
            for child in directory.iterdir():
                match = regex.match(child.name)
                if not match:
                    continue
                cycle_index = int(match.group(1))
                if cycle_index > last_completed_cycle:
                    child.unlink()

    @staticmethod
    def _truncate_cycles_csv(path: Path, last_completed_cycle: int) -> None:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            kept_rows = [
                row
                for row in reader
                if int(row["cycle_index"]) <= last_completed_cycle
            ]

        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_CYCLES_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(kept_rows)
            handle.flush()
            os.fsync(handle.fileno())
