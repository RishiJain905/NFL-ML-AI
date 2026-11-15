# Model card: availability and role (FL00)

For every quarterback, running back, wide receiver, tight end, lineman, linebacker, cornerback and safety who could play for a team this week: **the chance he plays, his snap share if he plays (with an 80% range), his share of the team's targets and carries if he plays, and who inherits the work if he sits.** Built in FL00 (2026-11-14); the W&B artifact `availability-model` (its own `production` alias), version **`2019-2025_20261114T224537Z`**. Spec: [FL00 phase file](../forecast-lab/FL00-availability-and-role.md), [Forecast lab README](../forecast-lab/README.md); plain-language guide: [guides/forecast-lab.md §3](../guides/forecast-lab.md#3-availability-and-role-fl00); decision D158; the W&B runs in [W&B guide §6.15](../guides/weights-and-biases.md#615-the-forecast-lab-group-forecast-lab-fl00-fl01). The sibling model is [outcome curves](outcome-curves.md).

## In one paragraph

Two views of the same week: the **Tuesday** view (what a run after Monday night knows: usage through last week, last week's injury report, roster and reserve history, how crowded his position group is) and the **Saturday** view (adds this week's final report status and practice line, and one folded value from this week's roster row). LightGBM models give the chance he plays (then a Platt calibration layer), his snap share if he plays (three quantile models, the range shifted on the last 2 seasons' misses), and his target and carry share if he plays (one L1 regression each); the shares also see how much work his teammates' possible absences leave open, and the same models, run with a player in and out, give "who inherits". In a walk-forward backtest over 2019-2025 (138,214 player-weeks, refit every week) every output beat its baseline pooled and in 7 of 7 seasons, in both views, so **all four outputs ship in both views**: the chance he plays by 28% (Tuesday) and 41% (Saturday) on the Brier score, the snap share by 8% and 11%, the target share by 6% and 7%, the carry share by 5% and 7%. Two caveats shape how to read that: the baseline for the chance he plays knows the report status but not how much a player has been playing (so part of the gain is knowing who is a regular), and the Saturday view has not yet run live on a complete report.

## The target

| Item | Value |
|---|---|
| Grain | One row per player x game x view (`tuesday`, `saturday`), regular season; the live week may be any game type |
| The list | Every QB, RB, WR, TE, DL, LB, CB and S who took a snap on his side in one of the team's last 3 games with snap counts, whose latest appearance was for the team, and whose latest roster row before the week doesn't put him on another team or off the team. 34-39 per team-game, 17,500-21,200 rows a season, 73-78% of them play |
| Position groups | DE, DT, NT, DL -> DL; LB, OLB, ILB, MLB -> LB; CB, DB -> CB; S, FS, SS -> S; QB, RB, WR, TE as labelled. Fullbacks and specialists are left out |
| Label `played` | At least one snap on his side (offense for QB, RB, WR, TE; defense for DL, LB, CB, S) |
| Snap share | His side's snap share in the game (0-1); scored only for players who played |
| Target share | `player_games.target_share` (0 when he played with no box-score row); WR, TE, RB; scored only for players who played |
| Carry share | Designed runs (`rush == 1`, no two-point tries) per rusher over the team's; RB, QB; scored only for players who played |
| Training window | From 2014 (snap counts start in 2013; one season of lags); walk-forward predictions start in 2015 so every scored season has calibration history |
| Windows | Tuning 2017-2018 (every setting chosen here); reported 2019-2025 (touched once the settings were fixed) |

## What it reads, and what it never touches

- **Reads:** curated `games`, `snaps`, `injuries`, `rosters_weekly`, `player_games` and `plays` (designed runs only). Whole files, never held open (D100). It reads **no other model's output**.
- **Never reads:** the game-day inactive list (`rosters_weekly.status == 'INA'`): folded into "active" in every week and view, and a test pins that replacing every `INA` changes no row. The Tuesday view never reads week W's report or roster row, except **week 1**, where week 1's own roster row decides membership only (a Tuesday run before week 1 has those rows). The injury report's date is null in 2025 and 2026, so a report can't be time-scoped: a Saturday run reads the final report, which is what a Saturday run has.
- **Writes:** only under `forecastlab/` and `models/forecastlab/availability/` on D:. **Production is untouched** (D107): no game, ratings, player or team model, no feature, setting, artifact or alias, the digest, the weekly graph or the weekly run.

