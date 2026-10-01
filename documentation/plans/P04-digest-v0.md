# P04: Digest v0 (the First Live Digest)

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P03
- **Unlocks:** P05, P09 (any time), T00 (recommended start)
- **Read first:** [06 Weekly digest](../06-weekly-digest.md) (all of it), [01 → Design principles](../01-project-brief.md#design-principles), [11 → Accuracy scoreboard](../11-prediction-targets.md#the-accuracy-scoreboard), [02 → Weekly schedule](../02-system-architecture.md#weekly-schedule)

## Goal

The thinnest digest that's genuinely useful, produced for the **current 2026 week**. It has these sections: Report card, Game outlook (win % + predicted score), Team trend shifts, Last week under the hood, and a placeholder Players to watch. It's written by the **placeholder LLM**, every check is enforced, and it's saved to D:.

## Scope

- **In:** Pydantic payload, deterministic table rendering, the `LLMClient` interface + `PlaceholderLLM`, prompt files (ready for a real LLM later), all checks, the report card (scoring last week's saved predictions), the "Under the hood" selection logic, a placeholder watch list, `nfl digest` and a manual `nfl weekly run`, digest backtests on 2025 weeks.
- **Out:** graph sections (P05), the real player model (P06), scheduling (P07), a real LLM (P09).

## Tasks

### Payload
- [ ] 🤖 `digest/payload.py`: Pydantic models matching [06 → Payload](../06-weekly-digest.md#payload-json-validated-with-pydantic). Every number is a `{value, display}` pair, formatted in one place (`digest/format.py`).
- [ ] 🤖 Builders: `meta`, `report_card`, `games` (incl. `predicted_score`, `model_vs_consensus` direction and size, confidence), `team_trends`, `under_the_hood`, `players_to_watch` (placeholder), `news` (ESPN, fail-soft).
- [ ] 🤖 **Fact index:** entity → set of display strings, built from the payload. Used by the checks.

### "Last week under the hood"
- [ ] 🤖 Rule-based selection from NGS + PFR advanced + FTN for the previous week:
  - biggest week-over-week changes and season-relative standouts in separation, cushion, YAC above expected, rush yards over expected, time to throw, pressure rate
  - minimum-volume filters
  - ranked by size relative to the player's norm
- [ ] 🤖 3–5 items in the payload, each with `rank_note` text produced by code (e.g. "highest among WRs with 5+ targets").

### Placeholder "Players to watch" (until P06)
- [ ] 🤖 Rule-based: biggest recent usage increases (snap / target / carry share) × opponent weakness at the position, labeled `confidence: low`, `source: heuristic`.

### Report card
- [ ] 🤖 Reads the **previous week's saved** `predictions_games.parquet` (+ watch list) and scores them against actual results: pick record, Brier vs Elo, points MAE, biggest miss, calibration buckets, watch-list hits. Season-to-date totals too.
- [ ] 🤖 Appends to the W&B `season_scorecard` table ([08](../08-experiment-tracking.md#season-scorecard-the-long-term-view)).

### LLM layer
- [ ] 🤖 `digest/llm/base.py`: the `LLMClient` protocol; `digest/llm/placeholder.py`: deterministic sentence templates per section that use only display strings; a provider registry keyed by `llm.provider`.
- [ ] 🤖 `digest/prompt/`: system prompt + section specs from [06 → Prompt structure](../06-weekly-digest.md#prompt-structure), versioned with a hash. The placeholder ignores them, but they're ready for P09.
- [ ] 🤖 `digest/render.py`: header, game outlook table, report card numbers, footer (model versions, market used?, injury snapshot time, check status, data sources).

### Checks
- [ ] 🤖 `digest/checks.py`: every check in [06 → Automated checks](../06-weekly-digest.md#automated-checks-digestcheckspy): number provenance, spelled-out numbers, entity binding, unknown entities, banned language (with the allow-list), length, hedging.
- [ ] 🤖 Regenerate-once flow, the warning banner, `checks.json` in the run folder, results logged to W&B.
- [ ] 🤖 **Check tests:** sample texts with planted errors (made-up number, a number attached to the wrong player, "two-thirds", a betting word, "offensive line" must pass). Every one must be caught or passed correctly.

### Commands
- [ ] 🤖 `nfl digest --season 2026 --week N`: payload → LLM → checks → render → `reports/{season}/week{NN}-digest.md` on D:.
- [ ] 🤖 `nfl weekly run --season 2026 --week N` (manual for now): readiness check → ingest → ratings → game model → digest. Resumable with `--from-step`.

### Backtests and first live digest
- [ ] 🤖 Generate digests for 3–4 weeks of 2025 using as-of data only.
- [ ] 🧑 **Rishi reviews** the backtest digests and scores each 1–5 on: would I read this / did I learn something / did anything feel wrong or invented. Notes go in PROGRESS.
- [ ] 🤖 Adjust the selection logic, budgets and formatting based on the feedback.
- [ ] 🧑 **Rishi runs** `uv run nfl weekly run --season 2026 --week <current>` to produce the **first live digest**.
- [ ] ✋ **Checkpoint:** close P04. Agree on whether to continue the manual weekly runs until P07 (recommended: yes, Rishi runs `nfl weekly run` each Tuesday).

## Rishi-in-the-loop moments: what to look for

- **Digest reviews:** does every number feel grounded? Is the "Under the hood" section interesting or just noise? Is anything you'd care about missing?
- **Checks output (`checks.json`):** check for false positives (real numbers wrongly flagged), which mean the number normalization needs fixing.
- **Report card for the backtest weeks:** do the season-to-date numbers match W&B's `season_scorecard`?

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| A live digest for the current 2026 week exists and passes all checks | `reports/2026/weekNN-digest.md` on D:; `checks.json` all pass |
| Report card scores the previous week from saved predictions | Run the digest for two consecutive weeks; the second week's report card matches the first week's saved predictions |
| Checks catch planted errors | `pytest tests/digest/test_checks.py` passes |
| Rishi would read it | Backtest scores average ≥ 3.5 on "would I read this" |
| Swapping the LLM provider changes only config | Code review: nothing outside `digest/llm/` imports a provider |

## Handoff to P05 / P06 / P09

- The payload schema has slots for `graph_insights` (P05) and real `players_to_watch` (P06).
- `nfl weekly run` has named steps that P05 and P06 add to.
- The `LLMClient` interface is ready for P09.

## Pitfalls / notes

- Number formatting is the main source of false positives (e.g. "64%" vs "64 %", "52–118" vs "52-118"). Format numbers in **one** module, and normalize in the checks.
- Use team display names consistently (e.g. "Kansas City" vs "KC" vs "Chiefs"). The fact index needs an alias list per team.
- Watch the "Under the hood" thresholds: the first weeks of a season have small samples, so require a minimum volume.
