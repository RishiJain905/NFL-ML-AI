# P10: Season Operations and Offseason

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P07 (ongoing through the season; P08 work may overlap)
- **Unlocks:** the 2027 season
- **Read first:** `documentation/runbook.md` (from P07), [08 → Drift signals](../08-experiment-tracking.md#drift-signals-and-responses), [04 → Training and retraining cadence](../04-track1-models.md#training-and-retraining-cadence), [10 Decisions log](../10-decisions-log.md)

## Goal

Keep the system healthy through the rest of the 2026 season and the playoffs, review the season honestly, and get ready for 2027 (re-tuning, data refresh, Big Data Bowl 2027 check).

## Tasks

### In season (recurring)
- [ ] 🤖 Weekly: confirm the run landed (automatic), review any drift alerts, and add one line to the PROGRESS session log per week (`week NN: published ✓ / issues`).
- [ ] ✋ When a drift alert fires: the agent investigates (data freshness, feature distributions, role changes) and proposes a fix. Rishi decides.
- [ ] 🤖 Handle data-source breakages (an unofficial endpoint changes) using the runbook.

### Playoffs
- [ ] 🤖 Check the playoff handling (`game_type != 'REG'`, neutral-site Super Bowl, bracket-sized slates, no byes logic) before Wild Card weekend.
- [ ] 🤖 Digest variants for playoff weeks (fewer games; trend and under-the-hood sections still apply).

### End-of-season review
- [ ] 🤖 A season report (`documentation/reviews/2026-season-review.md`) built from the W&B scorecards:
  - game model vs Elo vs market
  - calibration
  - the accuracy scoreboard per target
  - watch-list hit rate
  - check failure rate
  - pipeline uptime
  - best and worst calls
- [ ] 🧑 **Rishi reviews** the season report. Decide what to keep, cut or rebuild for 2027.
- [ ] 🤖 Update the decisions log with the lessons learned.

### Offseason prep for 2027
- [ ] 🤖 Full data refresh, including **participation data for 2026** (now filled in; still research-only for live features unless the decisions change).
- [ ] 🧑 **Rishi runs** the pre-season re-tune (ratings half-life, prior factor, sample weights, model hyperparameters) with walk-forward evaluation that now includes 2026.
- [ ] 🤖 Roster churn: make sure offseason trades, free agency, the draft and coaching changes flow into the graph and the preseason priors (QB-change adjustments).
- [ ] 🤖 Check whether **Big Data Bowl 2027** has been announced. If it's relevant, plan a Track 2 extension.
- [ ] 🤖 Dry run of the full pipeline on 2026 Week 1 data, as if live, before 2027 Week 1.
- [ ] ✋ **Checkpoint:** ready for 2027.

## Exit criteria

| Criterion | Verify |
|---|---|
| Every 2026 week (regular season + playoffs) published | PROGRESS weekly lines + reports on D: |
| Season review written and discussed | Review doc + PROGRESS note |
| 2027-ready | The re-tune is recorded; the dry run passes |
