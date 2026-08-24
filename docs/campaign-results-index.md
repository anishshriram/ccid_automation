# Campaign Results Index

This is the bridge between the technical reference (`docs/`, which describes how the code works) and the actual campaign data — which runs are the real ones, what happened in each, where the data lives, and what hasn't been analyzed yet. Read this before pointing a report or an analysis session at the raw data; the run directories on disk don't distinguish a real campaign from a five-minute commissioning check by name alone.

---

## 1. Where the data lives

Pulled off the Pi's microSD card via `rsync` (not git — raw campaign data is deliberately never committed; see `legacy-documentation-audit.md` for why `runs/` was purged from git history). As of this writing it's sitting at:

```
/Users/anishshriram/Desktop/ccid_automation/ccid_campaign_data/
```

**This is inside the git repo and untracked** — check `git status` before assuming that's still true; it may have been moved to `~/Desktop/ccid_campaign_data/` (outside the repo) since. Either way, every subdirectory under it is one run, in the same layout `persistence-and-recovery.md` §3 describes: `cycles.csv`, `cycles/<n>.json`, `waveforms/<n>.npz`, `images/<n>_scope.png` / `<n>_green.jpg`, `runstate.json`, `config.yaml`.

**66 run directories exist in total. Most are not campaigns** — they're single-digit-cycle commissioning/debugging checks from the scope no-trigger investigation and the camera-gate redesign (`full_real_*`, `camera_gate_*`, `k3_disabled_*`, `v2_live_validation_*`, `gpio_real_check`, etc., dated Aug 5-12). Real, historical evidence (`build-and-commissioning-issue-log.md` tells their story), but not campaign results — don't fold them into a pass-rate calculation.

---

## 2. The real campaigns

Four runs actually matter for your 6,000-cycle goal. `runstate.json`'s `pass_count`/`fail_count` here are read directly, not recomputed — `fail_count` includes any non-`"PASS"` verdict (`FAIL` and `NO_TRIP` both), per `persistence-and-recovery.md`'s note on `record_cycle`.

| Run ID | Target | Completed | Pass | Fail/NO_TRIP | Halt reason | Status |
|---|---|---|---|---|---|---|
| `5317_v3_real_20260817T143315Z` | 5317 | 5317 | 5215 | 102 | — (clean `COMPLETE`) | Full, clean completion |
| `200_v3_real_20260813T131932Z` | 200 | 200 | 197 | 3 | — (clean `COMPLETE`) | Full, clean completion |
| `5800_v3_try2_20260813T195018Z` | 5800 | 483 | 471 | 12 | `controller:unexpected:StopRequested` | **Manually stopped, not a natural completion** — see §3 |
| `5800_v3_real_20260813T175531Z` | 5800 | 38 | 37 | 1 | `controller:unexpected:ValueError` | **Aborted by a real software bug, now fixed** — historical/diagnostic value only, see §3 |

**5317 + 200 + 483 = 6000 cycles** — this is almost certainly what "6,000 cycles complete" actually refers to, across three separate attempts rather than one continuous run. Worth stating explicitly and honestly in a report as three campaigns totaling 6,000 cycles, not one unbroken 6,000-cycle run — a careful reader will ask, and the honest framing is stronger than an imprecise one.

---

## 3. Two runs that need honest handling, not just a pass-rate rollup

**`5800_v3_try2` (483 cycles) was not a natural stopping point.** `cycles.csv` rows 482-483 show `led_state_at_gate: CAMERA_UNAVAILABLE`, `degraded_flags: vision_camera_unavailable_fixed_wait` — this is the real incident behind the camera re-enumeration bug (`docs/build-and-commissioning-issue-log.md` §8) that the equipment-refresh and stable-camera-path fixes were built for. The run was manually stopped around when that was noticed, before those fixes existed. Its `halt_reason` (`controller:unexpected:StopRequested`) is itself slightly misleading — that's an operator-requested stop getting misclassified by a real, still-open classification gap (a `StopRequested` raised inside a cycle attempt gets caught by the sequencer's generic exception handler instead of `_execute_campaign`'s dedicated one), not a second software bug. Its 483 cycles of data are real and usable; just don't describe the run as having "completed" on its own terms.

