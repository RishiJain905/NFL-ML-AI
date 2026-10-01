# T04: Track 2 → Track 1 Bridge

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** T03 and P06 (the player model must exist to test the recommendations)
- **Unlocks:** nothing (closes Track 2 for this season)
- **Read first:** [07 → How Track 2 connects to Track 1](../07-track2-big-data-bowl.md#how-track-2-connects-to-track-1), [04 → C. Player model](../04-track1-models.md#c-player-model), [05 → Player node](../05-knowledge-graph.md#nodes)

## Goal

Turn Track 2 into value for Track 1 **without live inference**:
1. Research findings that tell us which weekly NGS measures actually matter → tested in the player model.
2. Historical movement profiles for still-active players → labeled, static graph attributes for insights.

## Tasks

### Research findings
- [ ] 🤖 Using the BDB data and the T01–T03 models, answer:
  - How much does separation at release / at the catch point predict completion and play EPA?
  - Does defender closing speed matter beyond separation?
  - How stable is a player's separation creation from one week to the next (skill or noise)?
- [ ] 🤖 Write `documentation/findings/bdb-findings.md` with charts. It recommends which NGS weekly measures to use or drop as player-model features, and how to weight them.
- [ ] 🧑 **Rishi reviews** the findings.
- [ ] 🤖 Test the recommendations in the player model (P06 harness): with vs without each NGS feature group, walk-forward. Keep only what helps.
- [ ] 🧑 **Rishi runs** the ablation backtests.

### Historical movement profiles
- [ ] 🤖 For players in the BDB data who are still active: compute `bdb_sep_over_expected_{season}` (receivers) and `bdb_closing_speed_over_expected_{season}` (defenders) from model residuals, with sample sizes.
- [ ] 🤖 Write them to `Player` nodes **labeled with the source season**. Add an insight query that can surface them as historical context (never as current-season measurements).
- [ ] 🤖 Payload + checks: these facts carry the label "from {season} tracking data".

### Wrap-up
- [ ] 🤖 Update [07](../07-track2-big-data-bowl.md) and the decisions log with the outcome.
- [ ] 🤖 Re-check whether Big Data Bowl 2027 has been announced.
- [ ] ✋ **Checkpoint:** close Track 2 for the season.

## Exit criteria

| Criterion | Verify |
|---|---|
| Findings doc published | `documentation/findings/bdb-findings.md` |
| Recommendations tested in the player model | Ablation runs in W&B; decision recorded |
| Profiles in the graph, clearly labeled | Cypher spot check; an insight query golden test |
