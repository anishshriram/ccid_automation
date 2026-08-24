# Offline Campaign Analysis (`analysis/`)

**Source files:** `analysis/combine.py`, `enrich.py`, `report.py`, `plots.py`, `deep/deep_analysis.py`, `deep/run_batch.py`, `deep/deep_report.py`
**Outputs:** `analysis/REPORT.md`, `analysis/combined_analyzed.csv`, `analysis/plots/*.png`, `analysis/deep/DEEP_REPORT.md`, `analysis/deep/deep_results.csv`, `analysis/deep/plots/*.png`

This is the analysis that turned the raw 6,000-cycle campaign data (`campaign-results-index.md`) into an actual distribution, verdict breakdown, and set of plots — plus a from-scratch, non-authoritative offline algorithm that cross-checked the committed V3 verdicts a second way. Everything here is read-only against `ccid_campaign_data/` and write-only under `analysis/`; nothing in this directory is imported by, or touches, `ccid/`, `tools/`, or any hardware.

---

## 1. What this is and isn't

Two separate pieces:

- **Descriptive analysis of the already-committed V3 verdicts** (§2-4) — distributions, breakdowns, correlations, computed directly from each run's `cycles.csv`. Nothing here recomputes or reinterprets `trip_time_s`/`verdict`.
- **An independent exploratory re-analysis of the raw waveforms** (§5-6, `analysis/deep/`) — built in the spirit of `trip-time-analysis-algorithm.md`'s own versioned-boundary philosophy ("capture first, compute later"), but deliberately living *outside* `AnalysisVersion` entirely. It does not import `ccid.analysis` and its output is not a replacement for V3's committed numbers under any circumstance.

No campaign-level pass/fail acceptance criterion is computed or implied anywhere in this directory — per `campaign-results-index.md` §4, that's a deliberate, offline, human decision.

## 2. The combine → enrich → report → plots pipeline

- **`combine.py`** loads the three real campaigns' `cycles.csv` (per `campaign-results-index.md` §2 — `200_v3_real`, `5800_v3_try2`, `5317_v3_real`) and concatenates them in **chronological order by actual run-start timestamp** (200 → 5800_v3_try2 → 5317 — *not* the order their cycle counts might suggest) into one 6,000-row `combined_cycles.csv`. A new `global_cycle_index` (1-6000) is added; every row keeps full provenance (`source_run`, `source_cycle_index`) back to its original file.
- **`enrich.py`** adds the six `sanity_checks` booleans, read from each cycle's `cycles/<n>.json` sidecar (the authoritative source — not re-derived), plus the numeric diagnostics packed into `cycles.csv`'s `notes` column (`ref_amplitude_v`, `noise_sigma_v`, thresholds, `t0_s`, `t0_source`, etc.) via `key=value` parsing.
- **`report.py`** computes the verdict breakdown, trip-time distribution/percentiles, drift regression, retry reconstruction (§3), `degraded_flags`/sanity-check breakdown, time-of-day pattern, and amplitude/noise correlations. Writes `analysis/REPORT.md` and `analysis/combined_analyzed.csv`.
- **`plots.py`** writes 9 PNGs to `analysis/plots/`: trip-time histogram (linear + zoomed, with a KDE overlay), ECDF, trip time vs. cycle index (with a 200-cycle moving average), a FAIL/NO_TRIP timeline strip, cycle-to-cycle timing gaps, a by-hour boxplot, and trip-time-vs-amplitude/noise scatter plots.

Drift regression is computed twice: within `5317_v3_real` alone (the one run long and continuous enough for a same-sitting drift check to mean anything) and across the full combined timeline. The three source runs are three separate attempts on different days, not one continuous 6,000-cycle sitting — see `campaign-results-index.md` §2 and `REPORT.md`'s own framing note.

## 3. Retry reconstruction from timing (no literal log exists)

`cycles.csv`/`runstate.json` don't record whether a given cycle followed an auto-retry — only the campaign's *final* `halt_reason` survives (`persistence-and-recovery.md` §2; `cli-lifecycle-and-monitoring.md` §8 on the auto-retry loop itself). `report.py` reconstructs likely retries from timing instead:

- A gap more than 40s above a process segment's own median cadence is flagged as a likely retry (`cooldown_retry_s` = 60s in every run's `config.yaml`).
- A negative `monotonic_start` delta marks the start of a new process segment (a crash or an operator-initiated restart) — `monotonic_start` is only comparable within one continuous process, so gaps are sized using `utc_timestamp` instead, per-segment.
- Anything over an hour is reported separately as a **process pause**, not a retry.

Result on this dataset: exactly **1** likely retry, matching the single NO_TRIP (cycle 3115 of `5317_v3_real`), and **3** process-level pauses inside `5800_v3_try2` (two multi-hour/multi-day gaps, ~19.7h and ~51.4h, plus its own final stop) — none of which are retries. See `REPORT.md` §5.

## 4. Key numbers

Full detail lives in `analysis/REPORT.md` — this is a pointer, not a duplicate:

