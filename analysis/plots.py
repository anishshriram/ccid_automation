"""Plots for the combined 6,000-cycle dataset. Run after report.py (reads
analysis/combined_analyzed.csv, which has the gap/segment columns already
computed). Saves PNGs to analysis/plots/.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

IN_PATH = Path(__file__).resolve().parent / "combined_analyzed.csv"
PLOTS_DIR = Path(__file__).resolve().parent / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

PASS_LIMIT_S = 0.02497
NO_TRIP_LIMIT_S = 0.100

VERDICT_COLORS = {"PASS": "#2c7fb8", "FAIL": "#e34a33", "NO_TRIP": "#756bb1"}


def plot_histogram(df: pd.DataFrame):
    from scipy.stats import gaussian_kde

    tt = df.loc[df["trip_time_s"].notna(), "trip_time_s"] * 1000
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(tt, bins=120, color="#2c7fb8", alpha=0.85, edgecolor="none", density=True, label="histogram (120 bins)")
    kde = gaussian_kde(tt)
    xs = np.linspace(tt.min(), tt.max(), 1000)
    ax.plot(xs, kde(xs), color="black", linewidth=1.5, label="Gaussian KDE (smoothed density)")
    ax.axvline(PASS_LIMIT_S * 1000, color="green", linestyle="--", label=f"pass limit ({PASS_LIMIT_S*1000:.2f} ms)")
    ax.axvline(NO_TRIP_LIMIT_S * 1000, color="red", linestyle="--", label=f"no-trip limit ({NO_TRIP_LIMIT_S*1000:.0f} ms)")
    ax.set_xlabel("trip_time_s (ms)")
    ax.set_ylabel("density")
    ax.set_title("Trip-time distribution - combined 6,000 cycles (linear x)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "trip_time_histogram.png", dpi=150)
    plt.close(fig)

    # zoomed view near the pass limit, where FAILs concentrate
    fig, ax = plt.subplots(figsize=(9, 5))
    zoomed = tt[(tt > 20) & (tt < 30)]
    ax.hist(zoomed, bins=100, color="#2c7fb8", alpha=0.85, edgecolor="none")
    ax.axvline(PASS_LIMIT_S * 1000, color="green", linestyle="--", label="pass limit")
    ax.set_xlabel("trip_time_s (ms)")
    ax.set_ylabel("count")
    ax.set_title("Trip-time distribution, zoomed to 20-30ms (pass/FAIL boundary)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "trip_time_histogram_zoomed.png", dpi=150)
    plt.close(fig)


def plot_ecdf(df: pd.DataFrame):
    tt = np.sort(df.loc[df["trip_time_s"].notna(), "trip_time_s"].to_numpy()) * 1000
    y = np.arange(1, len(tt) + 1) / len(tt)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(tt, y, color="#2c7fb8")
    ax.axvline(PASS_LIMIT_S * 1000, color="green", linestyle="--", label="pass limit")
    ax.axvline(NO_TRIP_LIMIT_S * 1000, color="red", linestyle="--", label="no-trip limit")
    ax.set_xlabel("trip_time_s (ms)")
    ax.set_ylabel("cumulative fraction")
    ax.set_title("ECDF of trip_time_s - combined 6,000 cycles")
    ax.legend()
    ax.set_xscale("log")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "trip_time_ecdf.png", dpi=150)
    plt.close(fig)


def plot_vs_cycle_index(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(14, 6))
    for verdict, color in VERDICT_COLORS.items():
        sub = df[df.verdict == verdict]
        if verdict == "NO_TRIP":
            ax.scatter(sub["global_cycle_index"], [NO_TRIP_LIMIT_S * 1000] * len(sub),
                       color=color, marker="x", s=80, label="NO_TRIP (no trip_time_s; plotted at no-trip limit)", zorder=5)
        else:
            ax.scatter(sub["global_cycle_index"], sub["trip_time_s"] * 1000,
                       color=color, s=4, alpha=0.5, label=verdict)

    pass_only = df[df.verdict == "PASS"].sort_values("global_cycle_index")
    roll = pass_only.set_index("global_cycle_index")["trip_time_s"].rolling(200, center=True, min_periods=50).mean() * 1000
    ax.plot(roll.index, roll.values, color="black", linewidth=1.5,
            label="200-cycle moving average (PASS)")

    ax.axhline(PASS_LIMIT_S * 1000, color="green", linestyle="--", linewidth=1)
    ax.set_xlabel("global_cycle_index (three runs concatenated chronologically)")
    ax.set_ylabel("trip_time_s (ms)")
    ax.set_title("Trip time across the combined 6,000-cycle timeline")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "trip_time_vs_cycle_index.png", dpi=150)
    plt.close(fig)


def plot_gap_timeline(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.scatter(df["global_cycle_index"], df["utc_delta_s"], s=4, alpha=0.4, color="gray", label="cycle-to-cycle gap")
    retries = df[df["is_likely_retry"]]
    ax.scatter(retries["global_cycle_index"], retries["utc_delta_s"], color="red", marker="D", s=60, label="likely retry", zorder=5)
    ax.set_ylabel("gap since previous cycle (s)")
    ax.set_xlabel("global_cycle_index")
    ax.set_title("Cycle-to-cycle timing gaps (process-pause outliers >1hr excluded from y-axis; see REPORT.md §5)")
    ax.set_ylim(0, 150)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "gap_timeline.png", dpi=150)
    plt.close(fig)


def plot_by_hour(df: pd.DataFrame):
    d = df.copy()
    d["utc_hour"] = pd.to_datetime(d["utc_timestamp"]).dt.hour
    fig, ax = plt.subplots(figsize=(10, 5))
    d.boxplot(column="trip_time_s", by="utc_hour", ax=ax, grid=False,
              showfliers=True, patch_artist=True,
              boxprops=dict(facecolor="#2c7fb8", alpha=0.6))
    ax.set_xlabel("UTC hour of day")
    ax.set_ylabel("trip_time_s (s)")
    ax.set_title("Trip time by hour of day (UTC)")
    plt.suptitle("")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "trip_time_by_hour.png", dpi=150)
    plt.close(fig)


def plot_vs_amplitude_noise(df: pd.DataFrame):
    for col, fname, xlabel in [
        ("ref_amplitude_v", "trip_time_vs_amplitude.png", "ref_amplitude_v (V)"),
        ("noise_sigma_v", "trip_time_vs_noise.png", "noise_sigma_v (V)"),
    ]:
        fig, ax = plt.subplots(figsize=(8, 5))
        for verdict, color in VERDICT_COLORS.items():
            sub = df[(df.verdict == verdict) & df["trip_time_s"].notna()]
            ax.scatter(sub[col], sub["trip_time_s"] * 1000, s=6, alpha=0.4, color=color, label=verdict)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("trip_time_s (ms)")
        ax.set_title(f"trip_time_s vs {col}")
        ax.legend()
        fig.tight_layout()
        fig.savefig(PLOTS_DIR / fname, dpi=150)
        plt.close(fig)


def plot_verdict_strip(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(14, 2.2))
    for verdict, color in VERDICT_COLORS.items():
        if verdict == "PASS":
            continue
        sub = df[df.verdict == verdict]
        ax.scatter(sub["global_cycle_index"], [0] * len(sub), color=color, marker="|", s=400, label=verdict)
    ax.set_yticks([])
    ax.set_xlabel("global_cycle_index")
    ax.set_title("FAIL / NO_TRIP positions across the combined 6,000-cycle timeline")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "verdict_strip.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    df = pd.read_csv(IN_PATH)
    plot_histogram(df)
    plot_ecdf(df)
    plot_vs_cycle_index(df)
    plot_gap_timeline(df)
    plot_by_hour(df)
    plot_vs_amplitude_noise(df)
    plot_verdict_strip(df)
    print(f"Wrote plots to {PLOTS_DIR}")
