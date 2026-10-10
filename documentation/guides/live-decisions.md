# Live decisions guide: the 3rd- and 4th-down bot

**What this is:** a plain-language guide to the live 3rd- and 4th-down bot: what a "4th-down model" is, the six models behind it, how one call is computed (worked through on a real 2025 4th down), what the confidence labels mean (LD00); then the live ESPN feed: where the data comes from, how a game state is read from it, how fresh it is, replaying a game and the parity check against nflverse (§7–8, LD01); and the commands. LD02 adds the **Game day** tab in the control room, LD03 the weekly decision review; each extends this guide.

**Related docs:** the spec is the [live-decisions README](../live-decisions/README.md) and the phase files ([LD00](../live-decisions/LD00-decision-models.md), [LD01](../live-decisions/LD01-live-feed.md)). The results are in the [model card](../model_cards/live-decision-models.md). Decisions: D107 (the no-touch rule), D109 (the bot's rules), D113 (as built), D114 (the ship decision), D116 (the live feed as built), D117 (the Sol review's fixes) in the [decisions log](../10-decisions-log.md). The W&B side is in the [W&B guide §6.7](weights-and-biases.md). Agents use the `live-decisions` skill.

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

## 7. The live feed (LD01)

### 7.1 Where the data comes from

The bot reads **ESPN's public site API**: the JSON behind espn.com's scoreboards. It's free and needs no key, but it's **unofficial**: Disney's terms bar automated access and ESPN can change or block it without notice. Rishi accepted both for personal, low-volume use (D109). `api.nfl.com` needs a token; the licensed feed (Sportradar) is paid.

Three endpoints, all under `https://site.api.espn.com/apis/site/v2/sports/football/nfl/` (measured 2026-10-10):

| Endpoint | What it holds | Size (JSON / gzipped on the wire) | ESPN caches it for |
|---|---|---|---|
| `scoreboard` (`?dates=2026&seasontype=2&week=5`) | the week's games: status, score, clock, and for a game in progress the `situation` of the **next snap** | 270 KB / ~22 KB | 6 s |
| `scoreboard/{event}` | one game, same fields: **the call behind a check** | 18 KB / ~3.4 KB | 1 s |
| `summary?event={event}` | every play so far: its start and end state, clock, score, text and `wallclock` (the snap time), ESPN's own win probability per play | 600 KB / ~50 KB | 3 s |

Answers took 45–170 ms. The `event` is ESPN's game id (TB at DAL, 2026 week 5 = `401872980`); `nfl live games` lists them, and curated `espn_scoreboard` maps each to our `game_id`.

**The client** (`live/espn.py`) is deliberately polite and narrow:
- it only ever calls `https://site.api.espn.com`; any other host, `http://`, a port or a redirect is refused before anything is sent (there are no credentials to send);
- a 5 s timeout; at most **one request per game every 2 s** (the game and its summary share that clock) and 0.5 s between any two requests; a fixed User-Agent naming the project; gzip;
- after an error it waits 2 s, then 4, 8 … up to 60 s before asking again;
- it **fails soft**: every answer is kept with its fetch time, so when ESPN doesn't answer you get the last good answer, marked stale, with the reason ("ESPN didn't answer (HTTP 503); showing its answer from 14 s ago"). Only with no earlier answer does a check fail outright.

ESPN's play text is data: it's shown as plain text, never as markup (the terminal escapes it; the app will run it through `clean()`).

### 7.2 From ESPN's JSON to a game state

`live/state.py` turns ESPN's `situation` into the `GameState` of §3. The rules, each checked on 2026 data:

