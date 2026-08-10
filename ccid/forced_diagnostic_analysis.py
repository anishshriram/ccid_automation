"""Offline, diagnostic-only analysis of a forced-diagnostic waveform.

SCOPE_TRIGGER_DEBUG_LOG.md Entry 13: a forced-diagnostic capture (Entry 11)
showed a large bipolar burst (~-167 V to +141 V), and an earlier attempt to
say when it happened relative to K3 close assumed a single Pi-side
monotonic timestamp (`force_command_*_monotonic_s`) corresponds to the
scope waveform's own t=0. That assumption is unsupported - the Pi's
monotonic clock and the scope's internal waveform timebase are not
synchronized by anything in this system. This module deliberately does
NOT use any Pi-side timestamp: every result here is computed purely from
the waveform samples and the scope-supplied preamble time base
(`x_increment`/`x_origin`, via `ccid.analysis.load_waveform`), which is
self-consistent on its own.

This is diagnostic-only, mirroring `ccid.analysis`'s low-level numeric
primitives (`load_waveform`, `rms`) but never its verdict-producing
functions. Callers must never feed this module's output into
`analyze_waveform`, a `Verdict`, `cycles.csv`, or any PASS/FAIL decision -
it exists only to describe a forced, non-measurement capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ccid.analysis import Waveform, load_waveform, rms

DEFAULT_POSITIVE_THRESHOLD_V = 20.0
DEFAULT_NEGATIVE_THRESHOLD_V = -20.0
# Minimum duration a threshold excursion must persist to count as the
# sustained onset/collapse, rather than a single noisy sample.
DEFAULT_SUSTAINED_DURATION_S = 0.0005
DEFAULT_COLLAPSE_THRESHOLD_V = 5.0
DEFAULT_COLLAPSE_SUSTAINED_S = 0.001


@dataclass(frozen=True)
class ForcedDiagnosticWaveformSummary:
    """Purely descriptive summary of a forced-diagnostic capture. Every
    field is derived from the waveform's own samples/preamble time base -
    never a real measurement, never used for PASS/FAIL or trip-time
    calculation."""

    sample_count: int
    duration_s: float
    min_v: float
    max_v: float
    rms_v: float
    positive_threshold_v: float
    negative_threshold_v: float
    positive_crossing_count: int
    first_positive_crossing_s: float | None
    negative_crossing_count: int
    first_negative_crossing_s: float | None
    sustained_onset_s: float | None
    quiet_baseline_rms_v: float | None
    quiet_baseline_duration_s: float | None
    collapse_s: float | None
    burst_duration_s: float | None
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "duration_s": self.duration_s,
            "min_v": self.min_v,
            "max_v": self.max_v,
            "rms_v": self.rms_v,
            "positive_threshold_v": self.positive_threshold_v,
            "negative_threshold_v": self.negative_threshold_v,
            "positive_crossing_count": self.positive_crossing_count,
            "first_positive_crossing_s": self.first_positive_crossing_s,
            "negative_crossing_count": self.negative_crossing_count,
            "first_negative_crossing_s": self.first_negative_crossing_s,
            "sustained_onset_s": self.sustained_onset_s,
            "quiet_baseline_rms_v": self.quiet_baseline_rms_v,
            "quiet_baseline_duration_s": self.quiet_baseline_duration_s,
            "collapse_s": self.collapse_s,
            "burst_duration_s": self.burst_duration_s,
            "notes": self.notes,
        }


def analyze_forced_diagnostic_waveform(
    waveform_npz_bytes: bytes,
    *,
    positive_threshold_v: float = DEFAULT_POSITIVE_THRESHOLD_V,
    negative_threshold_v: float = DEFAULT_NEGATIVE_THRESHOLD_V,
    sustained_duration_s: float = DEFAULT_SUSTAINED_DURATION_S,
    collapse_threshold_v: float = DEFAULT_COLLAPSE_THRESHOLD_V,
    collapse_sustained_s: float = DEFAULT_COLLAPSE_SUSTAINED_S,
) -> ForcedDiagnosticWaveformSummary:
    """Identifies the burst directly from a forced-diagnostic waveform's own
    samples and preamble time base. Diagnostic only - must never be called
    on a real (triggered) measurement capture and must never feed
    PASS/FAIL or trip-time calculation."""

    waveform = load_waveform(waveform_npz_bytes)
    samples = waveform.samples_v
    n = int(samples.size)

    min_v = float(np.min(samples))
    max_v = float(np.max(samples))
    rms_v = rms(samples)

    onset_threshold_v = max(abs(positive_threshold_v), abs(negative_threshold_v))
    onset_run = max(1, int(round(sustained_duration_s / waveform.sample_interval_s)))
    above_onset = np.abs(samples) >= onset_threshold_v
    onset_index = _first_sustained_run_start(above_onset, onset_run)
    sustained_onset_s = waveform.time_of_index(onset_index) if onset_index is not None else None

    if onset_index is not None and onset_index > 0:
        quiet_baseline_rms_v = rms(samples[:onset_index])
        quiet_baseline_duration_s = onset_index * waveform.sample_interval_s
    elif onset_index is None:
        quiet_baseline_rms_v = rms_v
        quiet_baseline_duration_s = waveform.duration_s
    else:
        quiet_baseline_rms_v = None
        quiet_baseline_duration_s = 0.0

    collapse_s = None
    if onset_index is not None:
        collapse_run = max(1, int(round(collapse_sustained_s / waveform.sample_interval_s)))
        below_collapse = np.abs(samples) < collapse_threshold_v
        tail_hit = _first_sustained_run_start(below_collapse[onset_index:], collapse_run)
        if tail_hit is not None:
            collapse_s = waveform.time_of_index(onset_index + tail_hit)

    burst_duration_s = (
        collapse_s - sustained_onset_s
        if sustained_onset_s is not None and collapse_s is not None
        else None
    )

    positive_crossing_count, first_positive_crossing_s = _threshold_crossings(
        samples, waveform, lambda v: v >= positive_threshold_v
    )
    negative_crossing_count, first_negative_crossing_s = _threshold_crossings(
        samples, waveform, lambda v: v <= negative_threshold_v
    )

    notes = (
        "Diagnostic-only burst summary computed entirely from the forced "
        "waveform's own samples and preamble time base (x_increment/"
        "x_origin) - never from a Pi-side monotonic timestamp. Not a "
        "measurement; never used for PASS/FAIL or trip-time calculation."
    )

    return ForcedDiagnosticWaveformSummary(
        sample_count=n,
        duration_s=waveform.duration_s,
        min_v=min_v,
        max_v=max_v,
        rms_v=rms_v,
        positive_threshold_v=positive_threshold_v,
        negative_threshold_v=negative_threshold_v,
        positive_crossing_count=positive_crossing_count,
        first_positive_crossing_s=first_positive_crossing_s,
        negative_crossing_count=negative_crossing_count,
        first_negative_crossing_s=first_negative_crossing_s,
        sustained_onset_s=sustained_onset_s,
        quiet_baseline_rms_v=quiet_baseline_rms_v,
        quiet_baseline_duration_s=quiet_baseline_duration_s,
        collapse_s=collapse_s,
        burst_duration_s=burst_duration_s,
        notes=notes,
    )


def _first_sustained_run_start(mask: np.ndarray, min_run: int) -> int | None:
    """First index at which `mask` is True for at least `min_run`
    consecutive samples, or None if no such run exists."""

    min_run = max(1, int(min_run))
    if mask.size < min_run:
        return None
    counts = mask.astype(np.int64)
    cumulative = np.concatenate(([0], np.cumsum(counts)))
    window_sums = cumulative[min_run:] - cumulative[:-min_run]
    hits = np.nonzero(window_sums >= min_run)[0]
    if hits.size == 0:
        return None
    return int(hits[0])


def _threshold_crossings(
    samples: np.ndarray, waveform: Waveform, predicate
) -> tuple[int, float | None]:
    mask = predicate(samples)
    rising = np.flatnonzero(np.diff(mask.astype(np.int8)) == 1) + 1
    if mask.size and mask[0]:
        rising = np.concatenate(([0], rising))
    count = int(rising.size)
    first_s = waveform.time_of_index(int(rising[0])) if count else None
    return count, first_s
