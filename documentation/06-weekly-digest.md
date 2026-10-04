# 06: Weekly Digest and LLM Synthesis

## Role of the LLM

The LLM turns a validated, structured payload into readable prose. **It does no prediction, no arithmetic and no lookups.**

- Every number it writes must already be in the payload as a display string.
- Every claim must be tied to an entity in the payload.
- Automated checks enforce both before anything is published.
- **Tables are rendered by code, not the LLM.** That covers the game outlook table, the report card numbers and the footer, which keeps the LLM's room for error small.
- **The LLM is a placeholder until Rishi connects a real one.** See "LLM provider interface" below.

## LLM provider interface

```python
class LLMClient(Protocol):
    def generate(self, system_prompt: str, payload: dict, output_spec: dict) -> dict[str, str]:
        """Return {section_id: markdown} for the prose sections."""
```

| Provider (`llm.provider`) | What it is | When |
|---|---|---|
| `placeholder` | A deterministic template writer: fills simple sentence templates straight from the payload's display strings | From the start. Lets the full pipeline, checks and report card run end to end with no API key. The checks get tested on predictable text. Also the automatic fallback when a real provider fails (D56) |
| `openrouter` (**default since P04**, D56) | Any model on OpenRouter (`OPENROUTER_API_KEY`); configured as `z-ai/glm-5.3-flash`, reasoning effort `max`, routed to the cheapest of `baseten/fp8`, `relace`, `novita/fp8`, `deepinfra/fp4` | Connected in P04 at Rishi's request |
| `anthropic` | Claude via the Anthropic SDK | When Rishi connects it ([P09](plans/P09-llm-connection.md)) |
| `openai_compatible` | Any OpenAI-compatible endpoint (`LLM_BASE_URL`), which covers open-source models served by Ollama, vLLM or LM Studio | Same |

- Only `settings.yaml` (`llm.*`) and `.env` (`OPENROUTER_API_KEY`, or `LLM_API_KEY` / `LLM_BASE_URL` for the P09 providers) change when switching.
- **All checks below apply to every provider.** They matter most with smaller open-source models.
- Writing the prose is a simple task. The intelligence is in the payload, so a modest model is fine.

## Digest layout

Target: **one screen-scroll page**, ~550–700 words of prose plus one table. Sections in fixed order:

| # | Section | Built by | Content | Prose budget |
|---|---|---|---|---|
| 0 | Header | code | Season, week, run date, data-freshness line | — |
| 1 | **Report card** | code numbers + LLM sentence | Last week: pick record (probability > 50%), Brier score vs Elo baseline, average score-prediction error, the biggest miss, watch-list hits (e.g. "4 of 6 beat their baseline"), and the highlights of the accuracy scoreboard for player stats ([11](11-prediction-targets.md)) | 60 |
| 2 | **Game outlook** | **code table** + LLM blurb | Every game: matchup, win % for each side, **predicted score**, expected margin, confidence flag. Below the table, a 1–3 game blurb on "where the model disagrees most with consensus" (no lines quoted) | 80 |
| 3 | **Team trend shifts** | LLM | 2–4 teams trending up or down, with drivers (which unit moved, and why) | 120 |
| 4 | **Last week under the hood** | LLM | Look back at the previous week using tracking-based stats: separation risers/fallers, YAC above expected, rush yards over expected, time-to-throw changes, pressure spikes | 110 |
| 5 | **Players to watch** | LLM | 4–6 players: projection vs their baseline, the main drivers, matchup context, confidence | 160 |
| 6 | **Matchup / risk to watch** | LLM | One graph-derived angle (injury ripple, style matchup, QB change, or a tough spot for a usually reliable player) | 80 |
| 7 | **Non-obvious insights** | LLM | 1–2 multi-hop graph findings (revenge game, coaching link, common-opponent chain, passing-network shift, crew tendency) | 90 |
| 8 | Footer | code | Model versions, whether market data was used, injury snapshot time, check status, attributions (nflverse, FTN, NGS) | — |

