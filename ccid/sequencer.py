"""Explicit per-cycle state machine (coding_instructions.txt Phase 8).

Orchestrates the required sequence - mains close, charging gate, scope
configure/arm/poll, K3 injection, acquisition poll, K3 open/backstop,
transfer, analysis, commit, mains open, cooldown - plus its retry/degrade/
halt branches. Safety-critical invariants (K3 interlock, K1/K2 blocked from
opening while K3 is closed, SafeOff ordering) are enforced one layer down, in
the HAL (`ccid/hal/gpio_real.py`/`ccid/hal/gpio_sim.py`) and in
`ccid/safety.py`; this module orchestrates the sequence and cannot bypass
those checks even if it tried.

Every failure path funnels into one of two internal signal exceptions
(`_RetryCycle`, `_SequencerHalt`) rather than nested conditionals, so
`_run_cycle`'s single `try/except` block is the one place that decides
retry-vs-halt-vs-continue for the whole state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import io
import json
import logging
import shutil
import zipfile
from typing import Callable

from ccid.analysis import (
    SANITY_NO_PRETRIGGER_LEAKAGE,
    SANITY_RECORD_SPANS_NO_TRIP_LIMIT,
    TripResult,
    Verdict,
    analyze_waveform,
)
from ccid.classify import (
    DEFAULT_OPTICAL_CONFIG,
    DEGRADED_FLAG_CAMERA_UNAVAILABLE,
    GateTimeoutAction,
    RegionOfInterest,
    await_charging_gate,
    gate_timeout_action,
)
from ccid.config import AppConfig
from ccid.errors import CcidError, PersistenceError
from ccid.hal.base import (
    ChargingGateToken,
    ContactorInterface,
    ScopeInterface,
    ScopeSettings,
)
from ccid.recorder import CycleArtifacts, CycleCsvRow, RunRecorder, RunState
from ccid.safety import safe_off
from ccid.states import CycleState, LedState, Terminal

_LOGGER = logging.getLogger(__name__)

# Shared between the halt reason raised below and the diagnostics bundle's
# persisted `primary_halt_reason`, so the two strings can't silently drift
# apart in a future edit.
_SCOPE_TIMEOUT_REASON = "scope_never_triggered_or_acquire_timeout"


class FaultCategory(str, Enum):
    DUT = "dut"
    RIG = "rig"
    PERIPHERAL = "peripheral"
    PERSISTENCE = "persistence"
    CONTROLLER = "controller"


@dataclass(frozen=True)
class StateTransition:
    cycle_index: int
    state: CycleState
    at_monotonic_s: float
    detail: str = ""


@dataclass(frozen=True)
class CycleExecution:
    cycle_index: int
    terminal: Terminal
    verdict: Verdict | None
    trip_time_s: float | None
    led_state_at_gate: LedState
    degraded_flags: tuple[str, ...]
    notes: str
    halt_reason: str | None = None
    fault_category: FaultCategory | None = None
    latch_slow_clear: bool = False


@dataclass(frozen=True)
class SequencerRunResult:
    terminal: Terminal
    state: RunState
    cycles: tuple[CycleExecution, ...]
    transitions: tuple[StateTransition, ...]
    halt_reason: str | None
    fault_category: FaultCategory | None
    latch_slow_clear_count: int


@dataclass
class _CycleContext:
    cycle_index: int
    monotonic_start: float
    degraded_flags: list[str] = field(default_factory=list)
    transitions: list[StateTransition] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    led_state_at_gate: LedState = LedState.OFF_OR_UNKNOWN
    latch_slow_clear: bool = False
    k3_closed_monotonic_s: float | None = None
    k3_open_monotonic_s: float | None = None
    k3_open_reason: str | None = None


class Sequencer:
    """Phase 8 explicit state machine.

    Safety-critical invariants (K3 interlock, K1/K2 open blocking while K3 is closed)
    remain enforced at the HAL layer; this sequencer orchestrates the required sequence,
    retry/degrade branches, and durable commit flow.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        contactors: ContactorInterface,
        scope: ScopeInterface,
        camera,
        recorder: RunRecorder,
        scope_settings: ScopeSettings | None = None,
        monotonic_now: Callable[[], float],
        sleep: Callable[[float], None],
        # Duck-typed to whatever shutil.disk_usage() returns (only `.free` is
        # used); injectable so low-disk conditions are testable without an
        # actual near-full filesystem.
        disk_usage: Callable[[str], object] = shutil.disk_usage,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._contactors = contactors
        self._scope = scope
        self._camera = camera
        self._vision_roi = RegionOfInterest(
            x=config.vision.roi_x,
            y=config.vision.roi_y,
            width=config.vision.roi_width,
            height=config.vision.roi_height,
        )
        self._vision_optical_config = replace(
            DEFAULT_OPTICAL_CONFIG,
            charging_green_window_s=config.vision.charging_green_window_s,
            charging_green_required_frames=config.vision.charging_green_required_frames,
            charging_green_min_span_s=config.vision.charging_green_min_span_s,
        )
        self._recorder = recorder
        self._scope_settings = scope_settings or ScopeSettings()
        self._now = monotonic_now
        self._sleep = sleep
        self._disk_usage = disk_usage
        self._logger = logger or _LOGGER

    def run(self, *, run_dir, state: RunState) -> SequencerRunResult:
        transitions: list[StateTransition] = []
        cycles: list[CycleExecution] = []
        current_state = state
        latch_slow_clear_count = 0

        self._scope.connect()
        try:
            cycle_index = current_state.last_completed_cycle + 1
            while cycle_index <= current_state.target_cycles:
                execution, next_state, cycle_transitions = self._run_cycle(
                    run_dir=run_dir,
                    state=current_state,
                    cycle_index=cycle_index,
                )
                transitions.extend(cycle_transitions)
                cycles.append(execution)
                current_state = next_state
                if execution.latch_slow_clear:
                    latch_slow_clear_count += 1
                if execution.terminal in (Terminal.NO_TRIP, Terminal.RIG_FAULT, Terminal.HALTED):
                    return SequencerRunResult(
                        terminal=execution.terminal,
                        state=current_state,
                        cycles=tuple(cycles),
                        transitions=tuple(transitions),
                        halt_reason=execution.halt_reason,
                        fault_category=execution.fault_category,
                        latch_slow_clear_count=latch_slow_clear_count,
                    )
                cycle_index += 1
        finally:
            try:
                safe_off(self._contactors)
            finally:
                self._scope.disconnect()

        self._transition(
            transitions,
            cycle_index=current_state.last_completed_cycle,
            state=CycleState.COMPLETE,
            detail="campaign_complete",
        )
        return SequencerRunResult(
            terminal=Terminal.COMPLETE,
            state=current_state,
            cycles=tuple(cycles),
            transitions=tuple(transitions),
            halt_reason=None,
            fault_category=None,
            latch_slow_clear_count=latch_slow_clear_count,
        )

    def _run_cycle(self, *, run_dir, state: RunState, cycle_index: int) -> tuple[CycleExecution, RunState, list[StateTransition]]:
        context = _CycleContext(cycle_index=cycle_index, monotonic_start=self._now())
        retry_used = False

        while True:
            try:
                self._attempt_cycle(context, run_dir=run_dir, run_id=state.run_id)
            except _RetryCycle as retry:
                if retry_used:
                    execution = self._halt_without_capture(
                        context,
                        terminal=Terminal.HALTED,
                        category=FaultCategory.RIG,
                        reason=f"{retry.reason}_retry_exhausted",
                    )
                    return execution, self._mark_halt_state(run_dir, state, execution), context.transitions
                retry_used = True
                context.latch_slow_clear = True
                context.notes.append("latch_slow_clear")
                self._transition(
                    context.transitions,
                    cycle_index=cycle_index,
                    state=CycleState.RETRY_COOLDOWN,
                    detail=retry.reason,
                )
                safe_off(self._contactors)
                self._sleep(self._config.timing.cooldown_retry_s)
                continue
            except _SequencerHalt as halt:
                execution = self._halt_without_capture(
                    context,
                    terminal=halt.terminal,
                    category=halt.category,
                    reason=halt.reason,
                )
                return execution, self._mark_halt_state(run_dir, state, execution), context.transitions
            except PersistenceError as exc:
                execution = self._halt_without_capture(
                    context,
                    terminal=Terminal.HALTED,
                    category=FaultCategory.PERSISTENCE,
                    reason=f"persistence_error:{type(exc).__name__}",
                )
                return execution, self._mark_halt_state(run_dir, state, execution), context.transitions
            except CcidError as exc:
                execution = self._halt_without_capture(
                    context,
                    terminal=Terminal.HALTED,
                    category=FaultCategory.RIG,
                    reason=f"rig_error:{type(exc).__name__}",
                )
                return execution, self._mark_halt_state(run_dir, state, execution), context.transitions
            except Exception as exc:  # pragma: no cover - defensive safety net
                execution = self._halt_without_capture(
                    context,
                    terminal=Terminal.HALTED,
                    category=FaultCategory.CONTROLLER,
                    reason=f"unexpected:{type(exc).__name__}",
                )
                return execution, self._mark_halt_state(run_dir, state, execution), context.transitions
            break

        capture = self._scope.capture_after_acquire()
        self._transition(
            context.transitions,
            cycle_index=cycle_index,
            state=CycleState.TRANSFERRING,
            detail="capture_complete",
        )

        waveform_blob = _pack_waveform_blob(capture.samples, capture.preamble)
        analysis = analyze_waveform(waveform_blob, self._config.analysis)
        if not analysis.sanity_checks.get(SANITY_NO_PRETRIGGER_LEAKAGE, True):
            return self._commit_and_halt(
                run_dir=run_dir,
                state=state,
                context=context,
                analysis=analysis,
                capture=capture,
                reason="k3_pretrigger_current_detected",
                category=FaultCategory.RIG,
                terminal=Terminal.RIG_FAULT,
            )
        if not analysis.sanity_checks.get(SANITY_RECORD_SPANS_NO_TRIP_LIMIT, True):
            return self._commit_and_halt(
                run_dir=run_dir,
                state=state,
                context=context,
                analysis=analysis,
                capture=capture,
                reason="scope_record_too_short_for_no_trip_window",
                category=FaultCategory.RIG,
                terminal=Terminal.RIG_FAULT,
            )

        verdict_terminal, halt_reason, category = self._map_verdict(analysis.verdict)
        if context.latch_slow_clear and "latch_slow_clear" not in context.degraded_flags:
            context.degraded_flags.append("latch_slow_clear")

        self._transition(
            context.transitions,
            cycle_index=cycle_index,
            state=CycleState.COMMITTING,
            detail=analysis.verdict.value,
        )
        csv_row, artifacts = self._build_record_payload(
            context=context,
            analysis=analysis,
            capture=capture,
            run_id=state.run_id,
        )
        next_state = self._recorder.record_cycle(
            run_dir=run_dir,
            state=state,
            csv_row=csv_row,
            artifacts=artifacts,
            halt_reason=halt_reason,
        )
        self._open_mains_with_cooldown(context, include_cooldown=halt_reason is None)
        execution = CycleExecution(
            cycle_index=cycle_index,
            terminal=verdict_terminal,
            verdict=analysis.verdict,
            trip_time_s=analysis.trip_time_s,
            led_state_at_gate=context.led_state_at_gate,
            degraded_flags=tuple(context.degraded_flags),
            notes=";".join(context.notes) if context.notes else analysis.notes,
            halt_reason=halt_reason,
            fault_category=category,
            latch_slow_clear=context.latch_slow_clear,
        )
        return execution, next_state, context.transitions

    def _attempt_cycle(self, context: _CycleContext, *, run_dir, run_id: str) -> None:
        cycle_index = context.cycle_index
        self._assert_sufficient_disk_space(run_dir)

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.SAFE_OFF, detail="cycle_start")
        safe_off(self._contactors)

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.MAINS_CLOSING)
        self._contactors.close_k1()
        self._contactors.close_k2()
        self._assert_no_mains_mismatch()

        self._transition(
            context.transitions,
            cycle_index=cycle_index,
            state=CycleState.WAITING_FOR_CHARGING,
        )
        gate = await_charging_gate(
            self._camera,
            roi=self._vision_roi,
            timeout_s=self._config.timing.boot_timeout_s,
            degraded_flag_out=context.degraded_flags,
            config=self._vision_optical_config,
            monotonic=self._now,
            sleep=self._sleep,
            logger=self._logger,
        )
        context.led_state_at_gate = gate.led_state
        if gate.degraded and DEGRADED_FLAG_CAMERA_UNAVAILABLE not in context.degraded_flags:
            context.degraded_flags.append(DEGRADED_FLAG_CAMERA_UNAVAILABLE)
            self._transition(
                context.transitions,
                cycle_index=cycle_index,
                state=CycleState.DEGRADED_FIXED_WAIT,
                detail="camera_unavailable",
            )
        if not gate.success and not gate.degraded:
            action, reason = gate_timeout_action(gate.led_state)
            if action == GateTimeoutAction.RETRY_EXTENDED_COOLDOWN:
                self._open_mains_with_cooldown(context, include_cooldown=False)
                raise _RetryCycle(reason)
            if action == GateTimeoutAction.HALT:
                self._open_mains_with_cooldown(context, include_cooldown=False)
                raise _SequencerHalt(
                    terminal=Terminal.HALTED,
                    category=FaultCategory.RIG,
                    reason=reason,
                )

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.SCOPE_CONFIGURING)
        self._scope.configure_for_cycle(self._scope_settings)

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.SCOPE_ARMING)
        self._scope.arm_single()

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.SCOPE_ARMED)
        if not self._poll_scope_armed():
            self._open_mains_with_cooldown(context, include_cooldown=False)
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason="scope_not_armed_timeout",
            )

        # Allow the fresh Single acquisition to settle, then confirm that an
        # unrelated transient has not consumed it before K3 can close.
        self._sleep(0.05)

        if not self._poll_scope_armed():
            self._open_mains_with_cooldown(
                context,
                include_cooldown=False,
            )
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason="scope_lost_armed_before_injection",
            )

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.INJECTING)
        gate_token = ChargingGateToken(cycle_index=cycle_index, granted_at_monotonic_s=self._now())
        self._contactors.close_k3(gate_token)
        k3_closed_s = self._now()
        context.k3_closed_monotonic_s = k3_closed_s
        self._assert_no_mains_mismatch()

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.ACQUIRING)
        acquired = self._poll_acquisition_with_backstop(context=context, k3_closed_s=k3_closed_s)
        if not acquired:
            self._capture_timeout_diagnostics_best_effort(context, run_dir=run_dir, run_id=run_id)
            self._open_mains_with_cooldown(context, include_cooldown=False)
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason=_SCOPE_TIMEOUT_REASON,
            )

    def _poll_scope_armed(self) -> bool:
        start_s = self._now()
        timeout_s = self._config.timing.scope_arm_timeout_s
        while self._now() - start_s <= timeout_s:
            if self._scope.wait_until_armed(timeout_s=timeout_s, now_monotonic_s=self._now()):
                return True
            self._sleep(0.01)
        return False

    def _poll_acquisition_with_backstop(self, *, context: _CycleContext, k3_closed_s: float) -> bool:
        # K3's 300 ms hard backstop (handoff safety invariant 9) must fire on
        # its own deadline even if the scope's acquisition-complete poll is
        # itself blocking (a scope that never returns from
        # wait_until_acquisition_complete must not keep leakage injection
        # closed indefinitely). `opened` is a single-fire latch so the
        # backstop path and the normal/timeout paths below - which can each
        # independently decide to open K3 - never issue a second, redundant
        # open_k3() for the same cycle.
        start_s = self._now()
        acq_timeout_s = self._config.timing.scope_acquisition_timeout_s
        k3_deadline = k3_closed_s + self._config.timing.k3_backstop_s
        opened = False
        while self._now() - start_s <= acq_timeout_s:
            now_s = self._now()
            if not opened and now_s >= k3_deadline:
                self._transition(
                    context.transitions,
                    cycle_index=context.cycle_index,
                    state=CycleState.INJECTION_OPENING,
                    detail="k3_backstop",
                )
                self._contactors.open_k3()
                context.k3_open_monotonic_s = self._now()
                context.k3_open_reason = "backstop"
                opened = True
            if self._scope.wait_until_acquisition_complete(
                timeout_s=min(0.01, acq_timeout_s - (now_s - start_s)),
                now_monotonic_s=now_s,
            ):
                if not opened:
                    self._transition(
                        context.transitions,
                        cycle_index=context.cycle_index,
                        state=CycleState.INJECTION_OPENING,
                        detail="normal",
                    )
                    self._contactors.open_k3()
                    context.k3_open_monotonic_s = self._now()
                    context.k3_open_reason = "normal"
                return True
            self._sleep(0.01)
        if not opened:
            self._transition(
                context.transitions,
                cycle_index=context.cycle_index,
                state=CycleState.INJECTION_OPENING,
                detail="acquisition_timeout",
            )
            self._contactors.open_k3()
            context.k3_open_monotonic_s = self._now()
            context.k3_open_reason = "acquisition_timeout"
        return False

    def _capture_timeout_diagnostics_best_effort(self, context: _CycleContext, *, run_dir, run_id: str) -> None:
        # Best-effort by design: an exception here must never prevent
        # safe-off or replace the primary halt reason (handoff safety
        # invariants 3-4). K3 is already commanded open by the caller
        # before this runs.
        self._transition(
            context.transitions,
            cycle_index=context.cycle_index,
            state=CycleState.DIAGNOSTICS_CAPTURING,
            detail=_SCOPE_TIMEOUT_REASON,
        )
        try:
            diagnostics = self._scope.capture_timeout_diagnostics()
        except Exception as exc:
            self._logger.warning(
                "cycle=%d timeout diagnostics capture failed: %s", context.cycle_index, exc
            )
            return
        try:
            self._recorder.write_timeout_diagnostics(
                run_dir=run_dir,
                run_id=run_id,
                cycle_index=context.cycle_index,
                diagnostics=diagnostics,
                k3_closed_monotonic_s=context.k3_closed_monotonic_s,
                k3_open_monotonic_s=context.k3_open_monotonic_s,
                k3_open_reason=context.k3_open_reason,
                primary_halt_reason=f"{FaultCategory.RIG.value}:{_SCOPE_TIMEOUT_REASON}",
            )
        except Exception as exc:
            self._logger.warning(
                "cycle=%d timeout diagnostics write failed: %s", context.cycle_index, exc
            )

    def _assert_sufficient_disk_space(self, run_dir) -> None:
        """Fault-matrix row: halt before energizing anything if the run/output
        filesystem is critically low, rather than commanding mains and
        injection for a cycle whose artifacts might not fit."""

        free_bytes = self._disk_usage(str(run_dir)).free
        min_free_bytes = self._config.paths.min_free_disk_gb * 1024**3
        if free_bytes < min_free_bytes:
            raise _SequencerHalt(
                terminal=Terminal.HALTED,
                category=FaultCategory.PERSISTENCE,
                reason="insufficient_disk_space",
            )

    def _assert_no_mains_mismatch(self) -> None:
        if self._contactors.detect_mains_command_mismatch(
            allowed_stagger_ms=self._config.timing.mains_stagger_ms,
            now_monotonic_s=self._now(),
        ):
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason="k1_k2_command_mismatch",
            )

    def _open_mains_with_cooldown(self, context: _CycleContext, *, include_cooldown: bool) -> None:
        self._transition(
            context.transitions,
            cycle_index=context.cycle_index,
            state=CycleState.MAINS_OPENING,
        )
        safe_off(self._contactors)
        if include_cooldown:
            self._transition(
                context.transitions,
                cycle_index=context.cycle_index,
                state=CycleState.COOLDOWN,
            )
            self._sleep(self._config.timing.cooldown_s)

    def _build_record_payload(
        self,
        *,
        context: _CycleContext,
        analysis: TripResult,
        capture,
        run_id: str,
    ) -> tuple[CycleCsvRow, CycleArtifacts]:
        notes = analysis.notes
        if context.notes:
            notes = f"{notes};{';'.join(context.notes)}" if notes else ";".join(context.notes)
        degraded = ",".join(sorted(set(context.degraded_flags)))
        row = CycleCsvRow.from_values(
            cycle_index=context.cycle_index,
            run_id=run_id,
            monotonic_start=context.monotonic_start,
            trip_time_s=analysis.trip_time_s,
            verdict=analysis.verdict.value,
            analysis_version=analysis.algorithm_version.value,
            led_state_at_gate=context.led_state_at_gate.value,
            degraded_flags=degraded,
            notes=notes,
        )
        sidecar = {
            "analysis": analysis.to_dict(),
            "state_transitions": [
                {
                    "state": transition.state.value,
                    "at_monotonic_s": transition.at_monotonic_s,
                    "detail": transition.detail,
                }
                for transition in context.transitions
            ],
            "scope_readback": dict(capture.settings_readback),
            "scope_preamble": dict(capture.preamble),
        }
        gate_frame = self._camera.latest_frame()
        gate_jpg = gate_frame.frame_bgr if gate_frame is not None else b""
        artifacts = CycleArtifacts(
            waveform_samples=capture.samples,
            waveform_preamble=dict(capture.preamble),
            scope_png=capture.scope_png,
            gate_jpg=gate_jpg,
            cycle_sidecar=sidecar,
        )
        return row, artifacts

    def _commit_and_halt(
        self,
        *,
        run_dir,
        state: RunState,
        context: _CycleContext,
        analysis: TripResult,
        capture,
        reason: str,
        category: FaultCategory,
        terminal: Terminal,
    ) -> tuple[CycleExecution, RunState, list[StateTransition]]:
        self._transition(
            context.transitions,
            cycle_index=context.cycle_index,
            state=CycleState.COMMITTING,
            detail=reason,
        )
        row, artifacts = self._build_record_payload(
            context=context,
            analysis=analysis,
            capture=capture,
            run_id=state.run_id,
        )
        halt_reason = f"{category.value}:{reason}"
        next_state = self._recorder.record_cycle(
            run_dir=run_dir,
            state=state,
            csv_row=row,
            artifacts=artifacts,
            halt_reason=halt_reason,
        )
        self._open_mains_with_cooldown(context, include_cooldown=False)
        execution = CycleExecution(
            cycle_index=context.cycle_index,
            terminal=terminal,
            verdict=analysis.verdict,
            trip_time_s=analysis.trip_time_s,
            led_state_at_gate=context.led_state_at_gate,
            degraded_flags=tuple(context.degraded_flags),
            notes=";".join(context.notes) if context.notes else analysis.notes,
            halt_reason=halt_reason,
            fault_category=category,
            latch_slow_clear=context.latch_slow_clear,
        )
        return execution, next_state, context.transitions

    def _halt_without_capture(
        self,
        context: _CycleContext,
        *,
        terminal: Terminal,
        category: FaultCategory,
        reason: str,
    ) -> CycleExecution:
        self._transition(
            context.transitions,
            cycle_index=context.cycle_index,
            state=CycleState.HALTED,
            detail=reason,
        )
        halt_reason = f"{category.value}:{reason}"
        return CycleExecution(
            cycle_index=context.cycle_index,
            terminal=terminal,
            verdict=None,
            trip_time_s=None,
            led_state_at_gate=context.led_state_at_gate,
            degraded_flags=tuple(context.degraded_flags),
            notes=";".join(context.notes),
            halt_reason=halt_reason,
            fault_category=category,
            latch_slow_clear=context.latch_slow_clear,
        )

    def _mark_halt_state(self, run_dir, state: RunState, execution: CycleExecution) -> RunState:
        if execution.halt_reason is None:
            return state
        halted = RunState(
            run_id=state.run_id,
            last_completed_cycle=state.last_completed_cycle,
            target_cycles=state.target_cycles,
            config_hash=state.config_hash,
            pass_count=state.pass_count,
            fail_count=state.fail_count,
            halt_reason=execution.halt_reason,
        )
        try:
            self._recorder._write_runstate_atomic(run_dir / "runstate.json", halted)  # noqa: SLF001
        except Exception as exc:
            raise PersistenceError("Unable to persist halted runstate") from exc
        return halted

    @staticmethod
    def _map_verdict(verdict: Verdict) -> tuple[Terminal, str | None, FaultCategory | None]:
        if verdict is Verdict.PASS:
            return (Terminal.PASS, None, None)
        if verdict is Verdict.FAIL:
            return (Terminal.FAIL, None, None)
        return (Terminal.NO_TRIP, f"{FaultCategory.DUT.value}:dut_no_trip", FaultCategory.DUT)

    def _transition(
        self,
        transitions: list[StateTransition],
        *,
        cycle_index: int,
        state: CycleState,
        detail: str = "",
    ) -> None:
        entry = StateTransition(
            cycle_index=cycle_index,
            state=state,
            at_monotonic_s=self._now(),
            detail=detail,
        )
        transitions.append(entry)
        self._logger.info(
            "cycle=%d state=%s t=%.6f detail=%s",
            cycle_index,
            state.value,
            entry.at_monotonic_s,
            detail,
        )


@dataclass(frozen=True)
class _SequencerHalt(Exception):
    """Internal control-flow signal, not an error condition.

    Raised from deep inside `_attempt_cycle` to unwind straight to
    `_run_cycle`'s single `except _SequencerHalt` handler, carrying exactly
    the terminal/category/reason that handler needs - the alternative would
    be threading a halt decision back up through every intermediate call as a
    return value, which is exactly the "ad hoc chain of nested conditionals"
    Phase 8 explicitly rules out.
    """

    terminal: Terminal
    category: FaultCategory
    reason: str


@dataclass(frozen=True)
class _RetryCycle(Exception):
    """Internal control-flow signal for the one-shot vision-gate retry
    (blinking-red-then-clears). Same rationale as `_SequencerHalt`."""

    reason: str


def _pack_waveform_blob(samples: bytes, preamble: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("samples.bin", samples)
        zf.writestr("preamble.json", json.dumps(preamble, sort_keys=True))
    return buffer.getvalue()