**`5800_v3_real` (38 cycles) is not campaign data at all.** It's the run that first exposed the acquisition-timeout `ValueError` (`docs/sequencer-and-state-machine.md` §5.7.3). Genuinely fixed since, confirmed by regression tests, but the fix has not been proven against a repeat of the exact real-hardware condition — its 38 cycles are historical evidence for that investigation, not part of your results.

---

## 4. What's been analyzed since, and what's still genuinely open

**As of `docs/offline-campaign-analysis.md`, the trip-time distribution, verdict breakdown, retry reconstruction, and correlation analysis below have all been done** — see that document and `analysis/REPORT.md` for the full results, and `analysis/deep/DEEP_REPORT.md` for an independent, non-authoritative cross-check of the committed V3 verdicts against three from-scratch detection methods. What was open at the time this section was originally written:

- ~~No trip-time distribution, percentile, or pass-rate-vs-24.97ms-limit analysis exists anywhere yet.~~ Done — `analysis/REPORT.md` §2, computed across the combined 5,999 cycles with a recorded `trip_time_s` (5317+200+483, one NO_TRIP excluded).
- ~~`degraded_flags` and non-`PASS` breakdown haven't been separated.~~ Done — `analysis/REPORT.md` §1 and §6 keep `PASS`/`FAIL`/`NO_TRIP` and `degraded_flags` separate throughout, and §5 reconstructs which cycles were likely auto-retried from timing (no literal retry log exists — see `offline-campaign-analysis.md` §3 for the method).
- **No campaign-level acceptance criteria have ever been defined by the software, and that's deliberate — this is still true and still open on purpose.** `handoff_latest.md` §16 states this explicitly as the user's own decision, offline, after the run. The analysis work above deliberately reports numbers only and does not infer or default to one; don't let a future report or analysis session invent one either.
- **V3 replay of pre-V3 data is still outstanding** — the older 25-cycle archived campaign (`real_25cycle_20260811T211126Z`, referenced throughout `build-and-commissioning-issue-log.md` §5 for the Cycle 17 false-FAIL story) was analyzed under V2, which had the onset-refinement defect V3 fixed. It appears in `ccid_campaign_data/` with only a partial file set (3 entries, not the usual full run layout) — verify what's actually present before relying on it, and don't assume its original V2 verdicts are correct without replaying under V3 (`tools/replay_waveform.py`, `docs/tools.md`). Not part of the 6,000-cycle analysis above; still a separate, open task.
- **Two open questions surfaced by the new analysis, not resolved yet:** the combined trip-time distribution is genuinely tri-modal (three distinct KDE peaks, not a binning artifact), and the exploratory algorithm's trip-time-vs-V3 scatter shows an unexplained banded/stepped structure that may or may not be related — see `analysis/REPORT.md` §2 and `analysis/deep/DEEP_REPORT.md` §2.

---

## 5. Where the methodology/schema documentation actually lives

- What every `cycles.csv` column and `degraded_flags` value means: `persistence-and-recovery.md` §2-3
- What `analysis_version`/`notes`/`t0_source`/verdict logic mean, and the V1→V2→V3 story: `trip-time-analysis-algorithm.md`
- What `led_state_at_gate` values mean and how the charging gate works: `vision-and-charging-gate-classification.md`
- The full cycle state sequence a report's "Test Procedure" section should describe: `sequencer-and-state-machine.md`
- Physical rig/equipment description (not yet migrated into `docs/` — still only in the legacy file): `handoff_latest.md` §3-9
- The actual 6,000-cycle results (distributions, verdict breakdown, retry reconstruction) and the independent exploratory cross-check of V3: `offline-campaign-analysis.md`, `analysis/REPORT.md`, `analysis/deep/DEEP_REPORT.md`