- 5,883 PASS / 116 FAIL / 1 NO_TRIP across the combined 6,000 cycles.
- All 6 sanity checks passed on all 6,000 cycles — zero failures anywhere in the dataset.
- The trip-time distribution is **not unimodal** — a Gaussian KDE shows three distinct peaks (~12.0/17.7/23.0ms), confirmed as real structure rather than a histogram-binning artifact. Flagged as an open question, not explained here.
- A weak-to-moderate correlation exists between `trip_time_s` and each waveform's own `ref_amplitude_v`/`noise_sigma_v` — flagged with the caveat that V3's own detection thresholds are themselves derived from `ref_amplitude_v` (`trip-time-analysis-algorithm.md` §4.1), so part of this could be the algorithm's own sensitivity to signal amplitude rather than a DUT effect.

## 5. The exploratory offline algorithm (`analysis/deep/`)

`deep_analysis.py` deliberately does not import `ccid.analysis` or participate in `AnalysisVersion`. It reimplements its own waveform loader (`load_waveform_raw`) from scratch against the same `samples.bin` + `preamble.json` container format documented in `trip-time-analysis-algorithm.md` §3, and computes three independent detectors per waveform:

- **Method A** — an independent RMS-envelope threshold-crossing detector, with its own noise-floor/reference-amplitude estimators and window sizing (not copied from `ccid/analysis.py`'s formulas).
- **Method B** — CUSUM sequential change-point detection on windowed log-power. Vectorized via a closed-form solution to the Lindley/CUSUM recursion (`_cusum_recursion`'s docstring works through the identity) rather than a per-sample Python loop — a naive loop was impractical at 6,000 × 1,000,000-sample records.
- **Method C** — a sigmoid curve fit (`scipy.optimize.curve_fit`) to each transition edge, giving both a fitted center *and* a real statistical standard error from the fit covariance. This is the "principled curve-fitting instead of threshold-crossing" piece of the brief.

`run_batch.py` runs all three across all 6,000 waveforms (~10 minutes, zero errors) into `analysis/deep/deep_results.csv`. `deep_report.py` compares the three methods against each other and against the committed V3 numbers and writes `analysis/deep/DEEP_REPORT.md`.

## 6. What the cross-check found

- Method B, once stabilized with a physical-amplitude confirmation gate (unconfirmed CUSUM alone was unstable during development), converges to Method A's exact answer on effectively every cycle. Reported honestly in `DEEP_REPORT.md` §1 as a weaker independent check than originally intended, not oversold as two fully separate methods agreeing.
- A/B read ~2.4ms higher than V3 on average (no raw-sample endpoint refinement, unlike V3's `_refine_end_index`); C reads ~1.9ms lower (a curve-fit midpoint is a different, equally valid, but not-the-same endpoint definition than a threshold crossing). Both offsets are explainable definitional differences, not disagreements about the underlying event — see `DEEP_REPORT.md` §2.
- After correcting for each method's own systematic offset, **no cycle far from the 24.97ms pass limit ever flips PASS/FAIL** under any method; the handful that do flip all sit within 1.45ms of the limit already — expected boundary-band jitter given each method's own spread, not a discovered error (`DEEP_REPORT.md` §3).
- All three methods independently agree with V3's single NO_TRIP call (`DEEP_REPORT.md` §5).
- A genuine limitation was found in Method A: its ~8.33ms half-mains-cycle smoothing window introduces a much larger bias (~+5.5ms vs. its usual +2.4ms) for the very fastest trips (~7.7ms, comparable to the window itself) — a point *in favor of* V3's raw-sample-based refinement, which isn't window-limited the same way (`DEEP_REPORT.md` §4).
- An unexplained banded/stepped structure appears in the deep-vs-V3 scatter plot, possibly connected to the tri-modal trip-time distribution in §4 above — flagged as an open question in `DEEP_REPORT.md` §2, not resolved here.

**Bottom line, stated the way it should be read:** this is corroborating evidence that V3's algorithmic logic isn't obviously missing something a differently-built offline method would catch. It does not validate the hardware capture chain (scope calibration, trigger timing) upstream of the stored samples — all four methods (V3 and A/B/C) analyze the same captured samples, so a systematic problem there would be invisible to this comparison entirely.

## 7. Reproducing this

```bash
python3 analysis/combine.py
python3 analysis/enrich.py
python3 analysis/report.py
python3 analysis/plots.py
python3 analysis/deep/run_batch.py   # ~10 minutes, 6000 waveforms
python3 analysis/deep/deep_report.py
```

Reads only from `ccid_campaign_data/` (untracked, outside git — see `campaign-results-index.md` §1); writes only under `analysis/`. No step here touches `ccid/`, `tools/`, or any hardware.

## 8. Things to know if you're about to change this

- **Never import `ccid.analysis` from anything under `analysis/deep/`.** The whole point of that subdirectory is to be a genuinely independent second opinion — importing the production module's helpers, even just the loader, would undermine that.
- If you extend the exploratory algorithm, keep labeling its output non-authoritative wherever a number from it is reported — the discipline that keeps V3's committed verdicts the single source of truth depends on that never being ambiguous.
- `combine.py`'s chronological ordering (200 → 5800_v3_try2 → 5317) is derived from each run's actual first-row `utc_timestamp`, not hardcoded — if campaign data is ever re-pulled or a new run added, re-verify the order rather than assuming it.
