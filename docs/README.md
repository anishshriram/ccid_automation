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

All 9 documents complete as of 2026-08-12.

**Also here:**
- [Legacy Documentation Audit](legacy-documentation-audit.md) — a discrepancy report checking the seven legacy root-level docs (`CODING_AGENT_HANDOFF.md`, `coding_instructions.txt`, `DEPLOYMENT.txt`, `handoff_latest.md`, `IMPLEMENTATION_QUESTIONS.md`, `IMPLEMENTATION_STATUS.md`, `PI_SETUP_AND_TEST_PLAN.md`) against current reality before they're deleted — what was stale/wrong, what's still open, and what content has no replacement yet.
- [Build and Commissioning Story](build-and-commissioning-issue-log.md) — a narrative account (not a catalog) of the project's real struggles, consolidated from the three raw issue logs (71/87/154 entries) plus the full technical depth of `SCOPE_TRIGGER_DEBUG_LOG.md`: the oscilloscope's four-week no-trigger mystery, the vision/charging-gate redesigns, the V1→V2→V3 analysis story, and what's still open going into the 6,000-cycle campaign.
