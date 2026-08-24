# Exploratory Offline Algorithm vs. Committed V3 - Comparison Report

**NON-AUTHORITATIVE.** Everything in this file is exploratory research output from analysis/deep/deep_analysis.py, a from-scratch algorithm that never imports ccid/analysis.py and does not participate in AnalysisVersion. The committed V3 verdicts in each run's cycles.csv remain the official record. Nothing here changes, corrects, or reinterprets those verdicts.

Three independent detectors are computed per waveform: **A** (independent RMS-envelope threshold-crossing, own noise floor and window sizing), **B** (CUSUM sequential change-point on log-power, confirmed against the same physical amplitude floor as A for stability), **C** (sigmoid curve fit to each edge, trip_time = fitted-collapse-center minus fitted-onset-center, with a real statistical standard error from the fit covariance).


## 1. Do A and B actually behave as independent methods here?

**No, not really - 100.0% of cycles.** B's raw CUSUM change-point is confirmed against the same amplitude-threshold floor A uses (added during development because unconfirmed CUSUM was unstable - see deep_analysis.py's docstring on Method B). In practice that confirmation gate makes B converge to A's exact answer on almost every cycle in this dataset. Read this as: CUSUM change-point detection did not surface anything a threshold-crossing detector with the same physical floor didn't already find - a real result, just a weaker one than 'two fully independent methods agree' would be. C (curve-fit) is the genuinely distinct comparison point below.

## 2. Systematic offset from V3 (definitional, not a disagreement)

- **A/B (threshold-crossing)**: mean Δ = +2.407 ms, median Δ = +2.393 ms, std = 1.457 ms, r vs V3 = 0.9363
- **C (curve-fit midpoint)**: mean Δ = -1.897 ms, median Δ = -1.988 ms, std = 2.025 ms, r vs V3 = 0.8855

Both offsets are expected and explainable, not evidence of a problem: A/B read consistently *higher* than V3 because they don't perform V3's raw-sample endpoint refinement (trip-time-analysis-algorithm.md §4.5) that walks the collapse point back to the true raw-sample crossing - A/B report the coarser envelope-threshold crossing directly. C reads *lower* than both because a sigmoid's fitted center is the midpoint of a transition, not either edge of it - a different, equally valid, but not-the-same definition of 'when did it end.' None of this is a disagreement about the underlying event; it's three different conventions for where inside the same transition to place the number. See plots/deep_vs_v3_scatter.png.

