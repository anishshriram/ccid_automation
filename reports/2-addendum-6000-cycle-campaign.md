# Project A.M.P.E.R.E.
## Addendum I: 6,000-Cycle Endurance Test Campaign

## Abstract

Project A.M.P.E.R.E., described in full in the accompanying System Design and Validation report, was built to exercise a CCID's trip-time behavior across a far larger number of cycles than a manual test bench can practically produce. This addendum reports what happened when it was: a combined 6,000-cycle endurance campaign, run across three separate attempts rather than one unbroken sitting, producing 5,883 passing verdicts, 116 fails, and a single no-trip out of 5,999 cycles with a recorded trip time. It covers the campaign's operational structure and preflight procedure, the incidents that actually occurred during the run itself, the resulting trip-time distribution and its behavior over the course of the campaign, a reconstruction of when the system's auto-retry logic actually engaged, several observations that were not specifically anticipated going in, and an independent, non-authoritative cross-check of the recorded verdicts using a second, from-scratch analysis method. Consistent with a deliberate design decision described in the System Design report, no campaign-level pass/fail acceptance criterion is proposed or implied anywhere in this document; the numbers are reported, not graded.

## 1. Introduction

### 1.1 Purpose of This Addendum

Where the System Design and Validation report describes what Project A.M.P.E.R.E. is and how it was built and validated to be trusted with a large unattended campaign, this addendum describes what actually happened the one time it was pointed at the scale it was built for. Its purpose is narrow and specific: to report, honestly and in detail, how the 6,000-cycle campaign was structured and run, what incidents occurred during execution, what the resulting trip-time data looks like, and what a second, independently built analysis method finds when it is pointed at the same raw waveforms. It is a record of one campaign's execution and results, not a restatement of the system's design.

### 1.2 Relationship to the System Design Report

This document assumes the System Design and Validation report as prerequisite reading and does not re-derive anything covered there. The physical rig, the safety interlocks, the trip-time measurement methodology, the analysis algorithm and its versioning, the vision-based charging-gate logic, and the commissioning process that validated all of it before this campaign ever began are described in that report and are referenced here by section rather than repeated. This addendum picks up specifically where that report's own conclusion left off: it covers the campaign's execution and the incidents that arose during that execution, not the commissioning history that preceded it - a scope boundary drawn deliberately, since a defect found and fixed during commissioning belongs to the story of how the system came to be trustworthy, while an incident occurring during the campaign itself belongs to the story of what happened when it was actually run.

## 2. Campaign Structure and Rationale

### 2.1 Why Three Attempts Totaling 6,000 Cycles

The 6,000 cycles reported in this addendum were not produced by one unbroken run. They come from three separate real campaigns, run on different days, whose target cycle counts happen to sum to exactly 6,000: a 200-cycle run, a 483-cycle run, and a 5,317-cycle run. This is stated plainly rather than left for a reader to discover, because it materially changes how several of the results in Section 5 should be read - most importantly, the trip-time-over-time analysis in Section 5.3, which cannot treat the combined dataset as one continuous timeline without acknowledging the seams between attempts. The intended meaning of "6,000 cycles complete" for this project was always that the CCID be exercised across 6,000 cycles in total, not that it survive 6,000 uninterrupted repetitions in a single sitting, and the data reflects that intention rather than falling short of a stricter one.

Of the three, only the 5,317-cycle run was both a full, clean completion and internally continuous throughout - it ran to its target with no halt and no evidence of a process interruption anywhere in the middle. The 200-cycle run also completed cleanly to its target. The 483-cycle run did not: it was targeting 5,800 cycles, was manually stopped well short of that target once camera-unavailable behavior was noticed on its final two cycles, and was itself not a single continuous sitting - two real, multi-hour gaps occurred inside it (reconstructed from its own timing data in Section 4), meaning the process running it was left idle or restarted between spans of activity more than once before it was ultimately stopped by hand. None of this makes its 483 cycles of data any less real or usable; it means that run specifically should not be read as 483 consecutive cycles any more than the full campaign should be read as 6,000 consecutive ones.

A fourth attempt exists in the underlying data and is deliberately excluded from this addendum's 6,000-cycle total: an earlier 5,800-cycle attempt that halted after only 38 cycles due to a genuine software defect - a boundary-condition race in the scope-acquisition timeout logic, described in the System Design report's commissioning section - that has since been fixed and covered by regression tests, but whose fix has not been re-proven against a live repeat of the exact real-hardware condition that originally exposed it. Those 38 cycles are retained as historical evidence of that investigation, not folded into the campaign results reported here.

### 2.2 Campaign Identifiers and Data Provenance

