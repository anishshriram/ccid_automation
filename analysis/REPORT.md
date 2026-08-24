# Combined 6,000-Cycle Campaign Analysis

Source: 200_v3_real (200 cycles) + 5800_v3_try2 (483 cycles) + 5317_v3_real (5317 cycles), concatenated in chronological order (by actual run-start timestamp) into one 6,000-row dataset. See docs/campaign-results-index.md for why these three runs (and not the other two 5800_* attempts) are the real campaign data.

All numbers below are read from the already-committed V3 `trip_time_s`/`verdict` values in each run's `cycles.csv`. Nothing here recomputes or reinterprets those verdicts, and no campaign-level pass/fail acceptance judgment is made - that is an offline decision, not something inferred here.

**Framing note:** the goal of this test was to establish that the CCID functions correctly over 6,000 cycles *in total*, not that it completed 6,000 consecutive cycles in one unbroken sitting. The data reflects that: it's three separate attempts (200, then 483, then 5317 cycles) run on different days, and the 5317-cycle attempt itself is treated below as one continuous timeline for drift purposes because it *was* collected essentially continuously (§5's process-segment check found no restarts in it). The 483-cycle attempt was not continuous internally either (two multi-hour/day pauses - §5) and should not be read as 483 consecutive cycles any more than the whole 6,000 should be read as 6,000 consecutive ones.


## 1. Verdict Breakdown

- **PASS**: 5883 (98.050%)
- **FAIL**: 116 (1.933%)
- **NO_TRIP**: 1 (0.017%)
- **Total**: 6000

Note: `runstate.json`'s own `fail_count` folds FAIL and NO_TRIP into one number per run; this table keeps them separate as requested.

## 2. Trip-Time Distribution (PASS + FAIL cycles, trip_time_s not null)

n = 5999 (excludes the 1 NO_TRIP cycle, which has no trip_time_s)

```
count    5999.000000
mean        0.016769
std         0.004065
min         0.007691
1%          0.011055
5%          0.011552
25%         0.012885
50%         0.016529
75%         0.019156
95%         0.024251
99%         0.025152
99.9%       0.025396
max         0.025558
```

- Pass limit: 24.970 ms
- No-trip limit: 100.000 ms
- Max trip_time_s among PASS cycles: 24.9605 ms (0.0095 ms of margin below the pass limit)
- Min trip_time_s among FAIL cycles: 24.9715 ms
- Max trip_time_s among FAIL cycles: 25.5580 ms (74.4420 ms of margin below the no-trip limit)

**Worth a second look:** the distribution isn't unimodal - a Gaussian KDE (plots/trip_time_histogram.png) shows three clear humps at roughly 12.0 ms, 17.7 ms, and 23.0 ms (peak spacing ≈5.3-5.6 ms), not just histogram binning noise. That spacing doesn't cleanly match a half (8.33 ms) or quarter (4.17 ms) mains cycle, so it isn't obviously explained by simple zero-crossing-locked tripping - flagging as a real structural feature of the distribution worth a closer look, not a data artifact and not something interpreted further here.

## 3. FAIL Cycles (24.97-100ms band) - full list