**Worth a second look:** that scatter plot isn't a clean scattered line around y=x with a constant offset - it shows a visible banded/stepped structure (flat plateaus connected by steep risers) for both A and C. The most likely explanation is quantization in these exploratory detectors themselves (window size and persistence-run requirements only let the detected edge move in discrete jumps), not a real physical effect - but it's also plausibly connected to the multi-modal structure already flagged in the main REPORT.md §2 (three humps in V3's own trip-time distribution), and that connection hasn't been run down here. Flagging both together as one open question rather than asserting a cause.

## 3. The question that actually matters: any PASS/FAIL disagreement after correcting the systematic offset?

Raw A/B trip times run several ms above V3's, which would put many ordinary PASS cycles over the 24.97ms line if compared naively - that's just the offset in §2, not a real disagreement. Correcting for it (subtracting each method's own median offset from V3, then re-applying the same 24.97ms/100ms limits) asks the right question: given where each method sits *relative to its own typical behavior*, does it ever land on the opposite side of a limit from V3?

- **A/B**: 27 cycle(s) out of 5999 flip PASS/FAIL after offset correction.
```
 global_cycle_index                    source_run  source_cycle_index v3_verdict  v3_trip_time_s  a_trip_time_s
                 79  200_v3_real_20260813T131932Z                  79       FAIL        0.025090       0.027213
                703 5317_v3_real_20260817T143315Z                  20       FAIL        0.025102       0.027352
                985 5317_v3_real_20260817T143315Z                 302       FAIL        0.025119       0.027308
               1156 5317_v3_real_20260817T143315Z                 473       PASS        0.024821       0.027443
               1260 5317_v3_real_20260817T143315Z                 577       PASS        0.024958       0.027394
               1464 5317_v3_real_20260817T143315Z                 781       FAIL        0.024973       0.027276
               2049 5317_v3_real_20260817T143315Z                1366       FAIL        0.025154       0.027354
               2095 5317_v3_real_20260817T143315Z                1412       PASS        0.024771       0.027626
               2451 5317_v3_real_20260817T143315Z                1768       FAIL        0.025139       0.027301
               2573 5317_v3_real_20260817T143315Z                1890       FAIL        0.024994       0.027358
               2580 5317_v3_real_20260817T143315Z                1897       FAIL        0.025002       0.027278
               3050 5317_v3_real_20260817T143315Z                2367       PASS        0.024497       0.027531
               3175 5317_v3_real_20260817T143315Z                2492       PASS        0.024931       0.027526
               3217 5317_v3_real_20260817T143315Z                2534       FAIL        0.024972       0.027131
               3296 5317_v3_real_20260817T143315Z                2613       FAIL        0.025001       0.027305
               3357 5317_v3_real_20260817T143315Z                2674       FAIL        0.025093       0.027359
               3459 5317_v3_real_20260817T143315Z                2776       FAIL        0.025115       0.027363
               3829 5317_v3_real_20260817T143315Z                3146       PASS        0.024961       0.027662
               4170 5317_v3_real_20260817T143315Z                3487       PASS        0.024953       0.027535
               4297 5317_v3_real_20260817T143315Z                3614       PASS        0.024613       0.027467
               4298 5317_v3_real_20260817T143315Z                3615       PASS        0.023527       0.027453
               4613 5317_v3_real_20260817T143315Z                3930       FAIL        0.025041       0.027220
               4629 5317_v3_real_20260817T143315Z                3946       FAIL        0.025056       0.027248
               4767 5317_v3_real_20260817T143315Z                4084       FAIL        0.025090       0.027311
               4873 5317_v3_real_20260817T143315Z                4190       FAIL        0.025035       0.027266
               5167 5317_v3_real_20260817T143315Z                4484       FAIL        0.025086       0.027349
               5334 5317_v3_real_20260817T143315Z                4651       FAIL        0.025029       0.027315
```
- **C**: 62 cycle(s) out of 5999 flip PASS/FAIL after offset correction.
```
 global_cycle_index                    source_run  source_cycle_index v3_verdict  v3_trip_time_s  c_trip_time_s
                 14  200_v3_real_20260813T131932Z                  14       PASS        0.024761       0.023490
                 64  200_v3_real_20260813T131932Z                  64       PASS        0.024885       0.023582
                165  200_v3_real_20260813T131932Z                 165       PASS        0.024866       0.023564
                196  200_v3_real_20260813T131932Z                 196       PASS        0.024766       0.023017
                359 5800_v3_try2_20260813T195018Z                 159       PASS        0.024939       0.023808
                387 5800_v3_try2_20260813T195018Z                 187       PASS        0.024383       0.023248
                512 5800_v3_try2_20260813T195018Z                 312       PASS        0.024635       0.023245
                564 5800_v3_try2_20260813T195018Z                 364       PASS        0.024933       0.023404
                575 5800_v3_try2_20260813T195018Z                 375       PASS        0.024604       0.023328
                578 5800_v3_try2_20260813T195018Z                 378       PASS        0.024877       0.023779
                837 5317_v3_real_20260817T143315Z                 154       PASS        0.024824       0.023079
               1156 5317_v3_real_20260817T143315Z                 473       PASS        0.024821       0.024271
               1260 5317_v3_real_20260817T143315Z                 577       PASS        0.024958       0.024111
               1447 5317_v3_real_20260817T143315Z                 764       PASS        0.024949       0.023968
               1670 5317_v3_real_20260817T143315Z                 987       PASS        0.024828       0.023301
               1715 5317_v3_real_20260817T143315Z                1032       PASS        0.024887       0.023012
               2001 5317_v3_real_20260817T143315Z                1318       PASS        0.024482       0.023294
               2095 5317_v3_real_20260817T143315Z                1412       PASS        0.024771       0.024660
               2150 5317_v3_real_20260817T143315Z                1467       PASS        0.024896       0.023419
               2432 5317_v3_real_20260817T143315Z                1749       PASS        0.024937       0.023443
               2436 5317_v3_real_20260817T143315Z                1753       PASS        0.024827       0.023254
               2459 5317_v3_real_20260817T143315Z                1776       PASS        0.024851       0.023838
               2491 5317_v3_real_20260817T143315Z                1808       PASS        0.024858       0.023669
               2688 5317_v3_real_20260817T143315Z                2005       PASS        0.024650       0.023400
               2814 5317_v3_real_20260817T143315Z                2131       PASS        0.024825       0.023541
               2972 5317_v3_real_20260817T143315Z                2289       PASS        0.024816       0.023303
               3043 5317_v3_real_20260817T143315Z                2360       PASS        0.024848       0.023267
               3050 5317_v3_real_20260817T143315Z                2367       PASS        0.024497       0.024522
               3096 5317_v3_real_20260817T143315Z                2413       PASS        0.024929       0.023870
               3175 5317_v3_real_20260817T143315Z                2492       PASS        0.024931       0.024517
               3317 5317_v3_real_20260817T143315Z                2634       PASS        0.024792       0.023185
               3520 5317_v3_real_20260817T143315Z                2837       PASS        0.024608       0.023039
               3537 5317_v3_real_20260817T143315Z                2854       PASS        0.024584       0.023170
               3629 5317_v3_real_20260817T143315Z                2946       PASS        0.024885       0.023485
               3665 5317_v3_real_20260817T143315Z                2982       PASS        0.024902       0.023652
               3687 5317_v3_real_20260817T143315Z                3004       PASS        0.024856       0.023067
               3697 5317_v3_real_20260817T143315Z                3014       PASS        0.024633       0.023078
               3739 5317_v3_real_20260817T143315Z                3056       PASS        0.024434       0.023468
               3829 5317_v3_real_20260817T143315Z                3146       PASS        0.024961       0.024730
               3921 5317_v3_real_20260817T143315Z                3238       PASS        0.024956       0.023727
               3956 5317_v3_real_20260817T143315Z                3273       PASS        0.024842       0.023022
               3970 5317_v3_real_20260817T143315Z                3287       PASS        0.024933       0.023778
               4099 5317_v3_real_20260817T143315Z                3416       PASS        0.024913       0.023550
               4148 5317_v3_real_20260817T143315Z                3465       PASS        0.024925       0.024068
               4170 5317_v3_real_20260817T143315Z                3487       PASS        0.024953       0.024547
               4238 5317_v3_real_20260817T143315Z                3555       PASS        0.024792       0.023028
               4279 5317_v3_real_20260817T143315Z                3596       PASS        0.024790       0.023141
               4297 5317_v3_real_20260817T143315Z                3614       PASS        0.024613       0.024355
               4298 5317_v3_real_20260817T143315Z                3615       PASS        0.023527       0.024313
               4542 5317_v3_real_20260817T143315Z                3859       PASS        0.024861       0.023182
               4659 5317_v3_real_20260817T143315Z                3976       PASS        0.024733       0.023752
               4973 5317_v3_real_20260817T143315Z                4290       PASS        0.024632       0.023036
               5002 5317_v3_real_20260817T143315Z                4319       PASS        0.024956       0.023569
               5029 5317_v3_real_20260817T143315Z                4346       PASS        0.024861       0.023019
               5254 5317_v3_real_20260817T143315Z                4571       PASS        0.024889       0.023410
               5318 5317_v3_real_20260817T143315Z                4635       PASS        0.024920       0.023701
               5527 5317_v3_real_20260817T143315Z                4844       PASS        0.024849       0.023243
               5850 5317_v3_real_20260817T143315Z                5167       PASS        0.024755       0.023077
               5915 5317_v3_real_20260817T143315Z                5232       PASS        0.024879       0.023867
               5948 5317_v3_real_20260817T143315Z                5265       PASS        0.024650       0.023072
               5952 5317_v3_real_20260817T143315Z                5269       PASS        0.024746       0.023000
               5968 5317_v3_real_20260817T143315Z                5285       PASS        0.024734       0.023086
```

Context for reading those counts: the method's own std-dev around its offset (§2 - 1.5ms for A/B, 2.0ms for C) is not much smaller than the width it needs to stay inside to avoid the 24.97ms line - every flipped cycle listed here has a V3 trip_time_s within 1.45ms of that line (visible in the tables). Read this as 'a method with a few ms of spread will inevitably cross a very tight boundary for cycles already sitting right on it,' not as '27/62 specific cycles were independently found to be mis-timed.' No cycle far from the boundary flips under either method.

## 4. Largest residual disagreements (beyond the systematic offset)

For each method, residual = (method trip_time - V3 trip_time) - that method's own median offset. A large residual means a cycle where the two disagree by more than their usual pattern - worth a look regardless of which side of a pass/fail line it's on. Top 10 by |residual|, method A:

```
 global_cycle_index                    source_run  source_cycle_index v3_verdict  v3_trip_time_s  a_trip_time_s  c_trip_time_s  residual_ms
               1270 5317_v3_real_20260817T143315Z                 587       PASS        0.007713       0.015641       0.014032       5.5355
               4476 5317_v3_real_20260817T143315Z                3793       PASS        0.007691       0.015604       0.013986       5.5210
               4070 5317_v3_real_20260817T143315Z                3387       PASS        0.007703       0.015616       0.014022       5.5200
               1433 5317_v3_real_20260817T143315Z                 750       PASS        0.007788       0.015695       0.014121       5.5150
               5780 5317_v3_real_20260817T143315Z                5097       PASS        0.007696       0.015600       0.013975       5.5115
               2713 5317_v3_real_20260817T143315Z                2030       PASS        0.007733       0.015618       0.013993       5.4930
               1006 5317_v3_real_20260817T143315Z                 323       PASS        0.007780       0.015662       0.014054       5.4895
               5890 5317_v3_real_20260817T143315Z                5207       PASS        0.007693       0.015574       0.013904       5.4885
               1362 5317_v3_real_20260817T143315Z                 679       PASS        0.007791       0.015660       0.014072       5.4765
               1617 5317_v3_real_20260817T143315Z                 934       PASS        0.007745       0.015610       0.013904       5.4720
```

**A real methodological finding, not noise:** every one of these top-residual cycles sits at essentially the same V3 trip_time_s - the fastest trips in the entire dataset (≈7.7-7.8ms, right at the 7.691ms minimum). That's not a coincidence: Method A's smoothing window is ~8.33ms (half a mains cycle - §onset/collapse detection in deep_analysis.py), so once the true event is comparable in duration to the window itself, the window can no longer cleanly resolve it and the positive bias balloons from its usual ~+2.4ms to ~+5.5ms. This is a genuine limitation of a fixed-window envelope approach at the fast end, not evidence about the DUT - and it's a point in V3's favor: V3's raw-sample endpoint refinement (rather than a fixed smoothing window) is structurally better suited to exactly this fast-trip regime than this exploratory method is.

## 5. The one NO_TRIP cycle

All three methods' collapse search on this cycle:
```
 global_cycle_index  a_trip_time_s  b_trip_time_s  c_trip_time_s
               3798            NaN            NaN            NaN
```
All three independently found no collapse within the record (None/NaN trip_time) - i.e. all three agree with V3's NO_TRIP call on the one cycle where it matters most.

## 6. Borderline cycles - closest PASS/FAIL/NO_TRIP calls to their limits

Closest PASS cycles to the 24.97ms pass limit:
```
 global_cycle_index  v3_trip_time_s  a_trip_time_s  c_trip_time_s
               3829        0.024961       0.027662       0.024730
               1260        0.024958       0.027394       0.024111
               5002        0.024956       0.027257       0.023569
```
Closest FAIL cycles to the 24.97ms pass limit (from above):
```
 global_cycle_index  v3_trip_time_s  a_trip_time_s  c_trip_time_s
               3217        0.024972       0.027131       0.023045
               1464        0.024973       0.027276       0.023671
               1282        0.024977       0.027465       0.024360
```
Closest FAIL cycles to the 100ms no-trip limit:
```
 global_cycle_index  v3_trip_time_s  a_trip_time_s  c_trip_time_s
                 70        0.025558       0.027747       0.024817
               1456        0.025442       0.027590       0.024620
               4756        0.025442       0.027665       0.024741
```

## 7. Uncertainty (Method C fit standard error)

Method C's fitted trip_time_s standard error across 5999 cycles: median 0.0029 ms, 95th percentile 0.0409 ms, max 0.0546 ms.
This is the fit's own statistical uncertainty on *where the sigmoid's center sits*, not an uncertainty on trip_time_s itself under V3's definition - the two detectors are measuring different things (§2), so this shouldn't be read as 'V3's numbers are uncertain by this much.'

## 8. Noise/signal characterization (from the deep algorithm's own estimates)

```
       ref_amplitude_v  pretrigger_noise_rms_v       snr_db  quantization_step_v
count      6000.000000             6000.000000  6000.000000           6000.00000
mean        117.010065                1.023319    42.926751              2.01005
std           4.109834                0.076855     1.327915              0.00000
min         101.560049                0.840658    40.000452              2.01005
25%         115.248204                0.979804    41.582828              2.01005
50%         118.006293                1.009710    43.175146              2.01005
75%         119.943452                1.047362    44.178239              2.01005
max         123.076987                1.445263    45.412658              2.01005
```