**Saturday injury update** (optional; see [02](02-system-architecture.md)): a short addendum, only if a win probability moved ≥ 5 points or a watch-list player's status changed. Same pipeline, a smaller payload and a 120-word prose budget.

### Followed teams

`config/followed_teams.yaml` lists Rishi's teams. They always appear in the game outlook (every game does). In the other sections they get a ranking boost but don't override stronger signals elsewhere.

## Payload (JSON, validated with Pydantic)

One payload per run, saved to `{NFL_DATA_ROOT}/runs/{season}/week{NN}/payload.json`. Each number appears as a **raw value** and a **display string** (`"display": "64%"`). The LLM is told to use display strings exactly.

```jsonc
{
  "meta": {
    "season": 2026, "week": 5, "run_id": "2026-w05-main",
    "data_through": "2026-W04", "injury_snapshot": "2026-10-06T09:00",
    "market_data_used": true, "model_versions": {"game": "game-v1.3:2026-w05", "player": "player-v1.1:2026-w05"}
  },
  "report_card": {
    "picks_correct": {"value": 11, "display": "11"}, "picks_total": {"value": 15, "display": "15"},
    "brier": {"value": 0.201, "display": "0.20"}, "brier_elo": {"value": 0.226, "display": "0.23"},
    "biggest_miss": {"game": "NYJ @ BUF", "favored": "BUF", "prob": {"value": 0.78, "display": "78%"}, "winner": "NYJ"},
    "watchlist_hits": {"value": 4, "display": "4"}, "watchlist_total": {"value": 6, "display": "6"}
  },
  "games": [
    {"game_id": "2026_05_KC_DEN", "home": "DEN", "away": "KC",
     "home_win_prob": {"value": 0.41, "display": "41%"}, "away_win_prob": {"value": 0.59, "display": "59%"},
     "expected_margin": {"value": -2.6, "display": "KC by 3"},
     "predicted_score": {"home": {"value": 20.8, "display": "21"}, "away": {"value": 23.4, "display": "23"}},
     "confidence": "medium",
     "model_vs_consensus": {"direction": "higher_on_home", "size": "notable"}}
  ],
  "team_trends": [
    {"team": "DET", "direction": "up", "trend_delta": {"value": 0.07, "display": "+0.07 EPA/play"},
     "drivers": [{"unit": "pass defense", "change": {"value": -0.11, "display": "0.11 fewer EPA per dropback allowed"},
                  "evidence": "pressure rate up to 41% over the last 3 weeks"}],
     "predictive_note": "descriptive"}
  ],
  "under_the_hood": [
    {"player": "…", "team": "…", "metric": "avg_separation", "label": "average separation",
     "last_week": {"value": 4.1, "display": "4.1 yards"}, "season_avg": {"value": 2.9, "display": "2.9 yards"},
     "rank_note": "highest among WRs with 5+ targets"}
  ],
  "players_to_watch": [
    {"player": "…", "team": "…", "position": "WR", "opponent": "…", "target": "receiving yards",
     "projection": {"value": 84, "display": "84"}, "baseline": {"value": 61, "display": "61"},
     "interval": {"display": "52–118"}, "confidence": "medium",
     "drivers": ["opponent allows the most receiving yards to WRs after adjusting for schedule",
                 "target share up to 27% since the WR2 went on IR"]}
  ],
  "graph_insights": [  // as built in P05 (D60): code-made text the LLM copies
    {"insight_id": "injury_ripple:00-0035717", "insight_type": "injury_ripple",
     "section": "matchup_risk", "strength": 0.70, "confidence": "medium",
     "graph_query": "q2_injury_ripple", "game_id": "2026_04_DEN_SF", "matchup": "Broncos at 49ers",
     "teams": ["SF", "DEN"],
     "people": [{"player": "Nick Bosa", "player_id": "00-0035717", "team": "SF", "role": "out"}],
     "headline": "Nick Bosa (49ers DE) is listed out for this week (knee).",
     "facts": [{"text": "In 18 games without Nick Bosa since the start of the 2024 season, the 49ers defense allowed +0.11 EPA per play, against -0.03 EPA per play in 16 games he started: worse without him",
                "owners": ["SF", "00-0035717"]}],
     "sample": {"value": 18, "display": "18 games without him"}, "note": ""}
  ],
  "news": [{"source": "ESPN", "headline": "…", "summary": "…", "entities": ["…"]}]
}
```