## Features (by plain-language group)

`model.json` keeps the exact lists. There is no feature hash: the model is refit from the lists each week.

| View | Chance he plays | Snap share and share models |
|---|---|---|
| Tuesday | 21 features: position group; week; snap share in his last game, last 2, last 4 and the season; games this season and in his career; weeks since his last game; whether he left his last game early; whether he played the team's last game; how many of its last 3 he missed; his snap share in the team's last game; last week's report status and practice line; Out / Doubtful in the last 3 reports; last week's roster status; practice-squad weeks among his last 4; his group's size, his rank in it and the others' snap shares | 30: the 21 plus his target and carry share (last game, last 4, season) and the three **teammates' risk** columns `vac_tgt`, `vac_car`, `vac_snap` (the share left open by teammates: the chance a teammate sits times his usual share, summed over the team's receivers, rushers or the same position group; training uses who actually sat, forecasts use 1 minus the forecast chance) |
| Saturday | 24: the 21 plus this week's report status, this week's final practice line and the folded roster value (`ACT` including the practice squad and `INA`; `RES` the reserve lists; `OFF` cut, traded, another team or no row; null when the team has no rows for the week yet) | 33: the same three added |

**Baselines** (the rule's, on exactly the same rows):

| Output | Baseline |
|---|---|
| Chance he plays | The historical play rate for what the view knows (status, practice line, roster status) by position group, fit on earlier seasons only, shrunk toward the coarser cell with a prior of 20 rows. **It doesn't know how much he has been playing** |
| Snap share | His mean snap share over his last 2 games |
| Target share, carry share | His rolling share over his last 4 games |

## The model, step by step

1. **The chance he plays:** LightGBM binary (300 rounds, 15 leaves, learning rate 0.05, at least 200 rows per leaf), then a **Platt layer**: a logistic fit of what happened on the model's own raw forecasts from the last 2 seasons before the block (identity in the 2015 burn-in). The production layer for 2026: Tuesday slope 1.036, intercept -0.055; Saturday 1.031 and -0.095 (`model.json`).
2. **Snap share if he plays:** three quantile LightGBM models (10th, 50th, 90th percentile; 31 leaves, 300 rounds). **The 90th-percentile model is fit reflected** (the 10th percentile of 1 - share): 13% of snap shares are exactly 1.0 (QB 76%, S 36%, CB 23%), and the plain 0.9 model started at 1.0 and never moved (every row predicted 1.0 in the first probe run). The range ends are then shifted by the 90th percentile of the last 2 seasons' misses at each end (a conformal shift: 2026 Tuesday +0.008 at the low end and 0.000 at the top), so each tail holds about 10%.
3. **Target share (WR, TE, RB) and carry share (RB, QB):** one LightGBM regression each, L1 loss (the outputs are scored on absolute error), 15 leaves, 150 rounds, pooled over groups.
4. **Teammates' risk** (`vac_*`) goes into the share models, so one player's possible absence reaches another's forecast.
5. **`exp_snap`** = chance he plays x snap share if he plays: the expected share counting the risk. **`at_risk`** = a chance under 85% or a report of Questionable, Doubtful or Out (about 35% to 39% of the list; the tab shows a narrower list).
6. **Who inherits:** for each at-risk player and each kind (snaps, targets, carries), the share models are run for his teammates with his absence at 0 and at 1, everything else as forecast. Teammates whose share rises are kept and ranked (up to 5; the tab shows 3).
7. **Walk-forward:** every week of 2015-2025 is predicted by models **refit on every earlier week**; the Platt layer and range shifts come from the run's own earlier predictions (the last 2 seasons, never the block itself); the baseline rates from earlier seasons only. The weekly forecast does the same with the production settings (9-10 seconds per view).

## Tuning (2017-2018 only, before any reported run)

A local scan without W&B (17 settings, walk-forward from 2015, refit per season for speed). Pooled 2017-2018:

| Setting | Tuesday Brier | Saturday Brier | Snap MAE (Sat) | Target MAE (Sat) | Carry MAE (Sat) |
|---|---|---|---|---|---|
| Trees 7 leaves / 150 rounds | 0.0975 | 0.0668 | 0.1179 | 0.0474 | 0.0909 |
| **15 / 300 (play, snap: kept)** | **0.0957** | 0.0649 | **0.1161** | 0.0477 | 0.0924 |
| 15 / 600 (learning rate 0.03) | 0.0958 | 0.0648 | 0.1159 | 0.0477 | 0.0931 |
| 31 / 300 | 0.0961 | 0.0654 | 0.1159 | 0.0479 | 0.0925 |
| min rows per leaf 50 / 800 | 0.0960 / 0.0959 | 0.0646 / 0.0653 | 0.1162 / 0.1165 | 0.0477 / 0.0474 | 0.0929 / 0.0916 |
| Share models: L1 loss | same | same | same | **0.0467** | **0.0893** |
| Share models: L1, 15 leaves / 150 rounds (kept) | same | same | same | 0.0467 | **0.0886** |
| Calibration from the last 1 season (vs 2) | 0.0959 | 0.0651 | 0.1162 | 0.0476 | 0.0924 |
| **Refit every week (kept)** | **0.0954** | **0.0640** | 0.1159 | 0.0475 | 0.0912 |

- **The grid is flat** for the chance he plays and the snap share (best to worst within 2%): the features carry the model, not the settings.
- **The share models gain most from an L1 loss** (-2% targets, -3% carries) and smaller trees.
- **Weekly refits** cut the Saturday Brier by 0.0009 and the decile calibration error from 0.017 to 0.008 (Tuesday 0.012 to 0.007): a season-old model drifts within the season. They cost about 20x the fitting time (8 minutes for 2015-2018), which a backtest affords; the weekly forecast refits anyway.
- **Saturday's folded roster value:** 2017-2018 Saturday Brier 0.0649 with it, 0.0741 without (refit per season), a 0.009 gain: kept. The 2019-2025 numbers without it are below.
- **The W&B tuning run** `h4g8948l`: Tuesday Brier 0.0960 against the baseline's 0.1434, Saturday 0.0641 against 0.1091; decile calibration error 0.0077 / 0.0079; snap MAE 0.1185 / 0.1160 against 0.1288, range covering 81.1% / 81.2%; target share 0.0469 / 0.0467 against 0.0497; carry share 0.0902 / 0.0885 against 0.0964. Every output won both seasons.

## The pre-registered ship rule and its verdict

Written 2026-11-14, before any reported run. Each output ships on its own, per view. An output that misses its rule is not shown in the tab and goes to Rishi.

| Output | Baseline | Ships when, on 2019-2025 |
|---|---|---|
| Chance he plays | The historical play rate for what the view knows, by position group | Brier below the baseline's pooled **and** in at least 6 of 7 seasons; calibration error by deciles at most 0.02 pooled |
| Snap share if he plays | His mean over his last 2 games | MAE below the baseline's pooled **and** in at least 6 of 7 seasons; the 80% range covers 72-88% pooled |
| Target share, carry share | His rolling share (last 4 games) | MAE below the baseline's pooled **and** in at least 6 of 7 seasons |

**Verdict: every output ships in both views.** The chance he plays: Brier below the baseline's pooled and in 7 of 7 seasons, calibration error 0.0031 (Tuesday) / 0.0061 (Saturday) against the 0.02 limit. Snap share: 7 of 7, range 80.3% / 81.1% (72-88% required). Target and carry share: 7 of 7. Promoted: `availability-model:2019-2025_20261114T224537Z` (alias `production`), `shipped` = all four outputs per view. The same rule (the code's `verdicts`) is applied to every backtest, so the research and tuning runs carry a verdict too.

