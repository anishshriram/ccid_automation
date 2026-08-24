"""Run deep_analysis.analyze_cycle over every waveform in the combined
6,000-cycle dataset and write analysis/deep/deep_results.csv.

Reads analysis/combined_analyzed.csv for the (source_run, source_cycle_index)
list, locates each waveforms/<n>.npz under ccid_campaign_data/, and writes
one row per cycle. Independent of ccid/analysis.py end to end.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_analysis import analyze_cycle  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_ROOT = REPO_ROOT / "ccid_campaign_data"
COMBINED_PATH = REPO_ROOT / "analysis" / "combined_analyzed.csv"
OUT_PATH = Path(__file__).resolve().parent / "deep_results.csv"


def main():
    combined = pd.read_csv(COMBINED_PATH)
    rows = []
    t_start = time.time()
    total = len(combined)
    for i, row in enumerate(combined.itertuples()):
        npz_path = DATA_ROOT / row.source_run / "waveforms" / f"{int(row.source_cycle_index)}.npz"
        result = analyze_cycle(npz_path, row.source_run, int(row.source_cycle_index))
        rows.append(asdict(result))
        if (i + 1) % 250 == 0 or (i + 1) == total:
            elapsed = time.time() - t_start
            rate = (i + 1) / elapsed
            eta = (total - i - 1) / rate if rate > 0 else float("nan")
            print(f"{i+1}/{total} done ({elapsed:.0f}s elapsed, {eta:.0f}s ETA)", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(out)} rows -> {OUT_PATH}")
    n_errors = out["error"].notna().sum()
    if n_errors:
        print(f"WARNING: {n_errors} cycles raised an error during analysis")


if __name__ == "__main__":
    main()
