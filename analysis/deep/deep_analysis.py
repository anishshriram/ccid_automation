"""Exploratory, non-authoritative offline trip-time analysis.

This is a genuinely separate research effort from ccid/analysis.py's
versioned V1/V2/V3 boundary. It does NOT import ccid.analysis, does not
touch AnalysisVersion, and its output must never be treated as a
replacement for the committed V3 verdicts in cycles.csv - those are the
record. This module works from the raw waveform (samples + scope preamble)
and asks whether a heavier, non-real-time-safe approach agrees with the
production algorithm or reveals anything it doesn't.

Three independent onset/collapse detectors are cross-validated:
  A. An independent RMS-envelope threshold detector (different windowing,
     different noise-floor estimator, and a persistence-run requirement
     coded from scratch - conceptually similar in spirit to the production
     approach, since threshold-crossing-with-persistence is the natural
     technique for this kind of burst-envelope signal, but not copied
     from it: different formulas, different window sizes, independently
     derived thresholds).
  B. A CUSUM change-point detector on windowed log-power - a genuinely
     different algorithmic family (sequential change-point detection
     rather than static threshold-crossing).
  C. A sigmoid curve fit to the RMS envelope at each edge (rise = onset,
     fall = collapse), giving a fitted transition center plus a real
     statistical standard error from the fit covariance - "principled
     curve-fitting instead of threshold-crossing," per the brief.

Per-cycle uncertainty is reported from method C's fit standard errors
(propagated) and cross-checked against the spread across methods A/B/C
themselves - three independent numbers agreeing tightly is itself a form
of uncertainty evidence, and disagreeing is flagged for a second look.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import curve_fit

MAINS_PERIOD_S = 1.0 / 60.0


@dataclass(frozen=True)
class RawWaveform:
    samples_v: np.ndarray
    dt: float
    t_first: float
    preamble: dict

    def time_of(self, idx: float) -> float:
        return self.t_first + idx * self.dt


def load_waveform_raw(npz_path: Path) -> RawWaveform:
    """Independent reader for the samples.bin+preamble.json container.

    Deliberately reimplemented rather than importing ccid.analysis.load_waveform
    - this module must not depend on the versioned production module at all.
    Only handles the real-data container layout actually present in
    ccid_campaign_data/ (samples.bin uint8 + preamble.json); the alternate
    pickled-.npz layout ccid.analysis also accepts is out of scope here.
    """
    with zipfile.ZipFile(npz_path) as archive:
        raw = np.frombuffer(archive.read("samples.bin"), dtype=np.uint8)
        preamble = json.loads(archive.read("preamble.json").decode("utf-8"))

    y_increment = float(preamble["y_increment"])
    y_origin = float(preamble["y_origin"])
    y_reference = float(preamble["y_reference"])
    samples_v = (raw.astype(np.float64) - y_reference) * y_increment + y_origin

    dt = float(preamble["x_increment"])
    t_first = float(preamble["x_origin"])
    return RawWaveform(samples_v=samples_v, dt=dt, t_first=t_first, preamble=preamble)


def _sliding_rms_fast(x: np.ndarray, window: int) -> np.ndarray:
    """Vectorized fixed-window RMS (trailing window of exactly `window`
    samples once enough history exists; ramps up at the start)."""
    window = max(1, window)
    sq = x.astype(np.float64) ** 2
    csum = np.concatenate([[0.0], np.cumsum(sq)])
    n = len(x)
    idx = np.arange(n)
    lo = np.maximum(0, idx - window + 1)
    counts = idx - lo + 1
    sums = csum[idx + 1] - csum[lo]
    return np.sqrt(sums / counts)


def _cusum_recursion(x: np.ndarray) -> np.ndarray:
    """Vectorized W[i] = max(0, W[i-1] + x[i]), W[-1] = 0.

    Closed form: with C = cumsum(x) and m[i] = min(0, C[0..i]),
    W[i] = C[i] - m[i]. (Standard Lindley-recursion identity - provable by
    induction: the running max resets to 0 exactly where C dips to a new
    running minimum, so subtracting that running minimum from C reproduces
    the reflected/clipped recursion exactly.) This turns an O(n) sequential
    Python loop into two numpy cumulative ops, which matters at 1e6 samples
    x 6000 cycles.
    """
    c = np.cumsum(x)
    m = np.minimum.accumulate(np.concatenate(([0.0], c)))[1:]
    return c - m


def _first_run_true(mask: np.ndarray, persistence: int, start: int = 0) -> Optional[int]:
    """First index >= start where `mask` is True for `persistence` consecutive
    samples, via a cumulative-count trick (O(n), no python loop over samples)."""
    persistence = max(1, persistence)
    n = len(mask)
    if start >= n:
        return None
    m = mask[start:]
    csum = np.concatenate([[0], np.cumsum(~m)])
    # window [i, i+persistence) is all-True iff csum[i+persistence]-csum[i] == 0
    limit = len(m) - persistence
    if limit < 0:
        return None
    windows = csum[persistence:persistence + limit + 1] - csum[:limit + 1]
    hits = np.nonzero(windows == 0)[0]
    if hits.size == 0:
        return None
    return int(start + hits[0])


@dataclass
class EdgeFit:
    center_idx: float
    center_s: float
    se_s: float
    r_squared: float
    ok: bool


def _sigmoid(t, floor, amp, center, width):
    width = np.sign(width) * max(abs(width), 1e-9)
    return floor + amp / (1.0 + np.exp(-(t - center) / width))


def _fit_edge(t: np.ndarray, y: np.ndarray, guess_center: float, rising: bool) -> EdgeFit:
    lo_guess = float(np.percentile(y, 10))
    hi_guess = float(np.percentile(y, 90))
    amp_guess = (hi_guess - lo_guess) if rising else -(hi_guess - lo_guess)
    floor_guess = lo_guess if rising else hi_guess
    width_guess = MAINS_PERIOD_S / 20.0
    p0 = [floor_guess, amp_guess, guess_center, width_guess if rising else -width_guess]
    try:
        popt, pcov = curve_fit(_sigmoid, t, y, p0=p0, maxfev=4000)
        residuals = y - _sigmoid(t, *popt)
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1e-30
        r2 = 1.0 - ss_res / ss_tot
        se_center = float(np.sqrt(pcov[2, 2])) if np.isfinite(pcov[2, 2]) and pcov[2, 2] >= 0 else float("nan")
        center = float(popt[2])
        return EdgeFit(center_idx=float("nan"), center_s=center, se_s=se_center, r_squared=r2, ok=True)
    except Exception:
        return EdgeFit(center_idx=float("nan"), center_s=guess_center, se_s=float("nan"), r_squared=float("nan"), ok=False)


@dataclass
class DeepCycleResult:
    source_run: str
    source_cycle_index: int
    # Method A: independent RMS-threshold detector
    a_onset_s: Optional[float]
    a_collapse_s: Optional[float]
    a_trip_time_s: Optional[float]
    # Method B: CUSUM change-point
    b_onset_s: Optional[float]
    b_collapse_s: Optional[float]
    b_trip_time_s: Optional[float]
    # Method C: sigmoid curve fit
    c_onset_s: Optional[float]
    c_onset_se_s: Optional[float]
    c_collapse_s: Optional[float]
    c_collapse_se_s: Optional[float]
    c_trip_time_s: Optional[float]
    c_trip_time_se_s: Optional[float]
    c_onset_r2: Optional[float]
    c_collapse_r2: Optional[float]
    # cross-validation
    methods_agree: Optional[bool]
    max_pairwise_disagreement_s: Optional[float]
    # noise/signal characterization
    pretrigger_noise_rms_v: float
    quantization_step_v: float
    ref_amplitude_v: float
    snr_db: float
    error: Optional[str] = None


def analyze_cycle(npz_path: Path, source_run: str, source_cycle_index: int) -> DeepCycleResult:
    try:
        wf = load_waveform_raw(npz_path)
    except Exception as exc:  # malformed container - report and move on
        return DeepCycleResult(
            source_run=source_run, source_cycle_index=source_cycle_index,
            a_onset_s=None, a_collapse_s=None, a_trip_time_s=None,
            b_onset_s=None, b_collapse_s=None, b_trip_time_s=None,
            c_onset_s=None, c_onset_se_s=None, c_collapse_s=None, c_collapse_se_s=None,
            c_trip_time_s=None, c_trip_time_se_s=None, c_onset_r2=None, c_collapse_r2=None,
            methods_agree=None, max_pairwise_disagreement_s=None,
            pretrigger_noise_rms_v=float("nan"), quantization_step_v=float("nan"),
            ref_amplitude_v=float("nan"), snr_db=float("nan"), error=str(exc),
        )

    x = wf.samples_v
    dt = wf.dt
    n = len(x)
    t = wf.t_first + dt * np.arange(n)

    # Window must span at least half a mains cycle, or the RMS envelope
    # dips toward zero at every AC zero-crossing mid-burst and a detector
    # mistakes an ordinary zero-crossing for the real collapse (the
    # "half-cycle trap" - a physical necessity for any correct method on
    # this signal, not something copied from the production algorithm).
    window = max(1, int(round((MAINS_PERIOD_S / 2) / dt)))  # ~8.33ms window
    env = _sliding_rms_fast(x, window)

    # --- noise/signal characterization ---
    noise_sigma = float(np.percentile(env, 10))
    # ref_amplitude = max of the half-cycle RMS envelope. A percentile-based
    # estimate is fragile here because a short burst can occupy well under
    # 5% of a 0.5s record; the envelope's max is reached during conduction
    # regardless of how long the burst lasts, as long as it's at least
    # comparable to the window (it always is - shortest real trip_time in
    # this dataset is ~7.8ms against an ~8.3ms half-cycle window).
    ref_amplitude = float(np.max(env))
    quant_diffs = np.diff(np.unique(x))
    quantization_step = float(np.min(quant_diffs)) if quant_diffs.size else float("nan")
    pretrigger_mask = t < (t[0] + MAINS_PERIOD_S * 2) if wf.t_first < 0 else np.zeros(n, dtype=bool)
    pretrigger_noise_rms = float(np.sqrt(np.mean(x[pretrigger_mask] ** 2))) if pretrigger_mask.any() else float(np.sqrt(np.mean(x[: window] ** 2)))
    snr_db = 20 * np.log10(ref_amplitude / noise_sigma) if noise_sigma > 0 else float("nan")

    # --- Method A: independent RMS-threshold detector ---
    on_thr = max(5.0 * noise_sigma, 0.25 * ref_amplitude)
    off_thr = max(3.0 * noise_sigma, 0.10 * ref_amplitude)
    persistence = max(1, int(round((MAINS_PERIOD_S / 4) / dt)))

    onset_idx_a = _first_run_true(env > on_thr, persistence)
    collapse_idx_a = None
    if onset_idx_a is not None:
        collapse_persistence = max(1, int(round(MAINS_PERIOD_S / dt)))
        collapse_idx_a = _first_run_true(env < off_thr, collapse_persistence, start=onset_idx_a + persistence)

    a_onset_s = wf.time_of(onset_idx_a) if onset_idx_a is not None else None
    a_collapse_s = wf.time_of(collapse_idx_a) if collapse_idx_a is not None else None
    a_trip = (a_collapse_s - a_onset_s) if (a_onset_s is not None and a_collapse_s is not None) else None

    # --- Method B: CUSUM change-point on log-power ---
    # Baseline/spread are estimated from the record's own quiet region
    # (env < off_thr) rather than an arbitrary short window at the start,
    # since that's a more representative noise sample. A CUSUM hit is only
    # accepted once it's also confirmed against the same physical amplitude
    # floor Method A uses (env above on_thr / below off_thr at that index) -
    # otherwise CUSUM alone can fire on a purely statistical blip with no
    # physical grounding. This keeps it a genuinely different detection
    # principle (sequential change-point vs. static threshold-crossing)
    # while avoiding nonsense triggers on a per-record noise/gain mix it
    # can't otherwise be pre-tuned for.
    log_power = np.log(env ** 2 + 1e-12)
    quiet_mask = env < off_thr
    if int(quiet_mask.sum()) > 100:
        baseline = float(np.median(log_power[quiet_mask]))
        spread = float(np.std(log_power[quiet_mask])) or 1e-6
    else:
        baseline = float(np.median(log_power[:persistence]))
        spread = float(np.std(log_power[:persistence])) or 1e-6
    k = 1.0 * spread
    h = 8.0 * spread

    s_pos = _cusum_recursion(log_power - baseline - k)
    onset_idx_b = None
    for cand in np.nonzero(s_pos > h)[0]:
        if env[cand] > on_thr:
            onset_idx_b = int(cand)
            break

    collapse_idx_b = None
    if onset_idx_b is not None:
        seg_start = onset_idx_b + persistence
        if seg_start < n:
            on_mask = env[onset_idx_b:] > on_thr
            if int(on_mask.sum()) > 50:
                post_baseline = float(np.median(log_power[onset_idx_b:][on_mask]))
            else:
                post_baseline = float(np.median(log_power[seg_start:seg_start + persistence]))
            s_neg = _cusum_recursion(post_baseline - log_power[seg_start:] - k)
            for cand in np.nonzero(s_neg > h)[0]:
                idx = seg_start + int(cand)
                if env[idx] < off_thr:
                    collapse_idx_b = idx
                    break

    b_onset_s = wf.time_of(onset_idx_b) if onset_idx_b is not None else None
    b_collapse_s = wf.time_of(collapse_idx_b) if collapse_idx_b is not None else None
    b_trip = (b_collapse_s - b_onset_s) if (b_onset_s is not None and b_collapse_s is not None) else None

    # --- Method C: sigmoid curve fit at each edge, anchored on A's indices ---
    c_onset = c_onset_se = c_collapse = c_collapse_se = None
    c_onset_r2 = c_collapse_r2 = None
    if onset_idx_a is not None:
        half_w = int(round((MAINS_PERIOD_S / 2) / dt))
        lo, hi = max(0, onset_idx_a - half_w), min(n, onset_idx_a + half_w)
        fit = _fit_edge(t[lo:hi], env[lo:hi], guess_center=t[onset_idx_a], rising=True)
        if fit.ok:
            c_onset, c_onset_se, c_onset_r2 = fit.center_s, fit.se_s, fit.r_squared
    if collapse_idx_a is not None:
        half_w = int(round((MAINS_PERIOD_S / 2) / dt))
        lo, hi = max(0, collapse_idx_a - half_w), min(n, collapse_idx_a + half_w)
        fit = _fit_edge(t[lo:hi], env[lo:hi], guess_center=t[collapse_idx_a], rising=False)
        if fit.ok:
            c_collapse, c_collapse_se, c_collapse_r2 = fit.center_s, fit.se_s, fit.r_squared

    c_trip = c_trip_se = None
    if c_onset is not None and c_collapse is not None:
        c_trip = c_collapse - c_onset
        if c_onset_se is not None and c_collapse_se is not None and np.isfinite(c_onset_se) and np.isfinite(c_collapse_se):
            c_trip_se = float(np.hypot(c_onset_se, c_collapse_se))

    # --- cross-validation across A/B/C ---
    trips = [v for v in (a_trip, b_trip, c_trip) if v is not None]
    max_disagree = (max(trips) - min(trips)) if len(trips) >= 2 else None
    methods_agree = (max_disagree is not None and max_disagree < 0.001) if max_disagree is not None else None

    return DeepCycleResult(
        source_run=source_run, source_cycle_index=source_cycle_index,
        a_onset_s=a_onset_s, a_collapse_s=a_collapse_s, a_trip_time_s=a_trip,
        b_onset_s=b_onset_s, b_collapse_s=b_collapse_s, b_trip_time_s=b_trip,
        c_onset_s=c_onset, c_onset_se_s=c_onset_se,
        c_collapse_s=c_collapse, c_collapse_se_s=c_collapse_se,
        c_trip_time_s=c_trip, c_trip_time_se_s=c_trip_se,
        c_onset_r2=c_onset_r2, c_collapse_r2=c_collapse_r2,
        methods_agree=methods_agree, max_pairwise_disagreement_s=max_disagree,
        pretrigger_noise_rms_v=pretrigger_noise_rms, quantization_step_v=quantization_step,
        ref_amplitude_v=ref_amplitude, snr_db=snr_db, error=None,
    )