The three campaigns that make up the 6,000-cycle total, identified by their run IDs, chronological start time, and outcome:

| Run ID | Target | Completed | Pass | Fail / No-Trip | Outcome |
|---|---|---|---|---|---|
| `200_v3_real_20260813T131932Z` | 200 | 200 | 197 | 3 | Full, clean completion |
| `5800_v3_try2_20260813T195018Z` | 5,800 | 483 | 471 | 12 | Manually stopped short of target, not a natural completion |
| `5317_v3_real_20260817T143315Z` | 5,317 | 5,317 | 5,215 | 102 | Full, clean completion |

The chronological order in which these three campaigns actually ran - by their own start timestamps - is `200_v3_real` (August 13, 13:19 UTC), then `5800_v3_try2` (August 13, 19:51 UTC), then `5317_v3_real` (August 17, 14:33 UTC). This is the order used everywhere in this addendum a single combined timeline across all three campaigns is presented, since it reflects the sequence in which the underlying cycles were actually produced; it is not the order the campaigns' target sizes might otherwise suggest.

Every cycle used in this addendum's analysis carries its original source run ID and its original cycle index alongside a newly assigned position in the combined timeline, so any statistic or plot in this document can always be traced back to the exact source file it came from. All three campaigns ran under a configuration whose measurement-relevant values - the pass and no-trip time limits, the analysis algorithm version, and every threshold the analysis algorithm itself uses - were identical; the only configuration differences between them are operational rather than measurement-relevant (the mechanism used to resolve the camera's device path, and the periodic/reactive equipment-refresh behavior described in the System Design report's Section 4.4, which was only added after the 200- and 483-cycle campaigns had already run). Combining the three into one dataset for analysis purposes does not mix data collected under different measurement definitions.

The underlying data directory this addendum draws from also contains a large number of additional run directories from earlier commissioning and debugging work, along with the excluded 38-cycle attempt described in Section 2.1. None of those are campaign data, and none of them are included in any figure or statistic reported here.

## 3. Preflight and Execution Procedure

### 3.1 Preflight Checklist Summary

Every real campaign is preceded by a fixed preflight sequence, kept as a single living checklist document separate from this addendum and referenced here rather than reproduced in full. It runs, in order, through a physical safety inspection performed with all mains and coil supplies off (protective-earth continuity, correct contactor wiring and labeling, flyback diodes intact, probe grounding and attenuation, camera framing, an accessible emergency disconnect); a check of the Pi and repository state (a clean, synchronized checkout, no other campaign process already running, sufficient free disk space); a software test gate requiring the full automated test suite to pass with only its known, intentional skips; a check that the configured analysis algorithm version is the current one; a camera preflight confirming the webcam enumerates correctly and its exposure is locked to its validated manual value rather than left on automatic; a contactor preflight confirming each of the three contactors pulls in and releases cleanly after any wiring or mounting change; an oscilloscope preflight confirming USB enumeration, a clean SCPI error queue, and successful configuration; and, for an unattended run, a network preflight and a monitoring preflight confirming the external heartbeat service is actually reachable and correctly configured. No step in this sequence is treated as optional for a real-hardware campaign, and a failure at the software test gate in particular is treated as disqualifying regardless of how the hardware itself looks.

### 3.2 Run Procedure (Supervised and Autonomous)

Two run patterns are available once preflight passes. A supervised run is started interactively over SSH and depends on that session remaining open for the duration of the campaign; it is the pattern used for a first cycle after any hardware change, for recommissioning, or for a short debugging campaign where an operator wants to watch the run directly. An autonomous run is instead launched as a transient background service unit, tied to a freshly generated, never-reused run identifier, and continues running if the SSH session that launched it disconnects - the pattern intended for a long unattended campaign such as this one. In both cases the same rule applies once a run is underway: nothing about scope or camera controls is touched manually during the campaign, and a halt is never resumed automatically without a human first reading why it happened.

It is worth noting explicitly that a second, persistent, always-enabled version of the autonomous pattern exists in the project's own deployment configuration but has never actually been exercised in producing any of the three real campaigns behind this addendum, or any other real campaign to date; every real unattended run performed on this system has used the transient pattern described above. This is stated here because it bears directly on how any timing anomaly observed during campaign execution should be interpreted, and is discussed further where it becomes relevant in Section 4.

### 3.3 Post-Run Verification