```
 global_cycle_index                    source_run  source_cycle_index                    utc_timestamp  trip_time_s  ref_amplitude_v  noise_sigma_v      t0_source  trip_time_ms
                 70  200_v3_real_20260813T131932Z                  70 2026-08-13T14:31:00.911045+00:00     0.025558        164.82410       0.708490 detected_onset       25.5580
                 79  200_v3_real_20260813T131932Z                  79 2026-08-13T14:40:11.688185+00:00     0.025090        164.82410       0.669453 detected_onset       25.0900
                111  200_v3_real_20260813T131932Z                 111 2026-08-13T15:12:46.971298+00:00     0.025084        164.82410       0.690511 detected_onset       25.0845
                225 5800_v3_try2_20260813T195018Z                  25 2026-08-13T20:15:35.513078+00:00     0.025314        166.83415       0.668510 detected_onset       25.3145
                242 5800_v3_try2_20260813T195018Z                  42 2026-08-13T20:32:56.652974+00:00     0.025409        166.83415       0.667063 detected_onset       25.4095
                302 5800_v3_try2_20260813T195018Z                 102 2026-08-13T21:34:06.257972+00:00     0.025248        168.84420       0.675396 detected_onset       25.2480
                323 5800_v3_try2_20260813T195018Z                 123 2026-08-13T21:55:27.620681+00:00     0.025377        168.84420       0.707756 detected_onset       25.3775
                351 5800_v3_try2_20260813T195018Z                 151 2026-08-14T18:06:07.478043+00:00     0.025133        166.83415       0.700045 detected_onset       25.1335
                366 5800_v3_try2_20260813T195018Z                 166 2026-08-14T18:21:29.277377+00:00     0.025157        166.83415       0.697431 detected_onset       25.1575
                389 5800_v3_try2_20260813T195018Z                 189 2026-08-14T18:44:55.257657+00:00     0.025246        166.83415       0.730244 detected_onset       25.2455
                415 5800_v3_try2_20260813T195018Z                 215 2026-08-14T19:11:42.264782+00:00     0.025205        166.83415       0.721629 detected_onset       25.2055
                476 5800_v3_try2_20260813T195018Z                 276 2026-08-14T20:13:45.803103+00:00     0.025185        168.84420       0.754283 detected_onset       25.1850
                502 5800_v3_try2_20260813T195018Z                 302 2026-08-14T20:40:30.202558+00:00     0.025358        168.84420       0.697512 detected_onset       25.3575
                580 5800_v3_try2_20260813T195018Z                 380 2026-08-14T22:00:07.440154+00:00     0.025145        170.85425       0.715823 detected_onset       25.1445
                617 5800_v3_try2_20260813T195018Z                 417 2026-08-17T12:31:10.867385+00:00     0.025140        166.83415       0.774907 detected_onset       25.1405
                702 5317_v3_real_20260817T143315Z                  19 2026-08-17T14:52:32.898609+00:00     0.025395        166.83415       0.792006 detected_onset       25.3955
                703 5317_v3_real_20260817T143315Z                  20 2026-08-17T14:53:36.590630+00:00     0.025102        166.83415       0.743493 detected_onset       25.1020
                776 5317_v3_real_20260817T143315Z                  93 2026-08-17T16:08:38.282061+00:00     0.025108        166.83415       0.771420 detected_onset       25.1075
                824 5317_v3_real_20260817T143315Z                 141 2026-08-17T16:57:37.533927+00:00     0.025285        166.83415       0.752521 detected_onset       25.2845
                826 5317_v3_real_20260817T143315Z                 143 2026-08-17T16:59:39.847235+00:00     0.025006        166.83415       0.767170 detected_onset       25.0060
                862 5317_v3_real_20260817T143315Z                 179 2026-08-17T17:36:32.441102+00:00     0.025318        166.83415       0.791523 detected_onset       25.3185
                889 5317_v3_real_20260817T143315Z                 206 2026-08-17T18:04:16.777170+00:00     0.025083        166.83415       0.764657 detected_onset       25.0825
                932 5317_v3_real_20260817T143315Z                 249 2026-08-17T18:48:43.201373+00:00     0.025139        166.83415       0.721563 detected_onset       25.1385
                950 5317_v3_real_20260817T143315Z                 267 2026-08-17T19:07:04.712609+00:00     0.025081        166.83415       0.801892 detected_onset       25.0810
                956 5317_v3_real_20260817T143315Z                 273 2026-08-17T19:13:15.315167+00:00     0.025191        166.83415       0.764585 detected_onset       25.1910
                985 5317_v3_real_20260817T143315Z                 302 2026-08-17T19:42:39.114811+00:00     0.025119        166.83415       0.731769 detected_onset       25.1190
               1095 5317_v3_real_20260817T143315Z                 412 2026-08-17T21:35:06.424915+00:00     0.025069        168.84420       0.777040 detected_onset       25.0695
               1111 5317_v3_real_20260817T143315Z                 428 2026-08-17T21:51:25.040835+00:00     0.025133        168.84420       0.722927 detected_onset       25.1330
               1126 5317_v3_real_20260817T143315Z                 443 2026-08-17T22:06:48.624743+00:00     0.025248        168.84420       0.786694 detected_onset       25.2485
               1218 5317_v3_real_20260817T143315Z                 535 2026-08-17T23:40:44.807811+00:00     0.025246        168.84420       0.772079 detected_onset       25.2465
               1273 5317_v3_real_20260817T143315Z                 590 2026-08-18T00:36:32.277660+00:00     0.025309        168.84420       0.774742 detected_onset       25.3090
               1282 5317_v3_real_20260817T143315Z                 599 2026-08-18T00:45:43.020267+00:00     0.024977        168.84420       0.724090 detected_onset       24.9770
               1328 5317_v3_real_20260817T143315Z                 645 2026-08-18T01:32:35.629853+00:00     0.025327        168.84420       0.766958 detected_onset       25.3270
               1384 5317_v3_real_20260817T143315Z                 701 2026-08-18T02:29:58.388662+00:00     0.025036        168.84420       0.723716 detected_onset       25.0365
               1389 5317_v3_real_20260817T143315Z                 706 2026-08-18T02:35:05.697383+00:00     0.025369        168.84420       0.756418 detected_onset       25.3690
               1404 5317_v3_real_20260817T143315Z                 721 2026-08-18T02:50:23.039564+00:00     0.025285        168.84420       0.743608 detected_onset       25.2845
               1456 5317_v3_real_20260817T143315Z                 773 2026-08-18T03:43:29.164365+00:00     0.025442        168.84420       0.747810 detected_onset       25.4420
               1464 5317_v3_real_20260817T143315Z                 781 2026-08-18T03:51:38.215120+00:00     0.024973        170.85425       0.733592 detected_onset       24.9735
               1484 5317_v3_real_20260817T143315Z                 801 2026-08-18T04:11:53.959157+00:00     0.025010        170.85425       0.766613 detected_onset       25.0105
               1618 5317_v3_real_20260817T143315Z                 935 2026-08-18T06:28:41.624697+00:00     0.025135        170.85425       0.742428 detected_onset       25.1345
               1825 5317_v3_real_20260817T143315Z                1142 2026-08-18T09:59:43.223469+00:00     0.025096        166.83415       0.797683 detected_onset       25.0965
               1837 5317_v3_real_20260817T143315Z                1154 2026-08-18T10:12:00.587626+00:00     0.025352        166.83415       0.771640 detected_onset       25.3520
               1886 5317_v3_real_20260817T143315Z                1203 2026-08-18T11:01:52.877724+00:00     0.025320        166.83415       0.770170 detected_onset       25.3200
               2049 5317_v3_real_20260817T143315Z                1366 2026-08-18T13:48:16.675234+00:00     0.025154        164.82410       0.767314 detected_onset       25.1545
               2161 5317_v3_real_20260817T143315Z                1478 2026-08-18T15:42:14.290379+00:00     0.025152        164.82410       0.748798 detected_onset       25.1520
               2362 5317_v3_real_20260817T143315Z                1679 2026-08-18T19:07:21.307906+00:00     0.025286        164.82410       0.757271 detected_onset       25.2860
               2439 5317_v3_real_20260817T143315Z                1756 2026-08-18T20:25:35.979429+00:00     0.025253        164.82410       0.718721 detected_onset       25.2530
               2443 5317_v3_real_20260817T143315Z                1760 2026-08-18T20:29:39.191401+00:00     0.025225        164.82410       0.789163 detected_onset       25.2250
               2451 5317_v3_real_20260817T143315Z                1768 2026-08-18T20:37:47.875778+00:00     0.025139        164.82410       0.748883 detected_onset       25.1390
               2468 5317_v3_real_20260817T143315Z                1785 2026-08-18T20:55:08.179758+00:00     0.025396        164.82410       0.798391 detected_onset       25.3960
               2490 5317_v3_real_20260817T143315Z                1807 2026-08-18T21:17:38.968150+00:00     0.025109        166.83415       0.741104 detected_onset       25.1095
               2505 5317_v3_real_20260817T143315Z                1822 2026-08-18T21:32:54.857664+00:00     0.025297        166.83415       0.791792 detected_onset       25.2970
               2573 5317_v3_real_20260817T143315Z                1890 2026-08-18T22:42:17.628972+00:00     0.024994        168.84420       0.774628 detected_onset       24.9945
               2580 5317_v3_real_20260817T143315Z                1897 2026-08-18T22:49:25.031518+00:00     0.025002        168.84420       0.716101 detected_onset       25.0025
               2595 5317_v3_real_20260817T143315Z                1912 2026-08-18T23:04:47.546281+00:00     0.025083        168.84420       0.758895 detected_onset       25.0835
               2723 5317_v3_real_20260817T143315Z                2040 2026-08-19T01:15:27.406634+00:00     0.025049        168.84420       0.741839 detected_onset       25.0490
               2740 5317_v3_real_20260817T143315Z                2057 2026-08-19T01:32:48.876125+00:00     0.025352        168.84420       0.744617 detected_onset       25.3520
               2833 5317_v3_real_20260817T143315Z                2150 2026-08-19T03:07:33.787676+00:00     0.025118        168.84420       0.783191 detected_onset       25.1180
               2835 5317_v3_real_20260817T143315Z                2152 2026-08-19T03:09:35.708812+00:00     0.025170        168.84420       0.770124 detected_onset       25.1700
               2836 5317_v3_real_20260817T143315Z                2153 2026-08-19T03:10:38.664515+00:00     0.025310        168.84420       0.770378 detected_onset       25.3100
               2864 5317_v3_real_20260817T143315Z                2181 2026-08-19T03:39:21.088229+00:00     0.025194        168.84420       0.747784 detected_onset       25.1940
               2938 5317_v3_real_20260817T143315Z                2255 2026-08-19T04:54:50.535018+00:00     0.025298        170.85425       0.772432 detected_onset       25.2975
               3022 5317_v3_real_20260817T143315Z                2339 2026-08-19T06:19:58.648542+00:00     0.025183        168.84420       0.802065 detected_onset       25.1825
               3217 5317_v3_real_20260817T143315Z                2534 2026-08-19T09:39:10.935732+00:00     0.024972        166.83415       0.779845 detected_onset       24.9715
               3255 5317_v3_real_20260817T143315Z                2572 2026-08-19T10:17:47.411550+00:00     0.025201        166.83415       0.786205 detected_onset       25.2010
               3296 5317_v3_real_20260817T143315Z                2613 2026-08-19T10:59:35.569309+00:00     0.025001        166.83415       0.802290 detected_onset       25.0010
               3357 5317_v3_real_20260817T143315Z                2674 2026-08-19T12:01:17.787492+00:00     0.025093        164.82410       0.739579 detected_onset       25.0930
               3379 5317_v3_real_20260817T143315Z                2696 2026-08-19T12:23:54.082286+00:00     0.025121        164.82410       0.763942 detected_onset       25.1215
               3459 5317_v3_real_20260817T143315Z                2776 2026-08-19T13:45:55.162935+00:00     0.025115        164.82410       0.785313 detected_onset       25.1145
               3503 5317_v3_real_20260817T143315Z                2820 2026-08-19T14:30:52.319126+00:00     0.025011        164.82410       0.774387 detected_onset       25.0115
               3513 5317_v3_real_20260817T143315Z                2830 2026-08-19T14:41:02.594729+00:00     0.025048        164.82410       0.779572 detected_onset       25.0480
               3589 5317_v3_real_20260817T143315Z                2906 2026-08-19T15:58:54.984745+00:00     0.025094        164.82410       0.749738 detected_onset       25.0945
               3664 5317_v3_real_20260817T143315Z                2981 2026-08-19T17:15:39.893809+00:00     0.025085        162.81405       0.756881 detected_onset       25.0850
               3684 5317_v3_real_20260817T143315Z                3001 2026-08-19T17:36:05.048861+00:00     0.025163        164.82410       0.782716 detected_onset       25.1630
               3689 5317_v3_real_20260817T143315Z                3006 2026-08-19T17:41:11.846567+00:00     0.025171        164.82410       0.755057 detected_onset       25.1715
               3698 5317_v3_real_20260817T143315Z                3015 2026-08-19T17:50:22.455357+00:00     0.025225        164.82410       0.762053 detected_onset       25.2250
               3704 5317_v3_real_20260817T143315Z                3021 2026-08-19T17:56:33.345867+00:00     0.025169        164.82410       0.731026 detected_onset       25.1690
               3742 5317_v3_real_20260817T143315Z                3059 2026-08-19T18:35:37.990894+00:00     0.025332        164.82410       0.772198 detected_onset       25.3325
               3943 5317_v3_real_20260817T143315Z                3260 2026-08-19T22:01:58.875025+00:00     0.025233        168.84420       0.785810 detected_onset       25.2325
               4014 5317_v3_real_20260817T143315Z                3331 2026-08-19T23:14:38.888632+00:00     0.024994        168.84420       0.800202 detected_onset       24.9940
               4055 5317_v3_real_20260817T143315Z                3372 2026-08-19T23:56:16.995643+00:00     0.025134        170.85425       0.756948 detected_onset       25.1340
               4057 5317_v3_real_20260817T143315Z                3374 2026-08-19T23:58:20.239347+00:00     0.025243        170.85425       0.770677 detected_onset       25.2425
               4210 5317_v3_real_20260817T143315Z                3527 2026-08-20T02:35:07.719422+00:00     0.025107        170.85425       0.763760 detected_onset       25.1065
               4254 5317_v3_real_20260817T143315Z                3571 2026-08-20T03:19:56.106620+00:00     0.025056        170.85425       0.781214 detected_onset       25.0555
               4271 5317_v3_real_20260817T143315Z                3588 2026-08-20T03:37:15.837036+00:00     0.025319        170.85425       0.779283 detected_onset       25.3185
               4319 5317_v3_real_20260817T143315Z                3636 2026-08-20T04:26:16.680759+00:00     0.025367        170.85425       0.777757 detected_onset       25.3675
               4391 5317_v3_real_20260817T143315Z                3708 2026-08-20T05:39:34.127729+00:00     0.025056        170.85425       0.726083 detected_onset       25.0555
               4551 5317_v3_real_20260817T143315Z                3868 2026-08-20T08:23:23.722690+00:00     0.025049        170.85425       0.721790 detected_onset       25.0495
               4613 5317_v3_real_20260817T143315Z                3930 2026-08-20T09:27:03.319163+00:00     0.025041        168.84420       0.743146 detected_onset       25.0415
               4628 5317_v3_real_20260817T143315Z                3945 2026-08-20T09:42:30.500172+00:00     0.025298        166.83415       0.777945 detected_onset       25.2985
               4629 5317_v3_real_20260817T143315Z                3946 2026-08-20T09:43:30.269591+00:00     0.025056        168.84420       0.710958 detected_onset       25.0560
               4688 5317_v3_real_20260817T143315Z                4005 2026-08-20T10:43:29.098048+00:00     0.025220        166.83415       0.743682 detected_onset       25.2200
               4753 5317_v3_real_20260817T143315Z                4070 2026-08-20T11:49:56.172456+00:00     0.025055        164.82410       0.735254 detected_onset       25.0555
               4756 5317_v3_real_20260817T143315Z                4073 2026-08-20T11:52:59.693310+00:00     0.025442        164.82410       0.753362 detected_onset       25.4420
               4767 5317_v3_real_20260817T143315Z                4084 2026-08-20T12:04:20.219659+00:00     0.025090        164.82410       0.725781 detected_onset       25.0905
               4777 5317_v3_real_20260817T143315Z                4094 2026-08-20T12:14:20.575534+00:00     0.025025        164.82410       0.779718 detected_onset       25.0245
               4797 5317_v3_real_20260817T143315Z                4114 2026-08-20T12:34:43.647068+00:00     0.025312        164.82410       0.794293 detected_onset       25.3125
               4814 5317_v3_real_20260817T143315Z                4131 2026-08-20T12:52:06.415008+00:00     0.025225        164.82410       0.791369 detected_onset       25.2245
               4873 5317_v3_real_20260817T143315Z                4190 2026-08-20T13:52:23.582082+00:00     0.025035        164.82410       0.742232 detected_onset       25.0345
               4877 5317_v3_real_20260817T143315Z                4194 2026-08-20T13:56:28.884085+00:00     0.025424        164.82410       0.750630 detected_onset       25.4235
               4930 5317_v3_real_20260817T143315Z                4247 2026-08-20T14:50:30.523230+00:00     0.025438        164.82410       0.785857 detected_onset       25.4380
               4933 5317_v3_real_20260817T143315Z                4250 2026-08-20T14:53:34.825499+00:00     0.025206        164.82410       0.700079 detected_onset       25.2060
               4941 5317_v3_real_20260817T143315Z                4258 2026-08-20T15:01:46.218318+00:00     0.025340        164.82410       0.734614 detected_onset       25.3395
               5167 5317_v3_real_20260817T143315Z                4484 2026-08-20T18:53:10.965388+00:00     0.025086        166.83415       0.725619 detected_onset       25.0855
               5334 5317_v3_real_20260817T143315Z                4651 2026-08-20T21:44:10.089865+00:00     0.025029        168.84420       0.718452 detected_onset       25.0295
               5339 5317_v3_real_20260817T143315Z                4656 2026-08-20T21:49:17.062513+00:00     0.025131        168.84420       0.779715 detected_onset       25.1310
               5437 5317_v3_real_20260817T143315Z                4754 2026-08-20T23:30:11.149744+00:00     0.025164        168.84420       0.741804 detected_onset       25.1640
               5545 5317_v3_real_20260817T143315Z                4862 2026-08-21T01:20:32.910928+00:00     0.025257        170.85425       0.767536 detected_onset       25.2575
               5605 5317_v3_real_20260817T143315Z                4922 2026-08-21T02:21:47.873300+00:00     0.025085        170.85425       0.746979 detected_onset       25.0850
               5746 5317_v3_real_20260817T143315Z                5063 2026-08-21T04:46:38.058886+00:00     0.025221        170.85425       0.776666 detected_onset       25.2210
               5804 5317_v3_real_20260817T143315Z                5121 2026-08-21T05:45:47.887210+00:00     0.025118        170.85425       0.812606 detected_onset       25.1180
               5836 5317_v3_real_20260817T143315Z                5153 2026-08-21T06:18:28.091974+00:00     0.025128        170.85425       0.772666 detected_onset       25.1285
               5858 5317_v3_real_20260817T143315Z                5175 2026-08-21T06:41:00.728003+00:00     0.025164        170.85425       0.730206 detected_onset       25.1645
               5916 5317_v3_real_20260817T143315Z                5233 2026-08-21T07:40:19.050207+00:00     0.025061        170.85425       0.751416 detected_onset       25.0610
               5933 5317_v3_real_20260817T143315Z                5250 2026-08-21T07:57:42.818482+00:00     0.025188        170.85425       0.798549 detected_onset       25.1880
               5963 5317_v3_real_20260817T143315Z                5280 2026-08-21T08:28:22.814966+00:00     0.025169        170.85425       0.796490 detected_onset       25.1690
```