## Prompt structure

1. **System prompt**
   - Persona: "a smart friend who watches more film than you, texting you what actually matters." Direct, specific, a bit of personality, no hype.
   - Hard rules:
     - Use **only** numbers that appear as `display` strings in the payload, exactly as written.
     - Never compute, round, combine or compare numbers yourself ("twice as many" is not allowed unless it's in the payload).
     - **Never** mention point spreads, totals, moneylines, odds, betting, wagering, "locks", "value", "fades" or covering. Win probabilities are written as percentages.
     - When `model_vs_consensus` is present, it can be described in words only ("the model is notably higher on Denver than consensus").
     - **Never** mention fantasy points, rankings, start/sit or fantasy relevance.
     - When `confidence` is `low` or a sample is small, **say so plainly**.
     - Attribute news ("per ESPN"). Don't present a news claim as model output.
     - Don't add teams, players or facts that aren't in the payload.
   - Format rules: Markdown, the fixed section headers, word budgets per section.
2. **Payload:** the JSON, clearly delimited.
3. **Output spec:** sections 1 and 3–7 as prose (the code fills in table sections 2, 0 and 8 around them), returned as JSON `{section_id: markdown}` so each section can be checked separately.

## Automated checks (`digest/checks.py`)

Run on every generation. Results are logged to W&B.

| Check | How | On failure |
|---|---|---|
| **Number provenance** | Pull every numeric token (digits, %, ranges, ordinals, "11 of 15") out of the prose. Each must match a `display` string in the payload (normalized for spacing, dashes and the % sign) | Regenerate once with a list of the offending tokens |
| **Spelled-out numbers** | Flag number words ("two-thirds", "a dozen", "doubled", "half") unless they appear in the payload | Same as above |
| **Entity binding** | For each sentence with a number, at least one entity named in that sentence must own that number in the payload (fact index: entity → display strings) | Same as above |
| **Unknown entities** | Team and player names in the prose must exist in the payload (checked against nflverse rosters) | Same as above |
| **Banned language** | Regex list: spread, line, odds, moneyline, over/under, total points, bet, wager, lock, cover, fade, value play, fantasy, start/sit, PPR … (with word boundaries; allow "offensive line", "line of scrimmage") | Same as above |
| **Length** | Each section within ±25% of its budget; total within budget | Ask the LLM to trim |
| **Hedging present** | Each `confidence: low` item gets a hedge phrase in its sentence | Warn only |

If the second generation still fails, publish with a visible ⚠️ banner listing the failed checks, and mark the run `checks_failed` in W&B. (Holding the digest would break the weekly habit; a flagged digest is better than none.)

## Report card details

The report card is the digest's accountability loop, and it uses last week's data:

- Uses the **predictions saved in the previous week's run folder**, never ones recomputed later.
- Weekly numbers plus the season to date: pick record, Brier score vs Elo, calibration bucket results (e.g. "games we called 70–80%: 6 of 8"), watch-list hit rate.
- The biggest miss is named honestly.
- Also logged to W&B as the season-long scorecard (see [08](08-experiment-tracking.md)).

## Delivery

- Saved to `{NFL_DATA_ROOT}/reports/{season}/week{NN}-digest.md` on D:. The Saturday update goes to `week{NN}-injury-update.md`.
- Optional email (Markdown rendered to simple HTML) through SMTP settings in `.env`, or a push notification with a link or file path.
- The run folder keeps `payload.json`, `raw_llm_output.json`, `checks.json` and the final `digest.md` for reproducibility.

## Backtesting the digest

Before going live, generate digests for **3–4 past weeks of the 2025 season** (as if each were live, using as-of data) to tune:

- prompt wording and section budgets,
- insight ranking (strength and novelty),
- check strictness (false positives in number matching).

Rishi scores each backtest digest 1–5 on: *would I read this*, *did I learn something*, *did anything feel wrong or invented*. Prompt changes are versioned (prompt hash logged to W&B).

## As built in P04

The digest v0 lives in `src/nflengine/digest/` (the how-to and debugging guide is the `digest-checks` skill). Deviations and choices are in decisions D52–D55.

| Piece | Module | Notes |
|---|---|---|
| Payload | `payload.py` | Pydantic, `extra="forbid"`; every number a `Num {value, display}`. Extra fields beyond the sketch above: `meta.mode` / `run_time` / `sources` (freshness per source) / `early_season`; `report_card.status` (`scored`, `no_saved_predictions`, `first_week`), `not_graded`, `season_to_date`, `calibration`; `games[].status` (`upcoming` / `started`), `kickoff`, `market_fallback`, QB names; `team_trends[].window` / `net_rating` / `people`; `under_the_hood[].kind` (riser / faller / standout), `unit`, `norm_note`, `confidence`; `players_to_watch[].usage_*`, `opp_def_rank` (`source: heuristic`; since P06 only the fallback, see "As built in P06") |
| Formatting | `format.py` | The only place display strings are made, and the single number parser the checks use (`number_atoms`) |
| Fact index | `facts.py` | Entity → display strings / number atoms (owners in the `digest-checks` skill); season and week numbers are global |
| LLM | `llm/` | `LLMClient` protocol, registry keyed by `llm.provider`: `openrouter` (default, D56) and `PlaceholderLLM` (templates fitted to the word budgets; also the fallback when the provider fails). `anthropic` / `openai_compatible` raise "connect in P09". A test checks nothing outside `digest/llm/` imports a provider |
| Prompt | `prompt/system.md`, `prompt/sections.yaml` | Hash logged to W&B and printed in the footer. Sections tagged `phase: P05` (matchup / risk, non-obvious) stay off until then |
| Checks | `checks.py`, `synthesize.py` | All seven checks, plus a fail-level `meaning` check (home/road order of "A at B", game superlatives vs `game_highlights`, consensus tier wording) and a warn-only name heuristic; regenerate once with the offending tokens; then a ⚠️ banner and `checks_failed` in W&B |
| Meaning in the payload | `build.py` | The LLM never derives an order, direction, venue or tier: `games[].matchup`, `game_highlights` (code-ranked), `model_vs_consensus.text`, change-worded trend deltas, complete stat phrases, tiered defense ranks, time-scoped QB evidence (D53) |
| Report card | `report_card.py` | Grades the previous week's saved `predictions_games.parquet` (`is_primary` rows) and `watchlist.parquet`; predictions made after kickoff are never graded |
| Under the hood | `under_hood.py` | NGS / PFR / FTN for week N−1 (D54) |
| Players to watch | `watchlist.py` | Usage-increase × opponent-weakness heuristic, low confidence (D54); since P06 the fallback when a week has no player projections |
| Render | `render.py` | Header, report-card numbers, game table (win % away / home, predicted score, margin, P03 confidence band), footer |
| Commands | `run.py`, `weekly.py` | `nfl digest` (live or `--backtest`), `nfl weekly run` (resumable with `--from-step`) |

**Section budgets in P04.** Five prose sections are active (530 words); the two graph sections join in P05 (700 words in all). A section more than 25% over budget, or a total more than 5% over the active sum, fails; a section more than 25% short only warns (D53).

**Report card for the first live week.** No week-3 predictions were saved (the pipeline went live in week 4), so the week-4 digest says there is nothing to grade; week 5's grades week 4. Predictions are never backfilled (D55).

**Backtests** (`nfl digest --backtest`): as if live on the Tuesday of the week. Game predictions come from the canonical walk-forward backtests (`runs/backtests/game/{market,model_only}`), materialized into `runs/digest-backtests/<season>/week<NN>/` for that week and every earlier week, so the report card reads "saved" files exactly as it does live. Reports go to `reports/backtests/<season>/`; W&B group `digest-dev`.

**How the prompt, the checks and the provider work as built** (20 numbered prompt rules, 11 checks with real examples, the regeneration feedback, latency and cost, and how to switch the model) is in the [LLM writer guide](guides/llm-digest-writer.md). The tables above are the original spec.

## As built in P05

The knowledge-graph sections (6 and 7) are on. Decisions D60 and D61; the graph side is the `neo4j-graph` skill.

- **Where the items come from.** `digest/graph_sections.py` reads the week's `graph_results.json` (written by the weekly `graph` step or `nfl graph build`), or builds it: a backtest always builds the graph as of its Tuesday, a live run builds only if the file is missing (`nfl digest --graph auto|build|read|off`). It re-picks from the stored candidates with the digest's own run time, so a game that has kicked off since the build is never featured; a live digest also skips games kicking off within 90 minutes (writing can take 20+ minutes).
- **Payload.** `graph_insights` holds only the picked items (above), and `meta.graph_status` (`ok` / `unavailable` / `off`) + `graph_note` say whether the graph was there.
- **Sections.** *Matchup / risk to watch* (80 words) gets the top injury ripple or QB change (a trend mismatch if neither exists); *Non-obvious insights* (90 words) the top 1–2 of revenge, common opponents and trend mismatch. A section with no item is a code-written sentence ("No graph angle cleared the bar this week."). With both on, the prose budget is 700 words.
- **Checks.** Each fact's numbers are owned by the entities in its `owners` (a team's margin by that team only, a teammate's usage by him and the starter, not by the team), the headline's and sample's by the game's teams and the item's subject; every person and every team in an item (a common opponent too) is a known entity. Subject players of a low-confidence item need a hedge; a section holding a low-confidence item must contain a hedge phrase ("small sample", "descriptive", ...). Warn level, like the rest of hedging. Prompt rule 20: copy fact texts whole, never move a number, never turn a with / without comparison into a forecast.
- **Placeholder.** Each item is "`matchup`: headline", then its facts verbatim and its note, fitted to the budget (the first item's headline and first fact always; a second item only if its core fits).
- **Length** is measured on the LLM's sections only: code-written sections (a report card with nothing to grade, a graph section with no pick) are left out of the budget and the `length_short` warning.
- **Fail-soft.** If the graph is unavailable, the digest publishes without sections 6–7, with an ℹ️ banner and a "Knowledge graph: unavailable (...)" footer line.
- **Novelty.** After a digest is written, the picks its prose actually names are appended to `published_insights.parquet` (the log never loses a published pick, even on a re-run) (live in `runs/`, backtests in `runs/digest-backtests/`) and, when Neo4j is up, added as `PublishedInsight` nodes; the same story isn't picked again for 3 weeks.

### After P05: more of what the graph finds (D63)

Rishi asked for a more informative digest without bloat: each week the graph found 6–14 strong stories that the two prose sections had no room for, and QB changes crowded out everything else. The LLM's prose budgets are unchanged; the additions are written by code:

| Part | Where | What |
|---|---|---|
| QB column + ⚠ notes | Game outlook table | Each game's expected starters; ⚠ marks a team whose starter isn't its main one this season, with a note under the table ("Jalon Daniels starts in place of Baker Mayfield (out: thumb)"; "could start ... not confirmed yet" on a Tuesday). Payload `qb_changes` |
| Starters out this week | Under the game table | Regular starters who won't play (graph Q0), up to 3 per team, QBs first, one line per game. Payload `starters_out` |
| Players to watch table | Players to watch | All 8 picks (`digest.watchlist_size`): usage now vs before, the opponent's defense tier, the baseline; the prose covers the 4–6 most notable. Since P06 the table shows the model's projection, range, baseline, confidence and main driver ("As built in P06") |
| More from the graph | After the Non-obvious prose | Up to 6 strong stories that didn't fit (strength ≥ 0.7, no QB changes, ≤ 2 per kind and per game), one line each (`GraphInsight.brief`). Payload `graph_more`; logged as published for novelty |
| Latest news | Before the footer | Up to 4 recent non-fantasy ESPN headlines about this week's teams, as published (live runs) |

For the *Matchup / risk to watch* prose slot, QB changes count at 80% of their strength (they're all in the table already), so a strong injury ripple or another story usually takes it. The Non-obvious pool gains three non-QB kinds (head coach vs a former team, offense-vs-defense unit mismatch, special-teams edge; see the knowledge-graph guide). Prompt rule 21 tells the LLM not to restate the code-written lists.

## As built in P06

*Players to watch* comes from the player model ([player projections guide](guides/player-projections.md); decisions D64–D67). The report card grades last week's picks and quotes the accuracy scoreboard. The LLM's word budgets are unchanged.

| Piece | Module | Notes |
|---|---|---|
| Selection | `models/player_watch.py` | The rule below; `tough_spots`; `watchlist_backtest` (hit rate vs base rate) |
| Payload items | `digest/players.py` | Model picks, tough spots, the look-back, scoreboard highlights, scoring saved picks (`WatchScorer`) |
| Numbers | `digest/format.py` | `stat` ("84 receiving yards", "5.3 tackles"), `stat_range` ("52–118 receiving yards"), `vs_baseline` ("23 receiving yards above his baseline"), `driver_effect` ("puts the projection 9 receiving yards above a typical player in his group") |
| Render | `digest/render.py` | The model table, the tough-spots list, the report card's highlights and look-back |
| Backtests | `digest/run.py` | `materialize_backtest_player_predictions` copies a week's walk-forward projections into the digest-backtest folder |

**Where the picks come from.** The week's `predictions_players.parquet` in the run folder. The weekly `player` step writes it; a backtest digest copies the main-target rows of `runs/backtests/player/<target>/`, with the outcomes blanked (nobody knew them on that Tuesday). No file, or a `player_status.json` that says `degraded` (the week's refit failed; written by the `player` step and checked first, so an older projection file from an earlier run of the week is never used), means no projections: the digest falls back to the P04 heuristic and says so above the table ("Heuristic picks (no player-model projections this week)") and in the footer (`heuristic-v0`). The pipeline never breaks on it.

**The rule** (doc 04's definition; `select_watchlist`):
- main stats only (passing, rushing and receiving yards, pressures, tackles);
- a real role: snap share ≥ 50% over his last 2 games, or a role change (regular teammates who missed the team's last game or are ruled out this week, each counted once, leaving ≥ 15% of the targets for a WR/TE or ≥ 25% of the carries for an RB (`open_tgt` / `open_car`));
- his game hasn't kicked off at the digest's run time; not Out or Doubtful;
- ranked by `outperf_z` = (projection − baseline) / the target's typical baseline miss, only above zero, followed teams ×1.15;
- at most 2 per team, 3 per position group and **3 defenders** (EDGE/DL + LB/S together), relaxed only to fill the list; one row per player (a linebacker is in two pools).

The defense cap is a variety rule. Without it, 5–6 of the 8 picks were defenders every week and a receiver made the list about once a month. It was checked on the 2019–2020 backtests (hit rate 70.2% vs 68.8%) and confirmed on 2021–2025 (67.9% vs 66.9%) before it was kept.

**The payload.** A `WatchItem` with `source: "model"` carries `projection` (the median for yards, the average for counts), `interval` (P10–P90), `baseline`, `vs_baseline` (the gap in words), `matchup`, `group`, `confidence`, `drivers` and three optional notes. Every direction is in words, never a bare sign (D53).
- `drivers`: up to 3 SHAP drivers as "phrase (puts the projection N unit above / below a typical player in his group)": SHAP measures from the model's average prediction, never from his own baseline or form (the live week-4 fact-check found "his recent form raises the projection" read as "he's in form" for a pass rusher below his own baseline). A driver is kept only if it is at least 20% of the gap to baseline (a 3-yard driver can't stand for a 29-yard gap), same direction first; a driver that rounds to 0, or a phrase with a banned word, is dropped. With none left, `driver_note` = "no single factor stands out".
- `baseline_note`: where a thin baseline comes from ("his baseline comes from 2 games this season", "... mostly from last season (1 game this season)", "his baseline is the average for players in his role (little history of his own)"); a pressures baseline adds "(pressures data arrives a week late)", because a player who has played 3 games has only 2 counted. Drivers explain the projection against the model's average player, not against his own baseline, so a backup QB can sit 61 yards above a thin baseline while all three drivers lower his projection. The note gives that context without implying a cause.
- `role_note` (a role change, with the vacated share) and `injury_note` ("listed questionable on this week's injury report").

The heuristic fields (`usage_*`, `opp_def_rank`) are optional now and filled only for heuristic picks.

**The table** (code-written, all 8 picks): Player · Game · Projection · Range (80%) · Baseline · Confidence · Main driver / note, with a note line above it saying what the three numbers are. The prose covers 4–6 picks. Prompt rule 22: copy the numbers with their units and driver text whole; a driver compares him with a typical player in his group, never his own form, never the reason for the gap to his baseline; say `driver_note` when there's no driver; never compare or rank; never say a player "will" reach a number; the baseline note is context, not a reason. A low-confidence pick needs a hedge (the `hedging` check).

**Tough spots** (`tough_spots`, 3 a week): regular starters (snap share ≥ 50%, not low confidence) projected at least a quarter of a typical miss below their own baseline, one per team, never a watch-list player. A code-written list under *Matchup / risk to watch* (under the picks when the graph sections are off): "Nate Landman (Rams LB) vs the Saints: projected 7.3 tackles, 2 tackles below his baseline of 9.2 tackles (range 4–11 tackles)". The prose slot stays the graph's.

**Report card additions** (code-written, under the numbers line):
- **Last week's watch list:** each saved pick projected vs actual vs range ("projected 4.6 tackles, range 2–7 tackles → actual 4 tackles: inside the range, above his baseline of 1.8 tackles"), and the numbers line adds "watch list 7 of 8 above baseline, 6 inside their range". Picks are graded from the saved `watchlist.parquet` only if made before kickoff (`created_at` = the digest's run time; the projection's own time is `projected_at`). Model picks are scored with `score_predictions` on the season's player history; a stat not yet published (a PFR pressure count) isn't scored ("no result yet"). The scoreboard catches up by itself: the weekly `player` step re-scores every earlier week on each run (`score_weeks`), so late pressures reach the highlights a week later. Heuristic picks still grade the P04 way.
- **Player projections:** 2–3 highlights from the accuracy scoreboard, always including the weakest target: the best target ("receiving yards projections beat the rolling baseline by 9% so far this season (3 weeks scored)"), the weakest ("pressures: not yet better than the rolling baseline (2% worse)", or "the smallest gain, 3% better"), and the 80% ranges (overall coverage, or a target whose ranges are too narrow or wide, outside 72–88%). A target needs 20 scored projections to be named.
- **No live week scored yet:** a live digest reads only `mode = live` rows (the season file also holds walk-forward re-runs of earlier weeks as `mode = backtest`; they're never quoted as live). With none, the first line says "no live week of player projections has been scored yet", followed by up to 2 lines from the walk-forward backtests, labelled "in walk-forward backtests (2019–2025)". A backtest digest uses its own season's earlier backtest weeks, or earlier seasons ("in earlier backtest seasons (2024)").
- The season scorecard's `player_mae_vs_baseline` is the graded week's n-weighted mean improvement % from the scoreboard (live rows for a live digest).

**Checks.** A pick's or tough spot's numbers belong to the player; a look-back line to its player; the highlights to the model. The placeholder writer covers model picks: "the model projects 85 receiving yards, 25 receiving yards above his baseline of 60 receiving yards (range 60–135 receiving yards, low confidence)".

**Backtest digests** (2025 weeks 8 and 9, placeholder writer): both pass every fail-level check (warnings only: a short game-outlook blurb from the template writer, and the name heuristic tripping on "J.J. McCarthy"), and week 9's report card grades week 8's model picks (6 of 8 above baseline, 6 inside their range). On the 2019–2025 walk-forward backtests the watch list's hit rate is **69.5%** against a base rate of **42.2%** (992 picks): every season between 66% and 73%, every group above 61% (EDGE/DL lowest at 61.1% vs a 37.3% base rate). The picks are 36% high, 50% medium and 14% low confidence. A backtest digest rewrites a week's copied projections (and rebuilds its watch list) when the walk-forward files are newer, so a look-back never grades picks from superseded projections.

### After P06: 10 offense + 10 defense (D70)

Rishi asked for more players: *Players to watch* now holds **10 offense** (QB, RB, WR/TE) and **10 defense** (EDGE/DL, LB/S) picks, and **5 tough spots** (config `digest.watchlist_offense`, `watchlist_defense`, `tough_spots`; `watchlist_size: 8` stays for the heuristic fallback).

| Part | What changed |
|---|---|
| Selection | `select_watchlist(preds, n_offense=10, n_defense=10, ...)` picks each side on its own from the same pool: at most 2 per team per side, soft group caps (QB 3, RB 4, WR/TE 4, EDGE/DL 6, LB/S 6) relaxed only to fill a side, a linebacker in both defensive pools keeps his best row. The 3-defender cap is gone (each side has its own quota). Each pick has `side` and `rank` within its side |
| Payload | `WatchItem.side`; all 20 picks, offense first |
| Tables | Two code-written tables, **Offense** (10) and **Defense** (10), same columns as before |
| Prose | The 2–3 most notable per side, offense first (sections.yaml). The `players_to_watch` budget went from 160 to **200 words**, so the whole digest's prose budget is 740 with the graph sections on (the length check's total cap is the sum of the active budgets, so it follows automatically). The placeholder writes the top 2 of each side always and a 3rd of each while the budget allows |
| Volume floor | A pick needs at least **1.0 projected pressures** or **2.0 tackles** (`player_watch.MIN_VOLUME`, on the projection as shown). Chosen on 2019–2020, confirmed on 2021–2025: defense 64.2% → 65.7%, EDGE/DL 56.0% → 59.8%, no week short of 10. Never relaxed: a short side shows fewer picks ("Defense (9 picks)"). No offensive floor (low-yardage picks hit as often as the rest). Tough spots don't use it |
| Tough spots | 5 lines (was 3) |
| Look-back | One compact table instead of a bullet per pick: Side · Player · Stat · Projected · Range · Actual · In range (✓ / ✗) · Above baseline (✓ / ✗), "did not play" / "no result yet" for unscored picks. The numbers line adds the side split: "watch list 15 of 20 above baseline, 17 inside their range (offense 6 of 10, defense 9 of 10)" (`ReportCard.watchlist_by_side`, owned by the model). `LookbackItem` gained `side`, `projection`, `interval`, `actual`, `status`. Season totals unchanged |

**Backtests (2019–2025, 2,480 picks, with the volume floor):** hit rate **65.3%** against a **42.2%** base rate; offense 65.0% vs 42.0%, defense 65.6% vs 42.3%; every season (60.8–69.4%) and every group (EDGE/DL lowest at 59.8% vs 37.3%) above 50%; no side ever short of 10. Lower than the 8-pick list's 69.5% because picks 9–20 have smaller projected jumps (without the floor: 64.4%). The 2025 week 8 and 9 backtest digests (placeholder writer) pass every fail-level check; week 9's look-back grades all 20 of week 8's picks.
