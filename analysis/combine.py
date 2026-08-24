"""Combine the three real campaign runs into one 6000-cycle dataset.

Reads cycles.csv from the three campaigns that together make up the
6,000-cycle result (per docs/campaign-results-index.md):
    200_v3_real_20260813T131932Z    (200 cycles,  Aug 13 13:20 UTC start)
    5800_v3_try2_20260813T195018Z   (483 cycles,  Aug 13 19:51 UTC start)
    5317_v3_real_20260817T143315Z   (5317 cycles, Aug 17 14:34 UTC start)

Concatenated in that chronological order (by each run's actual start
timestamp, not by cycle count) into analysis/combined_cycles.csv, with a
new `global_cycle_index` (1..6000) added and every original column kept
verbatim plus provenance (`source_run`, `source_cycle_index`).

This does NOT recompute or reinterpret any trip_time_s/verdict - it only
concatenates the already-committed V3 records. See docs/campaign-results-
index.md for why these three (and not the other two 5800_* attempts) are
the real campaign data.
"""
from __future__ import annotations

import pandas as pd
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parent.parent / "ccid_campaign_data"
OUT_PATH = Path(__file__).resolve().parent / "combined_cycles.csv"

# Chronological order, confirmed from each run's first row utc_timestamp.
RUNS_IN_ORDER = [
    "200_v3_real_20260813T131932Z",
    "5800_v3_try2_20260813T195018Z",
    "5317_v3_real_20260817T143315Z",
]


def load_run(run_id: str) -> pd.DataFrame:
    path = DATA_ROOT / run_id / "cycles.csv"
    df = pd.read_csv(path, dtype={"trip_time_s": "float64"}, keep_default_na=True)
    df["source_run"] = run_id
    df["source_cycle_index"] = df["cycle_index"]
    # Per-run monotonic delta: how long since the previous cycle in the SAME
    # run started. Monotonic clocks are not comparable across separate
    # process runs, so this is left NaN at each run's first row.
    df["delta_monotonic_s"] = df["monotonic_start"].diff()
    df.loc[df.index[0], "delta_monotonic_s"] = float("nan")
    return df


def combine() -> pd.DataFrame:
    frames = [load_run(r) for r in RUNS_IN_ORDER]
    combined = pd.concat(frames, ignore_index=True)
    combined.insert(0, "global_cycle_index", range(1, len(combined) + 1))
    combined.to_csv(OUT_PATH, index=False)
    return combined


if __name__ == "__main__":
    df = combine()
    print(f"Combined {len(df)} cycles from {len(RUNS_IN_ORDER)} runs -> {OUT_PATH}")
    print(df["verdict"].value_counts())
    print(df.groupby("source_run", sort=False)["verdict"].value_counts())