## NO_TRIP Cycle - full detail

```
 global_cycle_index                    source_run  source_cycle_index                    utc_timestamp                               decision  ref_amplitude_v  noise_sigma_v      t0_source
               3798 5317_v3_real_20260817T143315Z                3115 2026-08-19T19:32:45.154484+00:00 no envelope collapse within the record        166.83415       0.976775 detected_onset
```

## 4. Trip-Time Drift Over the Combined 6,000-Cycle Timeline

`global_cycle_index` 1-6000 is the three runs laid end to end in chronological order (200_v3_real, then 5800_v3_try2, then 5317_v3_real - see combine.py). Index 200 and index 683 are the boundaries between separate attempts run on different days, not a continuous sequence - a level shift or discontinuity right at one of those two points reflects that grouping, not necessarily device behavior (they aren't marked on the plot itself, but are worth keeping in mind when reading it). Regression stats below are also computed within `5317_v3_real` alone (the one run long and continuous enough for a same-sitting drift check to mean anything) in addition to the full combined timeline.

- **5317_v3_real only (PASS)** (n=5215): slope = -0.0225 µs per cycle (over its 5316-cycle-index span ≈ -0.1197 ms total), r = -0.0088, p = 5.27e-01
- **5317_v3_real only (PASS + FAIL)** (n=5316): slope = -0.0303 µs per cycle (over its 5316-cycle-index span ≈ -0.1612 ms total), r = -0.0114, p = 4.06e-01
- **PASS-only** (n=5883): slope = -0.0118 µs per cycle (over its 5999-cycle-index span ≈ -0.0708 ms total), r = -0.0052, p = 6.90e-01
- **PASS + FAIL** (n=5999): slope = -0.0201 µs per cycle (over its 5999-cycle-index span ≈ -0.1204 ms total), r = -0.0086, p = 5.08e-01

Interpretation left to you - r and slope are reported as-is, no drift/no-drift call is made here. See plots/trip_time_vs_cycle_index.png for the visual (raw scatter + 200-cycle rolling mean).

## 5. Retry Reconstruction (inferred from timing, not a literal log)

cycle_index never skips in any of the three runs (confirmed - every index from 1..N is present), which per docs/cli-lifecycle-and-monitoring.md means no CONTROLLER/timeout-class halt (the kind that skips a cycle_index) occurred. The auto-retry loop only engages on a NO_TRIP or a sanity-triggered RIG_FAULT; a plain FAIL never halts `sequencer.run()` and cannot produce a retry. Detection method: within each uninterrupted process segment, a gap between consecutive cycles more than 40s above that segment's own median cadence is flagged as a likely retry cooldown (`cooldown_retry_s` = 60s in every run's config.yaml).