Once a campaign ends - by reaching its target cycle count or by halting - and the rig has been fully de-energized, the same verification sequence is applied before its results are treated as trustworthy. The run's own recorded state is checked first: that its highest completed cycle number matches either the target or an expected halt point, that its halt reason is empty for a campaign that reached its target normally, and that its pass and fail counts are plausible on their face. The full per-cycle log is then reviewed for internal consistency - every accepted pass has a strictly positive trip time, a computed end instant no earlier than its computed onset instant, the expected analysis algorithm version recorded against it, every waveform sanity check reported true, the camera confirming a genuine charging state at the moment the gate was granted, and no unexplained degraded-mode flag. Finally, the expected set of per-cycle artifacts - the JSON sidecar, the raw waveform, the scope screenshot, the camera gate frame - is confirmed present for every cycle the run claims to have completed. No cycle's data, including a failed or otherwise invalid one, is ever discarded during this process; every record is preserved for replay and later analysis regardless of what it shows.

## 4. In-Campaign Incidents

### 4.1 Mid-Run Process Pauses (5800_v3_try2)

Neither `cycles.csv` nor the run's own final state record whether or when a campaign's controlling process was paused or restarted mid-run - only the run's last halt reason survives. Three real gaps were nonetheless reconstructed from the 483-cycle run's own timestamps, by comparing the wall-clock time recorded against each cycle to the controller's own internal monotonic clock reading at that same cycle: a roughly 19.7-hour gap before cycle 140, a roughly 51.4-hour gap (over two days) before cycle 384, and a roughly 10.6-hour gap before cycle 414. The first two are confirmed process restarts, not merely long waits - the controller's internal clock reading actually went backward relative to where a continuously running process would have left it, which only happens when the process itself is stopped and started again. The third, by contrast, shows no such discontinuity: the same process appears to have simply sat idle for roughly ten and a half hours before resuming on its own clock, without restarting.

Nothing in the retained data records why any of these three pauses happened - whether by deliberate operator action, a lost connection, or something else entirely - and this addendum does not guess at a cause it cannot support. What can be said is that this run's 483 cycles were collected across at least four separate spans of activity over roughly four calendar days rather than in one sitting, which is consistent with a supervised session being started, stopped, and resumed by an operator across several separate visits to the rig rather than with an autonomous service running unattended and uninterrupted throughout (Section 3.2). None of the cycles immediately surrounding any of the three gaps show an unusual verdict or any sign that the pause itself affected a measurement; the pauses are a fact about when the data was collected, not about what the data shows.

### 4.2 Auto-Retry Activation

The asymmetric auto-retry behavior described in the System Design report's Section 4.4 - a short, three-strike leash for a genuine no-trip verdict, a longer, five-strike leash for anything else that halts a cycle without a device verdict - engaged exactly once across the entire 6,000-cycle campaign. It followed the single no-trip verdict recorded in the 5,317-cycle run (cycle 3,116 of that run), where the gap before the next cycle ran roughly fifty seconds longer than that run's own typical cadence - closely matching the campaign's configured sixty-second retry cooldown - immediately after which a normal cycle resumed. No cycle-index numbering gap appears anywhere in any of the three campaigns, which rules out the other class of halt this mechanism is meant to catch: nothing that halted before a cycle could even be committed to the record ever occurred. A full quantitative account of streak behavior, including how far the campaign ever came from exhausting either limit, is given in Section 5.5.

### 4.3 Equipment-Refresh Activations

The periodic and reactive equipment-refresh behavior described in the System Design report's Section 4.4 was only present in the configuration used for the 5,317-cycle run; it had not yet been introduced when the 200- and 483-cycle runs took place. Within the 5,317-cycle run itself, no direct evidence was found that either the scheduled every-fifty-cycle refresh or the reactive after-three-consecutive-unavailable refresh ever visibly affected a cycle - no degraded-mode flag of any kind appears anywhere in that run's 5,317 cycles, and no cycle-timing pattern in the data lines up cleanly with the fifty-cycle schedule in a way that could be distinguished with confidence from ordinary variation in how long the camera-based charging-gate wait naturally takes cycle to cycle. This is consistent with the refresh mechanism doing nothing more than its intended job silently in the background, but it cannot be read as direct confirmation that it fired and worked, since a silent absence of problems does not by itself prove a mechanism was exercised.

The one real camera-unavailable incident that occurred anywhere in this campaign's data - the two degraded cycles at the very end of the 483-cycle run, described further in Section 5.6 - happened in the run that preceded equipment refresh's introduction into the configuration, using the older fixed-wait degraded-handling behavior rather than the newer recovery mechanism. No comparable incident occurred in the later 5,317-cycle run, which did have equipment refresh available to it. That absence is suggestive of the fix having done its job, but it is not something this addendum can state with statistical confidence: the underlying event is rare enough in this dataset - one occurrence in 6,000 cycles - that its non-recurrence in a single subsequent run is not, on its own, strong evidence of anything.

