from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import io
import json
import logging
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
    ContactorName,
    ScopeInterface,
    ScopeSettings,
)
from ccid.recorder import CycleArtifacts, CycleCsvRow, RunRecorder, RunState
from ccid.safety import safe_off
from ccid.states import CycleState, LedState, Terminal

_LOGGER = logging.getLogger(__name__)


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
        self._recorder = recorder
        self._scope_settings = scope_settings or ScopeSettings()
        self._now = monotonic_now
        self._sleep = sleep
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
                self._attempt_cycle(context)
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

    def _attempt_cycle(self, context: _CycleContext) -> None:
        cycle_index = context.cycle_index
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

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.INJECTING)
        gate_token = ChargingGateToken(cycle_index=cycle_index, granted_at_monotonic_s=self._now())
        self._contactors.close_k3(gate_token)
        k3_closed_s = self._now()
        self._assert_no_mains_mismatch()

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.ACQUIRING)
        acquired = self._poll_acquisition_with_backstop(context=context, k3_closed_s=k3_closed_s)
        if not acquired:
            self._open_mains_with_cooldown(context, include_cooldown=False)
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason="scope_never_triggered_or_acquire_timeout",
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
                opened = True
            if self._scope.wait_until_acquisition_complete(
                timeout_s=acq_timeout_s,
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
        return False

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
    terminal: Terminal
    category: FaultCategory
    reason: str


@dataclass(frozen=True)
class _RetryCycle(Exception):
    reason: str


def _pack_waveform_blob(samples: bytes, preamble: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("samples.bin", samples)
        zf.writestr("preamble.json", json.dumps(preamble, sort_keys=True))
    return buffer.getvalue()