## Results: walk-forward 2019-2025

Run once, after the settings were fixed: W&B **`hklag5k1`** (group `forecast-lab`, job type `availability-backtest`, `launched-by: agent`, 26 minutes, weekly refits 2015-2025). A smoke run came first: `bv1f42pi` (group `forecast-lab-smoke`, 2025 only). There is **one** reported run (no re-run). Same rows for model and baseline (138,214 player-weeks; the shares only for players who played: 106,737 snap rows, 37,018 target rows, 13,899 carry rows). The play rate is 77.2%; always saying 77.2% scores a Brier of 0.1759.

**Pooled** (source: `backtests/availability/{tuesday,saturday}/summary.json`):

| Output | Baseline | Tuesday: model | Tuesday: baseline | Saturday: model | Saturday: baseline |
|---|---|---|---|---|---|
| Chance he plays, Brier | historical rate | **0.1070** | 0.1485 | **0.0691** | 0.1162 |
| Calibration error, deciles | | **0.0031** | 0.0185 | **0.0061** | 0.0323 |
| Snap share, MAE | his last 2 games | **0.1204** | 0.1309 | **0.1169** | 0.1309 |
| The 80% snap range covers | | 80.3% | | 81.1% | |
| Target share, MAE | rolling 4 games | **0.0461** | 0.0489 | **0.0456** | 0.0489 |
| Carry share, MAE | rolling 4 games | **0.0911** | 0.0959 | **0.0892** | 0.0959 |