## 5. Results

All figures in this section are read directly from the already-committed verdicts and trip-time values produced by the analysis algorithm described in the System Design report's Section 3 - nothing here recomputes, corrects, or reinterprets a verdict. No campaign-level pass/fail acceptance judgment is made anywhere in this section; the numbers are reported as they are, and any judgment of whether they constitute an acceptable outcome is deliberately left to the reader.

### 5.1 Verdict Summary

Across the combined 6,000 cycles: 5,883 pass (98.05%), 116 fail (1.93%), and 1 no-trip (0.02%). These three categories are kept separate throughout this addendum rather than folded together, since the system's own run-state record only ever tracks a single combined "not pass" count internally, which would otherwise obscure that all but one of the non-passing cycles in this entire campaign were ordinary fails rather than the more serious no-trip condition.

### 5.2 Trip-Time Distribution

Across the 5,999 cycles with a recorded trip time (the single no-trip cycle has none, by definition), the mean trip time was 16.77 ms with a standard deviation of 4.07 ms. The distribution's shape: minimum 7.69 ms, 5th percentile 11.55 ms, median 16.53 ms, 95th percentile 24.25 ms, 99th percentile 25.15 ms, maximum 25.56 ms. Against the campaign's two configured limits, a 24.97 ms pass limit and a 100 ms no-trip limit: the slowest passing cycle in the entire campaign recorded 24.9605 ms, 9.5 microseconds under the pass limit; the fastest failing cycle recorded 24.9715 ms, just above it; and the slowest failing cycle recorded 25.5580 ms, 74.4 ms clear of the no-trip limit on the other side.

The distribution is not unimodal. A smoothed density estimate shows three distinct peaks, at roughly 12.0 ms, 17.7 ms, and 23.0 ms, with consistent spacing of approximately 5.3-5.6 ms between them - confirmed as a real structural feature of the data rather than an artifact of how the underlying histogram was binned. That spacing does not cleanly correspond to either a half or a quarter mains cycle at 60 Hz, so it is not readily explained by a simple zero-crossing-locked tripping mechanism. This is reported here as an open observation rather than an explained one; no cause is proposed for it in this addendum.

### 5.3 Trip-Time Behavior Over the Campaign Timeline

Because the combined 6,000-cycle timeline is three separate attempts laid end to end rather than one continuous run (Section 2.1), a drift check was performed two ways: once across the full combined timeline, and once restricted to the 5,317-cycle run alone, since that is the only one of the three attempts both long enough and internally continuous enough (Section 4.1) for a same-sitting drift trend to mean anything on its own. Neither analysis found a statistically meaningful trend. Within the 5,317-cycle run alone, a linear fit against cycle index gave a slope of about -0.03 microseconds per cycle across passing and failing cycles together (r = -0.011, p = 0.41) - not distinguishable from no trend at all, and if anything very slightly negative rather than positive. The same fit across the full combined 6,000-cycle timeline gave a comparably small, comparably insignificant slope (r = -0.009, p = 0.51). Restricting either fit to passing cycles only changes the numbers negligibly and does not change this conclusion. No slow upward creep in trip time - the kind of trend that might indicate a device or measurement chain degrading over the course of the campaign - is present in this data at a level distinguishable from ordinary cycle-to-cycle noise.

### 5.4 FAIL and NO_TRIP Cycles in Detail

All 116 fail verdicts in this campaign fall inside the 24.97-100 ms band by definition, and every one of them sits close to the pass-limit end of that band: the closest fail to the pass limit recorded 24.9715 ms (0.0015 ms above the line), and even the fail furthest from the pass limit recorded only 25.5580 ms - meaning no fail anywhere in this campaign came remotely close to the no-trip limit on the other side. There is no cycle in this dataset that could be described as a near-miss no-trip that happened to clear just in time; every fail observed was a clearing that took slightly, not dramatically, longer than the pass threshold allows. Fails occurred in all three campaigns (3 of 200 cycles, 12 of 483, 101 of 5,317), broadly proportional to each campaign's size.

The single no-trip verdict occurred at cycle 3,116 of the 5,317-cycle run, recorded 2026-08-19 19:32:45 UTC. Its analysis record shows no envelope collapse anywhere within the full captured window - the fault current was still conducting at the end of the record, not merely slow to clear - and nothing about that waveform's own signal-quality figures (reference amplitude, noise level) stands out as anomalous relative to the rest of the campaign; there is no indication this was a capture or equipment problem rather than a genuine non-clearing event.

### 5.5 Retry and Streak Analysis

