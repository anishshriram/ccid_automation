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
from typing import Callable, Mapping

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
from ccid.forced_diagnostic_analysis import analyze_forced_diagnostic_waveform
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

# Distinguishes "trigger event register confirmed a trigger occurred, but
# the acquisition subsystem never reported Stop" from a genuine no-trigger
# condition (SCOPE_TRIGGER_DEBUG_LOG.md Entry 10) - these are different
# failure modes and must not be collapsed into the same halt reason.
_SCOPE_TRIGGERED_BUT_ACQUISITION_NOT_COMPLETED_REASON = "scope_triggered_but_acquisition_not_completed"

_SCOPE_STALE_TRIGGER_EVENT_BEFORE_ARM_REASON = "scope_stale_trigger_event_before_arm"
_SCOPE_TRIGGER_EVENT_BEFORE_INJECTION_REASON = "scope_trigger_event_before_injection"

# Entry 11: real-hardware evidence (TER=0 for the full 306.6 ms K3-closed
# window) confirmed a genuine no-trigger condition, not a
# triggered-but-stuck one. This delay gates a diagnostic-only forced
# acquisition (:TRIGger:FORCe) to see what the analog front end actually
# looks like when the real trigger doesn't fire - it is well inside the
# 300 ms K3 backstop, which this must never delay or otherwise affect.
_FORCED_DIAGNOSTIC_DELAY_S = 0.1


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
    # Entry 11: set if a live checkpoint read ever confirmed TER=1 this
    # cycle. read_trigger_event_register() is read-and-clear, so this is
    # the only record of that fact once a later read (or the post-timeout
    # diagnostics bundle's own :TER? query) would otherwise see a
    # since-cleared 0 and lose the evidence.
    live_trigger_event_seen: bool = False
    # Entry 13: split from the single forced_at_monotonic_s timestamp that
    # was previously (wrongly) assumed to correspond to the scope's own
    # waveform t=0 - it is a Pi-side monotonic instant, not a scope
    # timebase reference, and must never be mapped onto waveform samples.
    force_command_start_monotonic_s: float | None = None
    force_command_return_monotonic_s: float | None = None
    forced_acquisition_completion_monotonic_s: float | None = None
    # Best-effort, read-only per-stage snapshots (monotonic_s,
    # operation_condition, trigger_event_register, hal_status) recorded
    # throughout the cycle - see Sequencer._record_diagnostic_stage. Purely
    # descriptive; never influences cycle behavior.
    diagnostic_timeline: list[dict[str, object]] = field(default_factory=list)


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
        self._record_diagnostic_stage(context, "configuration_completion")

        # :TER? is a read-and-clear event register (confirmed in the
        # Keysight manual, Entry 11): a single nonzero read here cannot
        # distinguish a genuinely stale event left over from a prior
        # cycle/session (harmless once cleared) from an active problem.
        # Read twice - the first read clears any stale event, the second
        # verifies the baseline is actually clean - and only halt if the
        # *verification* read is still nonzero (SCOPE_TRIGGER_DEBUG_LOG.md
        # Entry 12: a real forced-diagnostic run halted here on what turned
        # out to be ordinary stale residue from a single-read check).
        baseline_clear_ter = self._scope.read_trigger_event_register()
        self._record_diagnostic_stage(
            context, "baseline_ter_clear_read", trigger_event_register=baseline_clear_ter
        )
        baseline_verify_ter = self._scope.read_trigger_event_register()
        self._record_diagnostic_stage(
            context, "baseline_ter_verify_read", trigger_event_register=baseline_verify_ter
        )
        if baseline_verify_ter:
            self._open_mains_with_cooldown(context, include_cooldown=False)
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason=_SCOPE_STALE_TRIGGER_EVENT_BEFORE_ARM_REASON,
            )

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.SCOPE_ARMING)
        self._record_diagnostic_stage(context, "single_command_start")
        self._scope.arm_single()
        self._record_diagnostic_stage(context, "single_command_return")

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.SCOPE_ARMED)
        if not self._poll_scope_armed():
            self._open_mains_with_cooldown(context, include_cooldown=False)
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason="scope_not_armed_timeout",
            )
        self._record_diagnostic_stage(context, "armed_observation_1")

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
        self._record_diagnostic_stage(context, "armed_observation_2")

        # Same intent as the armed recheck above, via a second, independent
        # signal: a trigger event latched between arm_single and here means
        # something fired before the deliberate K3 close, so any resulting
        # waveform would not correspond to the intended K3-close transient.
        pre_injection_ter = self._scope.read_trigger_event_register()
        self._record_diagnostic_stage(
            context, "pre_injection_ter_read", trigger_event_register=pre_injection_ter
        )
        if pre_injection_ter:
            self._open_mains_with_cooldown(context, include_cooldown=False)
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason=_SCOPE_TRIGGER_EVENT_BEFORE_INJECTION_REASON,
            )

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.INJECTING)
        gate_token = ChargingGateToken(cycle_index=cycle_index, granted_at_monotonic_s=self._now())
        self._contactors.close_k3(gate_token)
        k3_closed_s = self._now()
        context.k3_closed_monotonic_s = k3_closed_s
        self._record_diagnostic_stage(context, "k3_close")
        self._assert_no_mains_mismatch()

        self._transition(context.transitions, cycle_index=cycle_index, state=CycleState.ACQUIRING)
        acquired = self._poll_acquisition_with_backstop(context=context, k3_closed_s=k3_closed_s)
        if not acquired:
            # Diagnostics must run strictly after full safe-off (K1+K2+K3
            # all commanded open), not before - a hung/wedged diagnostics
            # query must never delay de-energizing the EVSE mains. See
            # SCOPE_TRIGGER_DEBUG_LOG.md Entry 3 for the incident that
            # required this ordering.
            self._open_mains_with_cooldown(context, include_cooldown=False)
            if context.force_command_return_monotonic_s is not None:
                self._capture_forced_diagnostic_best_effort(context, run_dir=run_dir, run_id=run_id)
            reason = self._capture_timeout_diagnostics_best_effort(
                context, run_dir=run_dir, run_id=run_id
            )
            raise _SequencerHalt(
                terminal=Terminal.RIG_FAULT,
                category=FaultCategory.RIG,
                reason=reason,
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
        forced_diagnostic_deadline = k3_closed_s + _FORCED_DIAGNOSTIC_DELAY_S
        opened = False
        forced_diagnostic_attempted = False
        while self._now() - start_s <= acq_timeout_s:
            now_s = self._now()
            if (
                not forced_diagnostic_attempted
                and not opened
                and now_s >= forced_diagnostic_deadline
            ):
                # Fast, bounded live-window work only (one TER read, one
                # fire-and-forget write) - see
                # _issue_forced_diagnostic_trigger. The actual waveform/PNG
                # transfer is deferred until after full safe-off so it can
                # never delay the backstop check just below.
                forced_diagnostic_attempted = True
                self._issue_forced_diagnostic_trigger(context)
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
                self._record_diagnostic_stage(context, "k3_open")
                opened = True
            if forced_diagnostic_attempted:
                # A forced trigger consumes the same single-shot
                # acquisition a real measurement would use -
                # wait_until_acquisition_complete can no longer distinguish
                # "genuinely triggered" from "we just forced it," so once
                # forced this loop must never again treat "complete" as a
                # real measurement success (SCOPE_TRIGGER_DEBUG_LOG.md
                # Entry 11). It still only returns via the backstop/timeout
                # paths below, exactly as an unforced no-trigger cycle
                # already does.
                #
                # Diagnostic-only: watch (via the loop's existing ~10 ms
                # cadence, no extra polling loop) for the run bit clearing
                # after a successful force, purely to record when it
                # happened - never to decide "acquired." See Entry 13.
                if (
                    context.force_command_return_monotonic_s is not None
                    and context.forced_acquisition_completion_monotonic_s is None
                ):
                    try:
                        condition = self._scope.read_operation_condition()
                    except Exception:
                        condition = None
                    if condition is not None and not (condition & (1 << 3)):
                        context.forced_acquisition_completion_monotonic_s = self._now()
                        self._record_diagnostic_stage(context, "acquisition_completion_observed")
                self._sleep(0.01)
                continue
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
                    self._record_diagnostic_stage(context, "k3_open")
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
            self._record_diagnostic_stage(context, "k3_open")
        return False

    def _issue_forced_diagnostic_trigger(self, context: _CycleContext) -> None:
        # Only the fast, bounded live-window operations happen here: one
        # :TER? read and one fire-and-forget :TRIGger:FORCe write, both the
        # same class of call already trusted elsewhere in this loop (e.g.
        # :OPERegister:CONDition? polled every ~10 ms). The actual
        # waveform/PNG transfer - the part that could plausibly stall, per
        # SCOPE_TRIGGER_DEBUG_LOG.md Entry 6 - is deliberately deferred to
        # _capture_forced_diagnostic_best_effort, called only after full
        # safe-off, so it can never delay the 300 ms K3 backstop check this
        # method runs alongside.
        try:
            gate_ter = self._scope.read_trigger_event_register()
        except Exception as exc:
            self._logger.warning(
                "cycle=%d forced-diagnostic TER pre-check failed, skipping force: %s",
                context.cycle_index,
                exc,
            )
            return
        self._record_diagnostic_stage(
            context, "forced_diagnostic_ter_gate_read", trigger_event_register=gate_ter
        )
        if gate_ter:
            # A real trigger occurred - forcing is neither needed nor
            # appropriate. Latched locally because this read just cleared
            # TER, so it's the only remaining record for Entry 10's
            # post-timeout reclassification.
            context.live_trigger_event_seen = True
            return
        context.force_command_start_monotonic_s = self._now()
        self._record_diagnostic_stage(context, "force_command_start")
        try:
            self._scope.force_trigger()
        except Exception as exc:
            self._logger.warning(
                "cycle=%d force_trigger() failed: %s", context.cycle_index, exc
            )
            return
        context.force_command_return_monotonic_s = self._now()
        self._record_diagnostic_stage(context, "force_command_return")

    def _capture_forced_diagnostic_best_effort(self, context: _CycleContext, *, run_dir, run_id: str) -> None:
        # Same best-effort contract as _capture_timeout_diagnostics_best_effort:
        # an exception here must never prevent safe-off (already complete by
        # the time the caller invokes this) or affect the halt reason -
        # this method's only job is preserving evidence.
        self._transition(
            context.transitions,
            cycle_index=context.cycle_index,
            state=CycleState.FORCED_DIAGNOSTIC_CAPTURING,
            detail="forced_trigger",
        )
        try:
            capture = self._scope.capture_after_acquire()
        except Exception as exc:
            self._logger.warning(
                "cycle=%d forced-diagnostic capture failed: %s", context.cycle_index, exc
            )
            return
        # Diagnostic-only burst identification, computed entirely from the
        # waveform's own samples/preamble - never from a Pi-side timestamp
        # (SCOPE_TRIGGER_DEBUG_LOG.md Entry 13). A failure here must not
        # prevent the raw waveform itself from still being written.
        waveform_analysis: Mapping[str, object] | None
        try:
            blob = _pack_waveform_blob(capture.samples, dict(capture.preamble))
            waveform_analysis = analyze_forced_diagnostic_waveform(blob).to_dict()
        except Exception as exc:
            self._logger.warning(
                "cycle=%d forced-diagnostic waveform analysis failed: %s", context.cycle_index, exc
            )
            waveform_analysis = None
        try:
            self._recorder.write_forced_diagnostic_capture(
                run_dir=run_dir,
                run_id=run_id,
                cycle_index=context.cycle_index,
                capture=capture,
                force_command_start_monotonic_s=context.force_command_start_monotonic_s,
                force_command_return_monotonic_s=context.force_command_return_monotonic_s,
                forced_acquisition_completion_monotonic_s=(
                    context.forced_acquisition_completion_monotonic_s
                ),
                k3_closed_monotonic_s=context.k3_closed_monotonic_s,
                diagnostic_timeline=context.diagnostic_timeline,
                waveform_analysis=waveform_analysis,
            )
        except Exception as exc:
            self._logger.warning(
                "cycle=%d forced-diagnostic write failed: %s", context.cycle_index, exc
            )

    def _capture_timeout_diagnostics_best_effort(self, context: _CycleContext, *, run_dir, run_id: str) -> str:
        # Best-effort by design: an exception here must never prevent
        # safe-off or replace the primary halt reason (handoff safety
        # invariants 3-4). Full safe-off (K1+K2+K3 all commanded open) has
        # already completed by the time the caller invokes this - a hung
        # diagnostics call must never be able to delay de-energizing the
        # EVSE mains.
        #
        # Returns the halt reason to raise: either a live checkpoint read
        # already confirmed TER=1 this cycle (Entry 11's
        # context.live_trigger_event_seen - a fact that a later :TER? read
        # elsewhere can no longer see, since the register is read-and-
        # clear), or the trigger-event-register value already present in
        # the diagnostics bundle (Entry 8) does. Either decides between the
        # generic never-triggered reason and the more specific
        # triggered-but-not-completed reason (Entry 10). A failed/partial
        # diagnostics capture - or a `trigger_event_register` reading this
        # method can't parse - falls back to the generic, already-proven
        # reason rather than asserting a trigger occurred on incomplete
        # evidence.
        reason = (
            _SCOPE_TRIGGERED_BUT_ACQUISITION_NOT_COMPLETED_REASON
            if context.live_trigger_event_seen
            else _SCOPE_TIMEOUT_REASON
        )
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
            return reason
        if _diagnostics_trigger_event_seen(diagnostics.settings):
            reason = _SCOPE_TRIGGERED_BUT_ACQUISITION_NOT_COMPLETED_REASON
        try:
            self._recorder.write_timeout_diagnostics(
                run_dir=run_dir,
                run_id=run_id,
                cycle_index=context.cycle_index,
                diagnostics=diagnostics,
                k3_closed_monotonic_s=context.k3_closed_monotonic_s,
                k3_open_monotonic_s=context.k3_open_monotonic_s,
                k3_open_reason=context.k3_open_reason,
                primary_halt_reason=f"{FaultCategory.RIG.value}:{reason}",
            )
        except Exception as exc:
            self._logger.warning(
                "cycle=%d timeout diagnostics write failed: %s", context.cycle_index, exc
            )
        return reason

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

    def _record_diagnostic_stage(
        self,
        context: _CycleContext,
        stage: str,
        *,
        trigger_event_register: bool | None = None,
    ) -> None:
        # Best-effort, read-only, purely descriptive - see
        # SCOPE_TRIGGER_DEBUG_LOG.md Entry 13. Must never raise into the
        # caller or influence cycle behavior; a failed operation_condition
        # read is recorded as None rather than aborting the snapshot.
        # trigger_event_register is only ever passed in from a checkpoint
        # that already legitimately read :TER? elsewhere (the baseline
        # clear/verify reads, the pre-injection read, the forced-diagnostic
        # gate read) - this method never issues its own :TER? query, since
        # every read consumes/clears that register and an extra read here
        # would corrupt the evidence the real checkpoints depend on.
        now_s = self._now()
        try:
            operation_condition: int | None = self._scope.read_operation_condition()
        except Exception:
            operation_condition = None
        try:
            hal_status = self._scope.status().value
        except Exception:
            hal_status = None
        context.diagnostic_timeline.append(
            {
                "stage": stage,
                "monotonic_s": now_s,
                "operation_condition": operation_condition,
                "trigger_event_register": trigger_event_register,
                "hal_status": hal_status,
            }
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


def _diagnostics_trigger_event_seen(settings: Mapping[str, object]) -> bool:
    """Parses the `trigger_event_register` field already captured in a
    timeout-diagnostics bundle's settings (Entry 8's `:TER?` query).
    Returns False - not just "unknown" - for a missing key (diagnostics
    aborted before reaching this query) or an unparseable value (e.g. a
    recorded `"<query failed: ...>"` error string): the caller must not
    reclassify a halt as "trigger confirmed" on anything less than an
    unambiguous reading."""
    raw = settings.get("trigger_event_register")
    if raw is None:
        return False
    try:
        return int(float(str(raw))) != 0
    except (TypeError, ValueError):
        return False
