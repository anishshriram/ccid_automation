# CCID Automation — Technical Reference

Deep, code-accurate documentation of how the system works: every module's role, every algorithm, every test's purpose. This is the mechanical "how it works" reference — the narrative/decision history (what bugs were found, what was decided and why) is being written up separately.

**Start here: [System Overview](system-overview.md)** — the big picture, how the subsystems fit together, where safety actually lives, and a few "written but not load-bearing" findings worth knowing about. Everything else is deep-dive detail on one piece.

| # | Document | Covers |
|---|---|---|
| 1 | [Sequencer & State Machine](sequencer-and-state-machine.md) | `ccid/sequencer.py`, `states.py`, `safety.py` |
| 2 | [Trip-Time Analysis Algorithm](trip-time-analysis-algorithm.md) | `ccid/analysis.py`, `forced_diagnostic_analysis.py` |
| 3 | [Hardware Abstraction Layer](hardware-abstraction-layer.md) | `ccid/hal/*` (scope, GPIO/contactors, camera — real + sim) |
| 4 | [Vision & Charging-Gate Classification](vision-and-charging-gate-classification.md) | `ccid/classify.py` |
| 5 | [Persistence & Recovery](persistence-and-recovery.md) | `ccid/recorder.py`, `config.py` |
| 6 | [CLI, Lifecycle & Monitoring](cli-lifecycle-and-monitoring.md) | `ccid/main.py` |
| 7 | [Tools](tools.md) | `tools/*.py` |
| 8 | [Test Suite Guide](test-suite-guide.md) | all of `tests/` |
| — | [System Overview](system-overview.md) | how everything connects |

All 9 documents complete as of 2026-08-12; refreshed 2026-08-17 to cover the auto-retry campaign loop, periodic/reactive equipment refresh, the acquisition-poll timeout-boundary fix, durable controller-exception diagnostics, and the stable udev-based camera device path.

**Also here:**
- [Campaign Results Index](campaign-results-index.md) — the bridge between this technical reference and the actual 6,000-cycle campaign data: which of the 66 pulled run directories are real campaigns versus commissioning debris, honest handling of the two partial/aborted runs, where the data lives, and what analysis has (and hasn't) been done. Start here before handing campaign data to a report-writing or data-analysis session.
- [Offline Campaign Analysis](offline-campaign-analysis.md) — the `analysis/` pipeline that turned the raw 6,000-cycle data into distributions, verdict breakdowns, and plots from the committed V3 numbers, plus a from-scratch, non-authoritative exploratory algorithm (`analysis/deep/`) that cross-checked those verdicts a second way using three independently-built detection methods.
- [Legacy Documentation Audit](legacy-documentation-audit.md) — a discrepancy report checking the seven legacy root-level docs (`CODING_AGENT_HANDOFF.md`, `coding_instructions.txt`, `DEPLOYMENT.txt`, `handoff_latest.md`, `IMPLEMENTATION_QUESTIONS.md`, `IMPLEMENTATION_STATUS.md`, `PI_SETUP_AND_TEST_PLAN.md`) against current reality before they're deleted — what was stale/wrong, what's still open, and what content has no replacement yet.
- [Deployment, Pi Bring-Up, and Operator Runbook](deployment-and-operator-runbook.md) — combines and corrects the three previously separate `DEPLOYMENT.txt`, `PI_SETUP_AND_TEST_PLAN.md`, and operator preflight runbook files into one document: fresh-Pi bring-up, the pre-hardware software validation ladder, and the full per-campaign preflight/run/troubleshooting checklist. Resolves the real inconsistencies between the source files (transient `systemd-run` vs. the never-validated persistent service, a wrong VISA product-ID decimal value, stale exposure/device-index claims) rather than just noting them.
- [Scope Trigger Debug Log](scope-trigger-debug-log.md) — the raw, entry-by-entry SCPI-level record of the oscilloscope no-trigger investigation (15 entries). Kept alongside the narrative version in Build and Commissioning Story §3 below; a resolution note was added at the top since the original log never got to write down that the investigation actually concluded.
- [Build and Commissioning Story](build-and-commissioning-issue-log.md) — a narrative account (not a catalog) of the project's real struggles, consolidated from the three raw issue logs (71/87/154 entries) plus the full technical depth of `scope-trigger-debug-log.md`: the oscilloscope's four-week no-trigger mystery, the vision/charging-gate redesigns, the V1→V2→V3 analysis story, the cycle-38 `ValueError` and the auto-retry/equipment-refresh work that followed it, and what's still open going into the 6,000-cycle campaign.