| State field | From | The catch |
|---|---|---|
| offense, defense | `situation.possession` (an ESPN team id) | codes go through `normalize_team` (ESPN's WSH → WAS, LAR → LA) |
| yards to the goal | `situation.yardLine` + who has the ball | `yardLine` counts from **ESPN's home team's** goal line: `100 − yardLine` when the offense is ESPN's home team, else `yardLine`. ESPN's own `yardsToEndzone` is never read: it's mirrored on home punts and 0 on timeouts |
| down, distance | `down`, `distance` | goal to go: the distance is the yards to the goal ("3rd & Goal at LA 2"); `down = -1` means **no snap is pending** (a TV timeout before a kickoff or a try) |
| clock | `status.period`, `status.clock` | game seconds = (4 − quarter) × 900 + clock; in overtime both clocks are the OT clock (nflverse's way) |
| score difference | the two competitors' scores | from the offense's side |
| timeouts | `homeTimeouts`, `awayTimeouts` | the summary has none; replays count "Timeout #N by TEAM" rows instead (§8) |
| home (+1 / 0 / −1) | our schedule (`games.neutral_site`) | ESPN's home team at a neutral site can differ from nflverse's: the yard line uses ESPN's, `home` and the spread's sign use ours |
| spread, total | curated `lines`, **pre-game only** | nflverse's schedule line (closing for a played game), else the median across the Odds API's books, else ESPN's DraftKings line, counting only rows saved before kickoff day. Never ESPN's live line: it would leak the score into the "pre-game strength" input |
| roof | `games.roof` | nflverse fills it at retractable-roof stadiums only after the game, so until then the stadium's usual roof (AT&T Stadium: closed in 109 of 112 games), then ESPN's `venue.indoor` |
| wind, temperature | `games.wind` / `temp` for a played game, else the pre-game forecast (`weather_forecasts`) | outdoors only; the field-goal model uses them |
| who gets the 2nd-half kickoff | the summary's first kickoff | the team that didn't receive the opening kickoff (first half only) |

What it says instead of a state: "the game hasn't started", "the game is over", "halftime: the second half starts with a kickoff", or "between plays: no snap pending (a try, a kickoff or a TV timeout)". When the scoreboard shows `down = -1` mid-drive, the next snap is read from the summary's last real play **up to the scoreboard's last play** (the two answers are separate requests; a summary that hasn't caught up yet isn't used).

**A real one** (ESPN's scoreboard archived at 15:41 ET on 2026-10-04): the Eagles lead the Rams 20–10, 7:31 left, **4th & Goal at the Rams' 2**. ESPN's situation: `down 4, distance 2, yardLine 98, possession "21"` (PHI, ESPN's home team), `homeTimeouts 2, awayTimeouts 3`. The parser: yards to goal = 100 − 98 = **2**; PHI at home (+1); the pre-game line had the Rams by 3.5, so the spread from PHI's side is **−3.5**; total 42.5; outdoors, wind 9 mph.

```text
LA 10 at PHI 20 · 7:31 - 4th Quarter
PHI ball, 4th & Goal at LA 2 · timeouts LA 3, PHI 2
Call: Field goal (Toss-up), 0.3 win-probability points over the next best
  Go for it    93.0% win probability for PHI
  Field goal   93.2% win probability for PHI <
  Punt         88.4% win probability for PHI
  convert 46% · field goal from 20 yd 99% · after a punt they start at their own 13 · now 94.9%
```

ESPN's own win probability for PHI at that moment was 94%; ours 94.9%. (Jalen Hurts had just been stopped for no gain on 3rd down.)

### 7.3 How fresh it is, and what "as of" means

Three clocks matter on a game night:

| | Seconds | Source |
|---|---|---|
| From a 3rd-down snap to the next 4th-down snap | median **42** (p10 36, p90 51) | 2026 week 4, 219 pairs (README §2) |
| ESPN's feed behind the snap | seen at 8, 12, 30 and 84 in archived snapshots; **measured live on Sunday 2026-10-11 (README §2 has the numbers)** | `nfl live latency` |
| Your TV behind the field (Super Bowl LX) | antenna ~19, cable 38, Peacock 48, YouTube TV / Hulu 53, NFL+ 62 | [The Desk](https://thedesk.net/2026/02/stats-perform-phenix-latency-super-bowl-lx/) |

On a stream the feed usually beats the picture; on cable or an antenna it sometimes won't. That's why a **3rd-down check already carries the 4th-down call for every distance** they could be left with (§6): check on 3rd down and the answer is ready before the 4th-down snap reaches ESPN.

**"As of"** on every answer: when ESPN answered ("as of 15:41:52, ESPN answered 1 s ago") and, when the summary was read, when the last play was snapped. If the last play on screen isn't the one you just watched, the feed hasn't caught up: wait a few seconds and check again.

### 7.4 Measuring the lag

`uv run nfl live latency --event <id> --minutes 200` watches one game: it asks for the game every 2 s and the summary every 20 s, and logs to `{NFL_DATA_ROOT}/live/latency/<event>.jsonl` the first time each play appears. At the end (or a minute after the final) it prints, per play:
- **lag** = first seen − its snap (`wallclock`). This includes the play itself (a few seconds) and up to 2 s of our own polling.
- **lead** = the next snap − first seen: how long the new situation was visible before the next play started. A positive lead on a 4th down means a check could have run in time.

Before kickoff it checks every 30 s. Start it a few minutes before kickoff (the console shows each play as it lands); `Ctrl+C` stops it and still writes the report (marked interrupted). Penalties before the snap (a false start, a delay of game) are logged but aren't counted as snaps, so they never pose as the "next snap".

## 8. Replaying a game, and the parity check

**Replay** (`live/replay.py`) steps through a game's summary as if it were live: each play's `start` is the situation it was snapped from. It rebuilds what the summary lacks:
- **timeouts** from the "Timeout #N by TEAM" rows (3 per half; 2 in a regular-season overtime; 3 per two-period half in playoff overtime), plus a timeout lost on a challenge ("Indianapolis challenged …, and the play was Upheld");
- **the score before each play**, moved only by scoring plays (ESPN puts a stray 0–0 on some timeout and two-minute-warning rows);
- **the snap clock of a scoring play**: ESPN gives a touchdown or a made field goal the clock *at the score*, so the snap clock is estimated from the previous snap and the real time between the two (every other play's clock is the snap clock, the same as nflverse's);
- **who received the opening kickoff**.

ESPN's play ids are the event id followed by nflverse's `play_id` (`40187298040` = event 401872980, play 40), which is how a replay lines up with nflverse.

`uv run nfl live replay --event 401872980` prints every 3rd and 4th down of TB at DAL with the bot's call next to what happened (3rd downs: the chance to convert and to pass; 4th downs: the call, the win probability after each choice, `=` when the coach did the same, `x` when not). TB at DAL: 34 3rd and 4th downs; the bot matched the coach on 7 of 11 4th downs. A finished game's summary is kept under `live/summaries/` (ESPN is asked once; `--refresh` asks again).

**The parity check** (`nfl live parity --season 2026 --weeks 1-5`) replays every finished 2026 game and compares each 3rd / 4th-down start state with nflverse's play-by-play: offense, down, distance, yards to goal, score difference, quarter, clock (within 5 s) and both teams' timeouts.

**Result (2026 weeks 1–5, run 2026-10-10): 2,745 of 2,788 states equal, 98.5%** (the target was 98%; a state found on one side only counts as not equal, so a feed that loses plays can't pass; of the 2,785 both sides have, 98.6%). 65 games: weeks 1–4 against curated `plays`, Thursday's TB at DAL against nflverse's newest file, read through nflreadpy (its download cache under `cache/nflreadpy`, like the ingest; nothing goes to curated data) (38 / 38). The offense, the down, the quarter and the score never differed. The 40 that did:

| Why | States | What happened |
|---|---|---|
| The clock of a scoring play | 14 | ESPN gives a touchdown the clock at the score; the estimate of the snap clock is still more than 5 s off (a punt-return touchdown: 523 vs 540 s) |
| The two feeds logged timeouts differently | 12 | ESPN's short-form text for one play left out "Indianapolis challenged … Upheld" (IND keeps a timeout for the rest of the half); nflverse corrected a CIN delay of game to "declined" and dropped the timeout ESPN still lists |
| nflverse charges a lost challenge one play early | 8 | nflverse counts the timeout in the challenged play's own start state; the replay charges it after the play, which is when it was lost |
| The spot differs by a yard | 6 | e.g. ESPN 3rd & 5 at the 10, nflverse 3rd & 4 at the 11 (ESPN's own text says "to ARZ 11"); a 4th & 9 vs 4th & 10 after a delay of game |

Three plays were on one side only: a fake field goal ESPN typed "End Period", a declined penalty ESPN doesn't list, and a 3rd & 1 run ESPN never moved the chains on. Overtime (12 states), goal to go (148 / 153), pre-snap penalties (243 / 244) and both neutral sites (Melbourne 33 / 33, Rio 39 / 39) all matched. Before three replay fixes found by this check (stray scores on timeout rows, challenge timeouts, the scoring-play clock) it stood at 92.4%.

**The decision replay:** the same engine scored the same 4th downs twice, once from ESPN's replayed state and once from nflverse's (as the LD00 backtest does), on five games: SF at LA (Melbourne), BAL at DAL (Rio) and the three overtime games. **The calls agree on 73 of 73.** The two states are identical on 55 (identical calls and win probabilities, as they must be); the other 18 differ by a few seconds of an estimated scoring-play clock or, twice, by the timeout ESPN's short-form text lost. The largest win-probability difference anywhere is 0.08 points.

## 9. Commands

```bash
uv run nfl live backtest                 # leave-one-season-out, 2014-2025 (W&B live-backtest), ~3 min
uv run nfl live backtest --ratings       # research: the yards-gained model with team ratings (not shipped)
uv run nfl live train --promote          # fit 2010-2025, run the checks, log + promote live-decision-models
uv run nfl live call --state '{"season": 2025, "score_diff": 0, "game_seconds": 2400, "half_seconds": 600, "down": 4, "ydstogo": 1, "yardline_100": 40}'
uv run nfl live call --state '...' --no-bootstrap   # skip the confidence label
uv run nfl live games                    # this week's games from ESPN (event ids, status, score, down & distance)
uv run nfl live games --week 4           # another week of seasons.current
uv run nfl live call --event 401872981   # a game right now: its next snap, and the call on a 3rd / 4th down
uv run nfl live call --event 401872981 --json
uv run nfl live replay --event 401872980 # every 3rd / 4th down of a game, the bot next to the coach
uv run nfl live replay --event 401872980 --downs 4 --bootstrap
uv run nfl live latency --event 401872981 --minutes 200   # ESPN's lag on a live game
uv run nfl live parity --season 2026 --weeks 1-5 --decisions 401872657,401872923   # ESPN vs nflverse
```

`nfl live call --state` prints the call as JSON: `wp` (go / fg / punt), `best`, `gap`, `label`, `boot_share`, `convert`, `fg_make`, `fg_distance`, `punt_start` (the receiving team's expected start, yards from its own goal), `wp_now` and `ms`. On a 3rd down: `convert`, `pass_prob`, `wp_now` and `table`. The state's fields: `season`, `score_diff`, `game_seconds`, `half_seconds`, `down`, `ydstogo`, `yardline_100` (required); `off_timeouts`, `def_timeouts` (3), `home` (+1 / 0 / −1), `receive_2h_ko`, `spread` (offense side, + = favored), `total`, `roof`, `ot`, `playoffs`, `wind`, `temp` (optional).

## 10. Files

| Where | What |
|---|---|
| `{NFL_DATA_ROOT}/models/live-decisions/<first>-<last>_<UTC>/` | one fitted bundle: `wp.txt` + `wp_base.json`, `gain.txt`, `pass.txt`, `gain_boot/00.txt`…`19.txt`, `fg.json` (with the 20 refits), `punt.json`, `kickoff.json`, `pat.json`, `meta.json`, `checks.json` |
| `{NFL_DATA_ROOT}/models/live-decisions/production.json` | which bundle is promoted (the live code reads this, not the W&B alias) |
| `{NFL_DATA_ROOT}/runs/backtests/live-decisions/main/` | the backtest: `predictions_<model>.parquet`, `decisions.parquet` (every real 4th down: the coach's call, the bot's, the gap, the cost), `summary.json` |
| W&B artifact `live-decision-models` | the same bundle, with its own `production` alias |
| `{NFL_DATA_ROOT}/live/summaries/<event>.json.gz` | finished games' ESPN summaries, saved by `nfl live replay` / `parity` so ESPN is asked once (LD01) |
| `{NFL_DATA_ROOT}/live/latency/<event>.jsonl` | `nfl live latency`'s log: a `start` line, one `game` line per play the first time the scoreboard showed it, one `summary` line per play with its `wallclock`, `error` lines, an `end` line with the statistics |
| `{NFL_DATA_ROOT}/live/parity/<season>-wNN-wNN.json` (+ `.parquet`) | the parity report and its row-by-row comparison |
| `{NFL_DATA_ROOT}/live/probe/` | LD01's probe: ESPN responses saved on 2026-10-10 and archived live scoreboards (the test fixtures were trimmed from them) |

None of the `live/` files feed curated tables: live data is a view, not a source.

## 11. Changing it safely

- Model settings live in `config/settings.yaml` → `live:` (the only block this track adds); code defaults in `live/models.py` `DEFAULTS`.
- Any model change: re-run `nfl live backtest`, compare with the ship rules (model card), then `nfl live train --promote`.
- **Never touch the production models** (D107): `tests/test_production_untouched.py` fails if a protected file or settings block changes.
- New rules eras (another kickoff change, say): add it to `schema.kickoff_era` and re-train; the kickoff model falls back to the latest era it has until then.

## 12. Limits and what's next

- **The bot is aggressive:** it says go on 51% of 2014–2025 4th downs (coaches 17%): 96% of 4th & 1, 73% of 4th & 4–5, 52% of 4th & 6–7. Valuing the same futures with nflfastR's expected points is even more aggressive, so the win-probability model isn't the cause; the 4th-down conversion chances are (they sit between the 3rd-down rate and the rate of 4th downs teams chose to attempt, the classic selection bias; model card → Is this good?). LD03 tracks the bot's calls and its conversion chances against real attempts each week.
- **Average team:** the models know the spread, not the roster. A great kicker or a bad short-yardage offense is context (LD02), not an input.
- **Two-point tries and timeouts** aren't advised; overtime uses the same win-probability model (the 2025 "both teams possess" rule isn't modelled explicitly; a tied playoff OT period does go on).
- **Onside kicks** are left out of the kickoff model.
- **The feed can break.** ESPN's API is unofficial: if a check fails every time, look at what `scoreboard/{event}` returns now and fix `live/state.py` (runbook → Game day). A check never changes anything, so a broken feed costs nothing but the answer.
- **The first half needs the summary** (who received the opening kickoff), so a first-half check makes two ESPN calls, 2 s apart.
- Next: **LD02** puts the check in the control room's **Game day** tab, with "Is this team good at this?" next to the call.
