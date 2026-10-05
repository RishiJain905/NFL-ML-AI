---
name: digest-checks
description: How the weekly digest is built, checked and debugged (P04+). Use when changing the digest payload, number formatting, the placeholder or a real LLM provider, the prompt files, the automated checks (number provenance, entity binding, banned language, length, hedging), the report card / season scorecard, the knowledge-graph sections (matchup / risk, non-obvious insights), `nfl digest` or `nfl weekly run`, or when a digest run shows a check failure, a false positive or a warning banner.
---

# Digest: build, checks, debugging

The spec is `documentation/06-weekly-digest.md` (layout, payload, prompt, checks, report card) plus its "As built in P04" section; the scorecard is in `documentation/08`. Code lives in `src/nflengine/digest/`, the weekly runner in `src/nflengine/weekly.py`.

## 1. The pipeline (one code path for live and backtest)
`digest/run.py::run_digest(season, week, mode=...)`:
1. **Context**: `live` reads `runs/<season>/week<NN>/predictions_games.parquet` (from `nfl train game`); `backtest` materializes the week's (and every earlier week's) predictions from the canonical walk-forward backtests into `runs/digest-backtests/<season>/week<NN>/`, stamped with that week's Tuesday 14:00 UTC run time.
2. **Payload** (`build_payload`): report card (`report_card.py`), games / trends / news (`build.py`), under the hood (`under_hood.py`), watch list (`watchlist.py`), graph insights (`graph_sections.py`, P05: reads or builds the week's `graph_results.json`, re-picks with the run time). Validated by `payload.py` (Pydantic, `extra="forbid"`).
3. **Fact index** (`facts.py`): entity → display strings + number atoms.
4. **LLM** (`llm/`): the registry picks `llm.provider` from `config/settings.yaml`. Only `digest/llm/` may import a provider (a test enforces it).
5. **Checks + regenerate once** (`synthesize.py`, `checks.py`), then a ⚠️ banner if they still fail.
6. **Render** (`render.py`): header, report-card numbers, game table and footer are code; prose goes between them.
7. **Files**: `payload.json`, `raw_llm_output.json`, `checks.json`, `digest.md`, `watchlist.parquet` in the run folder; `season_scorecard.parquet` per season; the report in `reports/<season>/week<NN>-digest.md` (backtests: `reports/backtests/...`).
8. **W&B**: live → group `weekly-pipeline`, job `main`; backtest → `digest-dev` / `backtest`. Summary `check/*`, `words/*`, `rc/*`; tables `season_scorecard`, `check_issues`, `game_outlook`; artifact `digest` / `digest-backtest` aliased `<season>-w<NN>`.

## 2. Numbers: format once, parse the same way
- **Every number shown comes from `digest/format.py`** (`pct`, `margin`, `brier`, `epa`, `driver_change`, `metric(x, unit)`, `amount`, `ordinal`, `bucket` ...). Never build a display string with an f-string elsewhere: the checks only accept numbers found in display strings or code-made text fields (`rank_note`, `evidence`, `norm_note`, `note`).
- `format.number_atoms(text)` is the single parser (prose and payload): sign dropped, thousands commas dropped, `%`/"percent" → `%`, ordinals kept (`28th`), ranges split (`52–118` → `52`, `118`), numbers glued to words ignored (`49ers`, `v0`, `2026_04_KC_LV`). `facts.text_atoms` also adds `60%` for a range written `60–70%`.
- **Meta numbers are global** (season, week, previous week): any sentence may use them without an owner. Anything else needs an owner named in the same sentence.

## 3. Owners (entity binding)
| Payload part | Owner(s) of its numbers |
|---|---|
| report card | "the model" (aliases: model, picks, we, our, report card, brier, calibration, watch list); Elo's Brier also "Elo"; the biggest miss also both teams |
| games | home win % / home score → home team; away → away team; the margin and both scores → both teams |
| team trends | the team (delta, window, net rating, driver changes, evidence) |
| under the hood, players to watch | the player (unique last names become aliases) |
| QB names, evidence names (`people`) | known names without numbers (so "unknown entity" doesn't fire) |
| graph insights (P05) | each fact's numbers go to its `owners` only (a team's margin to that team, never the shared opponent; a teammate's usage to him and the starter, never the team, because naming any player of the item names his team); headline / sample to the game's two teams and the item's subject players; every person and team in an item (common opponents too) is a known entity. In a fact with several "; " clauses, an owner named only in another clause doesn't own this clause's numbers |
Team aliases: code, nickname, full name, location (not "New York" / "Los Angeles"), plus a few extras (`digest/names.py`). Codes and capitalized aliases match case-sensitively. **Short aliases (unique last names) and team names only count outside every known full player name** (payload + rosters), so "Chase Brown" never counts as a mention of Ja'Marr Chase ("Chase"); found with the real LLM in P04. Names use plain word boundaries, so a hyphenated matchup ("Vikings-Steelers") names both teams; the phrase lists (banned words, hedges, number words) keep hyphen-aware boundaries so allow-list phrases like "two-point" stay whole. In the report card the model is the implicit owner unless the sentence names a calibration bucket, whose counts only the bucket owns.

## 4. The checks (`checks.py`)
| check | level | notes |
|---|---|---|
| `complete` | fail | every requested section has prose (a partial LLM reply must not pass) |
| `number_provenance` | fail | every atom in the prose ∈ payload atoms (`.999` counts as `0.999`) |
| `spelled_out_numbers` | fail | number words not in the payload; football phrases allowed ("second half", "double coverage", "two-minute drill"); "one", "first/second/third" are not flagged (too common) |
| `entity_binding` | fail | per sentence: each non-global atom owned by an entity named in it |
| `unknown_entities` | fail | rostered players (nflverse rosters, this + last season) and all 32 teams' aliases must be payload entities |
| `meaning` | fail | "A at B" must be a real game with that home/road order; in the game outlook, "closest" / "most lopsided" / "right behind" must name a team in `game_highlights` ("toss-up" also by the game's band); "much / notably higher … consensus" must match that team's gap tier |
| `banned_language` | fail | betting / fantasy regex with an allow-list ("offensive line", "line of scrimmage", "lined up", "sideline", "goal line" ...) |
| `length` | fail | a section > 125% of budget, or the total > 105% of the sum of active budgets |
| `length_short` | warn | a section < 75% of budget (D53: padding invites invention) |
| `hedging` | warn | a low-confidence item mentioned without a hedge phrase; a section-wide disclaimer (a hedged sentence naming no player or team) covers that section's items; a section holding a low-confidence graph item (`fx.hedge_sections`) must contain a hedge phrase ("small sample", "descriptive", "not predictive" ...) |
| `name_heuristic` | warn | capitalized pairs matching nothing known (possible made-up names) |

## 5. Debugging a failed run
1. Open `runs/.../checks.json` → `final.checks[].issues` (section, token, reason, sentence). `first_attempt` shows what triggered the regeneration.
2. **False positive?** Usually formatting: a display string built outside `format.py`, a unit phrase the parser splits oddly, or a number the template writes that isn't in the payload (e.g. "the last 3 weeks" needs `window` in the payload). Fix the payload or the template, never the check, unless the check is wrong; then add a test in `tests/digest/test_checks.py`.
3. **Unknown entity on a real name?** Register it in the payload (`people`, QB fields) so the fact index knows it.
4. Re-run quickly without W&B: `uv run nfl digest --season 2025 --week 8 --backtest --no-wandb`.
5. **Known limitation: the sentence splitter doesn't split after "Jr." / "Sr." / initials** (so "Michael Penix Jr. threw" stays whole), except before a common sentence opener (`_SUFFIX_SPLIT`: "…Deebo Samuel Sr. The Vikings…" does split). After any other word the two sentences still merge ("Penix Jr. Packers had 14"), which only loosens binding for them and can trip the warn-only name heuristic.
6. **Known limitation: small integers can collide across owners.** Ownership is per entity, not per field, so a team that owns "3" from "the last 3 weeks" also "owns" a 3 written next to it elsewhere ("The Raiders had 3 sacks" passes). Meaning checks and code-made phrases are the defence; per-field binding is a possible later fix (documentation/guides/llm-digest-writer.md §9).
6. **Read the prose for meaning, not just the checks.** The checks prove every number came from the payload and has an owner; they can't prove the sentence means the right thing. In P04 an independent fact-check (an opus-high subagent comparing each GLM digest with its `payload.json`) found 4 material meaning errors in 4 digests with every number correct. Rule that came out of it: **never let the LLM derive an order, direction, venue or tier**; put the code-made text in the payload (`matchup`, `game_highlights[].rank_note`, `model_vs_consensus.text`, change-worded trend deltas, tiered defense ranks), tell the prompt to copy it, and add a `meaning` check where it can be verified mechanically. Re-run the fact-check after prompt or payload changes. Make display strings self-describing: P04's bare `opp_def_rank` "1st" (meaning *weakest* defense) was written by GLM as "the 1st-ranked pass defense" and "toughest coverage". It is now "weakest" / "3rd-weakest" / "4th-strongest". Never put an ordinal, a sign or a unit in the payload without the word that says which way it points.

## 6. The real LLM (`llm/openrouter.py`, D56; reader guide: `documentation/guides/llm-digest-writer.md`)
- Config only: `config/settings.yaml` → `llm.provider: openrouter`, `model`, `reasoning_effort`, `max_tokens`, `timeout_seconds`, `retries`, and `llm.openrouter` (sent as OpenRouter's `provider` routing object: `only` + `sort: price`). The key is `OPENROUTER_API_KEY` (env file); `nfl doctor` reports set / accepted, never the value.
- Check the model's live endpoints (tags, prices, supported parameters) without a key: `curl -s https://openrouter.ai/api/v1/models/<model>/endpoints`.
- Replies are parsed leniently (`parse_sections`): code fences and text around the JSON are ignored; a reply with none of the section ids raises `LLMError`.
- A provider failure falls back to the placeholder for that attempt (`Synthesis.fallback_notes`, footer note, W&B `llm/fallback`). The regeneration sends the first draft plus the check feedback as a follow-up turn.
- Usage, cost, latency and the serving endpoint per call: `raw_llm_output.json` → `calls`, W&B `llm/*`.
- Side-by-side backtests: `uv run nfl digest --season 2025 --week 8 --backtest --llm placeholder` writes `reports/backtests/2025/week08-digest-placeholder.md` next to `...-openrouter.md`.

- **Code-written sections** (`synthesize(..., fixed=...)`, `run.fixed_sections`): a section whose content is fixed text (a report card with nothing to grade) is left out of the LLM's spec and merged back before the checks. Use it whenever the LLM adds nothing but risk.
- **Regeneration feedback** = the failed checks' tokens + every LLM section's word count and limit (`synthesize.length_feedback`), so a fix can't push another section over its limit.
- **A person named in a team's evidence or QB slot also names that team** for binding (`Entity.teams`).
- **Latency:** with `reasoning_effort: max` the live digest's two calls took 21 minutes. P07's schedule has to allow for it, or use a lower effort.

## 6b. Graph sections (P05; the graph side is the `neo4j-graph` skill)
- `nfl digest --graph auto|build|read|off`: a backtest builds the graph as of its Tuesday (~1.5 min); a live run reads the weekly `graph` step's `graph_results.json` and builds only if it's missing. `meta.graph_status` = ok / unavailable / off.
- `matchup_risk` (80) and `non_obvious` (90) are tagged `phase: P05` in `sections.yaml` and switched on through `synthesize(..., enabled_phases=state.enabled_phases)` only when the graph is `ok`. A section with no pick is fixed code text (`graph_sections.NO_ITEM`). Unavailable: no sections, an info banner (`render.graph_banner`) and a footer line.
- The text is code-made (`graph/insights.py`): one claim and its owners per fact. Never add a fact whose numbers the LLM must interpret (a sign, better / worse, who won): write the word in code ("worse without him", "lost to the Vikings by 7").
- After the digest is written, `record_published` appends the picks to the published-insight log (novelty, 3 weeks), plus the "More from the graph" items (code-written, always shown).
- **Code-written graph extras (D63)**: the game table's QBs column + ⚠ notes (`payload.qb_changes`, from Q3 rows), "Starters out this week" (`starters_out`, from Q0 rows, ≤ 3 per team, QBs first), the watch-list table (all `digest.watchlist_size` = 8 picks), "More from the graph" (`graph_more`: `insights.select(...).more`, strength ≥ 0.7, no QB changes, ≤ 2 per kind / game, ≤ 6) and "Latest news" (≤ 4 ESPN headlines). They're rendered by `render.py`, not the LLM; prompt rule 21 says not to restate them; `facts.py` registers everyone they name. QB changes rank at 80% for the Matchup / risk slot (`QB_RISK_WEIGHT`).

## 6c. Player model in the digest (P06; `digest/players.py`, `models/player_watch.py`)
- **Source:** `runs/<S>/week<NN>/predictions_players.parquet` (live: the weekly `player` step; backtest: `run.materialize_backtest_player_predictions` copies the main-target rows of `runs/backtests/player/<target-key>/` with `actual` / `played` blanked, and rebuilds an earlier week's `watchlist.parquet` from its projections when it's missing or heuristic; a copy older than the walk-forward files, e.g. after re-tuned backtests, is rewritten and its list rebuilt). No file, or `player_status.json` = `degraded` (`player_schema.read_status`, checked first in `build_payload`: a failed refit must not reuse an older projection file) -> the P04 heuristic, `meta.model_versions["players to watch"] = "heuristic-v0 (...)"`, and the render / prompt branch on `WatchItem.source`. `role_change` / `vacated_share` come from `open_tgt` / `open_car` (teammates who missed the last game or are ruled out, each once).
- **Selection** (`player_watch.select_watchlist`, D70): main targets, `role_ok` or `role_change`, not kicked off at the run time, not Out / Doubtful, positive `outperf_z` (x1.15 followed), one row per player (LBs sit in two pools); **10 offense + 10 defense**, each side picked on its own: <= 2 per team per side, soft `GROUP_CAPS` (QB 3, RB 4, WR/TE 4, EDGE/DL 6, LB/S 6) relaxed only to fill a side; a minimum projected volume `MIN_VOLUME` (pressures >= 1.0, tackles >= 2.0, on the shown projection; never relaxed, a short side shows fewer; not for tough spots; chosen on 2019-20, confirmed on 2021-25); picks carry `side` and `rank` within the side. `watchlist_backtest(...).short_weeks` counts weeks a side came up short. Sizes come from `digest.watchlist_offense` / `watchlist_defense` / `tough_spots` through `players.WatchSizes.from_config` (the heuristic fallback keeps `watchlist_size` = 8). `tough_spots` (5): `role_ok`, not low confidence, `outperf_z <= -0.25`, one per team, rendered by code under *Matchup / risk* (under the picks when the graph sections are off).
- **Render (D70):** two code-written tables, **Offense** and **Defense** (`render.watch_table`; picks without a side, i.e. older lists, in one table). The prose covers 2–3 per side, offense first (`players_to_watch` budget 200). The look-back is one compact table (`render.lookback_table`: Side, Player, Stat, Projected, Range, Actual, ✓ / ✗) plus the side split on the numbers line (`ReportCard.watchlist_by_side`, owned by the model); never 20 bullets.
- **Projection shown** (`players.center`): P50 for amounts, `mean` (1 decimal) for counts (a small count's median is a coarse whole number; the contract's `outperformance` is mean - baseline for counts). Scoreboard rows are filtered by `mode`: the live season file also holds walk-forward re-runs (`mode == "backtest"`), never quoted as live.
- **Numbers:** `F.stat` / `F.stat_range` / `F.vs_baseline` / `F.driver_effect` (words for every direction: "23 receiving yards above his baseline", "puts the projection 9 receiving yards above a typical player in his group": SHAP measures from the model's average, so a driver is never worded as his own form). Kept only if |contribution| >= 20% of the gap to baseline (`MIN_DRIVER_SHARE`), same direction first; else `driver_note` = "no single factor stands out". Driver phrases with a banned word are dropped (`checks.banned_terms`); a driver that rounds to 0 is dropped. Pressures baseline notes add "(pressures data arrives a week late)" (`LATE_TARGETS`). Highlights say "the rolling baseline" (counts are scored against its median, D65).
- **Report card:** `watch_lookback` (code-written, owned by each player), `scoreboard_highlights` (owned by the model; live rows only for live digests; "no live week ... scored yet" + backtest lines labelled "in walk-forward backtests (2019–2025)"), `watchlist_inside`, and the scorecard's `player_mae_vs_baseline` (n-weighted mean `improvement_pct` of the graded week, P06 targets only since P08). Saved picks are scored by `WatchScorer` (model picks: `score_predictions` on `player_history`, cached per season; heuristic: `score_watchlist`); `played` there means played **and** scored. Live scoreboard rows change after the fact: the weekly step re-scores every earlier week (`score_weeks`; PFR pressures are a week late), so highlights read whatever the file holds at digest time.
- Saved model picks: `created_at` = the digest run (graded only if before kickoff), `projected_at` = the projection's time, `source = "model"`.

## 6d. P08 targets in the digest (D82)
- **Table-only numbers:** `WatchItem.td_chance`, `pass_tds`, `sack_chance` are `Field(exclude=True)`: they never reach `model_dump(_json)`, the saved payload or the LLM input, so no fact / owner binding is needed. `render.watch_table` adds one column per side only when some pick on that side has it (Offense "TD chance": RB / WR/TE chance of a TD, the QB row "1.6 passing TDs"; Defense "Sack chance": the EDGE/DL pool, so an LB shows one and a safety "–"). With P06-only projections the tables and the payload JSON are byte-identical to before (`tests/digest/test_players_p08.py` compares with a literal copy).
- **Probability rows have null `p50`:** any new consumer must use `mean` / `p_ge1` for `kind == "prob"`. P08 targets are never `is_main`, so they never become picks or tough spots.
- **Scoreboard reads** (`players._mode_rows`) drop `position_group == "TEAM"` rows (team stat totals share the season file); highlights skip rows without an MAE (probability targets); `_shared_labels` is computed over the rows being summarised ("rushing yards (QB)" vs "(RB)"); the scorecard's `player_mae_vs_baseline` (`week_improvement`) stays on the **11 P06 targets** so the season series is comparable.
- **Backtest digests** see P08 targets because `run.materialize_backtest_player_predictions` copies main **and** shipped P08 targets (`live_targets()`).
- **Placeholder names:** `_short_names` counts last names over every player in the fact index (game QBs, graph items, starters out), not just the writer's own players: "Jones" with Mac / Zay / Jonathan Jones elsewhere in the payload binds to nobody (found by the P08 graph agent in a 2025 w13 backtest).

## 7. Placeholder writer rules (`llm/placeholder.py`)
- Uses only display strings; every sentence with a number names its owner.
- Fits each section to its budget (`TRIM = 1.0`): required sentences first, then optional ones / extra items while they fit (`_fit`, `_fit_items`), so the total stays within budget.
- Low-confidence items get "(low confidence)" or "a small sample" in the same sentence.
- Graph sections: each item is "`matchup`: headline", then its facts verbatim (capitalized, with a period), then its note; a low-confidence item without a note ends with a small-sample sentence. The first item's headline and first fact are always kept; a second item only when its core fits the budget.

## 8. Report card and scorecard
- Grades the previous week's **saved** `predictions_games.parquet` (`is_primary` rows). Predictions made at or after kickoff are never graded (`not_graded`).
- The watch list follows the same rule: picks only for games not yet started, saved with `created_at` + `kickoff_utc` (`run.stamp_watch` / `save_watch`), graded only when made before kickoff (`report_card.pre_kickoff`). Games that kicked off before a run are in the payload as names only (`started_games`).
- Provider errors carry only the HTTP status and a short code (`llm.base.LLMError`); never put provider message text into an exception, a note or a file.
- **Never re-run a past week to "update" it.** Each weekly run is a point-in-time record; later phases (P05 graph, P06 players) add steps that apply from the next run on, and new sections are tried on `--backtest` weeks. If a week is re-run anyway, `game_runs.keep_started` keeps the saved rows of games that already kicked off, so gradable pre-game picks survive.
- Pick record excludes ties and exact 50% calls; Brier next to Elo's on the same games; the market's Brier goes to W&B only.
- Season to date covers every saved week of the season before the digest week; calibration buckets are on the favorite's probability.
- The scorecard row is for the **graded** week; `checks_passed` comes from that week's own `checks.json`.

## 9. `nfl weekly run`
Steps `ingest → ready → curate → ratings → game → graph → player → digest` (P08 added the team fit and the consistency layer inside `player`, no new step); state in `runs/<season>/week<NN>/weekly_run.json`; resume with `--from-step <name>`. Readiness failures exit 3. A fail-soft step raises `StepDegraded` (recorded `degraded`, the run goes on): the `graph` step does when Neo4j can't be started (since P07 it first runs `docker compose up -d` once). `--llm placeholder` runs the digest step with the template writer. New phases add steps to `weekly.STEPS` / `STEP_FUNCS`.
- **P07 wraps the steps** (`weekly.run_pipeline`, the `weekly-ops` skill): `--auto` picks the week from the calendar and skips a published week; every run writes `run_summary.json` (its `digest` block reads this run's `checks.json` only when the digest step ran in this run), a `pipeline_history.parquet` row and the `weekly-pipeline` / `pipeline` W&B run. A digest that publishes with the ⚠️ banner raises a warn alert "digest checks failed", and the `checks` drift signal alerts when more than 20% of the last 4 digests did.
- **Simulations** (`--as-of <past>`) run the digest as a backtest at that moment (`run_digest(mode="backtest", run_time=as_of, provider=llm or "placeholder", use_wandb=False, graph="off")`, so no graph sections and the live graph is untouched); the backtest novelty log and scorecard get those weeks, as with `nfl digest --backtest`.
- **The Saturday addendum** (`week<NN>-injury-update.md`) is code-written, not LLM-written, so the checks don't run on it (D73).

## Improving this skill
Add new false-positive patterns, allow-list phrases, owner rules or debugging steps here in the same commit as the fix, and note it in the `PROGRESS.md` session log. Keep it procedural; the spec stays in documentation/06.
