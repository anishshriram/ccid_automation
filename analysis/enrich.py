"""Enrich the combined cycle dataset with per-cycle detail not present in
cycles.csv's own columns: the six sanity_checks booleans (from cycles/<n>.json
- authoritative source, not re-derived) and the numeric diagnostics packed
into the notes column (ref_amplitude_v, noise_sigma_v, thresholds, t0, etc.).

Read-only against ccid_campaign_data/ - does not touch or reinterpret any
recorded verdict/trip_time_s.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

DATA_ROOT = Path(__file__).resolve().parent.parent / "ccid_campaign_data"
IN_PATH = Path(__file__).resolve().parent / "combined_cycles.csv"
OUT_PATH = Path(__file__).resolve().parent / "combined_enriched.csv"

SANITY_KEYS = [
    "signal_present",
    "no_pretrigger_leakage",
    "record_spans_no_trip_limit",
    "burst_starts_near_t0",
    "collapse_is_clean",
    "no_trip_persistent",
]

NUMERIC_NOTE_KEYS = [
    "ref_amplitude_v",
    "noise_sigma_v",
    "on_threshold_v",
    "off_threshold_v",
    "t0_s",
    "record_after_t0_s",
    "burst_start_s",
    "t_end_s",
]
STRING_NOTE_KEYS = ["t0_source", "decision"]

_NOTE_KV_RE = re.compile(r"(\w+)=([^;]*)")


def parse_notes(notes: str) -> dict:
    out: dict = {}
    if not isinstance(notes, str):
        return out
    for m in _NOTE_KV_RE.finditer(notes):
        key, val = m.group(1), m.group(2).strip()
        if key in NUMERIC_NOTE_KEYS:
            try:
                out[key] = float(val)
            except ValueError:
                pass
        elif key in STRING_NOTE_KEYS:
            out[key] = val
        elif key == "sanity_failed":
            out["sanity_failed_note"] = val
    return out


def load_sidecar_sanity(source_run: str, cycle_index: int) -> dict:
    path = DATA_ROOT / source_run / "cycles" / f"{cycle_index}.json"
    with open(path) as f:
        sidecar = json.load(f)
    checks = sidecar.get("analysis", {}).get("sanity_checks", {})
    return {f"sanity_{k}": checks.get(k) for k in SANITY_KEYS}


def enrich() -> pd.DataFrame:
    df = pd.read_csv(IN_PATH)

    note_rows = df["notes"].apply(parse_notes)
    note_df = pd.DataFrame(list(note_rows))
    for k in NUMERIC_NOTE_KEYS:
        if k not in note_df.columns:
            note_df[k] = pd.NA
    for k in STRING_NOTE_KEYS:
        if k not in note_df.columns:
            note_df[k] = pd.NA
    if "sanity_failed_note" not in note_df.columns:
        note_df["sanity_failed_note"] = pd.NA

    sanity_rows = [
        load_sidecar_sanity(row.source_run, int(row.source_cycle_index))
        for row in df.itertuples()
    ]
    sanity_df = pd.DataFrame(sanity_rows)

    out = pd.concat([df.reset_index(drop=True), note_df.reset_index(drop=True),
                      sanity_df.reset_index(drop=True)], axis=1)
    out.to_csv(OUT_PATH, index=False)
    return out


if __name__ == "__main__":
    df = enrich()
    print(f"Enriched {len(df)} cycles -> {OUT_PATH}")
    print("Sanity-check failure counts (False = failed):")
    for k in SANITY_KEYS:
        col = f"sanity_{k}"
        n_fail = (df[col] == False).sum()  # noqa: E712
        print(f"  {col}: {n_fail} failed / {df[col].notna().sum()} checked")
    print("sanity_failed_note non-null count:", df["sanity_failed_note"].notna().sum())