Gains over the baseline, pooled: Brier -28% (Tuesday) / -41% (Saturday); snap MAE -8% / -11%; target share -6% / -7%; carry share -5% / -7%.

**Tuesday view, by season** (model / baseline):

| Season | Plays: Brier | Calibration error (deciles) | Snap MAE | 80% range covers | Target share MAE | Carry share MAE |
|---|---|---|---|---|---|---|
| 2019 | 0.0983 / 0.1426 | 0.0065 | 0.1212 / 0.1323 | 79.9% | 0.0473 / 0.0502 | 0.0889 / 0.0930 |
| 2020 | 0.1137 / 0.1533 | 0.0164 | 0.1234 / 0.1336 | 80.0% | 0.0458 / 0.0484 | 0.0963 / 0.1004 |
| 2021 | 0.1246 / 0.1646 | 0.0169 | 0.1268 / 0.1369 | 80.4% | 0.0462 / 0.0489 | 0.0967 / 0.1030 |
| 2022 | 0.1040 / 0.1456 | 0.0236 | 0.1174 / 0.1276 | 80.9% | 0.0463 / 0.0497 | 0.0915 / 0.0948 |
| 2023 | 0.1016 / 0.1438 | 0.0121 | 0.1177 / 0.1280 | 80.8% | 0.0449 / 0.0480 | 0.0864 / 0.0922 |
| 2024 | 0.1020 / 0.1425 | 0.0050 | 0.1178 / 0.1290 | 80.2% | 0.0463 / 0.0491 | 0.0916 / 0.0971 |
| 2025 | 0.1038 / 0.1464 | 0.0063 | 0.1185 / 0.1289 | 79.7% | 0.0459 / 0.0485 | 0.0858 / 0.0905 |
| **Pooled** | **0.1070 / 0.1485** | **0.0031** | **0.1204 / 0.1309** | **80.3%** | **0.0461 / 0.0489** | **0.0911 / 0.0959** |

**Saturday view, by season** (model / baseline):

| Season | Plays: Brier | Calibration error (deciles) | Snap MAE | 80% range covers | Target share MAE | Carry share MAE |
|---|---|---|---|---|---|---|
| 2019 | 0.0640 / 0.1060 | 0.0069 | 0.1185 / 0.1323 | 80.1% | 0.0469 / 0.0502 | 0.0874 / 0.0930 |
| 2020 | 0.0716 / 0.1255 | 0.0086 | 0.1198 / 0.1336 | 80.7% | 0.0452 / 0.0484 | 0.0933 / 0.1004 |
| 2021 | 0.0729 / 0.1274 | 0.0061 | 0.1219 / 0.1369 | 81.2% | 0.0456 / 0.0489 | 0.0939 / 0.1030 |
| 2022 | 0.0682 / 0.1136 | 0.0074 | 0.1142 / 0.1276 | 81.6% | 0.0460 / 0.0497 | 0.0903 / 0.0948 |
| 2023 | 0.0698 / 0.1148 | 0.0051 | 0.1150 / 0.1280 | 81.6% | 0.0446 / 0.0480 | 0.0849 / 0.0922 |
| 2024 | 0.0684 / 0.1123 | 0.0067 | 0.1142 / 0.1290 | 81.4% | 0.0457 / 0.0491 | 0.0898 / 0.0971 |
| 2025 | 0.0685 / 0.1130 | 0.0077 | 0.1151 / 0.1289 | 81.0% | 0.0454 / 0.0485 | 0.0843 / 0.0905 |
| **Pooled** | **0.0691 / 0.1162** | **0.0061** | **0.1169 / 0.1309** | **81.1%** | **0.0456 / 0.0489** | **0.0892 / 0.0959** |

2020-2021 (the COVID-era seasons, with their reserve lists and more practice-squad call-ups) are the hardest for the Tuesday chance; 2022 Tuesday has the worst calibration of a single season (0.024; pooled 0.003). The rule is pooled, and one season of about 20,000 rows has more noise in 10 bins than the pool.

