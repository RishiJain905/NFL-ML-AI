---
name: digest-checks
description: How the weekly digest is built, checked and debugged (P04+). Use when changing the digest payload, number formatting, the placeholder or a real LLM provider, the prompt files, the automated checks (number provenance, entity binding, banned language, length, hedging), the report card / season scorecard, `nfl digest` or `nfl weekly run`, or when a digest run shows a check failure, a false positive or a warning banner.
---

# Digest: build, checks, debugging

The spec is `documentation/06-weekly-digest.md` (layout, payload, prompt, checks, report card) plus its "As built in P04" section; the scorecard is in `documentation/08`. Code lives in `src/nflengine/digest/`, the weekly runner in `src/nflengine/weekly.py`.

## 1. The pipeline (one code path for live and backtest)
`digest/run.py::run_digest(season, week, mode=...)`:
1. **Context**: `live` reads `runs/<season>/week<NN>/predictions_games.parquet` (from `nfl train game`); `backtest` materializes the week's (and every earlier week's) predictions from the canonical walk-forward backtests into `runs/digest-backtests/<season>/week<NN>/`, stamped with that week's Tuesday 14:00 UTC run time.
2. **Payload** (`build_payload`): report card (`report_card.py`), games / trends / news (`build.py`), under the hood (`under_hood.py`), watch list (`watchlist.py`). Validated by `payload.py` (Pydantic, `extra="forbid"`).
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
| `hedging` | warn | a low-confidence item mentioned without a hedge phrase; a section-wide disclaimer (a hedged sentence naming no player or team) covers that section's items |
| `name_heuristic` | warn | capitalized pairs matching nothing known (possible made-up names) |

## 5. Debugging a failed run
1. Open `runs/.../checks.json` → `final.checks[].issues` (section, token, reason, sentence). `first_attempt` shows what triggered the regeneration.
2. **False positive?** Usually formatting: a display string built outside `format.py`, a unit phrase the parser splits oddly, or a number the template writes that isn't in the payload (e.g. "the last 3 weeks" needs `window` in the payload). Fix the payload or the template, never the check, unless the check is wrong; then add a test in `tests/digest/test_checks.py`.
3. **Unknown entity on a real name?** Register it in the payload (`people`, QB fields) so the fact index knows it.
4. Re-run quickly without W&B: `uv run nfl digest --season 2025 --week 8 --backtest --no-wandb`.
5. **Known limitation: the sentence splitter never splits after "Jr." / "Sr." / initials** (so "Michael Penix Jr. threw" stays whole). When a name with a suffix ends a sentence, the next sentence merges into it ("…Deebo Samuel Sr. The Vikings…"). That only loosens binding for those two sentences and can trip the warn-only name heuristic.
6. **Read the prose for meaning, not just the checks.** The checks prove every number came from the payload and has an owner; they can't prove the sentence means the right thing. In P04 an independent fact-check (an opus-high subagent comparing each GLM digest with its `payload.json`) found 4 material meaning errors in 4 digests with every number correct. Rule that came out of it: **never let the LLM derive an order, direction, venue or tier**; put the code-made text in the payload (`matchup`, `game_highlights[].rank_note`, `model_vs_consensus.text`, change-worded trend deltas, tiered defense ranks), tell the prompt to copy it, and add a `meaning` check where it can be verified mechanically. Re-run the fact-check after prompt or payload changes. Make display strings self-describing: P04's bare `opp_def_rank` "1st" (meaning *weakest* defense) was written by GLM as "the 1st-ranked pass defense" and "toughest coverage". It is now "weakest" / "3rd-weakest" / "4th-strongest". Never put an ordinal, a sign or a unit in the payload without the word that says which way it points.

## 6. The real LLM (`llm/openrouter.py`, D56)
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

## 7. Placeholder writer rules (`llm/placeholder.py`)
- Uses only display strings; every sentence with a number names its owner.
- Fits each section to its budget (`TRIM = 1.0`): required sentences first, then optional ones / extra items while they fit (`_fit`, `_fit_items`), so the total stays within budget.
- Low-confidence items get "(low confidence)" or "a small sample" in the same sentence.

## 8. Report card and scorecard
- Grades the previous week's **saved** `predictions_games.parquet` (`is_primary` rows). Predictions made at or after kickoff are never graded (`not_graded`).
- The watch list follows the same rule: picks only for games not yet started, saved with `created_at` + `kickoff_utc` (`run.stamp_watch` / `save_watch`), graded only when made before kickoff (`report_card.pre_kickoff`). Games that kicked off before a run are in the payload as names only (`started_games`).
- Provider errors carry only the HTTP status and a short code (`llm.base.LLMError`); never put provider message text into an exception, a note or a file.
- **Never re-run a past week to "update" it.** Each weekly run is a point-in-time record; later phases (P05 graph, P06 players) add steps that apply from the next run on, and new sections are tried on `--backtest` weeks. If a week is re-run anyway, `game_runs.keep_started` keeps the saved rows of games that already kicked off, so gradable pre-game picks survive.
- Pick record excludes ties and exact 50% calls; Brier next to Elo's on the same games; the market's Brier goes to W&B only.
- Season to date covers every saved week of the season before the digest week; calibration buckets are on the favorite's probability.
- The scorecard row is for the **graded** week; `checks_passed` comes from that week's own `checks.json`.

## 9. `nfl weekly run`
Steps `ingest → ready → curate → ratings → game → digest`; state in `runs/<season>/week<NN>/weekly_run.json`; resume with `--from-step <name>`. Readiness failures exit 3. New phases add steps to `weekly.STEPS` / `STEP_FUNCS`.

## Improving this skill
Add new false-positive patterns, allow-list phrases, owner rules or debugging steps here in the same commit as the fix, and note it in the `PROGRESS.md` session log. Keep it procedural; the spec stays in documentation/06.