Of the two auto-retry streak limits described in the System Design report - three consecutive no-trip verdicts, or five consecutive halts of any other kind, before the campaign requires a human to intervene - neither came close to being exhausted anywhere in this campaign. The no-trip streak reached a maximum of one, since only a single no-trip verdict occurred in the entire 6,000 cycles and it was not adjacent to another one. The other streak, covering rig faults, unexpected controller exceptions, and persistence failures, never advanced past zero: no cycle-index gap appears anywhere in any of the three campaigns' records, which is what a halt of that class would leave behind, and no sanity check of the kind that can itself trigger a rig-fault halt ever failed (Section 5.7). The one retry that did occur (Section 4.2) resolved on its very next attempt.

### 5.6 Correlations and Secondary Observations

A moderate correlation was found between a cycle's trip time and two of the diagnostic figures the analysis algorithm computes from each waveform: a positive correlation with the waveform's own reference amplitude (r = 0.44), and a negative correlation with its estimated noise level (r = -0.59), both statistically significant given the sample size. Neither should be read as necessarily physical without qualification: the algorithm's own onset and collapse thresholds are themselves derived from each waveform's reference amplitude (System Design report, Section 3.2), so part of this relationship may reflect the detection algorithm's own sensitivity to signal amplitude rather than a genuine change in how the device under test behaves at different fault current levels. Separately, reference amplitude itself varies across a narrow, quantized range consistent with ordinary mains-voltage fluctuation rather than equipment drift, and its own correlation with cycle index across the campaign is weak (r = 0.13), which does not support reading it as a monotonically drifting instrument calibration.

No meaningful pattern was found in trip time or pass rate by hour of day; both stayed within a narrow, unremarkable band across all hours represented in the data. The only two degraded-mode cycles observed anywhere in the campaign are the pair discussed in Section 4.3, at the end of the 483-cycle run.

### 5.7 Sanity-Check and Data-Integrity Summary

Every one of the analysis algorithm's six waveform sanity checks - signal presence, absence of pre-fault leakage current, sufficient record length to rule out a no-trip, onset timing consistency, a clean final collapse, and no-trip persistence where relevant - passed on all 6,000 cycles in this campaign, with zero exceptions. No cycle's notes record a failed sanity check of any kind. Every cycle's onset time was resolved from the waveform's own detected onset, the only t0 source path actually wired into the live system (System Design report, Section 3.1), with no cycle falling back to an unresolved default. Taken together, this is a clean result for the capture and analysis pipeline's own internal consistency across the full campaign, independent of and separate from the trip-time results themselves.

## 6. Independent Algorithm Cross-Validation

### 6.1 Motivation

The production analysis algorithm described in the System Design report's Section 3 is deliberately simple, real-time-safe, and constrained by having to run unattended, cycle after cycle, on the rig itself. Nothing about that constraint applies to an analysis performed after the fact, offline, against the raw waveforms the campaign already preserved. This section reports what happened when three independently built, structurally unconstrained analysis methods were pointed at the same 6,000 raw waveforms this campaign produced, asked one specific question: does a heavier, differently designed approach ever disagree with the production algorithm's committed verdict. This exercise is a second opinion, not a second measurement system - its output is not, and is not treated anywhere in this document as, a replacement for the verdicts reported in Section 5.

### 6.2 Methodology

Three detectors were built from scratch specifically for this cross-check, deliberately kept structurally separate from the production algorithm - none of them import or depend on it in any way, and none of their output is fed back into a verdict anywhere. The first is an independent envelope-threshold detector, with its own noise-floor and reference-amplitude estimators and its own window sizing, conceptually similar to the production approach's threshold-crossing philosophy but built and calibrated independently rather than sharing any of its formulas. The second is a sequential change-point detector operating on windowed signal power, a genuinely different algorithmic family from threshold-crossing entirely. The third fits a smooth sigmoid curve to each transition edge in a waveform and reports the fitted center of that transition, together with a real statistical standard error taken from the fit itself, rather than reporting the instant a fixed threshold happened to be crossed - a fundamentally different definition of where a transition sits, not merely a different way of finding the same instant. All three ran successfully across all 6,000 waveforms in this campaign with no failures.

One methodological result is worth stating plainly rather than glossing over: the change-point detector, once modified during development to require confirmation against the same physical amplitude floor the threshold detector uses (an earlier, unconfirmed version proved unstable on real data), ended up landing on effectively the same answer as the threshold detector on nearly every cycle in this dataset. That makes it a weaker independent check than originally intended - not two fully separate methods agreeing, but one method's answer confirmed by a second method that had to be tied closely to the first one's own physical calibration to behave reliably. The curve-fit method remained the genuinely distinct comparison point throughout.