**Like with like.** The share models are fit on absolute error (a median), and the rule's baseline is the rolling mean. The rolling median of the last 4 games does worse than the mean (Tuesday target share 0.0494 against 0.0489; carries 0.0962 against 0.0959), so the comparison doesn't flatter the model.

**By position group** (Brier, model / baseline, pooled; computed from the saved backtest predictions):

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

## Calibration

The reliability table: ten equal-size groups of forecasts, pooled 2019-2025, from the saved predictions.

| Group | Tuesday: forecast | played | baseline's forecast | Saturday: forecast | played | baseline's forecast |
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

The model's forecast agrees with the real play rate to about a point in every group (the largest gap, 3 points, is the second Saturday group: 31.9% against 34.8%). The baseline can't separate the groups: from the 5th group up its forecast hardly moves.

**By this week's report status** (Saturday view; model's average chance against the played rate):

| This week's report | Rows | Played | Model's average | Baseline's | Brier: model / baseline |
|---|---|---|---|---|---|
| Out | 4,624 | 0.0% | 0.1% | 0.4% | 0.0002 / 0.0003 |
| Doubtful | 712 | 1.0% | 1.0% | 5.3% | 0.0099 / 0.0136 |
| Questionable | 6,440 | 66.2% | 64.2% | 66.0% | 0.1825 / 0.2088 |
| On the report, no game status | 14,613 | 91.9% | 91.7% | 93.3% | 0.0369 / 0.0600 |
| Not on the report | 111,825 | 79.6% | 79.7% | 83.4% | 0.0700 / 0.1237 |

**By last week's report status** (Tuesday view): Out 3,750 rows, played 30.4%, model 28.2%, baseline 27.6% (Brier 0.1935 / 0.1992); Doubtful 576, 51.4%, 48.5%, 47.6% (0.2420 / 0.2501); Questionable 6,303, 77.7%, 76.2%, 78.1% (0.1388 / 0.1689); on the report with no game status 14,275, 86.3%, 86.2%, 87.8% (0.0855 / 0.1088); not listed 113,310, 77.7%, 77.8%, 79.9% (0.1044 / 0.1502).

**The snap range by group** (Tuesday / Saturday coverage): QB 75.3% / 79.4%; RB 80.3% / 82.1%; WR 79.9% / 80.7%; TE 80.8% / 81.9%; DL 81.0% / 81.5%; LB 81.8% / 81.9%; CB 78.6% / 79.2%; S 80.0% / 81.0%.

## How much of Saturday rests on this week's roster row

A research run without the folded roster value (W&B **`bikjocnb`**, folder `backtests/availability/saturday-2019-2025`, never used for the decision): Saturday's Brier is **0.0827** against its baseline's 0.1251 (7 of 7 seasons, calibration error 0.0039); snap MAE 0.1177 (range 80.8%), target share 0.0458, carry share 0.0897, all 7 of 7. The play output passes its rule without the feature. The feature is worth 0.0136 Brier (0.0827 -> 0.0691).

**Why the feature exists, and what the backtest can't know.** nflverse publishes week W's roster rows before the week, and the Saturday injury update re-ingests `rosters_weekly`. Comparing each real pre-game snapshot with the final row (universe rows only): Tue 2026-10-06 (week 5) 45 of 1,106 differ; Wed 10-14 (week 6) 29 of 1,035; Thu 10-29 (week 8) 18 of 1,051; Fri 11-06 (week 9) 36 of 1,119; Sat 10-03 (week 4) 13 of 1,180 (8 practice-squad elevations, 3 to reserve, 1 cut, 1 signed to the practice squad); Sun 10-04 (the game-day list, folded away) 0. The backtest uses the **final** row as a stand-in for Saturday's snapshot. With practice-squad status folded into `ACT` (elevations can't leak), about 5 of 1,180 Saturday rows (0.4%) carry information from after a Saturday run. In week 1, the Tuesday view's membership comes from the final week-1 row; on the Tuesdays measured, 5-8 of about 1,100 universe rows (0.5-0.7%) changed membership by game day (no week-1 snapshot exists to measure week 1 itself).

## The leak test