**Likely retries detected: 1**

```
 global_cycle_index                    source_run  source_cycle_index  utc_delta_s  gap_over_baseline_s
               3799 5317_v3_real_20260817T143315Z                3116   111.276379            50.041031
```

Each flagged retry is immediately preceded by the cycle shown below (the one whose halt presumably triggered the retry):
```
 global_cycle_index                    source_run  source_cycle_index verdict
               3798 5317_v3_real_20260817T143315Z                3115 NO_TRIP
```

**Streak proximity to limits (NO_TRIP: 3, everything else: 5):**
Only 1 NO_TRIP occurred in the entire 6,000-cycle dataset (no consecutive NO_TRIPs possible), and 0 sanity-check failures occurred anywhere (see §7), so no RIG_FAULT-class halt happened either. The tighter 3-limit streak and the 5-limit streak both sat at 0/at most 1 the entire time - neither came anywhere close to exhausting.

## Process-level pauses (NOT retries - operator/process restarts)

3 gap(s) exceeded 1 hour(s) - these are process restarts (monotonic clock epoch resets, confirmed via a negative monotonic_start delta at the same row), not retries. cycles.csv/runstate.json do not preserve *why* the process restarted (only the run's final halt_reason survives), so this is reported as an observed timing fact, not a root cause.
```
 global_cycle_index                    source_run  source_cycle_index                    utc_timestamp   utc_delta_s  gap_hours
                340 5800_v3_try2_20260813T195018Z                 140 2026-08-14T17:54:54.890600+00:00  70991.469800  19.719853
                584 5800_v3_try2_20260813T195018Z                 384 2026-08-17T01:25:07.694432+00:00 184915.090175  51.365303
                614 5800_v3_try2_20260813T195018Z                 414 2026-08-17T12:28:09.325651+00:00  38016.842176  10.560234
```

## 6. degraded_flags Breakdown

2 cycle(s) out of 6000 carry a non-empty degraded_flags value.

```
 global_cycle_index                    source_run  source_cycle_index verdict                       degraded_flags
                682 5800_v3_try2_20260813T195018Z                 482    PASS vision_camera_unavailable_fixed_wait
                683 5800_v3_try2_20260813T195018Z                 483    PASS vision_camera_unavailable_fixed_wait
```

## 7. Sanity-Check Failures (logged-only checks; never veto the verdict)

- `sanity_signal_present`: 0 failed / 6000 checked
- `sanity_no_pretrigger_leakage`: 0 failed / 6000 checked
- `sanity_record_spans_no_trip_limit`: 0 failed / 6000 checked
- `sanity_burst_starts_near_t0`: 0 failed / 6000 checked
- `sanity_collapse_is_clean`: 0 failed / 6000 checked
- `sanity_no_trip_persistent`: 0 failed / 6000 checked

**Every one of the 6 sanity checks passed on all 6,000 cycles.** No `sanity_failed` note appears anywhere in the combined dataset either.

## 8. Time-of-Day Pattern

```
            n  pass_rate  mean_trip_ms
utc_hour                              
0         234   0.991453     16.657844
1         264   0.984848     16.367208
2         236   0.978814     16.901451
3         235   0.965957     16.709830
4         236   0.983051     16.808072
5         233   0.991416     16.998223
6         236   0.983051     16.920591
7         236   0.991525     16.625036
8         233   0.991416     16.811717
9         184   0.972826     16.071924
10        177   0.977401     16.660023
11        176   0.982955     17.180233
12        208   0.966346     17.023418
13        253   0.984190     16.972287
14        262   0.969466     16.921223
15        294   0.986395     16.653522
16        277   0.989170     16.678791
17        239   0.974895     16.385835
18        293   0.976109     17.285544
19        303   0.980198     16.859440
20        352   0.977273     16.851514
21        352   0.977273     16.843418
22        251   0.980080     16.565596
23        236   0.974576     16.509108
```
(UTC hour of day; see plots/trip_time_by_hour.png for the visual.)

## 9. Correlation of trip_time_s with Capture Diagnostics

- trip_time_s vs ref_amplitude_v: r = 0.4447, p = 2.27e-289, n = 5999
- trip_time_s vs noise_sigma_v: r = -0.5873, p = 0.00e+00, n = 5999

**Worth a second look, not just a footnote:** `ref_amplitude_v` (the burst-amplitude estimate `analyze_samples` derives from each waveform, presumably tracking mains voltage) has a moderate positive correlation with trip_time_s, and `noise_sigma_v` a moderate negative one. Two caveats before reading anything physical into this: (1) `on_threshold`/`off_threshold` in `ccid/analysis.py` are themselves computed *from* `ref_amplitude_v` (trip-time-analysis-algorithm.md §4.1) - some or all of this correlation could be the threshold-crossing algorithm's own sensitivity to signal amplitude, not a change in the DUT. (2) `ref_amplitude_v` is quantized in exact 0.00000 V steps (the scope's ADC y_increment) and ranges 156.78-174.87 V across the dataset - consistent with ordinary mains-voltage variation (~120Vrms), not an equipment fault, and its correlation with cycle index is weak (r=0.134) so it doesn't look like monotonic instrument drift either. This is exactly the kind of question the independent offline algorithm (§B of the plan / analysis/deep/) is positioned to help answer, since it doesn't derive its thresholds from the same per-waveform amplitude estimate - see that report for whether the correlation survives under a differently-built detector. See plots/trip_time_vs_amplitude.png and plots/trip_time_vs_noise.png.

## t0_source breakdown

```
t0_source
detected_onset    6000
```
