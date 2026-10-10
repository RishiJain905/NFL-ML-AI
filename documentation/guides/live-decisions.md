# Live decisions guide: the 3rd- and 4th-down bot

**What this is:** a plain-language guide to the live-decision models (LD00): what a "4th-down model" is, the six models behind the bot, how one call is computed (worked through on a real 2025 4th down), what the confidence labels mean, and the commands. LD01 adds the live ESPN feed, LD02 the **Game day** tab in the control room, LD03 the weekly decision review; each extends this guide.

**Related docs:** the spec is the [live-decisions README](../live-decisions/README.md) and the [LD00 phase file](../live-decisions/LD00-decision-models.md). The results are in the [model card](../model_cards/live-decision-models.md). Decisions: D107 (the no-touch rule), D109 (the bot's rules), D113 (as built), D114 (the ship decision) in the [decisions log](../10-decisions-log.md). The W&B side is in the [W&B guide §6.7](weights-and-biases.md). Agents use the `live-decisions` skill.

---

## 1. What a 4th-down model is, in plain words

On 4th down a team has three choices: **go for it**, **kick a field goal** or **punt**. A 4th-down model doesn't guess what the coach *will* do; it asks *which choice gives the team the best chance to win*, and answers in **win probability** (WP): the chance the team with the ball wins the game from here.

It works backwards from what can happen after each choice:

- **Go for it:** sometimes they convert (a first down where the play ends, or a touchdown), sometimes they don't (the other team takes over right there).
- **Field goal:** it's good (+3, then a kickoff), or it misses (the other team takes over at the spot of the kick).
- **Punt:** the other team takes over wherever the punt and the return leave them.

Each of those futures is a game situation with its own win probability. Weight each by how likely it is, add them up, and you get **the win probability after each choice**. The call is the biggest number; the **gap** between the best and the next best says how much the choice matters.

> Example (a real 2025 call, §4): Atlanta up 7 on the Rams with 7:34 left, 4th & 1 at the Rams' 49. The coach punted. The bot: **go 76.4%**, punt 69.9%, a 67-yard field goal 61.8%: go, by 6.5 points, Confident.

Two honest caveats:
- The models describe an **average team** in that spot (with the pre-game point spread as its strength). LD02's "Is this team good at this?" panel adds the team's own record next to the call.
- Many calls are **close**. When the options are within 1 win-probability point, the bot says **Toss-up** instead of pretending to know (Brill, Yurko & Wyner found 44% of 2018–22 4th downs were that close; our backtest: 46% of 2014–2025 4th downs).

## 2. The six models

All six are new (the no-touch rule, D107: the production game, player and team models are never touched). They are trained on every regular-season and playoff play of 2010–2025 and live in `src/nflengine/live/models.py`.

| Model | Answers | How | Compared against |
|---|---|---|---|
| **Win probability** (`wp`) | the chance the team with the ball wins, from any snap | a logistic regression on smooth terms (spread, score, both faded by time; field position late in the game), then LightGBM trees boosted from it; it can only rise with the score and with the pre-game spread | nflfastR's `vegas_wp` |
| **Yards gained** (`gain`) | on a 3rd or 4th down, the chance of every gain from −10 to +65 yards and of a touchdown (77 outcomes) | LightGBM multiclass on down, distance, yard line, spread, total, the offense's implied points, era, roof | the conversion rate by distance and era |
| **Field goal** (`fg`) | the chance the kick is good | a logistic regression: distance (bent at 30, 40, 50 and 60 yards), era, indoors, wind, cold | a logistic on distance only |
| **Punt** (`punt`) | where the other team's first snap comes after a punt from any yard line (or a return touchdown, or a muff the kicking team recovers) | what really happened after 2010–2025 punts from that yard line and its neighbours | the season's real punts |
| **Kickoff** (`kickoff`) | where a team starts after receiving a kickoff after a score | what really happened, per **kickoff era** (touchback at the 20 to 2015, the 25 to 2023, the 30 in 2024, the 35 since 2025) | the season's real kickoffs |
| **Pass** (`pass`) | the chance the offense drops back to pass | LightGBM on the situation + our own win probability + the spread | nflfastR's `xpass` |

Plus two small tables: the **extra-point rate** (95.9% from the last three seasons) and the **two-point rate** (about 48%).

**Why boost the win-probability model from a logistic regression?** Every snap of a game shares one label (who won), so a tree model fitted from scratch learns each game's noise. On the seasons kept aside for tuning (fit 2010–12, test 2013) the boosted model scored a Brier of 0.1529 vs 0.1586 for trees alone, and its calibration error halved (model card → Tuning).

## 3. How a call is computed

`live/decide.py` turns a game state into a call. A **game state** is from the offense's side: season, score difference, game and half seconds left, down, distance, yards to the goal, both teams' timeouts, home / away / neutral, who gets the second-half kickoff, the pre-game spread and total (from the offense's side), roof and weather.

1. **Every future becomes a first down for someone.** Converting: our first down where the play ends. Stopped: their first down at the spot. A field goal: +3, then their first down after the kickoff (from the kickoff model). A touchdown, ours or a return touchdown against us, gets the try at the era's extra-point rate (95.9%: +7, else +6). A miss: their first down at the spot of the kick (8 yards behind the line) or their 20. A punt: their first down where the punt model says.
2. **Football's rules come first.** Each play takes 6 seconds. If time runs out in the second half, the score decides (a tie is 0.5; a regular-season overtime that ends tied too). A tied **playoff** overtime period goes on into a new 15-minute period. If the first half ends, the second-half kickoff goes to whoever receives it, and both teams get their 3 timeouts back. A team with the lead and a first down that can **kneel out the clock** wins (the clock needed is 6 s + 40 s for each of the other team's missing timeouts, up to three kneels).
3. **The win-probability model scores the rest**, all in one batch (a few hundred states), from the side of the team with the ball.
4. **Weight and add:** go = Σ P(gain) × WP(after); field goal = P(make) × WP(after a make) + P(miss) × WP(after a miss); punt = Σ P(spot) × WP(after).
5. **The call** is the largest; the **gap** is best minus second best.

## 4. A real 2025 4th down, worked through

**2025 week 17, Rams at Falcons.** Atlanta (at home, a 7.5-point underdog before the game) leads 7 points, 7:34 left in the 4th quarter, 4th & 1 at the Rams' 49. The coach punted: 38 yards, fair catch at the Rams' 11. Atlanta won by 3.

The state the bot sees: `season 2025, score_diff +7, game_seconds 454, half_seconds 454, down 4, ydstogo 1, yardline_100 49, timeouts 3 / 3, home +1, spread −7.5, total 48.5, roof dome`. Win probability right now: **74.7%**.

| Choice | What can happen | Win probability after |
|---|---|---|
| **Go for it** | converts 69.5% of the time (a first down about 6.6 yards on, average; a touchdown 0.8%): WP **83.5%**. Stopped 30.5% (0.7 yards lost, average): the Rams' ball near midfield, WP **60.3%** | 0.695 × 83.5% + 0.305 × 60.3% = **76.4%** |
| **Field goal** | a 67-yard try is good 19.5% of the time: +3 then a kickoff, WP 81.0%. A miss gives the Rams the ball at their 43: WP 57.2% | 0.195 × 81.0% + 0.805 × 57.2% = **61.8%** |
| **Punt** | the Rams start at their own 13 on average: inside their 10 30% of the time, at their 10–19 45%, a touchback (their 20) 17%, past their 20 7%; a return touchdown 0.1%, a muff the Falcons keep 0.7% | **69.9%** |

**The call: go, by 6.5 points** (76.4 vs 69.9), **Confident**: the gap is over 5 points, so no bootstrap refit could flip it (§5). The punt keeps a 7-point lead with the Rams 87 yards away; converting keeps the ball and burns clock with a 7-point lead. The cost of the punt by the bot's numbers: 6.5 points of win probability. (The game is in the shipped models' training data, so this shows the arithmetic, not a forecast; the backtest's fold that never saw 2025 said the same: go 76.7%, punt 69.9%, a 6.8-point gap.)

Reproduce it: `uv run nfl live call --state '{"season": 2025, "score_diff": 7, "game_seconds": 454, "half_seconds": 454, "down": 4, "ydstogo": 1, "yardline_100": 49, "home": 1, "spread": -7.5, "total": 48.5, "roof": "dome"}'`.

## 5. The confidence labels

| Label | Meaning |
|---|---|
| **Confident** | the best choice also wins in at least 90% of the bootstrap refits (below), or the gap is at least 5 points |
| **Lean** | it wins in 60–90% of them |
| **Toss-up** | it wins in fewer than 60%, or the gap is under 1 win-probability point |

**The bootstrap, in plain words:** the yards-gained and field-goal models are refit 20 times, each time on a resampled copy of the training data (some plays twice, some not at all). If the call still comes out the same with every refit, the data really support it; if it flips, the evidence is thin.

**The 5-point gate:** a call more than 5 points wide never flipped in any refit on 1,498 real 2025 4th downs (D113), so wide calls skip the bootstrap (it keeps the 3rd-down check under 20 ms). Every `nfl live train` re-checks this on about 400 real 4th downs and refuses to promote if it fails.

**What the labels look like in practice** (every 2025 4th down, 3,995, with the shipped models): **Confident 55%**, **Toss-up 44%**, **Lean 0.3%**; 92% of calls are within 5 points and get the bootstrap; a call takes 4.6 ms (median). "Lean" is rare: the refits move the conversion and field-goal chances by fractions of a point, so a call either clears 1 point comfortably or it doesn't.

## 6. The 3rd-down check

On a 3rd down the bot answers three things:
- **the chance they convert** (from the yards-gained model);
- **the chance they pass** (the pass model);
- **if they're stopped short:** for every gain from −10 yards to one short of the line to gain, the 4th-down call they'd face (4th & 1 … 4th & N from where they'd be), with its gap and label. It's ready before the 4th-down snap, because ESPN's feed can trail the broadcast (README §2).

## 7. Commands

```bash
uv run nfl live backtest                 # leave-one-season-out, 2014-2025 (W&B live-backtest), ~3 min
uv run nfl live backtest --ratings       # research: the yards-gained model with team ratings (not shipped)
uv run nfl live train --promote          # fit 2010-2025, run the checks, log + promote live-decision-models
uv run nfl live call --state '{"season": 2025, "score_diff": 0, "game_seconds": 2400, "half_seconds": 600, "down": 4, "ydstogo": 1, "yardline_100": 40}'
uv run nfl live call --state '...' --no-bootstrap   # skip the confidence label
```

`nfl live call` prints the call as JSON: `wp` (go / fg / punt), `best`, `gap`, `label`, `boot_share`, `convert`, `fg_make`, `fg_distance`, `punt_start` (the receiving team's expected start, yards from its own goal), `wp_now` and `ms`. On a 3rd down: `convert`, `pass_prob`, `wp_now` and `table`. The state's fields: `season`, `score_diff`, `game_seconds`, `half_seconds`, `down`, `ydstogo`, `yardline_100` (required); `off_timeouts`, `def_timeouts` (3), `home` (+1 / 0 / −1), `receive_2h_ko`, `spread` (offense side, + = favored), `total`, `roof`, `ot`, `playoffs`, `wind`, `temp` (optional).

## 8. Files

| Where | What |
|---|---|
| `{NFL_DATA_ROOT}/models/live-decisions/<first>-<last>_<UTC>/` | one fitted bundle: `wp.txt` + `wp_base.json`, `gain.txt`, `pass.txt`, `gain_boot/00.txt`…`19.txt`, `fg.json` (with the 20 refits), `punt.json`, `kickoff.json`, `pat.json`, `meta.json`, `checks.json` |
| `{NFL_DATA_ROOT}/models/live-decisions/production.json` | which bundle is promoted (the live code reads this, not the W&B alias) |
| `{NFL_DATA_ROOT}/runs/backtests/live-decisions/main/` | the backtest: `predictions_<model>.parquet`, `decisions.parquet` (every real 4th down: the coach's call, the bot's, the gap, the cost), `summary.json` |
| W&B artifact `live-decision-models` | the same bundle, with its own `production` alias |

## 9. Changing it safely

- Model settings live in `config/settings.yaml` → `live:` (the only block this track adds); code defaults in `live/models.py` `DEFAULTS`.
- Any model change: re-run `nfl live backtest`, compare with the ship rules (model card), then `nfl live train --promote`.
- **Never touch the production models** (D107): `tests/test_production_untouched.py` fails if a protected file or settings block changes.
- New rules eras (another kickoff change, say): add it to `schema.kickoff_era` and re-train; the kickoff model falls back to the latest era it has until then.

## 10. Limits and what's next

- **The bot is aggressive:** it says go on 51% of 2014–2025 4th downs (coaches 17%): 96% of 4th & 1, 73% of 4th & 4–5, 52% of 4th & 6–7. Valuing the same futures with nflfastR's expected points is even more aggressive, so the win-probability model isn't the cause; the 4th-down conversion chances are (they sit between the 3rd-down rate and the rate of 4th downs teams chose to attempt, the classic selection bias; model card → Is this good?). LD03 tracks the bot's calls and its conversion chances against real attempts each week.
- **Average team:** the models know the spread, not the roster. A great kicker or a bad short-yardage offense is context (LD02), not an input.
- **Two-point tries and timeouts** aren't advised; overtime uses the same win-probability model (the 2025 "both teams possess" rule isn't modelled explicitly; a tied playoff OT period does go on).
- **Onside kicks** are left out of the kickoff model.
- Next: **LD01** feeds real ESPN game states in (`live/state.py`, `nfl live games / call / replay / latency`).