The lead's check: players on the game-day inactive list who were not Out or Doubtful on the report never play (they are inactive), and the model still gives them an average chance of **58-60%** (the baseline: 73-80%). A model that had seen the list would give close to zero.

## Is this number good? (honest notes)

- **Brier 0.107 and 0.069 look small, and the headline gain is large (-28%, -41%), because the baseline is deliberately a fair one that knows the report.** The bar is the baseline's 0.1485 / 0.1162, not the 0.1759 of "always 77%". A fair reading is in the by-status tables: on a Questionable player the model's Saturday Brier is 0.1825 against 0.2088 (-13%), on Out and Doubtful the gain is a rounding error (both near 0), and **the large gain is in the 80% of rows with no game status** (0.0700 against 0.1237). That is the model knowing that a healthy scratch or a player in and out of the lineup isn't a regular. **The baseline knows the report status, the practice line and the roster status by position group, but not how much a player has been playing**, so part of the gain is simply knowing who plays a lot. It is a real skill (a user wants that), but it is not "the injury report's value".
- **A Tuesday forecast for a player listed Out or Doubtful last week is barely better than the baseline** (Brier 0.1935 against 0.1992 for Out): without this week's report there isn't much more to know.
- **Share errors are small but so is the scale.** A snap-share error of 0.12 means 12 points of snap share; the baseline's is 13. The gain (-8% to -11%) is real in 7 of 7 seasons and modest, as in the player model's own yardage gains. A target share is off by about 4.6 points (baseline 4.9), a carry share by about 9 points (baseline 9.6).
- **The ranges hold.** 80.3% and 81.1% pooled, 79.7% to 81.6% in every season. Quarterbacks are the exception (75.3% Tuesday), because a quarterback who plays usually plays every snap.
- **Calibration is checked three ways:** deciles (above), by status (above), and by season (the worst single season is 2022 Tuesday at 0.0236, against the rule's 0.02 for the pooled number: a one-season number in ten bins carries more noise than the pool).

## Reading the W&B charts

**Honest status:** every name below was checked against the code that logs it (`availability_runs.py`: `define_metric`, `run.log`, `wandb.Table`, `wandb.plot.line_series`, `run.summary`), not against the W&B pages: **nobody has opened these runs' charts yet** (agent processes can't use the W&B API). Expect small differences in wording on the pages; the keys and the numbers should match.

All runs are in W&B project `nfl-analytics-engine`, group **`forecast-lab`** (smoke runs: **`forecast-lab-smoke`**), tagged `fl00`, `availability` and `launched-by:<who>` (runs the control room launched also carry `via:control-room`).

### The metrics in one minute

| Metric | What it measures | Better | Our 2019-2025 values |
|---|---|---|---|
| **Brier** (chance he plays) | The average (forecast - what happened)^2 | Lower | Tuesday 0.1070 (baseline 0.1485), Saturday 0.0691 (0.1162); always saying 77.2%: 0.1759 |
| **Calibration error** (`ece_q10`) | The weighted gap between forecast and played rate over ten equal-count groups | Lower; the rule needs at most 0.02 | 0.0031 Tuesday, 0.0061 Saturday (baseline 0.0185 / 0.0323) |
| **MAE** (shares) | The average absolute miss, in share (0-1) | Lower | Snap 0.1204 / 0.1169 (baseline 0.1309); target 0.0461 / 0.0456 (0.0489); carry 0.0911 / 0.0892 (0.0959) |
| **Coverage 80** (snap range) | The share of results inside the 10th-90th percentile | About 0.80 (the rule needs 0.72-0.88) | 0.803 / 0.811 |
| **wins / need** | Seasons the model beat its baseline / seasons needed (6 of 7) | 7 of 7 | 7 of 7 for every output in both views |

### `availability-backtest` runs (run name `availability-backtest-<tag>-<first>-<last>`, tag `report`, `tune` or `smoke`)