### 6.3 Findings

Both alternative methods disagree with the production algorithm's trip-time numbers by a small, systematic, and explainable amount, not by an unpredictable one. The threshold-based methods read consistently about 2.4 ms higher on average than the production algorithm, because they lack the production algorithm's raw-sample endpoint refinement step (System Design report, Section 3.2) and report the coarser envelope-threshold crossing directly instead. The curve-fit method reads about 1.9 ms lower on average, because a fitted transition's center point is a different, equally valid, but not equivalent definition of "when did it end" than a threshold crossing is. Neither offset represents disagreement about what physically happened in a given cycle; both are consequences of three different conventions for where inside the same transition to place a number.

The question that actually matters is whether either alternative method ever disagrees with the production algorithm about which side of the pass/fail line a cycle falls on, once each method's own predictable offset is accounted for. After that correction, no cycle far from the 24.97 ms boundary flips from pass to fail or back under either alternative method anywhere in this campaign. A small number of cycles do flip - 27 under the threshold-based methods, 62 under the curve-fit method, out of 5,999 - but every one of them was already a cycle whose production-algorithm trip time sat within roughly a millisecond and a half of the boundary to begin with. That is consistent with what would be expected from any method carrying a few milliseconds of its own spread being compared against a very tight boundary, not with either method having identified a specific set of cycles it believes were mistimed. Separately, all three alternative methods independently agree with the production algorithm's single no-trip verdict - none of the three found a collapse anywhere in that waveform either.

One genuine, specific limitation of the alternative methods was found rather than merely disclaimed: the threshold-based methods' fixed smoothing window, sized to about eight milliseconds, produces a substantially larger bias - roughly 5.5 ms rather than the usual 2.4 ms - for the very fastest trips in this campaign's data, those in the 7-8 ms range, since an event that short is comparable in duration to the window used to smooth it. This is a real structural weakness of a fixed-window approach at the fast end of the distribution, and it stands as a specific, evidenced point in favor of the production algorithm's raw-sample refinement design, which is not limited in this same way. Separately, a scatter comparison of the alternative methods' trip times against the production algorithm's own numbers shows a visibly banded, stepped structure rather than a smooth relationship with simple scatter around it - a pattern not fully explained in this addendum, and one that may or may not be connected to the tri-modal distribution shape noted in Section 5.2. Both observations are recorded here as open questions rather than resolved ones.

Taken as a whole, this cross-check is corroborating evidence that the production algorithm's logic is not obviously leaving something on the table that a differently built, unconstrained offline method would catch. It is not, and cannot be, evidence about the measurement chain upstream of the stored samples - the oscilloscope's own calibration, trigger timing, and sample-rate accuracy are shared inputs to all three alternative methods and to the production algorithm alike, so a systematic problem at that level would be invisible to every method compared here, including the one this campaign's results in Section 5 are built on.

## 7. Discussion

### 7.1 System Reliability Assessment

What this campaign supports saying, directly from its own data: the rig completed 6,000 real fault-injection cycles across three attempts without a single safety-relevant incident - no pre-fault leakage detection ever fired, no waveform sanity check ever failed, and no cycle's own record shows any sign of an interlock behaving unexpectedly. The measurement and analysis pipeline behaved with complete internal consistency across the entire campaign (Section 5.7), and the one auto-retry event and the process pauses within the 483-cycle run were each recovered from cleanly, without losing, duplicating, or misattributing a single cycle's data. The independent cross-check in Section 6 found no evidence that the production analysis algorithm's real-time-safe design is missing something a heavier offline method would catch. Taken together, this is evidence that the system described in the accompanying System Design report performed as it was designed to, operationally, at the scale it was built for.

What this campaign does not, and cannot, support saying is whether a 98.05% pass rate, or the specific pattern of fails observed, constitutes an acceptable result for the device under test. That judgment was deliberately excluded from this system's own design from the start (System Design report, Section 4.5) and remains excluded here; this addendum reports the verdict counts, the distribution, and every other figure in Section 5 as data, not as a graded outcome, and it does not propose a threshold the reader should apply to them. It is also worth being precise about what this campaign does not validate: nothing in this addendum, including Section 6's cross-check, independently confirms the oscilloscope's own calibration, trigger timing accuracy, or sample-rate accuracy - every method compared in this document works from the same captured samples, so a systematic problem upstream of those samples would be invisible to all of them at once. Several limitations already known before this campaign began (System Design report, Section 4.5) also remain genuinely open after it: the measurement endpoint definition is still the project's own provisional reading of the governing standard, contactor state is still only ever known as commanded rather than confirmed, and protective-earth continuity is still only inferred indirectly.

