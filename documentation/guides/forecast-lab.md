# Forecast lab guide: availability and role, outcome curves

**What this is:** a plain-language guide to the Forecast lab. It explains the two models built so far: **availability and role** (will he play, how much, and who gets the work if he doesn't) and **outcome curves** (the whole spread behind a player projection, so any number or band has a chance). For each one: the idea, a short primer on the terms, how it works step by step on one real week-10 example, what it reads and produces, how good it is against its baselines, and where it is weak. Then how both refresh after **Run week N**, the commands, the two control-room tabs, how to look at the files yourself, and how to change them safely.

**Written 2026-11-14.** What has and hasn't been checked is in §1.3. Numbers come from the saved backtest summaries on D: and from the real 2026 week-10 files; where a number comes from somewhere else, the text says so.

**Related docs:** the track's spec is the [Forecast lab README](../forecast-lab/README.md) and the phase files ([FL00](../forecast-lab/FL00-availability-and-role.md), [FL01](../forecast-lab/FL01-outcome-curves.md), [FL02](../forecast-lab/FL02-update-chain-and-tabs.md)); decision D158 in the [decisions log](../10-decisions-log.md). The model cards hold every result table and the "Reading the W&B charts" notes: [availability and role](../model_cards/availability-and-role.md) and [outcome curves](../model_cards/outcome-curves.md). The player projections the curves are built on are explained in the [player projections guide](player-projections.md). The W&B side is [W&B guide §6.15](weights-and-biases.md#615-the-forecast-lab-group-forecast-lab-fl00-fl01). The weekly steps are in the [runbook](../runbook.md). Agents use the `forecast-lab` skill.

---

## 1. What the Forecast lab is

### 1.1 The gap it fills

The player model publishes, for each player and stat, a projection and an 80% range ("85 receiving yards, likely 60 to 135"). Two questions come up the moment you use that for research, and the engine couldn't answer either:

1. **"Will he play, and who gets the work if he doesn't?"** Every projection assumes the player is on the field. [Doc 11](../11-prediction-targets.md) says so directly: availability is an input, not a target. A Tuesday run has no injury report for the week, and some of the biggest misses are a player who sat, or a teammate who took his snaps.
2. **"What is the chance he gets more than 50 yards?"** The projection gives three points: the 10th, 50th and 90th percentile. To get the chance of any other number, you'd have to assume a bell curve and do the arithmetic by hand. Yardage isn't bell-shaped (it has a floor at zero and a long right tail), so the by-hand answer is wrong in a way you can measure.

The Forecast lab answers both. It is a set of **new models beside the existing ones**: nothing in the game model, the ratings, the player model, the digest, the weekly graph or the weekly run changes (the no-touch rule, D107).

### 1.2 What's in it

| Part | The question it answers | What you get | Baseline it has to beat | Status |
|---|---|---|---|---|
| **Availability and role** (FL00) | Will he play, how much, and who gets the work if he doesn't? | The chance he plays; his snap share if he plays (with an 80% range); his share of the team's targets and carries if he plays; for each player who might sit, the teammates whose share rises | The historical play rate for what the view knows; his last 2 games; his rolling share | Built, shipped (all four outputs, both views) |
| **Outcome curves** (FL01) | What is the chance he gets more than 50 yards, or lands between 60 and 90? | For each projection, the whole curve: the chance of reaching any number or landing in any band | A bell curve through the projection's 10th and 90th percentile; the player's own last games | Built, shipped (all ten stats) |
| **The update and two week tabs** (FL02) | How does it all stay current? | `nfl forecastlab update`, the follow-up the app starts after **Run week N**, the **Availability** and **Outcomes** tabs | n/a | Update and both tabs built (§8) |
| Season simulator (FL03) | Each team's playoff, division and seed chances, and how much this week's game moves them | | | Planned |
| Game-script simulator (FL04) | How is this game likely to be played, and what do each player's stats look like in each kind of game? | | | Planned |

**The two models fit together like this.** A curve says what happens **if he plays**: it is built only from games players played in. The chance that he plays is the availability model's. The tabs show both, side by side; no single combined number is published.

### 1.3 What has been checked, and what hasn't

| Checked | How |
|---|---|
| Both models beat their baselines on a walk-forward backtest, and passed rules written down before the reported runs | The runs and tables in §3.8 and §4.6 |
| The headline numbers are what the saved rows say | The lead recomputed them independently from the saved backtest rows: availability's Brier scores, by-season wins, snap, target and carry errors, range coverage and decile calibration; the curves' threshold scores, band calibration and a pinball-loss comparison. They matched |
| The availability model never saw the game-day inactive list | A leak test: players on that list who were not Out or Doubtful on the report never play (by definition), and the model still gave them an average chance of 58-60% (the baseline: 73-80%) |
| The real week-10 files exist and read back | `forecastlab/2026/week10/` (§9) |

| Not yet | Why |
|---|---|
| The **Saturday** availability view has not run live on a complete injury report | Week 10's report was only Friday's early pull when the first update ran; the backtest's Saturday rows use a stand-in (§3.3) |
| The **after-the-games** parts of both tabs, and the live grades, have not run on a real graded week | Week 10's games are this weekend. The first real grades come with Tuesday's run |
| Nobody has looked at the **W&B charts** of these runs | Agent processes can't use the W&B API. The names in the model cards were checked against the code that logs them, not against the pages |
| The app's **follow-up after Run week N** has not yet followed a real press | It is covered by tests; the first real one is Tuesday's run. The first real update was run from a terminal (§6) |

---

## 2. Four ideas you need first

Skip this if you already know a Brier score and a percentile.

### 2.1 A probability forecast, and the Brier score

"He plays: 31%" is a probability forecast. Two questions decide whether it is any good:

- **Is it sharp?** Does it say 3% for players who almost never play and 97% for players who almost always do, or does it hover around 77% for everyone?
- **Is it honest?** Of all the players it gave about 30%, do about 30% play? That is **calibration**.

The **Brier score** measures both at once: the average of (forecast - what happened)^2, where what happened is 1 (he played) or 0 (he didn't). A forecast of 0.9 for a player who plays costs 0.01; a forecast of 0.9 for a player who sits costs 0.81. Zero is perfect, and lower is better. In our data 77.2% of the players on the list play, so always saying "77%" scores a Brier of 0.1759. That is the floor any model has to beat; the real bar is a baseline that knows the injury report (§3.1).

A **reliability table** checks honesty: sort the forecasts into ten equal-size groups, and compare each group's average forecast with how often it happened. Honest forecasts sit on the diagonal (§3.8 has the real one).

### 2.2 A distribution, a percentile, and "the chance of more than X"

A projection of "82 yards" is one number. The truth is a spread: he might have 30, or 150. A **distribution** (here called a **curve**) gives the chance of every outcome.

- A **percentile** is the number that a given share of outcomes fall below. The 10th percentile of 27 yards means 10% of games end with 27 or fewer.
- **The chance of more than 49.5 yards** is 100% minus the height of the curve at 49 yards. (Yardage is whole numbers, so every mark is written as x.5.)
- **A band**, such as 60 to 90 yards, is the difference between two heights of the curve.

### 2.3 Why a bell curve is the wrong shape for yards

The player model's range (the 10th to 90th percentile) is well calibrated: it holds 82.4% of receiving-yards results across 2021-2025, close to its 80% target. The bell curve through those two points is the problem. A bell curve is symmetric and has no floor. Real receiving yards are neither:

| Projection (median) | Share of games with 0 yards or fewer | Where the 10th percentile of the miss lands | Where the 90th lands |
|---|---|---|---|
| up to 10 yards | 60% | -0.14 | +0.95 |
| 10 to 20 | 25% | -0.30 | +0.72 |
| 35 to 50 | 6% | -0.42 | +0.64 |
| over 70 | 1% | -0.41 | +0.59 |

(The "miss" here is the actual result minus the median, in units of the range's width. A bell curve would put the 10th and 90th percentiles at -0.50 and +0.50 at every size. Source: the FL01 probe, receiving yards 2019-2025, 29,266 rows.)

So a low-projection receiver has a pile of results at exactly zero, and a high-projection receiver's misses lean to the right.

### 2.4 An analog, and a CRPS

**An analog** is a past projection that looked like this one. To build a curve for a new projection, find the past projections of the same stat that were most similar (here: the same size), and look at where their results landed relative to what was projected. That set of real outcomes, rescaled to the new projection, *is* the curve. Nothing is assumed about its shape.

**CRPS** (continuous ranked probability score) scores a whole curve against what happened. For each whole number k, take the curve's chance of "k or fewer", compare it with what actually was true (0 if the result is above k, 1 if it is at or below k), square the gap, and add up over every k. It is in the stat's own unit, and lower is better. A "curve" that is a single number scores that number's absolute miss, so CRPS is a fair way to compare a curve with the old one-number projection. It is the headline score for the curves because it rewards a curve for being right everywhere, not at one mark.

**The rest of the method is the project's usual one:** a **baseline** the model has to beat, a **walk-forward** test (each week predicted by a model that has only seen earlier weeks), a window for **tuning** and a different one for **reporting**, and a **ship rule written down before the reported run**. See the [player projections guide §3](player-projections.md#3-the-ideas-it-rests-on-a-short-primer).

---

## 3. Availability and role (FL00)

### 3.1 The question and the baselines

For every quarterback, running back, wide receiver, tight end, lineman, linebacker, cornerback and safety who could play for a team this week:

| Output | Meaning | Baseline it must beat |
|---|---|---|
| **Chance he plays** | A probability that he takes at least one snap on his side of the ball | The historical play rate for what the view knows: his report status, his practice line and his roster status, by position group, fit on earlier seasons |
| **Snap share if he plays** | His share of his side's snaps, with an 80% range | His mean over his last 2 games |
| **Target share, carry share if he plays** | His share of the team's targets (WR, TE, RB) and carries (RB, QB) | His rolling share over his last 4 games |
| **Who inherits** | For each player who might sit: the teammates whose expected share rises, and by how much | None. It is a view built from the outputs above |

One thing about that first baseline matters for reading the results: **it knows the report status, the practice line and the roster status, but not how much the player has been playing.** Part of the model's large gain over it is simply knowing who is a regular and who is a healthy scratch (§3.8 and §3.9).

### 3.2 Who is on the list

The list is built from snap counts, not box scores. For a team's game in week W, a player is on it if:

- he took a snap on his side of the ball in one of the team's last 3 games with snap counts;
- his latest appearance was for this team;
- his latest roster row before the week doesn't put him on another team or off the team (cut, traded, retired, released and so on).

Position labels are grouped: DE, DT, NT and DL become **DL**; LB, OLB, ILB and MLB become **LB**; CB and DB become **CB**; S, FS and SS become **S**. Fullbacks and specialists are left out. That gives 34 to 39 players per team-game and about 20,000 rows a season; 77.2% of them play. "Plays" means at least one snap on his side (offense for QB, RB, WR and TE; defense for DL, LB, CB and S).

### 3.3 Two views of the same week

| View | When | What it knows | What it never reads |
|---|---|---|---|
| **Tuesday** | After Run week N | Snaps and usage through last week; whether he left his last game early; last week's injury report status and practice line; whether he played the team's last game and how many of the last 3 he missed; reserve and practice-squad history; how crowded his position group is | This week's injury report. This week's roster row (one exception: week 1, below) |
| **Saturday** | After the Saturday injury update | Everything above, plus this week's **final report status** and **practice line**, plus one folded value from this week's roster row: on the active roster (or practice squad), on a reserve list, or gone (cut, traded, another team, no row) | The game-day inactive list. Ever |

The Saturday view is where the model gains most (§3.8). Three rules keep both views honest:

- **The game-day inactive list is never an input**, in any week or view. In the weekly rosters it shows up as the status `INA`; the code folds every `INA` into "active" before the model sees it, and a test pins that replacing every `INA` changes no row.
- **The Tuesday view never reads this week's roster row, except in week 1.** Last season's roster can't know about the offseason: week-1 rows built from it played only 49.5% of the time (other weeks: 78.1%), because it keeps every player who left. So in week 1 only, the week-1 roster row decides *membership* (is he on the team?). A Tuesday run before week 1 has those rows (the cutdown is 10 or more days earlier).
- **A row is never re-made after its game kicks off.** If the file already holds rows for a game that has started, they stay as they were. If it holds none, they are made and the game is marked "made after kickoff".

**The backtest's stand-in, and what it costs.** The backtest can't replay what Saturday's snapshot of the roster said, so it uses the week's *final* roster row as a stand-in. Comparing real pre-game snapshots with the final rows shows about 0.4% of Saturday rows (5 of 1,180 on Saturday 2026-10-03) carry information from after a Saturday run: a player moved to reserve, or cut, on Saturday. A research run without that feature at all (§3.8) shows the result holds without it.

### 3.4 How it works, step by step, on one real player

**Jauan Jennings** (Vikings wide receiver) in week 10 against Green Bay, the **Tuesday view** from the real file `forecastlab/2026/week10/availability.parquet`.

| Step | What happens | Jennings's numbers |
|---|---|---|
| 1. **Is he on the list?** | He took snaps in at least one of Minnesota's last 3 games, and his latest roster row has him on the team | In. Position group WR |
| 2. **What the Tuesday view knows** | Usage, last week's report, roster status, how crowded his group is | Last week's report: **Out**. Roster: active. His share of the snaps in the last game he played: 55%; mean of his last 2: 55.5%; 6 games played this season. This week's report: not known (Tuesday) |
| 3. **The baseline** | The historical play rate for a wide receiver on the active roster whose last report said Out | **34.1%** |
| 4. **Chance he plays** | A LightGBM model (an ensemble of decision trees) on 21 features, then a small calibration step so the percentages stay honest | **31.3%**: a little below the baseline |
| 5. **Snap share if he plays** | Three quantile models (10th, 50th, 90th percentile) on 30 features. The range is then shifted by the last 2 seasons' misses so about 80% of results fall inside | **62%** (range 24% to 83%); his last-2-games mean, the baseline, is 55.5% |
| 6. **Target share if he plays** | A LightGBM regression on the same 30 features | **5.0%**; baseline (rolling) 6.5% |
| 7. **Expected share, counting the risk** | The chance he plays times his snap share if he plays | 31.3% x 61.6% = **19.3%** of the snaps |
| 8. **Who inherits** | His teammates' shares are forecast twice: once with him certain to play, once with him out | **Myles Price: 32.6% of the snaps with him in, 50.8% with him out (+18.2 points).** Tai Felton +4.3, Odell Beckham Jr. +3.5, Justin Jefferson +1.7, Jordan Addison +0.3 |

The same rows, read straight from the file with the recipe in §9:

| player | position | share_if_in | share_if_out | delta | rank |
|---|---|---|---|---|---|
| Myles Price | WR | 0.326 | 0.508 | 0.182 | 1 |
| Tai Felton | WR | 0.168 | 0.210 | 0.043 | 2 |
| Odell Beckham Jr. | WR | 0.053 | 0.088 | 0.035 | 3 |
| Justin Jefferson | WR | 0.902 | 0.919 | 0.017 | 4 |
| Jordan Addison | WR | 0.934 | 0.937 | 0.003 | 5 |

Three things to notice:

- **Targets move less than snaps.** If Jennings sits, Jordan Addison's forecast share of the targets rises from 18.8% to 18.9%: Jennings's own share has been small (5.0% if he plays), so his absence frees little. The snaps are where the work moves.
- **The chance is not a depth chart.** Minnesota's quarterbacks show it. Kyler Murray (last week's report: Out) has an 18.4% chance of playing; Carson Wentz has 73.9%. If Murray sits, Max Brosmer's forecast snap share rises from 25.1% to 70.1% while Wentz's rises from 93.6% to 100%. Each player's share is its own forecast, so teammates' shares don't have to add up to 100%. Read "who inherits" as "who has gained when players like him sat", not as a lineup.
- **A Tuesday forecast for a player listed Out last week is wide on purpose.** Of the 3,750 rows in 2019-2025 whose previous report said Out, 30.4% played. The model said 28.2% on average. Only the Saturday report can say more.

### 3.5 The pieces, in a little more detail

- **Chance he plays:** a LightGBM binary model (300 rounds, 15 leaves per tree, learning rate 0.05), then a **Platt layer**: a logistic fit of what happened on the model's own raw forecasts from the last 2 seasons, which keeps the percentages honest. It is near the identity (Tuesday slope 1.036, intercept -0.055).
- **Snap share:** three LightGBM quantile models (10th, 50th, 90th percentile). The top end needs a trick: 13% of snap shares are exactly 100% (quarterbacks 76%, safeties 36%), and a 90th-percentile model started at 1.0 and never moved. So the 90th-percentile model is fit "upside down" (as the 10th percentile of 1 - share). The range ends are then shifted by the 90th percentile of the last 2 seasons' misses at each end, so about 10% of results fall outside each end.
- **Target share (WR, TE, RB) and carry share (RB, QB):** one LightGBM regression each, fit on absolute error (an L1 loss), pooled over position groups. The comparison with the baseline is fair: the rolling median of the last 4 games does *worse* than the rolling mean the rule uses (Tuesday target share 0.0494 against 0.0489), so fitting to the median doesn't flatter the model.
- **Teammates' risk:** the shares models also see how much work his teammates have left open: (the chance a teammate sits) x (his usual share), summed over the team's receivers, rushers or the same position group (`vac_tgt`, `vac_car`, `vac_snap`). In training this is who actually sat; at forecast time it is 1 minus the forecast chance. That is how one player's absence reaches another's forecast.
- **Who inherits:** the shares models run twice for every teammate of each at-risk player, once with that player's absence at 0 and once at 1. Teammates whose share rises are kept and ranked, up to 5 per player and kind (snaps, targets, carries).
- **Walk-forward:** every week of 2015-2025 was predicted by models **refit on every earlier week**, from 2014 on. A weekly refit is better than a season-old model (it cut the Saturday Brier by 0.0009 and the calibration error roughly in half on the tuning seasons), and it is what the live forecast does too: a Tuesday run refits in about 10 seconds.
- **`at_risk`:** a player is flagged when his chance of playing is under 85% or his report says Questionable, Doubtful or Out. That is about 35% to 39% of the list, mostly healthy scratches. The tab shows a narrower list (§8).

### 3.6 What it reads, and what it never touches

- **Reads:** the curated tables `games`, `snaps`, `injuries`, `rosters_weekly`, `player_games` and `plays` (only to count designed runs). It does **not** read the player model's projections or any other model's output.
- **Never reads:** the game-day inactive list (`INA`), as above; the injury report's *date* (null in 2025 and 2026, so a report can't be time-scoped: a Saturday run reads the final report, which is what a Saturday run has).
- **Writes:** only under `forecastlab/` and `models/forecastlab/availability/` on D:. Curated files are read whole and never held open (a weekly run's curate step replaces them, and a held file blocks that on Windows).
- **Does not touch:** any production model, the digest, the weekly graph or the weekly run.

### 3.7 What it produces

Under `{NFL_DATA_ROOT}/forecastlab/` (the exact columns are in `src/nflengine/forecastlab/schema.py`):

| File | One row per | What it holds |
|---|---|---|
| `<S>/week<NN>/availability.parquet` | player x game x view (39 columns) | What the view knew (`report_status`, `practice_status`, `prev_status`, `roster_status`, `snap_l1`, `snap_l2`, `games_season`); the four outputs (`p_play` with its baseline `base_p_play`; `snap_p10 / p50 / p90` with `base_snap`; `tgt_share` and `carry_share` with their baselines); `exp_snap`; `at_risk`; `model_version`, `trained_through`; and, once the game is played, `played`, `actual_snap`, `actual_tgt_share`, `actual_carry_share` |
| `<S>/week<NN>/inherit.parquet` | at-risk player x teammate x kind (18 columns) | `out_player`, `out_p_play`, `kind` (targets, carries, snaps), the teammate, `share_if_in`, `share_if_out`, `delta`, `rank` |
| `<S>/week<NN>/availability.json` | the week | When each view was made, its row and game counts, the model version, what shipped, and every game made after its kickoff |
| `<S>/availability_scoreboard.parquet` | week x view x output x position group | The live grades: Brier or MAE of the model and the baseline, range coverage |
| `backtests/availability/<view>/` | | `predictions.parquet` (graded) and `summary.json` of the reported backtest. Research runs have their own folders (`<view>-tune`, `<view>-smoke`, `<view>-<first>-<last>`) and never overwrite these |
| `models/forecastlab/availability/<version>/` | | `model.json` (settings, feature lists, each view's calibration for the next season, what shipped) and the boosters, for inspection (the weekly forecast refits instead of loading them) |

For week 10 (the real file, made 2026-11-14): the Tuesday view has 1,073 rows for 14 games, and the inherit file 1,756 rows. One game, Washington at the Giants, had already kicked off (it was played on Thursday), so its rows are marked "made after kickoff".

### 3.8 How good it is

All numbers: walk-forward, 2019-2025 (the reported window; the settings were chosen on 2017-2018 only), 138,214 player-weeks. The model and the baseline are always scored on exactly the same rows, and the share outputs only for players who played. Source: `backtests/availability/{tuesday,saturday}/summary.json`; W&B `hklag5k1`.

**The rule, written before the reported run (2026-11-14).** Each output ships on its own, per view, if it beats its baseline pooled **and** in at least 6 of 7 seasons; plus: the play output's calibration error by deciles is at most 0.02 pooled, and the snap range covers 72% to 88%. **Every output passed in both views, 7 of 7 seasons.**

| Output (pooled) | Baseline | Tuesday: model / baseline | Saturday: model / baseline |
|---|---|---|---|
| Chance he plays, Brier (lower is better) | The historical rate for what the view knows | **0.1070** / 0.1485 (-28%) | **0.0691** / 0.1162 (-41%) |
| Same, always saying 77.2% | | 0.1759 | 0.1759 |
| Calibration error by deciles (rule: at most 0.02) | | **0.0031** / 0.0185 | **0.0061** / 0.0323 |
| Snap share if he plays, mean absolute error | His mean over his last 2 games | **0.1204** / 0.1309 (-8%) | **0.1169** / 0.1309 (-11%) |
| The 80% snap range covers (rule: 72-88%) | | 80.3% | 81.1% |
| Target share, mean absolute error | His rolling share (4 games) | **0.0461** / 0.0489 (-6%) | **0.0456** / 0.0489 (-7%) |
| Carry share, mean absolute error | His rolling share (4 games) | **0.0911** / 0.0959 (-5%) | **0.0892** / 0.0959 (-7%) |

**By season: the chance he plays and the snap share** (the model first, then the baseline):

| Season | Tuesday Brier | Saturday Brier | Tuesday snap MAE | Saturday snap MAE | 80% range covers (Tue / Sat) |
|---|---|---|---|---|---|
| 2019 | 0.0983 / 0.1426 | 0.0640 / 0.1060 | 0.1212 / 0.1323 | 0.1185 / 0.1323 | 79.9% / 80.1% |
| 2020 | 0.1137 / 0.1533 | 0.0716 / 0.1255 | 0.1234 / 0.1336 | 0.1198 / 0.1336 | 80.0% / 80.7% |
| 2021 | 0.1246 / 0.1646 | 0.0729 / 0.1274 | 0.1268 / 0.1369 | 0.1219 / 0.1369 | 80.4% / 81.2% |
| 2022 | 0.1040 / 0.1456 | 0.0682 / 0.1136 | 0.1174 / 0.1276 | 0.1142 / 0.1276 | 80.9% / 81.6% |
| 2023 | 0.1016 / 0.1438 | 0.0698 / 0.1148 | 0.1177 / 0.1280 | 0.1150 / 0.1280 | 80.8% / 81.6% |
| 2024 | 0.1020 / 0.1425 | 0.0684 / 0.1123 | 0.1178 / 0.1290 | 0.1142 / 0.1290 | 80.2% / 81.4% |
| 2025 | 0.1038 / 0.1464 | 0.0685 / 0.1130 | 0.1185 / 0.1289 | 0.1151 / 0.1289 | 79.7% / 81.0% |

The model wins every season on every output. 2020 and 2021 (COVID-era reserve lists, many practice-squad call-ups) are the hardest for the Tuesday chance. 2022 Tuesday has the worst calibration of any single season (0.0236, against 0.0031 pooled): one season of about 20,000 rows has more noise in ten bins than the pool does.

**Is the chance honest?** The reliability table: ten equal-size groups of forecasts, sorted by the forecast (pooled 2019-2025).

| Group | Tuesday: forecast | Tuesday: played | Tuesday: baseline's forecast | Saturday: forecast | Saturday: played | Saturday: baseline's forecast |
|---|---|---|---|---|---|---|
| 1 (lowest) | 16.6% | 17.3% | 50.6% | 1.8% | 0.9% | 21.9% |
| 2 | 43.3% | 43.7% | 75.0% | 31.9% | 34.8% | 81.2% |
| 3 | 65.4% | 64.5% | 81.5% | 63.8% | 63.8% | 83.5% |
| 4 | 81.1% | 81.1% | 83.0% | 85.3% | 84.6% | 86.2% |
| 5 | 88.8% | 88.9% | 82.9% | 94.3% | 93.6% | 88.1% |
| 6 | 92.2% | 92.2% | 83.2% | 97.4% | 97.1% | 88.1% |
| 7 | 94.1% | 94.5% | 83.7% | 98.6% | 98.6% | 88.1% |
| 8 | 95.4% | 95.9% | 83.9% | 99.2% | 99.4% | 88.2% |
| 9 | 96.5% | 96.5% | 83.9% | 99.5% | 99.7% | 89.0% |
| 10 (highest) | 97.6% | 97.6% | 83.0% | 99.7% | 99.8% | 90.4% |

(Computed for this guide from the saved backtest predictions.) The model's forecast and the real play rate agree to about a point in every group. The baseline can't separate the groups: its forecasts hardly move from the 5th group up.

**Where the Saturday gain comes from: the report status** (Saturday view, pooled):

| This week's report | Rows | Played | Model's average chance | Baseline's | Brier: model / baseline |
|---|---|---|---|---|---|
| Out | 4,624 | 0.0% | 0.1% | 0.4% | 0.0002 / 0.0003 |
| Doubtful | 712 | 1.0% | 1.0% | 5.3% | 0.0099 / 0.0136 |
| Questionable | 6,440 | 66.2% | 64.2% | 66.0% | 0.1825 / 0.2088 |
| On the report, no game status | 14,613 | 91.9% | 91.7% | 93.3% | 0.0369 / 0.0600 |
| Not on the report | 111,825 | 79.6% | 79.7% | 83.4% | 0.0700 / 0.1237 |

Most of the Saturday gain is in the last row: the 80% of rows with no injury designation. A player not on the report can still be a healthy scratch, a player who was cut or moved to the practice squad that week, or a regular. The baseline, which only sees "not on the report", gives them all 83%. The model knows who has been playing. **That is the honest reading of the large Brier gain:** a Questionable player is only slightly better forecast than the baseline's rate (0.1825 against 0.2088); the rest is the model knowing a regular from a scratch.

**How much of Saturday rests on this week's roster row** (a research run without that one feature, W&B `bikjocnb`, never used for the decision): Saturday's Brier is 0.0827 against 0.0691 with it, and the baseline's 0.1251; the play output passes its rule without the feature (7 of 7 seasons, calibration error 0.0039), and so do the others (snap MAE 0.1177, target share 0.0458, carry share 0.0897). The feature is worth 0.0136 Brier; the model doesn't need it to ship.

**The tuning seasons** (2017-2018), where every setting was chosen: Tuesday Brier 0.0960 against the baseline's 0.1434; Saturday 0.0641 against 0.1091; every output won both seasons (W&B `h4g8948l`). The settings grid is flat for the chance he plays and the snap share (best to worst within 2%): the features carry the model, not the settings.

**By position group** (Brier, model / baseline, pooled):

| Group | Rows | Play rate | Tuesday | Saturday |
|---|---|---|---|---|
| QB | 5,959 | 67.6% | 0.1173 / 0.2068 | 0.0936 / 0.1824 |
| RB | 12,979 | 76.0% | 0.1136 / 0.1561 | 0.0733 / 0.1227 |
| WR | 21,265 | 78.4% | 0.1070 / 0.1365 | 0.0643 / 0.1053 |
| TE | 12,733 | 82.3% | 0.0887 / 0.1163 | 0.0491 / 0.0842 |
| DL | 29,319 | 81.3% | 0.0903 / 0.1230 | 0.0555 / 0.0957 |
| LB | 23,115 | 76.3% | 0.1076 / 0.1576 | 0.0736 / 0.1243 |
| CB | 19,085 | 72.0% | 0.1312 / 0.1786 | 0.0881 / 0.1402 |
| S | 13,759 | 76.1% | 0.1146 / 0.1618 | 0.0759 / 0.1247 |

**The leak test** (the lead's): players on the game-day inactive list who were not Out or Doubtful on the report never play, and the model still gives them an average chance of 58% to 60% (the baseline: 73% to 80%). A model that had seen the list would have said close to zero.

### 3.9 Limits

- **The Saturday view's backtest uses a stand-in** for Saturday's roster snapshot: the week's final roster row (about 0.4% of rows carry later information; §3.3). The Saturday view **has not yet run live on a complete report**.
- **Week 1 uses the week-1 roster row for membership.** On the Tuesdays measured, 5 to 8 of about 1,100 list rows (0.5% to 0.7%) changed membership by game day; no week-1 snapshot exists to measure week 1 itself.
- **Part of the gain is knowing who is a regular** (§3.8). The baseline doesn't. Don't read -28% and -41% as "the injury report's value".
- **A Tuesday forecast for a player listed Out or Doubtful last week is only a little better than the baseline** (Brier 0.1935 against 0.1992 for Out): without this week's report, there isn't much more to know.
- **The snap range is awkward for quarterbacks.** A quarterback who plays usually plays every snap, so the range covers 75% of Tuesday results (79% Saturday); the other groups cover 79% to 82%.
- **Shares don't add up.** Each player's share is its own forecast, so a team's forecasts can sum to more or less than 100%.
- **"Who inherits" is learned, not a depth chart** (§3.4). It says who tended to gain when players like him sat.
- **Practice-squad call-ups and mid-week moves** are the largest source of surprise for "not on the report" players. Nothing before game day can see them.
- It forecasts **whether** he plays, not how much of a game a hurt player finishes (a player who leaves in the first quarter still "played").

---

## 4. Outcome curves (FL01)

### 4.1 The question

For each projection the player model publishes (ten stats, §4.5), what is the whole curve behind it: the chance of reaching any number, and of landing in any band? Today you could get the chance of one number only by assuming a bell curve. The curve is learned from past projections instead.

**Two honest notes before the method.**

- **A curve does not make ranges tighter.** The published 80% ranges already hold 80% to 85% of games. A band of plus or minus 7 yards around a typical receiving projection holds about **1 game in 9** (Jaxon Smith-Njigba in week 10: 11.5%). That is the sport, not a weakness of the model. What the curve adds is a chance for any number or band you choose that has been checked against what happened.
- **A curve is "if he plays".** It is built only from games players played in. The chance he plays is the availability model's (§3).

### 4.2 How it works

The curve is built at run time, for each projection, from the player model's own walk-forward history. Nothing about the player model changes.

**Amounts (yards).** For a projection with median P50 and range P10 to P90, width w = P90 - P10 (at least 5):

1. **Find analogs.** Among all earlier projections of the same stat that have a result, take the K most similar. "Similar" is the projection's size (P50), or, for passing yards, rushing yards and scrimmage yards, its size and width together. K is 500 or 1,000, chosen on the tuning window.
2. **Standardize each analog's miss:** z = (actual - P50) / w.
3. **Rescale to the new projection:** each analog gives a possible outcome of P50 + z x w, rounded to a whole yard.
4. **Keep zero as zero.** An analog that gained exactly 0 yards stays at 0 (it is never smeared onto 1 or -1 by the rescaling); a negative result keeps its own value; a positive result never lands below 1. This is `analog_zero`, added after the first reported run (§4.7).
5. **The curve is the share of those outcomes at or below each whole number.**

History is always strictly before the week being predicted.

**Counts (receptions, targets, carries, tackles).** A **negative binomial** distribution (a count distribution that allows more spread than a Poisson) centred on the published mean, with its spread fitted on the nearest K analogs (K from 500 to 2,000). For targets, RB receptions and tackles, the mean is also scaled by what those analogs actually did relative to their means.

**The two baselines.**

| Baseline | What it is |
|---|---|
| **Bell curve** ("the simple curve" on the tab) | A normal curve centred on P50, with its spread set by P10 and P90 (counts: a Poisson at the mean). What you'd work out by hand from the projection |
| **Own history** | The player's own last 16 results at most (6 at least; otherwise the bell curve) as the distribution |

### 4.3 Walked through one real receiver

**Jaxon Smith-Njigba** (Seahawks wide receiver), week 10 against the Raiders, from the real file `forecastlab/2026/week10/curves.parquet`.

| Step | What happens | His numbers |
|---|---|---|
| 1. **The projection** | The player model's receiving-yards projection (`player-model-v1:2026-w10`) | P10 15.6, **P50 81.6**, P90 147.1 yards. Width w = 131.5 |
| 2. **The history** | Every earlier WR/TE receiving-yards projection with a result, 2017 to week 9 of 2026 | 39,180 rows |
| 3. **Find analogs** | The 1,000 closest to a projection of 81.6 | Essentially the highest projections ever made |
| 4. **The analogs' misses** | z = (actual - P50) / w for each | The 10th, 50th and 90th percentiles of z are about -0.41, +0.02 and +0.60. A bell curve would say -0.50, 0, +0.50 |
| 5. **Rescale** | P50 + z x 131.5, in whole yards | |
| 6. **The curve** | Read off whole numbers | 5%: 14 yards. 10%: 27. 25%: 53. 50%: **84**. 75%: 127. 90%: 161. 95%: 183 |

What you can read from it:

| Question | The curve | The bell curve |
|---|---|---|
| Chance of more than 49.5 yards | **77.0%** | 73.4% |
| More than 74.5 | 58.3% | 55.5% |
| More than 99.5 | 40.1% | 36.4% |
| Between 60 and 90 yards | 23.8% | |
| 0 yards or fewer | 1.2% | |
| Within 7 yards of his projection (75 to 89) | 11.5% | 11.6% |

The 80% range from the curve is **27 to 161** yards, against the player model's published 15.6 to 147.1: the curve moves both ends up, because a receiver projected this high has rarely finished near zero and sometimes had a very big game.

**A cross-check from the saved backtest** (computed for this guide, a sanity check and not part of any rule): in 2021-2025 the player model projected **156** receivers between 76 and 87 yards. 74.4% of them gained more than 49.5 yards, 57.7% more than 74.5, and 35.9% more than 99.5. With 156 games, each of those carries a sampling error of about 4 points; Smith-Njigba's curve (77.0%, 58.3%, 40.1%) uses a thousand analogs, not 156.

**A low projection: Will Kacmarek** (Dolphins tight end, week 10 against the Colts). P50 2.3 yards, P90 21.6.

| Question | The curve | The bell curve |
|---|---|---|
| No yards at all (exactly 0) | **52.0%** | |
| 1 or more yards | 47.3% | |
| More than 14.5 yards | 18.6% | 7.3% |
| More than 24.5 | 9.7% | 0.4% |
| Within 7 of his projection | 72.6% | 62.6% |

A bell curve through his range thinks a 25-yard game is almost impossible. The curve gives it about 1 chance in 10, which is what the data says about players with this kind of projection.

**A count: James Cook** (Bills running back), his carries against the Jets. Mean 19.6 carries. Median 19; 10th to 90th percentile **12 to 28**. The chance of 18 to 22 carries (within 2 of his rounded mean of 20) is **30.2%**; a Poisson at 19.6 says 42.3%. Carries vary more from game to game than a Poisson allows, and the curve knows it.

**A stat where the curve and the bell curve agree: passing yards.** Dak Prescott (Cowboys, against the 49ers): P50 272.5, curve 10th to 90th percentile 175 to 368; the chance of more than 249.5 yards is 61.6% on the curve and 62.2% on the bell curve. For passing yards the player model's own range is already well shaped, and the curve adds almost nothing (§4.6, §4.8).

### 4.4 What it reads, and what it never touches

- **Reads:** the player model's published projection file for the week (`runs/<S>/week<NN>/predictions_players.parquet`: P10, P50, P90 and the mean); for history, the player model's walk-forward backtest (`runs/backtests/player/<stat>/predictions_players.parquet`, 2017-2025) and each later season's `player_walkforward.parquet`; for grading, the production scoreboard's `predictions_players_scored.parquet`.
- **Never changes** any feature, setting, training run, artifact or alias of the player model. It copies P10, P50, P90 and the mean into its own file and builds beside them.
- **Writes:** only under `forecastlab/` and `models/forecastlab/curves/`.
- **Never enters the digest.** A threshold chance is a property of the projection, shown in the control room only.

### 4.5 What it produces

| File | One row per | What it holds |
|---|---|---|
| `<S>/week<NN>/curves.parquet` | player x game x stat (37 columns) | The copied projection (`p10`, `p50`, `p90`, `mean`, `source_version`); the curve itself (`grid_lo`, then `cdf`, the chance of at most `grid_lo + i` on whole numbers; the list stops at the first 1.0); its quantiles (`q05` to `q95`); the standard marks (`marks`: for each, the curve's chance of more than it and the bell curve's); the band (`band`, `p_band`, `bell_p_band`); `n_analogs`; and, once played, `actual` and `played` |
| `<S>/week<NN>/curves.json` | the week | When it was made, the curves model version, the player model version it was built on, the rows by stat, the history rows used, any games made after kickoff |
| `<S>/curves_scoreboard.parquet` | week x stat | The live grades: CRPS, threshold Brier and band hit rate of the curve and both baselines, the whole-curve calibration error |
| `backtests/curves/<stat>/` | | `predictions.parquet` (graded, without the `cdf` lists) and `summary.json`. Research folders: `_tuning/`, `_smoke/`, `_reported_first_run/` |
| `models/forecastlab/curves/<version>/params.json` | | The model is its settings: for each stat the method, K, what "similar" means and how far back to look, plus the shipped list and the backtest verdicts. `production.json` points at the current version |

**The ten stats** (each ships on its own), with the standard marks they are graded on and the band around the projection:

| Stat | Kind | Standard marks | Band |
|---|---|---|---|
| Passing yards (QB) | amount | 174.5, 199.5, 224.5, 249.5, 274.5, 299.5 | plus or minus 15 yards |
| Rushing yards (QB) | amount | 9.5, 19.5, 29.5, 39.5, 49.5 | plus or minus 7 |
| Rushing yards (RB) | amount | 24.5, 39.5, 49.5, 59.5, 74.5, 99.5 | plus or minus 7 |
| Scrimmage yards (RB) | amount | 24.5, 49.5, 74.5, 99.5, 124.5 | plus or minus 7 |
| Receiving yards (WR, TE) | amount | 14.5, 24.5, 39.5, 49.5, 59.5, 74.5, 99.5 | plus or minus 7 |
| Receptions (WR, TE) | count | 1.5, 2.5, 3.5, 4.5, 5.5, 6.5 | plus or minus 1 |
| Targets (WR, TE) | count | 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5 | plus or minus 1 |
| Receptions (RB) | count | 0.5, 1.5, 2.5, 3.5, 4.5 | plus or minus 1 |
| Carries (RB) | count | 4.5, 7.5, 9.5, 12.5, 14.5, 17.5 | plus or minus 2 |
| Tackles (LB, S) | count | 2.5, 3.5, 4.5, 5.5, 6.5, 7.5 | plus or minus 1 |

For week 10 the real file holds **1,381 curves for 631 players in 13 games** (the Thursday game had already been played when the projections were made, so it has none), made 2026-11-14 22:33 UTC.

### 4.6 How good it is

Walk-forward 2021-2025 (the reported window; history from 2017, settings chosen on 2019-2020 only). Rows where the player played. The curve and both baselines are scored on exactly the same rows. CRPS is in the stat's own unit; lower is better. Source: `backtests/curves/<stat>/summary.json`; W&B `6glg980f` (the five amount stats, re-run once under the §4.7 addendum) and `rai6vnyq` (the five count stats, first run).

**The rule, written before the reported run (2026-11-14).** Each stat ships on its own if **all four** hold:

1. CRPS below the bell curve's, pooled, **and** in at least 4 of 5 seasons;
2. CRPS below the player's-own-history baseline's, pooled;
3. the mean Brier score over the stat's standard marks below the bell curve's, pooled;
4. the predicted chance of landing in the band around the projection within 2 percentage points of how often it happened, pooled.

**All ten stats ship.**

| Stat | Rows | CRPS: curve | bell | own games | Gain vs bell | Seasons won | Brier at the marks: curve / bell | Band: curve said / happened (bell said) |
|---|---|---|---|---|---|---|---|---|
| Passing yards (QB) | 2,714 | 39.290 | 39.329 | 41.826 | **+0.10%** | **4 of 5** | 0.18890 / 0.18893 | 17.4% / 17.2% (17.3%) |
| Rushing yards (QB) | 2,714 | 8.410 | 8.660 | 8.574 | +2.90% | 5 of 5 | 0.12861 / 0.13041 | 48.2% / 48.1% (48.8%) |
| Rushing yards (RB) | 7,485 | 13.585 | 13.991 | 14.821 | +2.90% | 5 of 5 | 0.11862 / 0.12015 | 35.1% / 36.1% (33.2%) |
| Scrimmage yards (RB) | 7,485 | 16.464 | 16.846 | 18.043 | +2.27% | 5 of 5 | 0.11284 / 0.11399 | 26.8% / 27.2% (25.4%) |
| Receiving yards (WR, TE) | 21,471 | 11.454 | 12.010 | 12.346 | +4.64% | 5 of 5 | 0.10497 / 0.10643 | 45.1% / 44.6% (41.0%) |
| Receptions (WR, TE) | 21,471 | 0.8281 | 0.8358 | 0.8989 | +0.92% | 5 of 5 | 0.10111 / 0.10156 | 70.7% / 70.7% (75.6%) |
| Targets (WR, TE) | 21,471 | 1.1093 | 1.1245 | 1.2265 | +1.35% | 5 of 5 | 0.10142 / 0.10233 | 59.9% / 60.6% (67.8%) |
| Receptions (RB) | 7,485 | 0.7536 | 0.7604 | 0.8087 | +0.90% | 5 of 5 | 0.13484 / 0.13612 | 73.8% / 74.7% (80.3%) |
| Carries (RB) | 7,485 | 2.3859 | 2.5225 | 2.7425 | **+5.41%** | 5 of 5 | 0.11341 / 0.11831 | 47.6% / 48.9% (68.7%) |
| Tackles (LB, S) | 24,468 | 1.2750 | 1.2915 | 1.3965 | +1.28% | 5 of 5 | 0.14182 / 0.14284 | 50.0% / 51.3% (59.3%) |

**How to read it.**

- **The gain over the bell curve is real but small, except where the bell curve is plainly the wrong shape.** Carries gain 5.4% and receiving yards 4.6%. What a bell curve gets wrong is its *honesty*: for carries it says 68.7% for "within 2 of the projection", and it happens 48.9% of the time (the curve said 47.6%). The whole-curve calibration error (PIT, 0 is perfect) shows it: curve against bell, receiving yards 0.0021 against 0.0455, carries 0.0055 against 0.0486, rushing yards (RB) 0.0024 against 0.0327.
- **Every curve beats the player's own last games** by 2% to 13%.
- **All ten bands are within 2 points** (the widest gap: carries, 1.3). Counts run slightly conservative: what happened ran about a point above what the curve predicted for carries, tackles and RB receptions.
- **Receiving yards, honest across the whole range** (the reliability table: ten groups of "chance of going past a mark", curve against bell, pooled over all seven marks):

| Chance said | Curve: said / happened | Bell: said / happened |
|---|---|---|
| 0% to 10% | 3.0% / 2.8% | 1.2% / 4.6% |
| 10% to 20% | 14.7% / 13.9% | 14.7% / 19.7% |
| 20% to 30% | 25.0% / 24.0% | 24.9% / 28.0% |
| 30% to 40% | 34.5% / 34.3% | 34.9% / 35.8% |
| 40% to 50% | 44.8% / 44.8% | 45.0% / 45.2% |
| 50% to 60% | 54.6% / 54.5% | 54.9% / 53.8% |
| 60% to 70% | 64.8% / 64.8% | 64.9% / 65.6% |
| 70% to 80% | 75.1% / 75.7% | 74.8% / 77.1% |
| 80% to 90% | 84.4% / 85.3% | 84.8% / 89.0% |
| 90% to 100% | 92.9% / 93.2% | 91.3% / 94.3% |

The curve sits on the diagonal. The bell curve says 1.2% for a mark that is passed 4.6% of the time, and 84.8% for one passed 89% of the time: it is too sure at both ends.

**Seasons.** CRPS of the curve against the bell curve, by season (2021 to 2025): receiving yards 11.75 / 12.32, 11.71 / 12.29, 11.42 / 11.99, 11.30 / 11.83, 11.10 / 11.63; carries 2.59 / 2.76, 2.37 / 2.51, 2.34 / 2.47, 2.37 / 2.49, 2.26 / 2.38. The full by-season tables are in the model card.

**Out of sample, live.** A scratch run curved the production projections of 2026 weeks 4, 5, 6, 8 and 9, using only history before each week, and graded them with the production scoreboard's own results (nothing was saved to the real files). The curve's CRPS was below the bell curve's for all ten stats (receiving yards 11.76 against 12.35 on 1,077 rows; carries 2.465 against 2.508; passing yards 40.82 against 40.98 on 130 rows). Receptions (WR, TE) was a tie on the threshold score (0.10023 against 0.10021). This is the FL01 build's own check, not a live grade.

### 4.7 The addendum: zero is its own outcome

The first reported run (W&B `rai6vnyq`) built every amount curve by rescaling each analog's miss. That smears the "no yards" pile across neighbouring whole numbers: an analog who gained 0 yards, but whose own projection was a little different, was mapped to 1 or 2 or -1, not 0. For Will Kacmarek the first curve said P(exactly 0) = 20.0% and P(exactly 1 yard) = 29.2%: nonsense for a receiver, since a player with no catch has exactly 0. The standard marks start at 14.5, so the first scores barely saw it, but "1 or more yards" and "within 7 of the projection" read wrong for every low-projection player, and both are on the tab.

**The rule for the fix was written before it was built (2026-11-14 22:27 UTC).** On the tuning window only (2019-2020), the variant `analog_zero` replaces `analog` for an amount stat if its CRPS is no worse by more than 0.1% **and** its Brier score for "1 or more yards" is lower. It replaced the old method on all five amount stats (CRPS 0.0% to 0.8% better; W&B `w6beygoe`). The reported backtest for the five amount stats was then re-run once (W&B `6glg980f`), the four-part rule re-applied unchanged, and both run ids kept.

| Stat | CRPS: first run / re-run | Chance of 1 or more yards, first run / re-run, what happened | Brier for "1 or more": first / re-run (bell) |
|---|---|---|---|
| Rushing yards (QB) | 8.4562 / 8.4095 | 81.9% / 79.2%, 80.1% | 0.14337 / 0.13720 (0.14284) |
| Rushing yards (RB) | 13.6147 / 13.5852 | 87.1% / 82.7%, 83.0% | 0.10826 / 0.10198 (0.10607) |
| Scrimmage yards (RB) | 16.5044 / 16.4642 | 91.3% / 87.6%, 88.2% | 0.08684 / 0.08302 (0.08620) |
| Receiving yards (WR, TE) | 11.4878 / 11.4535 | 71.2% / 65.3%, 65.8% | 0.16116 / 0.15084 (0.16207) |
| Passing yards (QB) | 39.2902 / 39.2902 | 99.95% / 100.00%, 100.00% | no zeros in the data |

The old curve over-stated "1 or more yards" by 2 to 5 points for the stats with many zeros; the new one is within a point. The standard marks and the band hardly moved, which is why the first scores didn't see it. Count stats were untouched. "1 or more" is a diagnostic only: it is not part of the ship rule.

On the real week-10 files after the fix: Will Kacmarek's chance of exactly 0 went from 20.0% to 52.0%, of exactly 1 yard from 29.2% to 0.7%, and the median from 1 to 0; everything from 14.5 up was unchanged. Smith-Njigba's numbers moved by less than a tenth of a point.

### 4.8 Limits

- **Passing yards ships by the letter of its rule, and no more.** Its curve is practically the bell curve: CRPS 0.10% better, in 4 of 5 seasons (2023 lost), and the threshold score is equal to four decimals (0.18890 against 0.18893). A quarterback's published 80% range was already well shaped, so there was nothing to add. Treat his curve as "the same as a bell curve through the range".
- **The curve doesn't tighten anything** (§4.1). It tells you the chance of the band you choose.
- **It is "if he plays"** (§4.1).
- **Tuning could not tell "all earlier seasons" from "the last 4".** History starts in 2017 and the tuning window is 2019-2020, so "the last 4 seasons" reaches back past the first season for every row, and both options gave identical numbers. The default is every earlier season. The question becomes real from 2022 on: revisit it at the pre-season retune, on a window that can show it.
- **The choice of K is flat** (the best settings sit within 0.1% of each other for most stats), so the choice was simply the lowest CRPS, with no judgment on top.
- **The shape depends on the size of the projection, not on who the player is.** A check by tight end or wide receiver, the player model's confidence label, role flags, home or away and thin history found no difference beyond noise inside each size, so no role columns are used.
- **A star receiver's curve borrows from the next sizes down.** Projections over 70 yards are rare (707 rows in 7 seasons); standardizing the misses is what lets the analogs be shared.
- **Counts:** the analogs' raw outcomes pile onto whatever the nearest rows did (a raw-outcome carries curve is 42% worse than a Poisson at K = 4,000), so counts use a negative binomial instead.
- **It uses the player model's projections as they are.** If a projection is wrong because a player's role changed, the curve inherits that.

---

## 5. How the two models and the tabs fit

Each tab answers one question:

| Tab | Before the games | After the games |
|---|---|---|
| **Availability** | Players at risk of sitting (the chance he plays; Tuesday and Saturday once both exist); expected snap and opportunity shares against each player's baseline; who inherits for each player unlikely to play | Who played and how much, against the forecast; the model against its baselines for the week |
| **Outcomes** | Choose a player and a stat: the curve, with a number or band you type and its chance; where the curve and the bell curve disagree most | The actual marked on each curve; how the threshold chances did against the baselines |

The two are separate on purpose: a curve is not multiplied by a chance of playing anywhere. If you want "the chance he plays and gets more than 49.5 yards", you multiply the two yourself, knowing the curve already assumes he plays.

---

## 6. How it refreshes (D158)

Rishi's rule: anything that refreshes weekly is tied to **Run week N**, so one press refreshes everything. D158 records how: **chained after the run, not inside it.**

1. **Run week N** runs the weekly run exactly as before, and publishes.
2. When that run ends well, **the app itself starts** `nfl forecastlab update --auto --expect-week N`. It runs only what is due (the table below), with its own progress and its own events file (`forecastlab/<S>/events/<run id>.jsonl`).
3. The same happens after the **Saturday injury update**: the Saturday availability view is made then. **The outcome curves are not re-made on Saturday**: the injury update doesn't rewrite the week's player projections, so a player ruled Out on Saturday keeps his Tuesday curve. Read the Availability tab for who plays and the Outcomes tab for "if he plays".
4. If the follow-up fails or doesn't happen, the published week is untouched, and both tabs show an **Update availability and outcomes** button that runs the same command.

**What it may and may not do.**

- It starts only from the press that started the run: a thread begun by that `POST /api/run`. **No page load and no GET ever starts anything.**
- It waits for the run the app launched to exit with code 0. A failed run, a "not ready" stop, a held lock or a lost child starts nothing. It waits up to 6 hours, re-checking every 2 seconds, and gives the lock a moment to be released.
- If the calendar's week has changed while the run was going, it stops.
- It never starts after a play-calling update or after itself (only after the weekly run, a resume or the injury update).
- It holds the weekly-run lock, so it never overlaps a run, a resume, an injury update or a play-calling update.
- It never touches the published week: it writes only under `forecastlab/`, and only reads the week's player projections.
- **Not in rehearsal mode** (`nfl app --rehearsal`), and over Tailscale every write is still refused (D157), so a phone can't start it.
- A run started from a terminal has no follow-up (the app didn't start it). Use the tabs' Update button, or `uv run nfl forecastlab update --auto`. The same if the app was closed between the press and the end of the run.
- This is the recorded exception to D107's "the Run button is unchanged": the button now starts a follow-up. The run, its command, `weekly.py` and every production model are untouched.

**What is due, and when** (`nfl forecastlab update`, `forecastlab/update.py`, `plan_update`; the pre-flight's `forecastlab` gate runs the same plan, and the button greys out when nothing is due):

| Step | Due when | Skipped when |
|---|---|---|
| **Availability** for week N | **Tuesday view:** none yet, or the week was run again after it was made (and no injury update since). **Saturday view:** the week's injury update has run and no Saturday view exists, or it ran again after the Saturday view was made | No promoted availability model; no games in the schedule; every game has kicked off and a file exists (the saved views stay) |
| **Outcome curves** for week N | A promoted curves model with at least one shipped stat; the week's player projections exist; and no curves yet, or the projections are newer and a game is still open | No promoted model; no projections yet ("Run week N"); up to date |
| **Grade week N-1** | Last week's availability or curves aren't in the season scoreboard yet and every game of last week has a result | Nothing to grade; already graded; last week isn't final in the data; week 1 |

Each step stands alone: a failed step never stops the others, and in the grade step one model failing never blocks the other. A kicked-off game's rows are kept as they were, or made and marked "made after kickoff".

**When something fails.**

| What you see | What it means | What to do |
|---|---|---|
| The card says "The availability and outcomes update failed at availability" (or "the outcome curves", "last week's grading"), with the last lines | One step failed. The others ran. The published week is untouched | Fix the cause, then press **Update availability and outcomes**: it runs only what's still due. Or `uv run nfl forecastlab update --auto` |
| "A run is in progress." | The weekly lock is held (a run, a resume, an injury update, a play-calling update) | Wait; the update can run after it |
| "Needs this week's Tuesday run first (Run week N)." | The week isn't published yet | Run the week |
| "Nothing to do until the models are promoted." | There is no `production` model for one family | `uv run nfl forecastlab backtest --model availability --promote` (or `curves`) |
| "Everything is up to date." | Nothing is due | Nothing |
| A terminal run ended and nothing followed | A terminal run has no follow-up | Press Update, or run the command |
| The app was restarted while a run was going | The follow-up lived in the app and went with it | Press Update when the run is over |

**The first real update (2026-11-14, from a terminal, `--launched-by agent`, week 10).** The plan: "availability: no Tuesday view yet (some games have started: their rows are kept or marked)", "curves: week 10's curves are up to date" (made earlier the same day), "grade: week 9 has nothing to grade". It made the Tuesday view: 1,073 players for 14 games, one made after kickoff, in 15 seconds (W&B `dp96fylf`). Exit code 0.

---

## 7. The commands

`uv run nfl forecastlab ...` has four commands. Each logs a W&B run unless you pass `--no-wandb`, and takes `--launched-by`.

```bash
# What's due, and why (nothing runs):
uv run nfl forecastlab update --auto --dry-run
# Everything that's due, in order. Holds the weekly-run lock:
uv run nfl forecastlab update --auto
uv run nfl forecastlab update --season 2026 --week 10        # a named week instead of the calendar's

# One piece by hand:
uv run nfl forecastlab predict --model availability --season 2026 --week 10 --view tuesday
uv run nfl forecastlab predict --model availability --season 2026 --week 10 --view saturday
uv run nfl forecastlab predict --model curves --season 2026 --week 10
uv run nfl forecastlab grade --model availability --season 2026 --week 9
uv run nfl forecastlab grade --model curves --season 2026 --weeks 4-9

# The backtests (a retune; see §10):
uv run nfl forecastlab backtest --model availability --tune
uv run nfl forecastlab backtest --model availability --promote
uv run nfl forecastlab backtest --model curves --tune
uv run nfl forecastlab backtest --model curves --promote
uv run nfl forecastlab backtest --model curves --targets rush_yds-qb,rec_yds-wrte --promote
```

| Command | What it does | Time (real) |
|---|---|---|
| `update` | Plans what's due, then runs it: availability view(s) for the week, the week's curves, and the grades of the last three weeks (a week once it is final, and again while rows of its final games still wait for results). `--auto` takes the calendar's week; `--expect-week N` (the control room always sends it) stops before anything if the week isn't N | The first real update: 15 seconds |
| `predict --model availability` | Refits on every earlier week with the production settings and forecasts one view of the week. Never re-makes a game that has kicked off | 9-10 seconds per view |
| `predict --model curves` | Makes the curves for every shipped stat from the week's published projections | About 1 second of work (17 seconds with W&B) |
| `grade` | Fills `played` and `actual_*` in the week's file for finished games and updates the season's scoreboard. Re-running gives the same files | Seconds |
| `backtest` | The walk-forward backtest with the pre-registered ship rule; `--tune` on the tuning window only; `--promote` (reported run only) writes the model folder, the `production` pointer and the W&B artifact; `--smoke` a throwaway run in `forecast-lab-smoke` | Availability reported: 26 minutes (weekly refits 2015-2025, both views). Curves reported, ten stats: 27 seconds; tuning: 82 seconds |

**Exit codes of `update`:** 0 done (or nothing due); 1 a step failed; 4 another run holds the lock; 5 the data drive isn't connected; 6 the calendar's week isn't `--expect-week` (nothing ran).

What the first real update printed (the log lines, week 10):

```
availability tuesday 2026 week 10: 1073 rows for 14 games, 1756 inherit rows; kept 0 games after kickoff, made 1 after kickoff (8.1 s) -> forecastlab/2026/week10
availability: ok (15 s): tuesday view: 1073 players (1 games after kickoff)
```

`nfl forecastlab predict` / `grade` also work on a week the update hasn't reached. Grading a week needs its file first. A week with no games finished yet has nothing to grade (`played` stays empty).

---

## 8. The two tabs

Both are **week tabs after Play calls**: `/week/2026/10/availability` and `/week/2026/10/outcomes`. They read only the files the update wrote (nothing is recomputed, nothing written) and only show outputs that **shipped**. Each has a designed empty state before its first refresh that says what will appear and names the command, a strip saying when it was made and from which model, and, on the current week, the **Update availability and outcomes** card at the bottom.

### 8.1 The Outcomes tab

The mockup: [Outcomes Tab Mockup](https://claude.ai/artifact/GtsLqoMb8Y5mQVaiXy7HDz) (source `documentation/forecast-lab/mockup/`). The tab is built and was checked on the real week-10 data.

- **The strip and three tiles.** "Made Sat Nov 14, 5:33 PM ET · curves model 2021-2025_20261114T223208Z · built on player model player-model-v1:2026-w10", and a count of games made after kickoff when there are any. Tiles: Curves (1,381), players and stats; This stat; Graded (Yes or Not yet).
- **One note, once:** each curve is the whole spread behind a projection, *if he plays*; ranges are wide because football is (a receiver lands within 7 yards of his projection about 1 game in 9); "the simple curve" is the bell curve the box compares with.
- **The stat picker:** the ten stats, grouped QB, RB, WR/TE, LB/S. It opens on receiving yards.
- **The curve panel** (the centrepiece): for the player you've chosen, the projection and the player model's 80% range, then two boxes:
  - **Chance of more than [x] yards** (counts: **Chance of [k] receptions or more**). It opens on the standard mark nearest his projection (a low-projection tight end opens on 14.5, a star receiver on 74.5). Type any number: the chance, "about 1 in 9", and "the simple curve" beside it.
  - **Within +/- [n] of [his projection]**, or **Between [a] and [b]**. It opens on the stat's own band. Same three readings.
  - **The chart:** for yardage, the chance of more than each number, falling left to right (the curve solid, the simple curve dashed), with your band shaded and your number marked; for counts, a bar per exact number with the simple curve as dots. Hover, or focus the chart and use the arrow keys, to read any number.
  - **Facts:** "No yards at all (0 or fewer)" when it's 0.5% or more; where half of games like this land (25th to 75th percentile) and 8 in 10 (10th to 90th); how many past projections it was built from; after the game, what happened: "He had 91 yards: the curve gave 28% to 91 or more · inside +/-7 of the projection". A player who didn't play says so (a curve is "if he plays", so it isn't graded).
- **The list**, beside it: every player for the stat, sorted by projection, with the 80% range as a bar, the chance within the band, and the chance of a standard mark you choose in the header (curve and simple curve). A search box filters by player or team; 40 rows, then "Show all N".
- **Where the curve and the simple curve disagree most:** two per stat, up to 12, each with a **Show** button. Where the curve and the bell curve differ by the most is where the curve is doing something: the low-projection receivers with a pile at zero, and the carries.
- **After the games:** the actuals appear in the list and on the chart, and **How the curves did** (this week, or season to date) shows, per stat: CRPS and the threshold Brier of the curve, the simple curve and his own games, and the band said against happened. A value is only bold when it is clearly the lowest (2% below the next), so passing yards' tiny gap doesn't look like a win. **Not yet run on a real graded week.**
- The URL keeps the stat and the player (`?stat=rec_yds-wrte&player=<id>`), so a link opens the same curve.
- **Endpoints:** `GET /api/weeks/{S}/{W}/outcomes[?target=]` (one stat's rows plus the week's summary), `.../outcomes/curve?player=&target=` (one whole curve). The target key and the player id are validated by pattern; an unknown curve is a fixed-text 404.

### 8.2 The Availability tab

The mockup: [Availability Tab Mockup](https://claude.ai/artifact/NiZt8wAJn6jqL1RWFwxDZU) (source in the same `mockup/` folder: `template_availability.html`, `availability.css`, `availability.js` and `payloads_availability.json`, the real week-10 answer). The tab is built and was checked on the real week-10 **Tuesday** view. **Not yet seen on real data:** the Saturday view on the page (this week's injury update hasn't run) and the graded state beyond Thursday's game.

From top to bottom (the numbers are the real week-10 page):

- **The strip.** When each view was made, which view is showing, the model, "trained through" and the games made after kickoff: "Tuesday view Sat Nov 14, 6:15 PM ET · showing the Tuesday view · model 2019-2025_20261114T224537Z · trained through 2026-w09 · 1 game made after kickoff (WAS at NYG)". Once the Saturday view exists, both views get a time here.
- **Four tiles.** **Players** (1,073, in 28 teams this week); **Might sit** (124: 0 on the report, 100 regulars, 24 on a reserve list); **This week's report** (a dash on Tuesday, "not out yet on Tuesday"; on Saturday the number Out, with the Doubtful and Questionable counts); **View** (Tuesday, "before the injury report", or Saturday, "after the final injury report").
- **One note, once.** Tuesday has no injury report for the week, so the chance he plays leans on last week (who played, who was listed, reserve lists); Saturday's view, after the final report, is the sharp one, and shows Tuesday's chance beside it. Snap, target and carry shares are **if he plays**, and a change of share is in points: 31% to 47% of the carries is +16 points.
- **Might sit**, in three sections, the most playing time at stake first. "Playing time at stake" is his usual snap share times the chance he sits, so a quarterback who might sit comes before a rotation player who probably will.
  - **On this week's injury report:** Out, Doubtful or Questionable (Out first). Empty on Tuesday, and the page says so: "No report yet: the first one comes out Wednesday, and Saturday's view reads the final one."
  - **Regulars with a low chance:** players with at least 30% of their side's snaps lately whose chance of playing is under 85% for a reason other than this week's report (last week's report and recent missed games, for example). 15 rows, then **Show all** (100 this week).
  - **On a reserve list:** collapsed; "not expected back this week, kept for who took their work" (24 this week).
  - **Who is left off:** a player on the practice squad (no role to lose) and anyone with under 30% of the snaps and no game status. Without that floor the list was about 300 fringe players and healthy scratches a week.
  - **Each row:** the player, his position and game, status chips (this week's status, his practice line, last week's status such as "Out last week", a reserve or practice-squad list); **the chance he plays**, with the usual rate for his status as a tick on a small meter ("18% to play · usual for his status 23%"); once both views exist, a line such as **Tuesday 39% -> Saturday 65%** (the format, from the mockup's example: no real Saturday view exists yet); his **snap share if he plays** with its 80% range against his usual ("If he plays: 98% of the snaps (80%: 37%-100%) · usually 100%"); **If he sits:** each teammate's gain in points of share, joined on one line; and, once his game is over, "Played: 61% of the snaps" or "Didn't play".
  - **A real row:** Kyler Murray (MIN at GB), quarterback, listed Out last week: **18%** to play, usual for his status 23%; if he sits, **Max Brosmer +45 points of the snaps**, Carson Wentz +6. Next in line: Sam Darnold (SEA at LV), 21%, Drew Lock +9 points of the snaps; Bobby Okereke (CAR at NO), 32%, Tyrel Dodson +19.
  - **What "If he sits" shows:** at most the top three teammates, and only gains of at least 1 point of the targets or carries or 3 points of the snaps. A teammate who is himself known to be out (Out, Doubtful or on a reserve list) is left off: "a backup quarterback's chance is low only while the starter plays".
- **Biggest role changes:** four short lists (six rows each) of the share if he plays against his usual: **More snaps**, **Fewer snaps**, **More of the targets**, **More of the carries**. "Usual" is his last two games for snaps and his rolling share for targets and carries. Only players with at least a 50% chance of playing count, and only changes over 2 points. A real one: Devin Singletary (NYG running back), carries **31% -> 47%** (+15 points).
- **Every player:** a team picker (28 buttons), then that team's table: offense, then defense. Columns: **Plays** (model and usual), **Snaps if he plays** (with its 80% range), **Usually**, **Targets** and **Carries** (model and usual). The rows on the might-sit list are marked. After the games a **Played** column shows his snap share or "didn't play". The team lives in the URL (`?team=NE`), and the page loads one team at a time (every player at once is close to a megabyte).
- **How it did** (after the games): **This week** or **Season to date**, per output and view: the players scored, the model, the baseline and how often the 80% snap range held. "Will he play" is the Brier score, the shares are how far off in points. A value is bold only when it is clearly lower (2% below the other). It appears once `nfl forecastlab grade --model availability` has graded the week (the update does this on Tuesday). **Not yet run on a real graded week.**
- **How good is this:** the backtest's four numbers, from the 2019-2025 walk-forward: 0.107 against 0.149 (will he play, Tuesday), 0.069 against 0.116 (Saturday), 12.0 against 13.1 points (snap share), 80% to 81% (the range held).
- **The Update card** (current week only), as in §8.3.
- **Endpoint:** `GET /api/weeks/{S}/{W}/availability[?team=NE]` (`src/nflengine/app/readers/forecastlab.py`, `get_availability`). The team is validated by pattern, and an unknown team is a fixed-text 404. Only outputs that shipped are shown (a model output that missed its rule is blanked, its baseline stays).

### 8.3 The Update card (both tabs)

On the current week, at the bottom of each tab: **Update availability and outcomes**. It follows the run live: the three steps tick off (availability, the outcome curves, last week's grading), with the last lines of its log, and a result notice at the end. While a run is going it says who started it: "Started by the app after the week's run ended", "You pressed Update", "Started with the Update button" or "Started from a terminal". Nothing due shows as one quiet line ("Everything is up to date.") with the steps behind "What it checked". The Run week N and injury-update dialogs each end with a sentence saying what follows, and **Run week N** and the **Saturday injury update** show "Availability and outcomes are updating" while it goes.

---

## 9. Looking at the files yourself

The files are plain Parquet. Read them whole with the project's own reader, which doesn't hold the file open (a weekly run replaces files on Windows, and a held file makes that fail):

```python
import polars as pl

from nflengine.forecastlab import schema as S
from nflengine.paths import ensure_data_root

pl.Config.set_tbl_formatting("ASCII_MARKDOWN")

paths = ensure_data_root(create_dirs=False)
week = S.week_dir(paths, 2026, 10)

# 1. Availability: who might sit on one team
av = S.read_parquet(week / "availability.parquet")
ih = S.read_parquet(week / "inherit.parquet")
print(av.group_by("view").len())
min_risk = (
    av.filter((pl.col("team") == "MIN") & pl.col("at_risk") & (pl.col("snap_l2") >= 0.3))
    .sort("p_play")
    .select("player", "position", "prev_status", "p_play", "base_p_play", "snap_p50", "base_snap")
)
print(min_risk.head(4))

# ... and who gains if Jauan Jennings sits
gain = (
    ih.filter((pl.col("out_player") == "Jauan Jennings") & (pl.col("kind") == "snaps"))
    .sort("rank")
    .select("player", "position", "share_if_in", "share_if_out", "delta", "rank")
)
print(gain)

# 2. Outcome curves: one receiver's whole curve
cv = S.read_parquet(week / "curves.parquet")
r = cv.filter(
    (pl.col("player") == "Jaxon Smith-Njigba") & (pl.col("target_key") == "rec_yds-wrte")
).row(0, named=True)


def at_most(k: int) -> float:
    """P(yards <= k): the stored list starts at grid_lo and stops at the first 1.0."""
    i = k - r["grid_lo"]
    return 0.0 if i < 0 else 1.0 if i >= len(r["cdf"]) else r["cdf"][i]


print(f"projection {r['p50']:.1f} (range {r['p10']:.1f} to {r['p90']:.1f})")
print(f"more than 49.5 yards: {1 - at_most(49):.1%}")
print(f"between 60 and 90 yards: {at_most(90) - at_most(59):.1%}")
print(f"0 yards or fewer: {at_most(0):.1%}")
print([m for m in r["marks"] if m["mark"] == 49.5])
```

What it printed on 2026-11-14 (the first line is `group_by("view")`):

```
| view    | len  |
|---------|------|
| tuesday | 1073 |

| player         | position | prev_status  | p_play | base_p_play | snap_p50 | base_snap |
|----------------|----------|--------------|--------|-------------|----------|-----------|
| Kyler Murray   | QB       | Out          | 0.184  | 0.230       | 0.980    | 1.000     |
| Jauan Jennings | WR       | Out          | 0.313  | 0.341       | 0.616    | 0.555     |
| Theo Jackson   | DB       | Questionable | 0.614  | 0.754       | 0.501    | 0.470     |
| Carson Wentz   | QB       | null         | 0.739  | 0.697       | 1.000    | 0.915     |

| player            | position | share_if_in | share_if_out | delta | rank |
|-------------------|----------|-------------|--------------|-------|------|
| Myles Price       | WR       | 0.326       | 0.508        | 0.182 | 1    |
| Tai Felton        | WR       | 0.168       | 0.210        | 0.043 | 2    |
| Odell Beckham Jr. | WR       | 0.053       | 0.088        | 0.035 | 3    |
| Justin Jefferson  | WR       | 0.902       | 0.919        | 0.017 | 4    |
| Jordan Addison    | WR       | 0.934       | 0.937        | 0.003 | 5    |

projection 81.6 (range 15.6 to 147.1)
more than 49.5 yards: 77.0%
between 60 and 90 yards: 23.8%
0 yards or fewer: 1.2%
[{'mark': 49.5, 'p_over': 0.77, 'bell_p_over': 0.734070255510787}]
```

**Where else to look.** The backtests' `predictions.parquet` have one graded row per player-week (availability: 138,214 rows per view) or player-game (curves, without the `cdf` lists), and `summary.json` has every pooled and by-season number the model cards quote. A Windows console needs `PYTHONIOENCODING=utf-8` for Polars tables, and the project data lives under the data root on D: (never hard-code the path: `ensure_data_root` finds it).

---

## 10. Changing it safely

- **The no-touch rule (D107).** New code lives in `src/nflengine/forecastlab/` and the app. It reads curated data and the player model's published projection files, and never writes to them or to any production model, alias, digest, weekly graph or weekly-run step. `tests/test_production_untouched.py` checks it.
- **Never read the game-day inactive list.** The availability model folds `INA` into "active" and a test pins it. Don't add a feature from it, however good it looks. (A model that sees it is a model that knows who sat.)
- **History is strictly before the week.** Every feature and every analog uses only earlier weeks. A test scrambles actuals from a week on and checks that nothing at or before it moves.
- **Never re-make a row after its game kicks off.** `predict` keeps what is saved for a game that has started and marks a game made late.
- **Settings:** `config/settings.yaml` -> `forecastlab:`. `availability` holds `train_from` (2014), `tune_seasons` (2017-2018), `report_seasons` (2019-2025) and `at_risk_below` (0.85); `curves` holds `history_from` (2017), `tune_seasons` (2019-2020) and `report_seasons` (2021-2025). A curves stat's method can be overridden under `forecastlab.curves.settings.<key>`.
- **The windows are pre-registered.** Choose every setting on the tuning window only; report on the reported window once the settings are fixed; write the ship rule down before the reported run (a timestamped note, then the decisions log). Don't tune on the reported weeks.
- **What "promote" does.** `backtest --promote` (only from the canonical reported run: no tune, smoke or overrides) writes the model folder, moves `production.json` to it and logs the W&B artifact with the `production` alias. For availability, the shipped outputs per view are stored with it. For curves, a run of only some stats (the zero-addendum re-run) is merged into the previous pointer: the other stats keep their parameters, status and numbers, and `sources` and `wandb_runs` say which stat comes from which run. A target that misses its rule isn't shown in the tab and goes to Rishi.
- **The pre-season retune.** After a season: add it to `report_seasons`, re-run both backtests with a rule written first, update the model cards. For curves, use a tuning window that can tell "all earlier seasons" from "the last 4" (§4.8). A new curve stat: add a `CurveTarget` to `schema.CURVE_TARGETS` (key, marks, band, floor), its grid top to `curves.GRID_HI`, a setting to `CHOSEN`, then tune, back-test and promote.
- **A new availability feature:** add it to `availability.py`'s feature lists with a leakage test (a row may only see what the view knew), then retune on 2017-2018.
- **A schema change** (`schema.py` is the contract): the readers, the update, the tests and the app's reader all follow it. Change it in one commit.
- **The docs:** a new W&B run or artifact goes in the [W&B guide](weights-and-biases.md) in the same commit; a retrained model goes in its model card.

---

## 11. Limits and what's next

- **What isn't done yet:** the Saturday view live, the after-the-games parts of both tabs, the live grades, a look at the W&B charts and the app's follow-up on a real press (§1.3). Week 10's games are this weekend: the first real grades come with Tuesday's run (the update's grade step grades week 10 then).
- **The first graded week.** After Tuesday's run, check the Outcomes tab's "How the curves did" and the Availability tab's grade for week 10 against the backtest numbers in this guide. One week is noisy (about 1,000 players, 1,400 curves): look at the season to date as it builds.
- **Season simulator (FL03, planned).** Simulate the rest of the season many times from the published game model and the team ratings, with the NFL's tiebreakers: each team's chance of the playoffs, the division, each seed and a bye, and each game's leverage. A simulation on top of published outputs, not a new trained model. Checked by replaying it from every week of past seasons.
- **Game-script simulator (FL04, planned).** Simulate each game many times to get the chance of each kind of game (shootout, blowout, one-score finish) and what each player's stats look like in each. It builds on the team totals, the play-calling forecast, availability and the curves. The hard part: a quarterback and his receivers move together, and joint outcomes need more care to validate than one stat.
- **A separate plan, not started:** `documentation/player-market-lines.md` (outside market numbers for player stats, shown beside the curves). It is written and waits for approval. The Forecast lab itself uses no market numbers: the digest never reads these files, and the tabs show a chance as a property of a projection.
- **Not in scope:** fantasy scoring, other sports.
