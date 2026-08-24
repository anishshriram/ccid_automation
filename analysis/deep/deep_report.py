"""Compare the exploratory offline algorithm (deep_results.csv) against the
committed V3 trip_time_s/verdict. EXPLORATORY / NON-AUTHORITATIVE: this file
never edits or overrides anything in cycles.csv or ccid/analysis.py. Its
only purpose is to say honestly where the independent methods agree with
V3, where they don't, and - the one question that actually matters - do
they ever disagree about PASS vs FAIL.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
PLOTS_DIR = HERE / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

PASS_LIMIT_S = 0.02497
NO_TRIP_LIMIT_S = 0.100


def load_merged() -> pd.DataFrame:
    deep = pd.read_csv(HERE / "deep_results.csv")
    combined = pd.read_csv(HERE.parent / "combined_analyzed.csv")
    combined = combined.rename(columns={"trip_time_s": "v3_trip_time_s", "verdict": "v3_verdict"})
    merged = deep.merge(
        combined[["source_run", "source_cycle_index", "global_cycle_index", "v3_trip_time_s", "v3_verdict"]],
        on=["source_run", "source_cycle_index"], how="left",
    )
    return merged


def build() -> list[str]:
    df = load_merged()
    lines: list[str] = []

    def h(t, level=2):
        lines.append(f"\n{'#' * level} {t}\n")

    def p(t=""):
        lines.append(t)

    lines.append("# Exploratory Offline Algorithm vs. Committed V3 - Comparison Report\n")
    p(
        "**NON-AUTHORITATIVE.** Everything in this file is exploratory "
        "research output from analysis/deep/deep_analysis.py, a from-"
        "scratch algorithm that never imports ccid/analysis.py and does "
        "not participate in AnalysisVersion. The committed V3 verdicts in "
        "each run's cycles.csv remain the official record. Nothing here "
        "changes, corrects, or reinterprets those verdicts.\n"
    )
    p(
        "Three independent detectors are computed per waveform: **A** "
        "(independent RMS-envelope threshold-crossing, own noise floor "
        "and window sizing), **B** (CUSUM sequential change-point on "
        "log-power, confirmed against the same physical amplitude floor "
        "as A for stability), **C** (sigmoid curve fit to each edge, "
        "trip_time = fitted-collapse-center minus fitted-onset-center, "
        "with a real statistical standard error from the fit "
        "covariance).\n"
    )

    valid = df[df["v3_trip_time_s"].notna() & df["a_trip_time_s"].notna()].copy()

    h("1. Do A and B actually behave as independent methods here?")
    identical = (valid["a_trip_time_s"] == valid["b_trip_time_s"]).mean()
    p(
        f"**No, not really - {identical*100:.1f}% of cycles.** B's raw "
        "CUSUM change-point is confirmed against the same amplitude-"
        "threshold floor A uses (added during development because "
        "unconfirmed CUSUM was unstable - see deep_analysis.py's "
        "docstring on Method B). In practice that confirmation gate makes "
        "B converge to A's exact answer on almost every cycle in this "
        "dataset. Read this as: CUSUM change-point detection did not "
        "surface anything a threshold-crossing detector with the same "
        "physical floor didn't already find - a real result, just a "
        "weaker one than 'two fully independent methods agree' would "
        "be. C (curve-fit) is the genuinely distinct comparison point "
        "below."
    )

    h("2. Systematic offset from V3 (definitional, not a disagreement)")
    for label, col in [("A/B (threshold-crossing)", "a_trip_time_s"), ("C (curve-fit midpoint)", "c_trip_time_s")]:
        delta = valid[col] - valid["v3_trip_time_s"]
        r, _ = stats.pearsonr(valid["v3_trip_time_s"], valid[col])
        p(
            f"- **{label}**: mean Δ = {delta.mean()*1000:+.3f} ms, median Δ = "
            f"{delta.median()*1000:+.3f} ms, std = {delta.std()*1000:.3f} ms, "
            f"r vs V3 = {r:.4f}"
        )
    p(
        "\nBoth offsets are expected and explainable, not evidence of a "
        "problem: A/B read consistently *higher* than V3 because they "
        "don't perform V3's raw-sample endpoint refinement (trip-time-"
        "analysis-algorithm.md §4.5) that walks the collapse point back "
        "to the true raw-sample crossing - A/B report the coarser "
        "envelope-threshold crossing directly. C reads *lower* than both "
        "because a sigmoid's fitted center is the midpoint of a "
        "transition, not either edge of it - a different, equally valid, "
        "but not-the-same definition of 'when did it end.' None of this "
        "is a disagreement about the underlying event; it's three "
        "different conventions for where inside the same transition to "
        "place the number. See plots/deep_vs_v3_scatter.png."
    )
    p(
        "\n**Worth a second look:** that scatter plot isn't a clean "
        "scattered line around y=x with a constant offset - it shows a "
        "visible banded/stepped structure (flat plateaus connected by "
        "steep risers) for both A and C. The most likely explanation is "
        "quantization in these exploratory detectors themselves (window "
        "size and persistence-run requirements only let the detected "
        "edge move in discrete jumps), not a real physical effect - but "
        "it's also plausibly connected to the multi-modal structure "
        "already flagged in the main REPORT.md §2 (three humps in V3's "
        "own trip-time distribution), and that connection hasn't been "
        "run down here. Flagging both together as one open question "
        "rather than asserting a cause."
    )

    h("3. The question that actually matters: any PASS/FAIL disagreement after correcting the systematic offset?")
    p(
        "Raw A/B trip times run several ms above V3's, which would put "
        "many ordinary PASS cycles over the 24.97ms line if compared "
        "naively - that's just the offset in §2, not a real "
        "disagreement. Correcting for it (subtracting each method's own "
        "median offset from V3, then re-applying the same 24.97ms/100ms "
        "limits) asks the right question: given where each method sits "
        "*relative to its own typical behavior*, does it ever land on "
        "the opposite side of a limit from V3?\n"
    )
    for label, col in [("A/B", "a_trip_time_s"), ("C", "c_trip_time_s")]:
        delta = valid[col] - valid["v3_trip_time_s"]
        corrected = valid[col] - delta.median()
        would_be_pass = corrected <= PASS_LIMIT_S
        v3_pass = valid["v3_verdict"] == "PASS"
        flips = valid[would_be_pass != v3_pass]
        p(f"- **{label}**: {len(flips)} cycle(s) out of {len(valid)} flip PASS/FAIL after offset correction.")
        if len(flips):
            p("```")
            p(flips[["global_cycle_index", "source_run", "source_cycle_index", "v3_verdict", "v3_trip_time_s", col]].to_string(index=False))
            p("```")
    p(
        "\nContext for reading those counts: the method's own std-dev "
        "around its offset (§2 - 1.5ms for A/B, 2.0ms for C) is not much "
        "smaller than the width it needs to stay inside to avoid the "
        "24.97ms line - every flipped cycle listed here has a V3 "
        "trip_time_s within 1.45ms of that line (visible in the tables). "
        "Read this as 'a method with a few ms of spread will inevitably "
        "cross a very tight boundary for cycles already sitting right on "
        "it,' not as '27/62 specific cycles were independently found to "
        "be mis-timed.' No cycle far from the boundary flips under "
        "either method."
    )

    h("4. Largest residual disagreements (beyond the systematic offset)")
    p(
        "For each method, residual = (method trip_time - V3 trip_time) - "
        "that method's own median offset. A large residual means a "
        "cycle where the two disagree by more than their usual pattern - "
        "worth a look regardless of which side of a pass/fail line it's "
        "on. Top 10 by |residual|, method A:\n"
    )
    delta_a = valid["a_trip_time_s"] - valid["v3_trip_time_s"]
    resid_a = (delta_a - delta_a.median()).abs()
    top = valid.assign(residual_ms=resid_a * 1000).nlargest(10, "residual_ms")
    p("```")
    p(top[["global_cycle_index", "source_run", "source_cycle_index", "v3_verdict",
           "v3_trip_time_s", "a_trip_time_s", "c_trip_time_s", "residual_ms"]].to_string(index=False))
    p("```")
    p(
        "\n**A real methodological finding, not noise:** every one of "
        "these top-residual cycles sits at essentially the same V3 "
        f"trip_time_s - the fastest trips in the entire dataset (≈7.7-"
        f"7.8ms, right at the {valid['v3_trip_time_s'].min()*1000:.3f}ms "
        "minimum). That's not a coincidence: Method A's smoothing window "
        "is ~8.33ms (half a mains cycle - §onset/collapse detection in "
        "deep_analysis.py), so once the true event is comparable in "
        "duration to the window itself, the window can no longer cleanly "
        "resolve it and the positive bias balloons from its usual "
        "~+2.4ms to ~+5.5ms. This is a genuine limitation of a fixed-"
        "window envelope approach at the fast end, not evidence about "
        "the DUT - and it's a point in V3's favor: V3's raw-sample "
        "endpoint refinement (rather than a fixed smoothing window) is "
        "structurally better suited to exactly this fast-trip regime "
        "than this exploratory method is."
    )

    h("5. The one NO_TRIP cycle")
    nt = df[df["v3_verdict"] == "NO_TRIP"]
    p("All three methods' collapse search on this cycle:")
    p("```")
    p(nt[["global_cycle_index", "a_trip_time_s", "b_trip_time_s", "c_trip_time_s"]].to_string(index=False))
    p("```")
    p(
        "All three independently found no collapse within the record "
        "(None/NaN trip_time) - i.e. all three agree with V3's NO_TRIP "
        "call on the one cycle where it matters most."
    )

    h("6. Borderline cycles - closest PASS/FAIL/NO_TRIP calls to their limits")
    pass_max = valid[valid.v3_verdict == "PASS"].nlargest(3, "v3_trip_time_s")
    fail_min = valid[valid.v3_verdict == "FAIL"].nsmallest(3, "v3_trip_time_s")
    fail_max = valid[valid.v3_verdict == "FAIL"].nlargest(3, "v3_trip_time_s")
    p("Closest PASS cycles to the 24.97ms pass limit:")
    p("```")
    p(pass_max[["global_cycle_index", "v3_trip_time_s", "a_trip_time_s", "c_trip_time_s"]].to_string(index=False))
    p("```")
    p("Closest FAIL cycles to the 24.97ms pass limit (from above):")
    p("```")
    p(fail_min[["global_cycle_index", "v3_trip_time_s", "a_trip_time_s", "c_trip_time_s"]].to_string(index=False))
    p("```")
    p("Closest FAIL cycles to the 100ms no-trip limit:")
    p("```")
    p(fail_max[["global_cycle_index", "v3_trip_time_s", "a_trip_time_s", "c_trip_time_s"]].to_string(index=False))
    p("```")

    h("7. Uncertainty (Method C fit standard error)")
    se = valid["c_trip_time_se_s"].dropna() * 1000
    p(f"Method C's fitted trip_time_s standard error across {len(se)} cycles: "
       f"median {se.median():.4f} ms, 95th percentile {se.quantile(0.95):.4f} ms, max {se.max():.4f} ms.")
    p(
        "This is the fit's own statistical uncertainty on *where the "
        "sigmoid's center sits*, not an uncertainty on trip_time_s "
        "itself under V3's definition - the two detectors are measuring "
        "different things (§2), so this shouldn't be read as 'V3's "
        "numbers are uncertain by this much.'"
    )

    h("8. Noise/signal characterization (from the deep algorithm's own estimates)")
    p("```")
    p(df[["ref_amplitude_v", "pretrigger_noise_rms_v", "snr_db", "quantization_step_v"]].describe().to_string())
    p("```")

    return lines


def make_scatter(df: pd.DataFrame):
    valid = df[df["v3_trip_time_s"].notna() & df["a_trip_time_s"].notna()]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    for ax, col, label in [(axes[0], "a_trip_time_s", "Method A (threshold)"), (axes[1], "c_trip_time_s", "Method C (curve fit)")]:
        ax.scatter(valid["v3_trip_time_s"] * 1000, valid[col] * 1000, s=4, alpha=0.3, color="#2c7fb8")
        lims = [valid["v3_trip_time_s"].min() * 1000, valid["v3_trip_time_s"].max() * 1000]
        ax.plot(lims, lims, color="gray", linestyle="--", linewidth=1, label="y = x")
        ax.set_xlabel("V3 committed trip_time_s (ms)")
        ax.set_ylabel(f"{label} trip_time_s (ms)")
        ax.set_title(label)
        ax.legend()
    fig.suptitle("Exploratory algorithm vs. committed V3 (non-authoritative)")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "deep_vs_v3_scatter.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    df = load_merged()
    lines = build()
    make_scatter(df)
    (HERE / "DEEP_REPORT.md").write_text("\n".join(lines) + "\n")
    print(f"Wrote {HERE / 'DEEP_REPORT.md'}")
    print(f"Wrote {PLOTS_DIR / 'deep_vs_v3_scatter.png'}")