### 7.2 Lessons Learned

The clearest thread connecting this campaign back to the commissioning story in the System Design report is that a system judged trustworthy in advance can still surface things nobody anticipated once it is actually run at scale - and that the right response, demonstrated repeatedly during commissioning and repeated again here, is to look for those things deliberately rather than to stop looking once the obvious checks pass. Retaining full raw timestamps on every cycle, not just a summary log, is what made it possible to reconstruct the three process pauses in the 483-cycle run after the fact (Section 4.1); nothing in the system's own explicit logging captured that those pauses happened, or why. Running a genuinely independent second analysis method against the same data, even with no specific reason to expect it would disagree, is what turned up both a real limitation in a candidate alternative method (Section 6.3's fast-trip window bias) and an unexplained structural pattern (the banded cross-validation scatter) that a simple "does it agree with the production number" check would have missed entirely.

The two open structural questions this campaign leaves behind - the trip-time distribution's three distinct peaks (Section 5.2) and the stepped structure in the cross-validation comparison (Section 6.3) - are, on their own, a useful reminder that a campaign with a completely clean sanity-check record (Section 5.7) is not the same thing as a campaign with nothing left to investigate. Both are recorded here as open rather than resolved specifically so they are not lost between this addendum and whatever analysis follows it.

### 7.3 Remaining Open Items

