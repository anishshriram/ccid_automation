"""Analysis boundary: raw waveform in, trip result out.

Locked behavior (handoff sections 4, 6, 9, 11):

- The trip-time *algorithm* is deliberately deferred ("capture first, compute
  later"). This module is therefore a **versioned boundary**, not a final
  algorithm: `AnalysisVersion` tags every result so a later algorithm can be
  swapped in and replayed offline (`tools/replay_waveform.py`) without any
  change to the stored data format or to the `cycles.csv` schema.
- The measurand is the duration of leakage-current flow, from injection
  (K3 close) to CCID clearing (envelope collapse to zero).
- Verdict table (final):
      trip <= 24.97 ms                 -> PASS     (continue)
      24.97 ms < trip < 100 ms         -> FAIL     (log, alert, continue)
      trip >= 100 ms, or no trip       -> NO_TRIP  (HALT run)
- The leakage current is AC: it crosses zero twice per mains cycle *while still
  flowing*. A naive threshold/width detector measures one half cycle (8.33 ms)
  instead of the burst. Trip time here is therefore always derived from a burst
  **envelope** (a leading half-cycle sliding maximum), never from a single
  threshold-crossing width.
- Sanity checks (signal present, pre-trigger leakage / K3 stuck closed, no-trip
  persistence, record length, burst start near t=0, clean collapse) are recorded
  and logged, but they never veto a computed verdict. Recording both the number
  and the doubts about it is the entire point.
- Analysis never runs in the hot path of a cycle beyond the crude inline sanity
  check; re-analysis happens offline against the stored `.npz`.

Endpoint definition (the open issue from handoff section 4)
-----------------------------------------------------------
24.97 ms sits 30 us below the three-half-cycle mark (3 x 8.333 ms = 25.00 ms),
so the definition of t=0 and t=end can flip a verdict. UL 2231-2 section 23.3.1
is the authority; if it defines the endpoints, that definition wins and this
module must be re-versioned to match. Until that is confirmed on paper, the
chosen definition is written into `config.yaml`
(`analysis.endpoint_definition`) and is reproduced in
`DEFAULT_ENDPOINT_DEFINITION` below. It is frozen for the campaign because it is
part of the config hash.

Known v1 endpoint bias, stated explicitly so nobody rediscovers it at cycle
4000: the burst end is confirmed by an envelope collapse and then refined
forward to the last sample above the residual floor, so on clean data the
endpoint lands within a few microseconds of the true collapse, while heavy
probe noise raises the floor and biases the measurement early by up to
`asin(floor / burst_amplitude) / (2*pi*f_line)` (a few hundred microseconds at
5 % noise). The raw data is retained precisely so a later version can do
better, and every threshold actually used is written into the result notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import io
import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping, MutableMapping
import zipfile

import numpy as np

from ccid.errors import WaveformFormatError

_LOGGER = logging.getLogger(__name__)

_EPSILON = 1e-12


class AnalysisVersion(str, Enum):
    """Version tag of the trip-time algorithm that produced a result.

    Stored per cycle in `cycles.csv` (`analysis_version`) so a later reader can
    tell which rows were computed under which algorithm. Adding a member is the
    supported way to change the algorithm; the CSV schema and the `.npz` format
    do not change.
    """

    V1 = "v1"
    V2 = "v2"


CURRENT_ANALYSIS_VERSION = AnalysisVersion.V2


class Verdict(str, Enum):
    """Per-cycle verdict from the locked verdict table (handoff section 3)."""

    PASS = "PASS"
    FAIL = "FAIL"
    NO_TRIP = "NO_TRIP"

    @property
    def halts_run(self) -> bool:
        """Only NO_TRIP halts the run; FAIL logs, alerts, and continues."""

        return self is Verdict.NO_TRIP


V1_ENDPOINT_DEFINITION = (
    "v1 endpoints: t0 is the K3 close (leakage injection) instant expressed in the "
    "scope time base, taken from the per-cycle sidecar when available and otherwise "
    "from the trigger instant (x_origin reference, t=0). The burst end is confirmed "
    "when the envelope (leading half-mains-cycle sliding maximum of |v|) stays below "
    "the collapse threshold for at least one full mains cycle; t_end is then the last "
    "sample above the residual floor at or after the final collapse-threshold "
    "crossing, searched no further than a quarter mains cycle. "
    "trip_time_s = t_end - t0. Supersede only by re-versioning AnalysisVersion; "
    "never by editing in place."
)

V2_ENDPOINT_DEFINITION = (
    "v2 endpoints: t0 is the K3 close (leakage injection) instant expressed in the "
    "scope time base, taken from the per-cycle sidecar when available, then from "
    "the waveform preamble, and otherwise from the detected sustained conduction "
    "onset. Detected onset refinement must not infer conduction at the record "
    "boundary solely from a forward-looking envelope. Pre-trigger leakage is "
    "checked using raw waveform samples so future burst energy cannot contaminate "
    "the beginning of the record. The burst end is confirmed when the envelope "
    "(leading half-mains-cycle sliding maximum of |v|) stays below the collapse "
    "threshold for at least one full mains cycle; t_end is then the last sample "
    "above the residual floor at or after the final collapse-threshold crossing, "
    "searched no further than a quarter mains cycle. "
    "trip_time_s = t_end - t0."
)

DEFAULT_ENDPOINT_DEFINITION = V2_ENDPOINT_DEFINITION

SANITY_SIGNAL_PRESENT = "signal_present"
SANITY_NO_PRETRIGGER_LEAKAGE = "no_pretrigger_leakage"
SANITY_RECORD_SPANS_NO_TRIP_LIMIT = "record_spans_no_trip_limit"
SANITY_BURST_STARTS_NEAR_T0 = "burst_starts_near_t0"
SANITY_COLLAPSE_IS_CLEAN = "collapse_is_clean"
SANITY_NO_TRIP_PERSISTENT = "no_trip_persistent"

ALL_SANITY_CHECKS: tuple[str, ...] = (
    SANITY_SIGNAL_PRESENT,
    SANITY_NO_PRETRIGGER_LEAKAGE,
    SANITY_RECORD_SPANS_NO_TRIP_LIMIT,
    SANITY_BURST_STARTS_NEAR_T0,
    SANITY_COLLAPSE_IS_CLEAN,
    SANITY_NO_TRIP_PERSISTENT,
)


@dataclass(frozen=True)
class AnalysisConfig:
    """Every threshold used by the analysis path. No scattered constants.

    `pass_limit_s` and `no_trip_limit_s` mirror the locked timing values and are
    normally supplied from `AppConfig.timing` so there is a single source of
    truth.
    """

    pass_limit_s: float = 0.02497
    no_trip_limit_s: float = 0.100
    line_frequency_hz: float = 60.0
    endpoint_definition: str = DEFAULT_ENDPOINT_DEFINITION
    algorithm_version: AnalysisVersion = CURRENT_ANALYSIS_VERSION
    # Envelope window, as a fraction of a mains cycle. Half a cycle is the
    # minimum that bridges the zero crossings of a live AC burst.
    envelope_window_cycles: float = 0.5
    # Burst is "on" above on_fraction * reference amplitude, and is considered
    # collapsed below off_fraction * reference amplitude.
    envelope_on_fraction: float = 0.25
    envelope_off_fraction: float = 0.10
    # Absolute floor so that an all-zero or noise-only record cannot synthesise
    # a burst out of its own noise.
    noise_floor_v: float = 0.5
    # The envelope must stay collapsed this long (in mains cycles) before the
    # collapse is accepted; this is what stops a zero crossing being read as a
    # trip (the 8.33 ms half-cycle trap).
    collapse_persistence_cycles: float = 1.0
    signal_present_rms_v: float = 1.0
    pretrigger_leakage_rms_v: float = 1.0
    burst_start_tolerance_s: float = 0.020
    # Conduction seen more than this many mains cycles before t0 is leakage, not
    # legitimate injection (the trigger can lag injection by up to a half cycle).
    pretrigger_leakage_guard_cycles: float = 1.0
    # Endpoint refinement: after the envelope confirms the collapse, t_end is
    # walked forward to the last sample above the residual floor, which recovers
    # the sub-threshold tail of a burst interrupted near a current zero. The
    # search is capped at a quarter mains cycle beyond the envelope crossing.
    residual_floor_noise_multiple: float = 5.0
    # The collapse threshold is also held this many noise sigma above the
    # estimated noise, so a noisy record still collapses cleanly.
    noise_collapse_multiple: float = 6.0
    # Residual endpoint uncertainty of the v1 definition. Applied only at the
    # no-trip boundary, and only in the fail-safe direction (a trip measured
    # within this margin of the no-trip limit halts the run). It is deliberately
    # NOT applied at the pass limit, where the strict table value stands.
    endpoint_uncertainty_s: float = 0.0005

    def __post_init__(self) -> None:
        positive_fields = (
            "pass_limit_s",
            "no_trip_limit_s",
            "line_frequency_hz",
            "envelope_window_cycles",
            "collapse_persistence_cycles",
            "signal_present_rms_v",
            "pretrigger_leakage_rms_v",
            "noise_floor_v",
            "residual_floor_noise_multiple",
            "noise_collapse_multiple",
            "pretrigger_leakage_guard_cycles",
        )
        for name in positive_fields:
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"AnalysisConfig.{name} must be finite and > 0: {value}")
        for name in ("burst_start_tolerance_s", "endpoint_uncertainty_s"):
            value = float(getattr(self, name))
            if value < 0.0 or not math.isfinite(value):
                raise ValueError(f"AnalysisConfig.{name} must be finite and >= 0: {value}")
        for name in ("envelope_on_fraction", "envelope_off_fraction"):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"AnalysisConfig.{name} must be in (0, 1): {value}")
        if self.envelope_off_fraction >= self.envelope_on_fraction:
            raise ValueError("envelope_off_fraction must be lower than envelope_on_fraction")
        if self.pass_limit_s >= self.no_trip_limit_s:
            raise ValueError("pass_limit_s must be lower than no_trip_limit_s")
        if not self.endpoint_definition.strip():
            raise ValueError("AnalysisConfig.endpoint_definition must be non-empty")

    @property
    def mains_period_s(self) -> float:
        return 1.0 / self.line_frequency_hz

    @property
    def envelope_window_s(self) -> float:
        return self.envelope_window_cycles * self.mains_period_s

    @property
    def collapse_persistence_s(self) -> float:
        return self.collapse_persistence_cycles * self.mains_period_s


DEFAULT_ANALYSIS_CONFIG = AnalysisConfig()


@dataclass(frozen=True)
class TripResult:
    """Outcome of one waveform analysis.

    `trip_time_s` is the raw measurement in seconds and is stored separately
    from `verdict` (handoff section 11) so verdicts stay re-derivable from
    `cycles.csv` alone if the pass limit or the endpoint definition changes.
    """

    trip_time_s: float | None
    verdict: Verdict
    sanity_checks: Mapping[str, bool] = field(default_factory=dict)
    notes: str = ""
    algorithm_version: AnalysisVersion = CURRENT_ANALYSIS_VERSION

    @property
    def failed_sanity_checks(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, ok in self.sanity_checks.items() if not ok))

    @property
    def sanity_ok(self) -> bool:
        return not self.failed_sanity_checks

    def to_dict(self) -> dict[str, Any]:
        """Sidecar/CSV friendly projection; round-trips through `from_dict`."""

        return {
            "trip_time_s": self.trip_time_s,
            "verdict": self.verdict.value,
            "analysis_version": self.algorithm_version.value,
            "sanity_checks": {name: bool(ok) for name, ok in sorted(self.sanity_checks.items())},
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TripResult":
        try:
            raw_trip = payload["trip_time_s"]
            verdict = Verdict(payload["verdict"])
            version = AnalysisVersion(payload["analysis_version"])
        except (KeyError, ValueError) as exc:
            raise WaveformFormatError(f"Malformed TripResult payload: {payload!r}") from exc
        sanity_raw = payload.get("sanity_checks") or {}
        if not isinstance(sanity_raw, Mapping):
            raise WaveformFormatError("TripResult sanity_checks must be a mapping")
        return cls(
            trip_time_s=None if raw_trip is None else float(raw_trip),
            verdict=verdict,
            sanity_checks={str(name): bool(ok) for name, ok in sanity_raw.items()},
            notes=str(payload.get("notes", "")),
            algorithm_version=version,
        )


@dataclass(frozen=True)
class Waveform:
    """Scaled samples plus the time base recovered from the scope preamble."""

    samples_v: np.ndarray
    sample_interval_s: float
    first_sample_time_s: float
    preamble: Mapping[str, Any]

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.sample_interval_s

    @property
    def duration_s(self) -> float:
        return self.samples_v.size * self.sample_interval_s

    def time_of_index(self, index: int) -> float:
        return self.first_sample_time_s + index * self.sample_interval_s

    def index_of_time(self, time_s: float) -> int:
        raw = int(round((time_s - self.first_sample_time_s) / self.sample_interval_s))
        return max(0, min(self.samples_v.size - 1, raw))


# --------------------------------------------------------------------------
# Configuration resolution
# --------------------------------------------------------------------------


def resolve_analysis_config(config: Any) -> AnalysisConfig:
    """Accept an `AppConfig`, an `AnalysisConfig`, a mapping, or `None`.

    Keeping this duck-typed avoids an import cycle with `ccid.config`, which
    builds `AnalysisConfig` from the optional `analysis:` section of
    `config.yaml`.
    """

    if config is None:
        return DEFAULT_ANALYSIS_CONFIG
    if isinstance(config, AnalysisConfig):
        return config
    analysis = getattr(config, "analysis", None)
    if isinstance(analysis, AnalysisConfig):
        return analysis
    timing = getattr(config, "timing", None)
    if timing is not None:
        overrides: dict[str, Any] = {}
        for name in ("pass_limit_s", "no_trip_limit_s"):
            value = getattr(timing, name, None)
            if value is not None:
                overrides[name] = float(value)
        base = analysis if isinstance(analysis, AnalysisConfig) else DEFAULT_ANALYSIS_CONFIG
        return replace(base, **overrides)
    if isinstance(config, Mapping):
        fields = {f for f in AnalysisConfig.__dataclass_fields__}
        payload = {key: value for key, value in config.items() if key in fields}
        version = payload.get("algorithm_version")
        if version is not None:
            payload["algorithm_version"] = AnalysisVersion(version)
        return replace(DEFAULT_ANALYSIS_CONFIG, **payload)
    raise TypeError(f"Unsupported analysis configuration object: {type(config)!r}")


# --------------------------------------------------------------------------
# Waveform loading
# --------------------------------------------------------------------------


def load_waveform(waveform_npz_bytes: bytes, *, allow_pickle: bool = False) -> Waveform:
    """Decode a stored waveform container into scaled volts plus a time base.

    Two container layouts are accepted, both zip-based:

    1. A numpy `.npz` with a `samples` array and a `preamble` entry (JSON text,
       or a pickled mapping when `allow_pickle=True`).
    2. The recorder's bundle (`samples.bin` + `preamble.json`), where the
       samples are raw scope BYTE codes.

    Integer samples are scaled with the preamble
    (`(raw - y_reference) * y_increment + y_origin`); floating-point samples are
    taken to be volts already. The preamble is non-negotiable (handoff section
    11): without a time base the samples are meaningless numbers.
    """

    if not isinstance(waveform_npz_bytes, (bytes, bytearray, memoryview)):
        raise WaveformFormatError("Waveform payload must be bytes")
    payload = bytes(waveform_npz_bytes)
    if not payload:
        raise WaveformFormatError("Waveform payload is empty")

    samples, preamble = _read_container(payload, allow_pickle=allow_pickle)
    if samples.ndim != 1:
        samples = np.asarray(samples).reshape(-1)
    if samples.size == 0:
        raise WaveformFormatError("Waveform contains no samples")

    volts = _scale_samples(samples, preamble)
    sample_interval_s = _resolve_sample_interval(preamble)
    first_sample_time_s = _resolve_first_sample_time(preamble, sample_interval_s)
    return Waveform(
        samples_v=volts,
        sample_interval_s=sample_interval_s,
        first_sample_time_s=first_sample_time_s,
        preamble=preamble,
    )


def _read_container(
    payload: bytes, *, allow_pickle: bool
) -> tuple[np.ndarray, dict[str, Any]]:
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        raise WaveformFormatError("Waveform payload is not a zip/npz container")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        if "samples.bin" in names and "preamble.json" in names:
            raw = np.frombuffer(archive.read("samples.bin"), dtype=np.uint8)
            try:
                preamble = json.loads(archive.read("preamble.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WaveformFormatError("preamble.json is not valid JSON") from exc
            if not isinstance(preamble, dict):
                raise WaveformFormatError("preamble.json must decode to a mapping")
            return raw, preamble

    try:
        archive_np = np.load(io.BytesIO(payload), allow_pickle=allow_pickle)
    except ValueError as exc:
        raise WaveformFormatError(
            "Waveform preamble appears to be a pickled object; re-load with "
            "allow_pickle=True or re-save the preamble as JSON text"
        ) from exc
    except Exception as exc:  # pragma: no cover - corrupt archive
        raise WaveformFormatError(f"Waveform container could not be read: {exc}") from exc

    with archive_np as data:
        keys = set(data.files)
        if "samples" not in keys:
            raise WaveformFormatError("Waveform .npz is missing the 'samples' array")
        samples = np.asarray(data["samples"])
        preamble = _extract_preamble(data, keys, allow_pickle=allow_pickle)
    return samples, preamble


def _extract_preamble(data: Any, keys: set[str], *, allow_pickle: bool) -> dict[str, Any]:
    for key in ("preamble", "preamble_json"):
        if key not in keys:
            continue
        try:
            raw = data[key]
        except ValueError as exc:
            raise WaveformFormatError(
                "Waveform preamble is pickled; re-load with allow_pickle=True"
            ) from exc
        value = raw.item() if getattr(raw, "shape", ()) == () else raw
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise WaveformFormatError("Waveform preamble is not valid JSON") from exc
            if not isinstance(decoded, dict):
                raise WaveformFormatError("Waveform preamble must decode to a mapping")
            return decoded
        raise WaveformFormatError(f"Unsupported preamble encoding: {type(value)!r}")

    # Fall back to scalar metadata stored at the top level of the archive.
    scalars: dict[str, Any] = {}
    for key in keys - {"samples"}:
        array = data[key]
        if getattr(array, "shape", ()) == ():
            scalars[key] = array.item()
    if not scalars:
        raise WaveformFormatError(
            "Waveform .npz is missing the preamble; the scope preamble is required "
            "to interpret samples (handoff section 11)"
        )
    return scalars


def _scale_samples(samples: np.ndarray, preamble: Mapping[str, Any]) -> np.ndarray:
    values = np.asarray(samples)
    if np.issubdtype(values.dtype, np.floating):
        return values.astype(np.float64, copy=False)
    if not np.issubdtype(values.dtype, np.integer):
        raise WaveformFormatError(f"Unsupported sample dtype: {values.dtype!r}")

    y_increment = _optional_float(preamble, "y_increment")
    y_origin = _optional_float(preamble, "y_origin")
    y_reference = _optional_float(preamble, "y_reference")
    if y_increment is None or y_origin is None or y_reference is None:
        raise WaveformFormatError(
            "Integer samples require y_increment, y_origin, and y_reference in the "
            "preamble; without them the samples are meaningless numbers"
        )
    return (values.astype(np.float64) - y_reference) * y_increment + y_origin


def _resolve_sample_interval(preamble: Mapping[str, Any]) -> float:
    x_increment = _optional_float(preamble, "x_increment")
    if x_increment is None:
        sample_rate = _optional_float(preamble, "sample_rate_hz")
        if sample_rate is not None and sample_rate > 0.0:
            x_increment = 1.0 / sample_rate
    if x_increment is None or not math.isfinite(x_increment) or x_increment <= 0.0:
        raise WaveformFormatError(
            "Preamble must provide a positive x_increment (or sample_rate_hz)"
        )
    return x_increment


def _resolve_first_sample_time(preamble: Mapping[str, Any], sample_interval_s: float) -> float:
    x_origin = _optional_float(preamble, "x_origin")
    if x_origin is not None and math.isfinite(x_origin):
        return x_origin
    pretrigger_s = _optional_float(preamble, "pretrigger_s")
    if pretrigger_s is not None and math.isfinite(pretrigger_s):
        return -pretrigger_s
    pretrigger_samples = _optional_float(preamble, "pretrigger_samples")
    if pretrigger_samples is not None and math.isfinite(pretrigger_samples):
        return -pretrigger_samples * sample_interval_s
    return 0.0


def _optional_float(preamble: Mapping[str, Any], key: str) -> float | None:
    if key not in preamble:
        return None
    value = preamble[key]
    if isinstance(value, bool):
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        raise WaveformFormatError(f"Preamble field '{key}' is not numeric: {value!r}") from None
    if not math.isfinite(as_float):
        raise WaveformFormatError(f"Preamble field '{key}' is not finite: {value!r}")
    return as_float


# --------------------------------------------------------------------------
# Envelope extraction
# --------------------------------------------------------------------------


def sliding_max(values: np.ndarray, window: int, *, align: str = "leading") -> np.ndarray:
    """O(n) sliding maximum over non-negative values (van Herk / Gil-Werman).

    `align="leading"` returns the maximum over `[i, i + window)`, which falls to
    zero exactly one sample after the burst ends. `align="trailing"` returns the
    maximum over `(i - window, i]`, which rises exactly at the first sample of
    the burst. Windows are truncated at the array edges; padding uses 0.0, which
    is why the input must be non-negative (it is always `|v|`).
    """

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError("sliding_max expects a 1-D array")
    if array.size == 0:
        return array.copy()
    if align not in {"leading", "trailing"}:
        raise ValueError(f"Unsupported align: {align!r}")
    if align == "trailing":
        return sliding_max(array[::-1], window, align="leading")[::-1]

    window = int(window)
    if window <= 1:
        return array.copy()
    window = min(window, array.size)

    n = array.size
    pad = (-n) % window
    padded = np.concatenate([array, np.zeros(pad, dtype=np.float64)]) if pad else array
    blocks = padded.reshape(-1, window)
    prefix = np.maximum.accumulate(blocks, axis=1).reshape(-1)
    suffix = np.maximum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1].reshape(-1)

    total = padded.size
    result = suffix.copy()
    tail_start = total - window + 1
    head = np.arange(tail_start)
    result[head] = np.maximum(suffix[head], prefix[head + window - 1])
    return result[:n]


def extract_envelope(
    samples_v: np.ndarray,
    *,
    sample_interval_s: float,
    config: AnalysisConfig = DEFAULT_ANALYSIS_CONFIG,
    align: str = "leading",
) -> np.ndarray:
    """Burst envelope: sliding maximum of |v| over half a mains cycle.

    Half a cycle is the shortest window that bridges the zero crossings of a
    live AC burst, so the envelope stays high for the whole burst instead of
    collapsing twice per cycle. This is the mechanism that avoids the 8.33 ms
    half-cycle trap (handoff section 4, trap 9).
    """

    magnitude = np.abs(np.asarray(samples_v, dtype=np.float64))
    window = envelope_window_samples(sample_interval_s, config)
    return sliding_max(magnitude, window, align=align)


def envelope_window_samples(
    sample_interval_s: float, config: AnalysisConfig = DEFAULT_ANALYSIS_CONFIG
) -> int:
    if sample_interval_s <= 0.0:
        raise ValueError("sample_interval_s must be > 0")
    return max(1, int(round(config.envelope_window_s / sample_interval_s)))


def reference_amplitude(magnitude: np.ndarray, window: int) -> float:
    """Robust burst peak: median of the largest few percent of a half cycle.

    A plain `max` would be inflated by a single noise spike, while a percentile
    over the whole record collapses to zero when most of the record is post-trip
    silence. Sizing the sample from the half-cycle window keeps the estimate
    independent of both the record length and the burst length.
    """

    if magnitude.size == 0:
        return 0.0
    count = int(max(1, min(max(1, window // 20), magnitude.size)))
    if count >= magnitude.size:
        top = magnitude
    else:
        top = np.partition(magnitude, magnitude.size - count)[magnitude.size - count :]
    return float(np.median(top))


def _rolling_mean_leading(magnitude: np.ndarray, window: int) -> np.ndarray:
    n = magnitude.size
    cumulative = np.concatenate([[0.0], np.cumsum(magnitude, dtype=np.float64)])
    ends = np.minimum(np.arange(n) + window, n)
    counts = ends - np.arange(n)
    return (cumulative[ends] - cumulative[: n]) / np.maximum(counts, 1)


def _first_sustained_low(
    below: np.ndarray, persistence: int, start: int, limit: int
) -> int | None:
    """First index in `[start, limit]` where `below` holds for `persistence` samples."""

    if persistence <= 0:
        persistence = 1
    last_start = min(limit, below.size - persistence)
    if last_start < start:
        return None
    high = (~below).astype(np.int64)
    cumulative = np.concatenate([[0], np.cumsum(high)])
    counts = cumulative[start + persistence : last_start + persistence + 1] - cumulative[
        start : last_start + 1
    ]
    hits = np.flatnonzero(counts == 0)
    if hits.size == 0:
        return None
    return int(start + hits[0])


# --------------------------------------------------------------------------
# Sanity checks
# --------------------------------------------------------------------------


def rms(values: np.ndarray) -> float:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(array))))


def check_signal_present(
    samples_v: np.ndarray, config: AnalysisConfig = DEFAULT_ANALYSIS_CONFIG
) -> bool:
    """False means the scope captured nothing: a rig fault, not a fast trip."""

    return rms(samples_v) > config.signal_present_rms_v


def check_no_pretrigger_leakage(
    pretrigger_samples_v: np.ndarray,
    config: AnalysisConfig = DEFAULT_ANALYSIS_CONFIG,
    *,
    reference_amplitude_v: float = 0.0,
) -> bool:
    """False means current was already flowing before K3 closed (K3 stuck closed).

    The threshold scales with the observed burst amplitude so that probe noise
    on a 120 V-RMS signal is not mistaken for leakage, while genuine leakage
    (the same amplitude as the post-trigger burst) is caught.
    """

    if pretrigger_samples_v.size == 0:
        return True
    threshold = max(config.pretrigger_leakage_rms_v, 0.10 * reference_amplitude_v)
    return rms(pretrigger_samples_v) <= threshold


def check_no_trip_persistent(
    envelope: np.ndarray, on_threshold_v: float, start_index: int, end_index: int
) -> bool:
    """A no-trip must look like continuous conduction, not an intermittent signal."""

    if end_index <= start_index:
        return False
    window = envelope[start_index:end_index]
    if window.size == 0:
        return False
    return float(np.mean(window >= on_threshold_v)) >= 0.95


# --------------------------------------------------------------------------
# Analysis entry points
# --------------------------------------------------------------------------


def analyze_waveform(
    waveform_npz_bytes: bytes,
    config: Any = None,
    *,
    injection_time_s: float | None = None,
    allow_pickle: bool = False,
) -> TripResult:
    """Analyse one stored waveform and return a versioned `TripResult`.

    `injection_time_s` is t=0 (the K3 close instant) expressed in the scope time
    base; it comes from the per-cycle JSON sidecar, not from the waveform
    header. When it is not supplied, the preamble is consulted
    (`k3_close_time_s` / `injection_time_s`), and failing that t=0 is recovered
    from the pre-trigger data as the detected conduction onset. The trigger
    instant is never assumed to be t=0: the +20 V trigger level can fire up to a
    half mains cycle after injection (handoff section 6).
    """

    analysis_config = resolve_analysis_config(config)
    waveform = load_waveform(waveform_npz_bytes, allow_pickle=allow_pickle)
    return analyze_samples(
        waveform,
        analysis_config,
        injection_time_s=injection_time_s,
    )


def analyze_waveform_file(
    path: str | Path,
    config: Any = None,
    *,
    injection_time_s: float | None = None,
    allow_pickle: bool = False,
) -> TripResult:
    """Offline replay helper: analyse a stored `waveforms/<n>.npz`."""

    file_path = Path(path)
    try:
        payload = file_path.read_bytes()
    except OSError as exc:
        raise WaveformFormatError(f"Could not read waveform file: {file_path}") from exc
    return analyze_waveform(
        payload,
        config,
        injection_time_s=injection_time_s,
        allow_pickle=allow_pickle,
    )


def analyze_samples(
    waveform: Waveform,
    config: AnalysisConfig = DEFAULT_ANALYSIS_CONFIG,
    *,
    injection_time_s: float | None = None,
) -> TripResult:

    """Run a registered, replayable version of the trip-time algorithm."""

    if config.algorithm_version not in {
        AnalysisVersion.V1,
        AnalysisVersion.V2,
    }:
        raise NotImplementedError(
            f"No implementation registered for {config.algorithm_version.value}; "
            "add it here and keep earlier versions available for replay comparisons"
        )

    dt = waveform.sample_interval_s
    samples = waveform.samples_v
    magnitude = np.abs(samples)
    notes: MutableMapping[str, str] = {}
    notes["endpoints"] = config.endpoint_definition

    window = envelope_window_samples(dt, config)
    persistence = max(1, int(round(config.collapse_persistence_s / dt)))
    envelope_end = sliding_max(magnitude, window, align="leading")
    envelope_start = sliding_max(magnitude, window, align="trailing")

    ref_amplitude = reference_amplitude(magnitude, window)
    noise_sigma = _noise_sigma(magnitude, window)
    # Thresholds must clear the noise as well as scale with the burst, otherwise
    # a noisy record either never collapses or collapses on a noise spike.
    off_threshold = max(
        config.noise_floor_v,
        min(
            0.5 * ref_amplitude,
            max(
                config.envelope_off_fraction * ref_amplitude,
                config.noise_collapse_multiple * noise_sigma,
            ),
        ),
    )
    on_threshold = max(config.envelope_on_fraction * ref_amplitude, 1.25 * off_threshold)
    residual_floor = min(
        off_threshold,
        max(config.noise_floor_v, config.residual_floor_noise_multiple * noise_sigma),
    )
    notes["ref_amplitude_v"] = f"{ref_amplitude:.6f}"
    notes["noise_sigma_v"] = f"{noise_sigma:.6f}"
    notes["on_threshold_v"] = f"{on_threshold:.6f}"
    notes["off_threshold_v"] = f"{off_threshold:.6f}"

    # Conduction onset, refined back through the sub-threshold leading edge. The
    # scope triggers at +20 V, which can be up to a half cycle after injection,
    # so the onset is recovered from the pre-trigger data (handoff section 6).
    onset_index = _find_burst_start(magnitude, envelope_start, on_threshold, 0, window)
    if onset_index is not None:
        onset_index = _refine_start_index(
            magnitude,
            envelope_end,
            burst_index=onset_index,
            residual_floor=residual_floor,
            window=window,
            algorithm_version=config.algorithm_version,
        )

    t0, t0_source = _resolve_t0(
        injection_time_s,
        waveform,
        onset_index=onset_index,
    )
    i0 = waveform.index_of_time(t0)
    notes["t0_s"] = f"{t0:.9f}"
    notes["t0_source"] = t0_source

    sanity: dict[str, bool] = {name: True for name in ALL_SANITY_CHECKS}
    sanity[SANITY_SIGNAL_PRESENT] = check_signal_present(magnitude[i0:], config)
    sanity[SANITY_NO_PRETRIGGER_LEAKAGE] = _pretrigger_leakage_ok(
        magnitude,
        waveform=waveform,
        envelope_lead=envelope_end,
        config=config,
        t0=t0,
        on_threshold=on_threshold,
        reference_amplitude_v=ref_amplitude,
        algorithm_version=config.algorithm_version,
    )
    record_tail_s = waveform.time_of_index(samples.size - 1) - t0
    sanity[SANITY_RECORD_SPANS_NO_TRIP_LIMIT] = record_tail_s + _EPSILON >= config.no_trip_limit_s
    notes["record_after_t0_s"] = f"{record_tail_s:.6f}"

    burst_start_index = None if onset_index is None else max(onset_index, i0)
    # The leading envelope is only trustworthy while its window is full; the
    # final `window` samples see zero padding.
    valid_end = max(i0, samples.size - window)

    trip_time_s: float | None = None
    collapse_index: int | None = None
    if onset_index is None:
        sanity[SANITY_BURST_STARTS_NEAR_T0] = False
    else:
        burst_start_s = waveform.time_of_index(onset_index)
        notes["burst_start_s"] = f"{burst_start_s:.9f}"
        delay = burst_start_s - t0
        sanity[SANITY_BURST_STARTS_NEAR_T0] = (
            -_EPSILON <= delay <= config.burst_start_tolerance_s + _EPSILON
        )
        collapse_index = _first_sustained_low(
            envelope_end < off_threshold,
            persistence,
            start=burst_start_index if burst_start_index is not None else i0,
            limit=valid_end,
        )

    if collapse_index is not None and collapse_index > 0:
        # t_end is the last sample above the collapse threshold, refined forward
        # through the sub-threshold residual (see DEFAULT_ENDPOINT_DEFINITION).
        end_index = _refine_end_index(
            magnitude,
            collapse_index=collapse_index,
            off_threshold=off_threshold,
            residual_floor=residual_floor,
            sample_interval_s=dt,
            config=config,
        )
        t_end = waveform.time_of_index(end_index)
        trip_time_s = max(0.0, t_end - t0)
        notes["t_end_s"] = f"{t_end:.9f}"
        sanity[SANITY_COLLAPSE_IS_CLEAN] = not bool(
            np.any(envelope_end[collapse_index:valid_end] >= on_threshold)
        )
    else:
        sanity[SANITY_NO_TRIP_PERSISTENT] = check_no_trip_persistent(
            envelope_end,
            on_threshold,
            start_index=burst_start_index if burst_start_index is not None else i0,
            end_index=valid_end,
        )
    verdict, verdict_note = _decide(trip_time_s, config, dt, sanity)
    notes["decision"] = verdict_note
    if trip_time_s is not None:
        notes["trip_time_s"] = f"{trip_time_s:.9f}"
    notes["analysis_version"] = config.algorithm_version.value

    failed = tuple(sorted(name for name, ok in sanity.items() if not ok))
    if failed:
        notes["sanity_failed"] = ",".join(failed)
        # Logged, never a veto: the verdict above already stands.
        _LOGGER.warning(
            "Waveform sanity checks failed (verdict %s, trip_time_s=%s): %s",
            verdict.value,
            trip_time_s,
            ", ".join(failed),
        )

    return TripResult(
        trip_time_s=trip_time_s,
        verdict=verdict,
        sanity_checks=sanity,
        notes=_format_notes(notes),
        algorithm_version=config.algorithm_version,
    )


def _noise_sigma(magnitude: np.ndarray, window: int) -> float:
    """Robust noise estimate: 10th percentile of half-cycle block RMS.

    A silent block reads the noise; a conducting block reads ~0.7 of the burst
    amplitude. Taking a low percentile therefore recovers the noise level
    without needing to know in advance where the record is quiet.
    """

    if magnitude.size == 0:
        return 0.0
    blocks = magnitude.size // max(1, window)
    if blocks < 2:
        return rms(magnitude)
    trimmed = magnitude[: blocks * window].reshape(blocks, window)
    block_rms = np.sqrt(np.mean(np.square(trimmed), axis=1))
    return float(np.percentile(block_rms, 10.0))


def _refine_end_index(
    magnitude: np.ndarray,
    *,
    collapse_index: int,
    off_threshold: float,
    residual_floor: float,
    sample_interval_s: float,
    config: AnalysisConfig,
) -> int:
    """Last conducting sample: envelope crossing, then the sub-threshold residual.

    The envelope crossing is immune to zero crossings but truncates the burst at
    the collapse threshold, which under-reports a burst interrupted near a
    current zero by up to `asin(off_fraction) / (2*pi*f)`. Walking forward to the
    last sample above the residual floor recovers most of that tail; the search
    is capped at a quarter mains cycle beyond the crossing.
    """

    above = np.flatnonzero(magnitude[:collapse_index] >= off_threshold)
    if above.size == 0:
        return max(0, collapse_index - 1)
    crossing_index = int(above[-1])
    if residual_floor >= off_threshold:
        return crossing_index

    quarter_cycle = max(1, int(round(0.25 * config.mains_period_s / sample_interval_s)))
    search_end = min(magnitude.size, crossing_index + quarter_cycle + 1)
    residual = np.flatnonzero(magnitude[crossing_index:search_end] >= residual_floor)
    if residual.size == 0:
        return crossing_index
    return int(crossing_index + residual[-1])


def _refine_start_index(
    magnitude: np.ndarray,
    envelope_lead: np.ndarray,
    *,
    burst_index: int,
    residual_floor: float,
    window: int,
    algorithm_version: AnalysisVersion,
) -> int:
    """First conducting sample, recovered from the sub-threshold leading edge.

    The leading envelope is the last thing to be silent before conduction: its
    window reaches forward, so the final silent window ends exactly one window
    before the first conducting sample. Working from the envelope rather than
    from raw samples means an interior zero crossing cannot be mistaken for the
    start of the burst, which matters because K3 closes at a random phase.
    """

    if burst_index <= 0 or magnitude.size == 0:
        return max(0, burst_index)
    below = np.flatnonzero(envelope_lead[: burst_index + 1] < residual_floor)
    if below.size == 0:
        if algorithm_version is AnalysisVersion.V1:
            # Historical V1 behavior: assume conduction began at the first
            # sample when no silent leading-envelope window is available.
            return 0

        # V2 behavior: a forward-looking envelope can include a burst that
        # begins shortly after the record starts. It therefore cannot prove
        # conduction existed at the first sample
        # V2 behavior: a forward-looking envelope can include a burst that
        # begins shortly after the record starts. It therefore cannot prove
        # conduction existed at the first sample.
        return max(0, burst_index)

    return int(min(burst_index, below[-1] + window))


def _resolve_t0(
    injection_time_s: float | None,
    waveform: Waveform,
    *,
    onset_index: int | None,
) -> tuple[float, str]:
    """t=0 precedence: cycle sidecar, then preamble, then detected conduction onset.

    The trigger instant is only the last resort: the +20 V trigger level can fire
    up to a half mains cycle after injection, so it is not t=0 (handoff section 6).
    """

    if injection_time_s is not None:
        return float(injection_time_s), "sidecar"
    from_preamble = _injection_time_from_preamble(waveform.preamble)
    if from_preamble is not None:
        return float(from_preamble), "preamble"
    if onset_index is not None:
        return waveform.time_of_index(onset_index), "detected_onset"
    return 0.0, "trigger"


def _pretrigger_leakage_ok(
    magnitude: np.ndarray,
    *,
    waveform: Waveform,
    envelope_lead: np.ndarray,
    config: AnalysisConfig,
    t0: float,
    on_threshold: float,
    reference_amplitude_v: float,
    algorithm_version: AnalysisVersion,
) -> bool:
    """False when current was already flowing before injection (K3 stuck closed).

    Two signatures are checked, because a stuck-closed K3 presents either way:
    energy well before the injection instant, and conduction already underway in
    the very first samples of a record that has real pre-trigger depth.
    """

    guard_s = config.pretrigger_leakage_guard_cycles * config.mains_period_s
    guard_index = waveform.index_of_time(t0 - guard_s)
    if not check_no_pretrigger_leakage(
        magnitude[:guard_index], config, reference_amplitude_v=reference_amplitude_v
    ):
        return False

    has_pretrigger_depth = waveform.first_sample_time_s < -guard_s

    if algorithm_version is AnalysisVersion.V1:
        if not has_pretrigger_depth or envelope_lead.size == 0:
            return True
        return not bool(envelope_lead[0] >= on_threshold)

    if not has_pretrigger_depth or magnitude.size == 0:
        # No usable pre-trigger data; nothing can be concluded.
        return True

    # Inspect only raw samples at the beginning of the record. Do not use the
    # leading envelope here because it includes future samples and can make a
    # later burst appear to have been present at the record boundary.

    if t0 <= waveform.first_sample_time_s + _EPSILON:
        # Detected conduction begins at the record boundary. Inspect the first
        # quarter cycle directly so genuine stuck-K3 conduction is not hidden
        # by an empty pre-t0 slice.
        initial_probe_end_s = (
            waveform.first_sample_time_s + 0.25 * config.mains_period_s
        )
    else:
        initial_probe_end_s = min(
            t0,
            waveform.first_sample_time_s + 0.25 * config.mains_period_s,
        )
    initial_probe_end = waveform.index_of_time(initial_probe_end_s)
    return check_no_pretrigger_leakage(
        magnitude[:initial_probe_end],
        config,
        reference_amplitude_v=reference_amplitude_v,
    )


def _decide(
    trip_time_s: float | None,
    config: AnalysisConfig,
    sample_interval_s: float,
    sanity: Mapping[str, bool],
) -> tuple[Verdict, str]:
    """The locked verdict table. Sanity results are never consulted here."""

    tolerance = sample_interval_s / 2.0
    if trip_time_s is None:
        if not sanity.get(SANITY_SIGNAL_PRESENT, True):
            return Verdict.NO_TRIP, "no signal captured; nothing to measure"
        return Verdict.NO_TRIP, "no envelope collapse within the record"
    if trip_time_s >= config.no_trip_limit_s - config.endpoint_uncertainty_s - tolerance:
        return Verdict.NO_TRIP, "trip time at or beyond the no-trip limit"
    if trip_time_s <= config.pass_limit_s + tolerance:
        return Verdict.PASS, "trip time within the pass limit"
    return Verdict.FAIL, "trip time between the pass limit and the no-trip limit"


def _find_burst_start(
    magnitude: np.ndarray,
    envelope_trailing: np.ndarray,
    on_threshold: float,
    start_index: int,
    window: int,
) -> int | None:
    """First sample at or after t0 where sustained conduction begins.

    The trailing envelope rises on the exact sample that first exceeds the
    threshold; the leading rolling mean then rejects an isolated noise spike,
    which cannot carry a half cycle worth of energy.
    """

    if start_index >= magnitude.size:
        return None
    risen = envelope_trailing[start_index:] >= on_threshold
    mean_ahead = _rolling_mean_leading(magnitude, window)[start_index:]
    sustained = risen & (mean_ahead >= 0.25 * on_threshold)
    hits = np.flatnonzero(sustained)
    if hits.size == 0:
        return None
    return int(start_index + hits[0])


def _injection_time_from_preamble(preamble: Mapping[str, Any]) -> float | None:
    for key in ("k3_close_time_s", "injection_time_s"):
        value = _optional_float(preamble, key)
        if value is not None:
            return value
    return None


def _format_notes(notes: Mapping[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in notes.items())


# --------------------------------------------------------------------------
# Synthetic waveforms (fixtures for tests and for replay-tool smoke checks)
# --------------------------------------------------------------------------


def synthesize_burst_samples(
    *,
    sample_rate_hz: float = 1_000_000.0,
    line_frequency_hz: float = 60.0,
    amplitude_v: float = 170.0,
    phase_rad: float = 0.0,
    pretrigger_s: float = 0.020,
    record_after_t0_s: float = 0.150,
    trip_time_s: float | None = 0.010,
    pretrigger_leakage: bool = False,
    noise_v: float = 0.0,
    seed: int = 12345,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build a deterministic leakage burst: sine while conducting, zero after.

    `trip_time_s=None` means the burst never stops within the record (no trip).
    Sample times run from `-pretrigger_s` to `record_after_t0_s`, with t=0 at the
    injection instant.
    """

    if sample_rate_hz <= 0.0:
        raise ValueError("sample_rate_hz must be > 0")
    dt = 1.0 / sample_rate_hz
    count = int(round((pretrigger_s + record_after_t0_s) / dt))
    times = -pretrigger_s + np.arange(count) * dt
    sine = amplitude_v * np.sin(2.0 * np.pi * line_frequency_hz * times + phase_rad)

    conducting = times >= 0.0
    if trip_time_s is not None:
        conducting &= times <= trip_time_s + _EPSILON
    if pretrigger_leakage:
        conducting |= times < 0.0

    samples = np.where(conducting, sine, 0.0)
    if noise_v > 0.0:
        rng = np.random.default_rng(seed)
        samples = samples + rng.normal(0.0, noise_v, size=samples.shape)

    preamble = {
        "format": "FLOAT",
        "points": int(count),
        "x_increment": dt,
        "x_origin": -pretrigger_s,
        "x_reference": 0,
        "y_increment": 1.0,
        "y_origin": 0.0,
        "y_reference": 0,
        "sample_rate_hz": sample_rate_hz,
        "pretrigger_s": pretrigger_s,
        "source": "CHANnel1",
    }
    return samples.astype(np.float64), preamble


def pack_waveform_npz(samples: np.ndarray, preamble: Mapping[str, Any]) -> bytes:
    """Pack samples plus preamble into a pickle-free `.npz` payload."""

    buffer = io.BytesIO()
    np.savez(
        buffer,
        samples=np.asarray(samples),
        preamble=np.array(json.dumps(dict(preamble), sort_keys=True)),
    )
    return buffer.getvalue()


def synthesize_waveform_npz(**kwargs: Any) -> bytes:
    """Convenience wrapper: synthesize a burst and pack it as `.npz` bytes."""

    samples, preamble = synthesize_burst_samples(**kwargs)
    return pack_waveform_npz(samples, preamble)