- **Config:** `family`, `seasons_scored`, `walk_forward`, `views`, `settings` (the whole settings block), `features` (the exact lists per view), `schema_version`, `dataset_version`, `git_commit`.
- **Live curves** (x-axis `bt/step`, one step per scored regular-season week of 2019-2025 in time order, about 124 points; each view counts its own steps from 1, so the Tuesday and Saturday series share an x-axis): `bt/<view>/play_brier_model_cum` and `bt/<view>/play_brier_baseline_cum` (**the main chart**: the model's line should sit below the baseline's from the first weeks, and its last point is the pooled number, 0.1070 or 0.0691 against 0.1485 or 0.1162); the same with `_week` (one week: noisy). The same pairs for `snap_mae`, `tgt_share_mae` and `carry_share_mae` (`_model_` and `_baseline_`, `_week` and `_cum`). `bt/<view>/snap_coverage_80_cum` and `_week` (should settle near 0.80-0.81). `bt/<view>/season` and `bt/<view>/week` say which week a step is.
- **The reliability chart** `<view>/reliability` (a line chart): the played rate in each of ten equal-count groups ("played (model deciles)") against "perfect", x = the group's average forecast. **Good:** the points on the diagonal. The real ones: Tuesday group 1 forecast 16.6%, played 17.3%; group 10 forecast 97.6%, played 97.6%. (W&B also stores the chart's own data table, `<view>/reliability_table`.)
- **Tables:**
  - `<view>/by_season_rows` (season, output, `n`, `model`, `baseline`, `coverage_80`, `ece_q10`): the by-season tables above. **Good:** `model` below `baseline` on every row.
  - `<view>/verdict_rows` (output, `wins`, `seasons`, `need`, `beats_pooled`, `ece_q10` / `ece_ok`, `coverage_80` / `coverage_ok`, `ships`): the rule's verdicts. **Good:** `ships` true on all four rows (it is, for both views).
  - `<view>/reliability_bins` (`who` = model or baseline, `bin`, `n`, `p_mean`, `y_mean`): the table behind the chart, with the baseline's bins beside it. **Good:** the model's `p_mean` and `y_mean` agree in every bin; the baseline's `p_mean` barely moves from bin 5 up.
  - `<view>/importance_rows` (model, feature, `gain_share`): each model's feature gain in the **last** weekly refit of the run (a share of the model's total gain). For the chance he plays, expect the snap-share features and last week's status near the top; for the Saturday view, this week's report status and practice line.
  - `<view>/at_risk_sample` (up to 2,000 at-risk rows, a seeded sample: season, week, game, team, player, group, report and practice status, last week's status, roster status, `p_play`, `base_p_play`, `played`, the snap range, `base_snap`, `actual_snap`): for reading rows.
- **Summary** (the pooled numbers the card quotes): `<view>/play/{n, brier_model, brier_baseline, brier_base_rate, ece_q10, ece_q10_baseline, play_rate}`, `<view>/snap/{n, mae_model, mae_baseline, coverage_80}`, `<view>/tgt_share/{n, mae_model, mae_baseline}`, `<view>/carry_share/{n, mae_model, mae_baseline}`, and for every output `<view>/<output>/ships` and `/wins`; `seconds`.
- **Artifact** `availability-model` (type `model`; only on the promoted run): the version folder (`model.json` and the boosters, `<view>_play.txt`, `<view>_snap_p10.txt`, `<view>_snap_p50.txt`, `<view>_snap_p90_reflected.txt`, `<view>_tgt_share.txt`, `<view>_carry_share.txt`); aliases `<version>` and `production`; metadata `version` and `shipped`.
- **The runs:** the reported run and promotion **`hklag5k1`**; tuning **`h4g8948l`** (both views, 2017-2018); smoke **`bv1f42pi`** (group `forecast-lab-smoke`, 2025 only); the research run without the folded roster value **`bikjocnb`** (label `saturday-2019-2025`; never read by the tab or the update).

### `availability-predict` runs (`availability-predict-<season>-w<NN>-<view>`; tags `season:S`, the view)

One run per view made. Table `at_risk_rows` (up to 500 at-risk players by lowest chance: game, team, player, group, this week's status and practice line, last week's status, `p_play`, `base_p_play`, `snap_p50`, `base_snap`); summary `rows`, `at_risk`, `games`, `mean_p_play`. **Good:** about 75 rows per game (week 10: 1,073 rows for 14 games) and a `mean_p_play` near the play rate (week 10: 0.78). The first real one is week 10's Tuesday view, **`dp96fylf`** (1,073 rows, 14 games).

### `availability-grade` runs (`availability-grade-<season>-w<NN>`)

Made only when at least one game of the week was graded. Table `scoreboard_rows` (every view, output and position group: `n`, Brier or MAE of the model and the baseline, range coverage); summary `season`, `week`, `games_graded`, `games`, `rows`, `waiting` (rows whose game has no snap counts yet: a later grade of the week fills them and replaces the week's scoreboard rows), `excluded_after_kickoff` (graded rows of games a view made after kickoff, left out of the scoreboard) and, for the "ALL" group, `<view>/<output>/{brier|mae}_model`, `_baseline`, `n`. **Good:** across the season's grade runs, model below baseline in most weeks and the range near 0.80. **None has run yet on a real graded week.**

## Known biases and limits

- **The Saturday view's backtest uses a stand-in** for Saturday's roster snapshot (about 0.4% of rows carry later information) and **has not yet run live on a complete report**. Week 10's curated report was only Friday's early pull (18 game statuses) when the first update ran.
- **Week 1 uses the week-1 roster row for membership** (0.5-0.7% of rows changed membership by game day on the Tuesdays measured).
- **The play baseline lacks a playing-time history** (above): part of the gain is knowing who is a regular.
- **Tuesday can't know this week's report.** A player listed Out last week gets a wide forecast (30% played; the model 28%).
- **Shares don't add up.** Each player's share is its own forecast, so a team's can sum to more or less than 100%. **Who inherits is learned, not a depth chart**: for Minnesota's quarterbacks in week 10 the model gives Max Brosmer 25% -> 70% of the snaps if Kyler Murray sits, and Carson Wentz 94% -> 100%.
- **The snap range is awkward for quarterbacks** (75.3% coverage on Tuesday).
- **2020-2021 are the hardest seasons** (the Tuesday chance); the COVID-era reserve lists and call-ups aren't modelled.
- **Practice-squad call-ups and cuts during the week** are the largest surprise for "not on the report" players; nothing before game day can see them.
- **The first live "Tuesday" view was made late** (the first real update was run on Saturday 2026-11-14): the view reads only what a Tuesday run could read, but it was made on Saturday, and one game (Washington at the Giants) had already kicked off, so its rows are made after kickoff and marked.
- **It forecasts whether a player plays, not how much of the game a hurt player finishes.**
- **Live calibration is frozen between promotions.** Live, the calibration layer (the Platt layer on the chance he plays) and the snap range's shifts are the ones saved at promotion (fit on the last two reported seasons) and stay fixed all season, while the backtest re-fit them every week from earlier weeks. The models themselves are refit every week live, so live calibration can drift a little from the backtest's until the next promotion.

## Outputs

`{NFL_DATA_ROOT}/forecastlab/<S>/week<NN>/availability.parquet` (one row per player x game x view, `AVAIL_SCHEMA`), `inherit.parquet` (`INHERIT_SCHEMA`), `availability.json` (when each view was made, row and game counts, the version, what shipped, every game made after its kickoff); `<S>/availability_scoreboard.parquet` (the live grades, `AVAIL_SCORE_SCHEMA`); `backtests/availability/<view>/predictions.parquet` + `summary.json` (and `<view>-tune`, `<view>-smoke`, `<view>-<first>-<last>` for research runs); `models/forecastlab/availability/<version>/model.json` + the boosters, and `production.json` (`{version, promoted_at, wandb_run, shipped}`). **The kickoff rule:** `predict` never re-makes a row whose game has kicked off; rows already in the file stay as they are, and a game with none is made and listed in `games_after_kickoff` (such rows never count in the scoreboard). `predict` always writes `played` and `actual_*` empty, even for a game already played; only `grade` fills them.

## Verification

- `uv run pytest -q tests/forecastlab -k fl_avail`: a synthetic league (`fl_avail_fixtures.py`) for the universe, the features per view, the leakage rules (the game-day inactive list never read; the Tuesday view never reads week W's report; future rows never move a past row), the models, the ship rule and the runs.
- The lead recomputed the headline numbers from the saved backtest rows (Brier, by-season wins, snap, target and carry MAE, range coverage, decile calibration): they matched. The leak test above.
- The production-unchanged check (D107): `tests/test_production_untouched.py`.

## Versioning

- **`2019-2025_20261114T224537Z`** (2026-11-14, FL00): all four outputs shipped in both views; `production`. Promoted 2026-11-14 23:11 UTC from W&B run `hklag5k1`.
- **Refit after each season:** add the finished season to `forecastlab.availability.report_seasons`, write the ship rule down first, re-run `uv run nfl forecastlab backtest --model availability --promote`, update this card. Tune only on 2017-2018 (or a window fixed before the run), never on the reported weeks.