Carried forward, unresolved by this campaign: contactor physical-state readback; independent protective-earth continuity monitoring; confirmation of the measurement endpoint definition against the published UL 2231-2 text; replay of the earlier archived 25-cycle dataset under the current analysis algorithm version; the persistent, always-enabled systemd deployment pattern, still never exercised in producing a real campaign (this addendum's own process-pause evidence in Section 4.1 is, if anything, more consistent with supervised sessions than with any systemd-managed pattern having run continuously); the external monitoring service's full pause/resume lifecycle, not independently reconfirmed by anything in this document; and full watchdog, reboot, and true power-loss recovery commissioning, as distinct from the unplanned process interruptions this campaign happened to produce and recover from.

Partially addressed by this campaign: the auto-retry mechanism was exercised for real, exactly once, and resolved cleanly (Section 4.2), which is real evidence toward - though not exhaustive proof of - its correctness under an actual repeat condition. The equipment-refresh mechanism's real-world activation remains unconfirmed either way (Section 4.3). The specific first-cycle zero-trip-time behavior noted as unresolved in the System Design report did not recur anywhere in this campaign - all three runs' own opening cycles produced ordinary, unremarkable trip-time readings - though its underlying cause was never identified, so its absence here should be read as "did not happen to occur in this data," not as "fixed."

New to this campaign, not anticipated going in: the tri-modal shape of the trip-time distribution (Section 5.2); the banded, stepped structure in the alternative-algorithm comparison (Section 6.3), possibly related to the first item but not established as such; the cause of the three mid-run process pauses in the 483-cycle run (Section 4.1), for which no root cause survives in the retained data; and whether the moderate correlation between trip time and each waveform's reference amplitude and noise level (Section 5.6) reflects a genuine property of the device under test or is partly an artifact of the production algorithm's own amplitude-derived thresholds. None of these four is resolved in this document.

## 8. Conclusion

Project A.M.P.E.R.E.'s System Design and Validation report closed by stating that whether the system performed as intended at scale was a question it could not answer in the abstract, and left that question to this addendum. Across three attempts totaling 6,000 real fault-injection cycles, it did: every cycle completed safely, the crash-safe persistence design recovered cleanly from every process interruption the campaign happened to encounter, the one auto-retry event resolved on its next attempt, and the analysis pipeline's own sanity checks passed without a single exception anywhere in the data. An independent, non-authoritative second analysis method, built from scratch specifically to look for disagreement, found none that could not be explained by the two methods' own differing definitions of a measurement endpoint.

The trip-time results themselves are reported in full in Section 5 and are not restated or graded here. This addendum has deliberately stopped short of judging whether they represent an acceptable outcome for the device under test, consistent with the system's own design principle that a campaign-level acceptance decision belongs to a human, made offline, not to anything this document or the software that produced its data infers on its own. What this addendum has established is narrower and, in its own way, more foundational: that the system performed reliably enough, and transparently enough, for its own results to be trusted as an accurate account of what actually happened during those 6,000 cycles - which is the precondition any acceptance judgment built on top of this data would depend on.

Several questions remain open. Two of them - the shape of the trip-time distribution and the structure seen in the algorithm cross-check - were not anticipated before this campaign began and are recorded here specifically so they are carried forward rather than lost. Section 7.3 lists all of them in full, alongside the limitations that were already known before this campaign started and remain known after it. None of them were treated as disqualifying to reporting the results in this document; all of them are stated plainly so that whoever makes use of this data next does so with the same honest picture of its limits that this report has tried to maintain throughout.

## Appendix A. Full Data Tables

**Table A.1 — Campaign summary**

| Run ID | Target | Completed | Pass | Fail | No-Trip |
|---|---|---|---|---|---|
| `200_v3_real_20260813T131932Z` | 200 | 200 | 197 | 3 | 0 |
| `5800_v3_try2_20260813T195018Z` | 5,800 | 483 | 471 | 12 | 0 |
| `5317_v3_real_20260817T143315Z` | 5,317 | 5,317 | 5,215 | 101 | 1 |
| **Combined** | — | **6,000** | **5,883** | **116** | **1** |

**Table A.2 — Trip-time distribution (n = 5,999, PASS + FAIL cycles)**

| Statistic | Value |
|---|---|
| Mean | 16.77 ms |
| Std. deviation | 4.07 ms |
| Minimum | 7.69 ms |
| 5th percentile | 11.55 ms |
| 25th percentile | 12.89 ms |
| Median | 16.53 ms |
| 75th percentile | 19.16 ms |
| 95th percentile | 24.25 ms |
| 99th percentile | 25.15 ms |
| Maximum | 25.56 ms |

**Table A.3 — Boundary cycles**

| Cycle | Trip time | Margin |
|---|---|---|
| Slowest PASS | 24.9605 ms | 9.5 µs below the 24.97 ms pass limit |
| Fastest FAIL | 24.9715 ms | 1.5 µs above the pass limit |
| Slowest FAIL | 25.5580 ms | 74.4 ms below the 100 ms no-trip limit |
| NO_TRIP | — | No collapse observed within the record |

**Table A.4 — Drift regression (linear fit of trip time vs. cycle index)**

| Scope | n | Slope | r | p |
|---|---|---|---|---|
| 5,317-cycle run only, PASS | 5,215 | -0.023 µs/cycle | -0.009 | 0.53 |
| 5,317-cycle run only, PASS+FAIL | 5,316 | -0.030 µs/cycle | -0.011 | 0.41 |
| Full combined timeline, PASS | 5,883 | -0.012 µs/cycle | -0.005 | 0.69 |
| Full combined timeline, PASS+FAIL | 5,999 | -0.020 µs/cycle | -0.009 | 0.51 |

**Table A.5 — Reconstructed process pauses (483-cycle run)**

| Before cycle | Gap | Process restart confirmed? |
|---|---|---|
| 140 | 19.7 hours | Yes |
| 384 | 51.4 hours | Yes |
| 414 | 10.6 hours | No — same process, idle |

**Table A.6 — Correlations**

| Pair | r | p |
|---|---|---|
| Trip time vs. reference amplitude | 0.44 | ≈0 |
| Trip time vs. noise level | -0.59 | ≈0 |
| Reference amplitude vs. cycle index | 0.13 | ≈0 |

**Table A.7 — Cross-validation summary (Section 6)**

| Method | Mean offset from production algorithm | PASS/FAIL flips after offset correction (of 5,999) |
|---|---|---|
| Threshold-based (Methods A/B) | +2.4 ms | 27 |
| Curve-fit (Method C) | -1.9 ms | 62 |

## Appendix B. Plot Index

All plots referenced in this addendum were generated from the combined campaign dataset and are archived alongside the underlying analysis code and data tables.

**Distribution and verdict plots**
- Trip-time histogram, linear scale, with a smoothed density overlay (Section 5.2)
- Trip-time histogram, zoomed to the 20-30 ms pass/fail boundary region
- Trip-time empirical cumulative distribution, log scale
- Verdict timeline strip, marking every FAIL and the single NO_TRIP across the combined 6,000-cycle sequence (Section 5.1, 5.4)

**Timeline plots**
- Trip time vs. combined cycle index, with a 200-cycle moving average, by verdict (Section 5.3)
- Cycle-to-cycle timing-gap timeline, with the reconstructed retry event marked (Section 4.1, 4.2)
- Trip time and pass rate by hour of day (Section 5.6)

**Correlation plots**
- Trip time vs. waveform reference amplitude, by verdict (Section 5.6)
- Trip time vs. waveform noise level, by verdict (Section 5.6)

**Cross-validation plot**
- Alternative-method trip time vs. production-algorithm trip time, threshold-based and curve-fit methods side by side, against the y = x identity line (Section 6.3)
