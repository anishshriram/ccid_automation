"""Representative PASS/FAIL/NO_TRIP waveform plots for the report figures.

Picks one cycle of each verdict closest to its verdict's own median
trip_time_s in the combined 6,000-cycle dataset (a "typical", not a
boundary-extreme, example of each), loads the raw stored waveform through
the real production loader (`ccid.analysis.load_waveform` - the exact
scaling/time-base code the sequencer itself uses, not a reimplementation),
and plots it with the same t0/t_end/threshold markers the analysis
algorithm actually computed for that cycle (read from
combined_analyzed.csv, not recomputed here).

Run after report.py/enrich.py (reads analysis/combined_analyzed.csv).
Reads raw waveforms from ccid_campaign_data/ (untracked, outside git - see
docs/campaign-results-index.md); writes only under analysis/plots/.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ccid.analysis import load_waveform

REPO_ROOT = Path(__file__).resolve().parent.parent
IN_PATH = Path(__file__).resolve().parent / "combined_analyzed.csv"
CAMPAIGN_DATA_DIR = REPO_ROOT / "ccid_campaign_data"
PLOTS_DIR = Path(__file__).resolve().parent / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

PASS_LIMIT_S = 0.02497
NO_TRIP_LIMIT_S = 0.100


def _pick_representative(df: pd.DataFrame, verdict: str) -> pd.Series:
    """The cycle of this verdict closest to that verdict's own median trip_time_s."""

    subset = df[df["verdict"] == verdict].copy()
    if verdict == "NO_TRIP":
        # Only one exists in this dataset; nothing to pick among.
        return subset.iloc[0]
    median = subset["trip_time_s"].median()
    subset["_dist"] = (subset["trip_time_s"] - median).abs()
    return subset.sort_values("_dist").iloc[0]


def _waveform_path(row: pd.Series) -> Path:
    return CAMPAIGN_DATA_DIR / row["source_run"] / "waveforms" / f"{int(row['source_cycle_index'])}.npz"


def plot_representative_waveform(row: pd.Series, verdict: str, out_name: str) -> None:
    npz_bytes = _waveform_path(row).read_bytes()
    waveform = load_waveform(npz_bytes)

    n = waveform.samples_v.size
    times_ms = (waveform.first_sample_time_s + np.arange(n) * waveform.sample_interval_s) * 1000.0

    t0_s = float(row["t0_s"])
    t0_ms = t0_s * 1000.0
    rel_ms = times_ms - t0_ms  # time relative to the resolved fault onset

    # Zoom window: representative PASS/FAIL cycles are done well within ~25ms
    # of onset; the NO_TRIP cycle is still conducting at the end of its
    # record, so its window is wider to show sustained conduction rather
    # than truncate it right at the pass/no-trip boundary.
    if verdict == "NO_TRIP":
        window_lo_ms, window_hi_ms = -5.0, 40.0
    else:
        trip_ms = float(row["trip_time_s"]) * 1000.0
        window_lo_ms, window_hi_ms = -5.0, trip_ms + 10.0

    mask = (rel_ms >= window_lo_ms) & (rel_ms <= window_hi_ms)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(rel_ms[mask], waveform.samples_v[mask], color="#2c7fb8", linewidth=0.6)

    ax.axvline(0.0, color="black", linestyle="--", linewidth=1, label="t0 (resolved onset)")

    on_threshold_v = row.get("on_threshold_v")
    off_threshold_v = row.get("off_threshold_v")
    if pd.notna(on_threshold_v):
        ax.axhline(float(on_threshold_v), color="darkorange", linestyle=":", linewidth=1, label="on_threshold")
        ax.axhline(-float(on_threshold_v), color="darkorange", linestyle=":", linewidth=1)
    if pd.notna(off_threshold_v):
        ax.axhline(float(off_threshold_v), color="seagreen", linestyle=":", linewidth=1, label="off_threshold")
        ax.axhline(-float(off_threshold_v), color="seagreen", linestyle=":", linewidth=1)

    if verdict != "NO_TRIP":
        t_end_ms = (float(row["t_end_s"]) - t0_s) * 1000.0
        ax.axvline(t_end_ms, color="red", linestyle="--", linewidth=1, label="t_end (resolved collapse)")
        trip_ms = float(row["trip_time_s"]) * 1000.0
        title_trip = f"trip_time_s = {trip_ms:.4f} ms"
    else:
        title_trip = "no envelope collapse within the record (trip_time_s = None)"

    ax.set_xlabel("time relative to t0 (ms)")
    ax.set_ylabel("voltage (V)")
    ax.set_title(
        f"Representative {verdict} waveform - {row['source_run']} cycle {int(row['source_cycle_index'])} "
        f"(global {int(row['global_cycle_index'])})\n{title_trip}"
    )
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / out_name, dpi=150)
    plt.close(fig)
    print(f"Wrote {PLOTS_DIR / out_name}")


if __name__ == "__main__":
    df = pd.read_csv(IN_PATH)
    for verdict, out_name in (
        ("PASS", "representative_pass_waveform.png"),
        ("FAIL", "representative_fail_waveform.png"),
        ("NO_TRIP", "representative_no_trip_waveform.png"),
    ):
        row = _pick_representative(df, verdict)
        plot_representative_waveform(row, verdict, out_name)
