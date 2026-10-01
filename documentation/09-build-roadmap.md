# 09: Build Roadmap

**The detailed build plan lives in [`plans/`](plans/README.md)**, with one file per phase and a live tracker ([`plans/PROGRESS.md`](plans/PROGRESS.md)). This page is the one-screen overview.

It replaces the original "Suggested phased build order". **What carries over** is the principle of a thin version working end to end first, then deepening each layer. **What changes:**
- The 2026 season is already underway (Week 4 kicked off 2026-10-01), so the order is set by getting a real digest out quickly.
- The graph comes *after* the first digest.
- Every phase has concrete exit criteria and Rishi-in-the-loop checkpoints.

## Phases

```
P00 Foundations ─► P01 Data ─► P02 Ratings ─► P03 Game model ─► P04 Digest v0 (FIRST LIVE DIGEST)
                                                                     │
          ┌──────────────────────────────────────────────────────────┤
          ▼                                                          ├──► P09 Connect real LLM (any time)
P05 Graph v1 ─► P06 Player model + scoreboard ─► P07 Automation ─► P08 Models v2 + advanced graph
                         │                            │
                         │                            └──► P10 Season ops + offseason
                         ▼
Track 2 (parallel, recommended after P04):  T00 Data+baselines ─► T01 GBT ─► T02 Sequence ─► T03 Interaction ─► T04 Bridge (needs P06)
```

| Phase | Outcome | Rough target |
|---|---|---|
| P00–P04 | First live digest: win %, predicted scores, trends, under the hood, report card | Before the Week 6 or 7 slate |
| P05–P06 | Graph insights + real player projections (offense and defense) + accuracy scoreboard | By around Week 9 |
| P07 | Fully hands-off weekly runs | By around Week 10–11 |
| P08 | Better models, more targets, GDS insights | Mid-to-late season |
| P09 | Real LLM | Whenever Rishi chooses (after P04) |
| P10 | Playoffs, season review, 2027 prep | January onward |
| T00–T04 | Track 2 ladder + bridge to Track 1 | Parallel, at Rishi's pace |

## How work is run

Every phase uses the **🤖 Agent / 🧑 Rishi runs / ✋ Checkpoint** working model. Agents build. Rishi runs the first training runs, tuning and backtests and watches them live in W&B. Phases close only at an approved checkpoint. Full protocol: [`plans/README.md`](plans/README.md).

## Risks

| Risk | Mitigation |
|---|---|
| Leakage in validation | As-of feature builder, walk-forward only, leakage unit tests ([04](04-track1-models.md)) |
| LLM invents or misattributes numbers | Display-string payload, code-rendered tables, number, entity and language checks ([06](06-weekly-digest.md)) |
| Missing the in-season window | P00–P04 deliberately thin; graph and player model come after the first digest |
| nflverse / ESPN / unofficial endpoints delayed or changed | Readiness checks with retries; extra sources fail soft; raw snapshots so data can be re-parsed |
| Home machine off, or the D: drive disconnected at run time | Task Scheduler wake-to-run; data-root check with a clear failure + notification; GitHub Actions fallback |
| Small-sample overfitting | Simple models first; fixed hyperparameters per season; ship only if it beats the baselines |
| Track 2 scope creep | Separate track, off the weekly critical path; connects only through T04 |
| Graph becomes decoration | The "must need multiple hops" rule; at least 3 recurring multi-hop queries in production |
| HDD speed for Neo4j | The graph is disposable; switch to a Docker volume if the bind mount is too slow |
