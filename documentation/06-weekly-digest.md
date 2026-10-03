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
  "graph_insights": [
    {"insight_type": "injury_ripple", "section": "matchup_risk", "strength": 0.82,
     "entities": ["…"], "facts": [{"label": "team EPA/play without starter", "display": "-0.08", "n_games": 5}],
     "confidence": "low", "graph_query": "q2_injury_ripple"}
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
| Payload | `payload.py` | Pydantic, `extra="forbid"`; every number a `Num {value, display}`. Extra fields beyond the sketch above: `meta.mode` / `run_time` / `sources` (freshness per source) / `early_season`; `report_card.status` (`scored`, `no_saved_predictions`, `first_week`), `not_graded`, `season_to_date`, `calibration`; `games[].status` (`upcoming` / `started`), `kickoff`, `market_fallback`, QB names; `team_trends[].window` / `net_rating` / `people`; `under_the_hood[].kind` (riser / faller / standout), `unit`, `norm_note`, `confidence`; `players_to_watch[].usage_*`, `opp_def_rank` (`source: heuristic` until P06) |
| Formatting | `format.py` | The only place display strings are made, and the single number parser the checks use (`number_atoms`) |
| Fact index | `facts.py` | Entity → display strings / number atoms (owners in the `digest-checks` skill); season and week numbers are global |
| LLM | `llm/` | `LLMClient` protocol, registry keyed by `llm.provider`: `openrouter` (default, D56) and `PlaceholderLLM` (templates fitted to the word budgets; also the fallback when the provider fails). `anthropic` / `openai_compatible` raise "connect in P09". A test checks nothing outside `digest/llm/` imports a provider |
| Prompt | `prompt/system.md`, `prompt/sections.yaml` | Hash logged to W&B and printed in the footer. Sections tagged `phase: P05` (matchup / risk, non-obvious) stay off until then |
| Checks | `checks.py`, `synthesize.py` | All seven checks, plus a fail-level `meaning` check (home/road order of "A at B", game superlatives vs `game_highlights`, consensus tier wording) and a warn-only name heuristic; regenerate once with the offending tokens; then a ⚠️ banner and `checks_failed` in W&B |
| Meaning in the payload | `build.py` | The LLM never derives an order, direction, venue or tier: `games[].matchup`, `game_highlights` (code-ranked), `model_vs_consensus.text`, change-worded trend deltas, complete stat phrases, tiered defense ranks, time-scoped QB evidence (D53) |
| Report card | `report_card.py` | Grades the previous week's saved `predictions_games.parquet` (`is_primary` rows) and `watchlist.parquet`; predictions made after kickoff are never graded |
| Under the hood | `under_hood.py` | NGS / PFR / FTN for week N−1 (D54) |
| Players to watch | `watchlist.py` | Usage-increase × opponent-weakness heuristic, low confidence (D54) |
| Render | `render.py` | Header, report-card numbers, game table (win % away / home, predicted score, margin, P03 confidence band), footer |
| Commands | `run.py`, `weekly.py` | `nfl digest` (live or `--backtest`), `nfl weekly run` (resumable with `--from-step`) |

**Section budgets in P04.** Five prose sections are active (530 words); the two graph sections join in P05 (700 words in all). A section more than 25% over budget, or a total more than 5% over the active sum, fails; a section more than 25% short only warns (D53).

**Report card for the first live week.** No week-3 predictions were saved (the pipeline went live in week 4), so the week-4 digest says there is nothing to grade; week 5's grades week 4. Predictions are never backfilled (D55).

**Backtests** (`nfl digest --backtest`): as if live on the Tuesday of the week. Game predictions come from the canonical walk-forward backtests (`runs/backtests/game/{market,model_only}`), materialized into `runs/digest-backtests/<season>/week<NN>/` for that week and every earlier week, so the report card reads "saved" files exactly as it does live. Reports go to `reports/backtests/<season>/`; W&B group `digest-dev`.
