# Model card: live-decision models (LD00)

The six models behind the 3rd- and 4th-down bot, and the decision engine that combines them. Built in LD00 (2026-10-10); promoted as the W&B artifact `live-decision-models:production` (version `2010-2025_20261010T054343Z`). Spec: [live-decisions README](../live-decisions/README.md); plain-language guide: [guides/live-decisions.md](../guides/live-decisions.md); decisions D107, D109, D113, D114, D115 (the Sol review's engine fixes).

## In one paragraph

Given a game state (score, clock, down, distance, yard line, timeouts, the pre-game spread and total), the engine returns the win probability after going for it, kicking a field goal and punting, the gap between the best two, and a confidence label. Five models feed it: **win probability** (LightGBM boosted from a logistic base), **yards gained** on 3rd and 4th downs (a 77-outcome distribution), **field goal** (logistic), **punt** and **kickoff** (empirical next-possession distributions); a sixth, **pass**, gives the 3rd-down card's "chance they pass". In a leave-one-season-out backtest over 2014–2025, win probability came within 0.0011 Brier of nflfastR's `vegas_wp` with calibration error 0.0048, the yards-gained model beat its baseline in 12 of 12 seasons and the field-goal model in 10 of 12; the pass model beat nflfastR's `xpass` only in the 6 seasons `xpass` was never trained on, and shipped by Rishi's decision (D114).

## The models

| Model | Target and grain | Method | Rows (2010–2025 fit) | Baseline |
|---|---|---|---|---|
| `wp` | the team with the ball wins the game; every snap with a down (ties dropped: 13 games) | logistic base on 5 smooth terms (spread, spread × time decay, score difference, score difference ÷ time decay, yards to goal × share of the game played) + LightGBM boosted from it (`init_score`), 7 leaves, lr 0.05, 150 rounds, min 200 rows per leaf, monotone + in score difference, its time ratio, spread and its time term | 646,434 | nflfastR `vegas_wp` |
| `gain` | yards gained on a 3rd or 4th down, 77 classes: −10 … +65 yards (clipped) + touchdown; runs, passes (sacks, scrambles), defensive-penalty first downs | LightGBM multiclass, 4 leaves, lr 0.15, 50 rounds, min 300 rows per leaf, L2 10 | 127,575 | conversion rate by distance (1–20, 21+) × era |
| `fg` | the field-goal try is good (blocks count as misses) | logistic: distance/10 + hinges at 30 / 40 / 50 / 60 yards, era, indoors, outdoor wind/10 (unknown → the training median, 8 mph), outdoor cold (< 40 °F); 0 beyond 70 yards | 16,927 | logistic on distance only |
| `punt` | the next snap after a punt: the receiver's spot, a return touchdown, or the kicking team keeping the ball (muffs, penalties) | empirical, Gaussian kernel over the punt's yard line (1 yard, widened until ≥ 400 effective punts) | 37,752 | the season's real outcomes |
| `kickoff` | the next snap after a kickoff following a score | empirical per kickoff era (tb20 / tb25 / tb30 / tb35), onside kicks left out (0.8–1.6% of kickoffs) | 34,587 | the season's real outcomes |
| `pass` | a dropback (scrambles and sacks are passes), downs 1–4 | LightGBM binary, 31 leaves, lr 0.05, 600 rounds; features: down, distance, yard line, score, clocks, timeouts, **our own `wp`**, spread, era, roof, home | 546,142 | nflfastR `xpass` |

Plus the try after a touchdown (`pat`: extra point 95.9% over 2023–2025, two-point ~48%) and **20 bootstrap refits** of `gain` and `fg` for the confidence label.

**Features** (`schema.py`): `wp` uses score difference, `diff_time_ratio` (score ÷ e^(−4 × share played)), spread from the offense's side, `spread_time` (spread × e^(−4 × share played)), game and half seconds, yards to goal, down, distance, both timeouts, home (+1 / 0 / −1), receives the second-half kickoff, overtime, era, indoors (nflfastR's terms plus era, roof and overtime). `gain`: down, distance, yards to goal, spread, total, implied team total, era, indoors. **Eras** (D113): 0 = 2010–14, 1 = 2015–17, 2 = 2018–20, 3 = 2021–23, 4 = 2024+.

## Data and labels (probed on the real tables, 2026-10-10)

- **Rows:** regular season and playoffs, 2010–2025 (2026 is the live season). 31,985 4th downs in 2018–2025 (the plan expected ~32k), 5,750 of them go-for-it attempts. Lines, `wp`, `vegas_wp` and `xpass` cover every run and pass.
- **Conversion label** = gain ≥ distance or an offensive touchdown; it agrees with nflverse's `first_down` on 99.5% of 2018–2025 runs and passes. A defensive-penalty first down gains max(penalty yards, distance), short of the goal line; the **goal-to-go** ones (276 rows) are left out: a first down short of the line to gain fits no class.
- **The second-half kickoff** goes to the team that didn't receive the opening one (2,227 of 2,227 games, 2018–2025).
- **Field goals:** distance = yards to goal + 18 on 90% of kicks; a miss goes to the spot of the kick, 8 yards behind the line (the data fit +8; nfl4th uses +7), or the 20.
- **Kickoffs after a score:** the receiver's mean start moved from its own 23 (2010–15) to 25.4 (2018–23), 29.8 (2024) and 30.6 (2025).
- **Extra points:** 99.3% to 2014, 93–96% since the 2015 move (95.9% in 2023–2025).

## Tuning (every round, honestly)

Shapes and round counts were chosen on a **tuning window the backtest never reports**: fit 2010–2012, score 2013 (and early-stopped there).
- `gain`: 4 leaves beat 7 / 15 / 31 (multiclass log loss 2.848 vs 2.853 / 2.857 / 2.868); lr 0.15 × 23 rounds matched lr 0.05 × 70 (2.8481 vs 2.8482) with a third of the trees, so 0.15 × 50 rounds shipped (D113: the 3rd-down check's speed).
- `pass`: 31 leaves, best at 276 rounds → 600 for ~5× more rows.
- `wp`, **two designs**. The first (trees from scratch, 15 leaves × 320 rounds, rounds scaled up for more rows) was smoke-tested on 2025 before the reported run and showed calibration error 0.036 vs `vegas_wp`'s 0.021. Disclosure: 2025 is a reported season. The fix was then chosen **on the tuning window only**: every snap of a game shares one label, so the effective sample is games, not plays, and more rounds learn game noise. On 2013, the best tree-only setting scored Brier 0.1550, a logistic base alone 0.1541, and the logistic base with boosting 0.1525. The shipped design (7 leaves × 150 rounds, chosen where the curve was flat on three seasons: 0.1525–0.1529 from 25 to 150 rounds) scored 0.1529 with calibration error 0.013, vs 0.1586 / 0.024 for the first design. One reported run followed.
- `fg`: a distance-only logistic lost to the full design in 11 of 12 seasons in the probe. **A kicker's own record** (a shrunken residual of his earlier kicks) helped in 9 of 12 seasons but only by −0.0003 log loss pooled (~0.1%): not shipped (D113).
- The research variant **`gain` + opponent-adjusted EPA ratings** (read-only from `features/team_ratings.parquet`) won 1 of 12 seasons and lost pooled (0.6217 vs 0.6216): not kept.

## Results: leave one season out, 2014–2025

Each held-out season is scored by models fit on the other 15 seasons of 2010–2025 (no bootstraps), W&B [`m70hg3pa`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/m70hg3pa) (final, after the Sol review's engine fixes, D115; the ship decision was made on [`p3y90nh0`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/p3y90nh0), with identical model metrics). **This is not walk-forward:** a held-out 2016 is scored with models that saw 2017–2025. The plan asked for it because the bot is about football situations, not about forecasting a season; LD03 grades it live, week by week.

**Pooled** (486,018 snaps; 96,297 3rd/4th downs; 12,787 field goals; 411,167 runs and passes):

| Model | Ours | Baseline | Rule | Verdict |
|---|---|---|---|---|
| `wp` Brier | 0.1481 | 0.1469 (`vegas_wp`) | ≤ baseline + 0.002 | pass (+0.0011) |
| `wp` calibration error (20 bins) | 0.0048 | 0.0061 | ≤ 0.010 | pass |
| `wp` log loss | 0.4473 | 0.4437 | — | — |
| `gain` conversion log loss | 0.6216 | 0.6261 | ≥ 9 of 12 seasons | **12 / 12** |
| … 3rd downs / 4th-down attempts | 0.6189 / 0.6492 | 0.6233 / 0.6539 | — | — |
| `fg` log loss | 0.3669 | 0.3697 | ≥ 9 of 12 | **10 / 12** (lost 2016, 2020) |
| `pass` log loss | 0.5201 | 0.5197 (`xpass`) | ≥ 9 of 12 | **6 / 12: miss → shipped by Rishi (D114)** |
| `pass` AUC | 0.7949 | 0.7962 | — | — |
| punt: receiver's mean start (yards to goal) | 75.07 | 75.43 actual | — | sd 14.7 vs 14.5 |
| kickoff: receiver's mean start | 75.14 | 74.82 actual | — | sd 6.4 vs 6.6 |

**By season** (log loss unless marked):

| Season | wp Brier / vegas | wp calib. | gain / base | fg / base | pass / xpass |
|---|---|---|---|---|---|
| 2014 | 0.1467 / 0.1461 | 0.016 | 0.6268 / 0.6322 | 0.3668 / 0.3717 | 0.5180 / 0.5106 |
| 2015 | 0.1573 / 0.1546 | 0.021 | 0.6269 / 0.6303 | 0.3620 / 0.3625 | 0.5128 / 0.5067 |
| 2016 | 0.1535 / 0.1518 | 0.014 | 0.6250 / 0.6307 | 0.3626 / 0.3599 | 0.5173 / 0.5103 |
| 2017 | 0.1321 / 0.1308 | 0.032 | 0.6207 / 0.6252 | 0.3897 / 0.3934 | 0.5116 / 0.5061 |
| 2018 | 0.1419 / 0.1408 | 0.018 | 0.6179 / 0.6220 | 0.3618 / 0.3628 | 0.5193 / 0.5117 |
| 2019 | 0.1414 / 0.1392 | 0.015 | 0.6222 / 0.6273 | 0.3955 / 0.3993 | 0.5106 / 0.5029 |
| 2020 | 0.1386 / 0.1366 | 0.014 | 0.6269 / 0.6313 | 0.3698 / 0.3697 | **0.5282 / 0.5300** |
| 2021 | 0.1460 / 0.1447 | 0.027 | 0.6202 / 0.6240 | 0.3541 / 0.3547 | **0.5211 / 0.5248** |
| 2022 | 0.1703 / 0.1712 | 0.032 | 0.6173 / 0.6210 | 0.3583 / 0.3643 | **0.5290 / 0.5356** |
| 2023 | 0.1519 / 0.1504 | 0.018 | 0.6135 / 0.6179 | 0.3455 / 0.3514 | **0.5234 / 0.5285** |
| 2024 | 0.1366 / 0.1366 | 0.031 | 0.6188 / 0.6226 | 0.3786 / 0.3814 | **0.5229 / 0.5309** |
| 2025 | 0.1593 / 0.1593 | 0.021 | 0.6237 / 0.6294 | 0.3594 / 0.3663 | **0.5261 / 0.5357** |

A single season's calibration error is noisy (every snap of a game shares one outcome: `vegas_wp` itself ranges 0.014–0.033); pooled over 12 seasons it's 0.0048.

### Win probability by quarter and score state (pooled)

| Group | Snaps | Win rate | Ours | `vegas_wp` | Brier ours / vegas | Calibration ours / vegas |
|---|---|---|---|---|---|---|
| Q1 | 108,745 | 0.515 | 0.518 | 0.516 | 0.2000 / 0.1986 | 0.011 / 0.008 |
| Q2 | 132,941 | 0.515 | 0.515 | 0.510 | 0.1692 / 0.1681 | 0.011 / 0.013 |
| Q3 | 109,761 | 0.509 | 0.506 | 0.507 | 0.1353 / 0.1349 | 0.008 / 0.009 |
| Q4 | 131,746 | 0.490 | 0.489 | 0.489 | 0.0931 / 0.0914 | 0.004 / 0.004 |
| OT | 2,825 | 0.638 | 0.617 | 0.588 | 0.2118 / 0.2220 | 0.044 / 0.076 |
| Q2 last 2:00 | 36,537 | 0.517 | 0.519 | 0.514 | 0.1532 / 0.1521 | 0.017 / 0.014 |
| Q4 last 2:00 | 25,236 | 0.466 | 0.462 | 0.466 | 0.0796 / 0.0745 | 0.018 / 0.013 |
| tied | 91,774 | 0.555 | 0.557 | 0.553 | 0.2126 / 0.2110 | 0.011 / 0.009 |
| 1–8 points | 225,040 | 0.539 | 0.538 | 0.537 | 0.1825 / 0.1812 | 0.009 / 0.011 |
| 9+ points | 169,204 | 0.440 | 0.439 | 0.438 | 0.0672 / 0.0666 | 0.005 / 0.007 |

The last two minutes of the game are where WP models usually break: ours is 0.005 Brier behind `vegas_wp` there (the engine's kneel-out and clock rules cover the most decision-relevant part).

### Conversion by distance (the selection bias)

| To go | 3rd downs: actual / ours / baseline | 4th-down attempts: n, actual / ours / baseline |
|---|---|---|
| 1 | 0.695 / 0.697 / 0.691 | 3,243: 0.685 / 0.685 / 0.693 |
| 2 | 0.584 / 0.585 / 0.584 | 1,167: 0.610 / 0.573 / 0.586 |
| 3 | 0.534 / 0.538 / 0.534 | 796: 0.544 / 0.536 / 0.533 |
| 4 | 0.503 / 0.503 / 0.505 | 659: 0.548 / 0.510 / 0.505 |
| 5 | 0.459 / 0.469 / 0.463 | 554: 0.532 / 0.479 / 0.463 |
| 7 | 0.396 / 0.397 / 0.396 | 266: 0.470 / 0.400 / 0.396 |
| 10 | 0.309 / 0.306 / 0.310 | 425: 0.332 / 0.309 / 0.310 |
| 15 | 0.162 / 0.164 / 0.166 | 87: 0.264 / 0.172 / 0.164 |

On 3rd downs the model matches the data. On attempted 4th downs it says less than what happened, increasingly so at longer distances: teams go when they expect to convert (and fakes and desperation plays sit in the long-distance rows). That's the selection bias the plan warned about; the bot's chances describe an average offense in an average 4th-down spot.

### Field goals by distance (pooled)

| Distance | Kicks | Made | Ours | Distance-only |
|---|---|---|---|---|
| ≤ 29 | 2,992 | 0.977 | 0.977 | 0.967 |
| 30–39 | 3,660 | 0.924 | 0.916 | 0.916 |
| 40–49 | 3,780 | 0.784 | 0.795 | 0.801 |
| 50–54 | 1,657 | 0.702 | 0.686 | 0.661 |
| 55+ | 698 | 0.560 | 0.571 | 0.529 |

### The decisions: coaches vs the bot

Every real 4th down of each held-out season (3,596–4,222 a season; 1 failed the input checks) through the engine with that fold's models:

| | 2014 | 2017 | 2020 | 2023 | 2025 | Pooled |
|---|---|---|---|---|---|---|
| Coaches go | 12.0% | 12.7% | 19.1% | 19.7% | 23.3% | 17.0% |
| Bot says go | 52.4% | 48.9% | 56.9% | 48.8% | 54.4% | 51.4% |
| Agree | 53.2% | 56.2% | 56.4% | 62.5% | 62.5% | 58.4% |
| Within 1 point (toss-up) | 48.5% | 47.3% | 48.1% | 44.8% | 43.8% | 45.8% |
| Coaches' cost (expected wins) | 25.1 | 24.3 | 20.7 | 20.7 | 19.5 | 271 in 12 seasons |

Coaches' go rate has nearly doubled since 2014, toward the bot's, and agreement has risen from 53% to 63%: the sanity check the plan asked for. The **cost** is the bot's own accounting (win probability of its call minus the coach's), so it's an upper bound on what coaches really gave away.

## Live calibration (LD03): the bot on real plays

The decision review (`nfl live review`, the Game day tab of a finished week; [guide §10](../guides/live-decisions.md#10-the-decision-review-ld03)) checks the promoted bundle on each season's real plays as the weeks come in: its win probability on every snap of finished games, its conversion chances on 3rd downs and attempted 4th downs, its make chances on field goals. **2026 is the first out-of-sample season** (the bundle was fit on 2010–2025); 2025 is shown only as the in-sample example.

| Through | Snaps (games) | WP Brier: bot / `vegas_wp` | Calibration error (10 bins): bot / `vegas_wp` | 3rd downs: converted / bot | Attempted 4th downs: converted / bot | Field goals: made / bot |
|---|---|---|---|---|---|---|
| **2026 week 4** (out of sample) | 9,368 (64) | 0.1680 / 0.1659 | 0.035 / 0.026 | 41.6% / 42.3% (1,644) | 57.0% / 54.4% (172) | 85.7% / 83.3% (259) |
| 2025, whole season (**in-sample**) | 40,610 (284) | 0.1586 / 0.1593 | 0.015 / 0.019 | 41.8% / 42.8% (7,376) | 56.4% / 55.3% (957) | 85.6% / 84.6% (1,140) |

How to read it:
- **Win probability:** four weeks is 64 games, and a game's snaps rise and fall together, so a 0.035 error after four weeks isn't yet a signal (`vegas_wp` is at 0.026 on the same snaps; one held-out season in the backtest ranged 0.014–0.032 on 20 bins). Watch the bins with the most snaps (0–10% and 90–100%) and whether the gap to `vegas_wp` keeps shrinking as weeks are added. A season-long error above the backtest's range, or Brier more than 0.005 behind `vegas_wp`, would be the sign to re-check.
- **Conversion:** on 3rd downs the bot is within a point of what happened (2026 and 2025). On attempted 4th downs (real attempts only: no penalty first downs before the snap, no wiped kicks) it says less than what happened (by 1–3 points): **selection bias**, coaches go when they like their chances, exactly as the backtest showed (above). The flip side is the one that matters for the calls: on 4th downs coaches *don't* try, the bot's chances are probably a little high, which is part of why it says go so often.
- **Field goals:** level within 1–2 points overall; small bands move a lot week to week (75 kicks of 50+ yards so far in 2026: 68% made vs 66% expected).

The trend is logged every week to W&B (`decision-review` runs, below) and drawn in the Game day tab's Season view.

## Is this good? (honest notes)

- **`vegas_wp` and `xpass` were fit on most of our test seasons** (nflfastR trained its models on 2000s–2019 data), so they're partly in-sample opponents. The pass model's 6 / 12 is exactly that: it loses all six seasons inside `xpass`'s 2006–2019 training data and wins all six after it. Win probability is within 0.0011 Brier of `vegas_wp` pooled, and level with it on 2024 and 2025, seasons `vegas_wp` never saw.
- **The bot is aggressive.** It recommends going for it on 51% of 4th downs: 96% of 4th & 1, 73% of 4th & 4–5, 52% of 4th & 6–7, 33% of 4th & 8–10. Even deep in its own territory it often says go on 4th & 4–5 when confident. A cross-check that values the same futures with nflfastR's expected points is *more* aggressive (57% vs 50% on 2023–24 close games), so the win-probability model isn't the cause. The driver is the conversion chance on 4th downs, which sits between the 3rd-down rate and the attempted-4th-down rate (above): the standard treatment (nfl4th does the same), but an average team in a random 4th & 5 may convert less than this. LD03 checks the bot's chances against real attempts every week.
- **Close calls are close:** 46% of real 4th downs are within 1 win-probability point, in line with Brill, Yurko & Wyner (44% for 2018–22). The label says so.

## Confidence, the bootstrap gate and speed

- 20 bootstrap refits of `gain` and `fg` (each on a resampled copy of the training rows) re-score a call. **Confident** ≥ 90% agree, **Lean** 60–90%, **Toss-up** < 60% or a gap < 1 point.
- **The gate (D113):** only calls closer than 5 points are bootstrapped. On 1,498 real 2025 4th downs no wider call flipped (169 of 169 had a share of 1.0); shares fell below 0.9 only under 2 points (12 cases) and once at 3–5 points (0.88). The promoted bundle's own check (`checks.json`): 398 real 2025 4th downs, 35 wider than 5 points, none below Confident (worst share 1.0). With the shipped models, every 2025 4th down (3,995): Confident 55%, Toss-up 44%, Lean 0.3%; 92% bootstrapped.
- **Speed** (on this machine, idle; the promoted bundle's `checks.json`): a full 3rd-down check, bootstraps included, takes a median **14.6 ms** for 3rd & 6 (16 table rows) and **19.6 ms** for 3rd & 10 (20 rows): under the 20 ms budget, the second only just (a 3rd & 15, 25 rows, takes ~27 ms). A single 4th-down call takes 1–6 ms. The cost is the bootstrap gain predictions (3,850 small trees per model), memory-bound: more threads don't help past 4.

## Reading the W&B charts

### The metrics in one minute

| Metric | What it is | Good looks like (this backtest) |
|---|---|---|
| Brier | mean squared error of a probability | wp 0.148; `vegas_wp` 0.147 |
| log loss | the penalty for confident misses | lower is better; compare model vs baseline on the same rows |
| calibration error (ECE) | the count-weighted gap between predicted and actual in 20 equal bins | ≤ 0.010 pooled; one season is noisy (0.014–0.032) |
| AUC | how well a probability ranks passes above runs | 0.795 |
| go rate | share of 4th downs where the coach / the bot goes | coaches 12% → 23%, bot ~50% |
| WP cost | the bot's best minus the coach's choice, in win probability | ~0.6 points per 4th down |

### `live-backtest` runs (group `live-decisions`; `live-backtest-main`, `-ratings`, `-smoke`)

- **Live curves** (x-axis `bt/season`, one point per held-out season, filled as each fold finishes): `bt/wp_brier` vs `bt/wp_brier_vegas` (the two lines should overlap; ours a hair above), `bt/wp_ece` vs `bt/wp_ece_vegas`, `bt/wp_logloss*`; `bt/gain_ll` vs `bt/gain_ll_base` (ours below every season), `bt/gain_ll_3rd*`, `bt/gain_ll_4th*`, `bt/gain_brier*`, `bt/gain_ece*`, `bt/gain_mll` (the 77-class log loss, ~2.85); `bt/fg_ll` vs `bt/fg_ll_base`; `bt/pass_ll` vs `bt/pass_ll_xpass` (above to 2019, below from 2020: the `xpass` training window), `bt/pass_auc*`; `bt/punt_start_pred` vs `bt/punt_start_actual` and the `sd`, return-TD and kept-ball pairs, the same for `bt/kick_*`; `bt/go_rate_coach` vs `bt/go_rate_bot`, `bt/agree`, `bt/toss_up_share`, `bt/wp_cost_mean`, `bt/wp_cost_total`; `bt/gain_wins_cum`, `bt/fg_wins_cum`, `bt/pass_wins_cum` (the seasons won so far: the ship rule needs 9 at the end); `bt/fit_seconds`, `bt/fold_seconds` (~13 s per fold).
- **Charts:** `wp/calibration` (20 bins: ours, `vegas_wp` and the diagonal; should hug the diagonal), `gain/conversion_by_distance` (actual, model and baseline by distance 1–15, for 3rd downs and for 4th-down attempts: smooth from ~0.69 at 1 yard to ~0.31 at 10; the 4th-down actual line sits above the model, the selection bias), `decisions/go_rate_vs_bot` (by season: coaches rising toward the bot).
- **Tables:** `wp/calibration_table`, `wp/calibration_by_quarter` (20 bins per quarter, OT and the last two minutes), `wp/by_quarter`, `wp/by_score_state`, `gain/conversion_by_distance_table`, `fg/by_distance`, `decisions/go_rate_vs_bot_table`, `decisions/by_season`, `decisions/costliest` (the 25 coach calls the bot rates worst), `backtest/by_season` (every metric, one row per season).
- **Summary:** `pooled/<metric>`; `ship/wp_pass`, `ship/gain_seasons_won` (12), `ship/fg_seasons_won` (10), `ship/pass_seasons_won` (6), `ship/<model>_pass`, `ship/rules_1_4_pass` (false: the pass model), `ship/full_backtest`, `ship/all_pass`; the ratings run adds `ship/ratings_*`.
- **Runs:** main [`m70hg3pa`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/m70hg3pa) (final, after the Sol fixes), [`p3y90nh0`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/p3y90nh0) (the ship decision: identical model metrics), ratings [`87txkdh1`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/87txkdh1); superseded by the label fix: `t0vj5glj` (main, same verdict) and `s01653rd` (ratings); one ratings attempt crashed before the decisions were skipped for that variant.

### `live-train` runs (`live-train-<version>`)

- **Training curves:** `wp/train_binary_logloss`, `gain/train_multi_logloss`, `pass/train_binary_logloss` against `wp/iter`, `gain/iter`, `pass/iter` (falling smoothly; no validation curve: the backtest is the validation).
- **Table** `checks/calls` (the three hand-made 4th downs: state, the call it must get, the call it got, gap, label, ms). **Summary:** `rows`, `seconds`, `checks_ok`, `timing_ok`, `timing/0_median_ms`, `timing/1_median_ms`, `version`.
- **Artifact** `live-decision-models` (type `model`): the whole bundle folder; aliases `<version>` and, with `--promote`, `production`; description = this card.
- **Run:** [`hxmgioka`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/hxmgioka) (version `2010-2025_20261010T054343Z`, promoted 2026-10-10 05:45 UTC).

### `decision-review` runs (`decision-review-<season>-w<NN>`, LD03)

One run per finished week (`nfl live review --wandb`), with the season through that week: the live record of this card's models. **Charts:** `season/wp_calibration` (the bot and `vegas_wp` against the diagonal, 10 bins: should hug it more closely week by week), `season/conversion` (by distance 1–10+: 3rd downs actual vs bot should overlap; the 4th-down attempts' actual line sits above the bot's, the selection bias), `season/go_rate_by_week` (coaches far below the bot). **Tables:** `review/decisions`, `review/costliest`, `season/leaderboard`, `season/wp_calibration_bins`, `season/conversion_rows`, `season/fg_table`, `season/trend`. **Scalars** (`week/*`, `season/*`; chart them across runs with `week` on the x-axis): `season/wp_brier` vs `season/wp_brier_vegas`, `season/wp_ece` vs `season/wp_ece_vegas`, `season/conv3_actual` vs `_pred`, `season/conv4_actual` vs `_pred`, `season/fg_made` vs `_pred`. Runs on a training season carry the tag `in-sample`. Every panel: [W&B guide §6.8](../guides/weights-and-biases.md#68-decision-review-group-live-decisions-job-type-decision-review-ld03).

## Known biases and limits

- **Aggressive on 4th downs** (above); **average team** (the spread is the only strength input; a team's own kicker or short-yardage offense is LD02's context panel).
- **Selection bias** in 4th-down conversion (3rd and 4th downs trained together with `down` as a feature, as nfl4th does).
- **Leave-one-season-out, not walk-forward:** a held-out season's fit saw later seasons. Fine for situations; the live record (LD03, above) is the real test, and 2026 is its first season.
- **Overtime:** one win-probability model; a tied playoff OT period goes on into a new 15-minute period (D115), but the 2025 "both teams possess" rule isn't modelled explicitly (2,825 OT snaps in the backtest; calibration 0.044, better than `vegas_wp`'s 0.076).
- **Not advised:** two-point tries, timeouts; onside kicks are left out of the kickoff model; a safety's free kick is a fixed start (60 yards to goal).
- **The last two minutes:** win probability is 0.005 Brier behind `vegas_wp` there; the kneel-out and clock rules handle the end of a half.

## Outputs

`{NFL_DATA_ROOT}/models/live-decisions/<version>/` (`wp.txt`, `wp_base.json`, `gain.txt`, `pass.txt`, `gain_boot/00–19.txt`, `fg.json`, `punt.json`, `kickoff.json`, `pat.json`, `meta.json`, `checks.json`) and `production.json` (the promoted version); backtests in `runs/backtests/live-decisions/<main|ratings>/`.

## Verification

- `uv run pytest -q tests/live` (unit tests with stub models: the engine's rules worked by hand, monotonicity, leakage in `fit_all`, save / load), `tests/live/test_live_backtest.py` (the harness, the ship rules), `-m integration tests/live/test_bundle_integration.py` (the promoted bundle: the hand-made calls, timing, meta).
- The production-unchanged check (D107): `tests/test_production_untouched.py`, and a rehearsal of 2026 week 5 pinned to its published moment that reproduced its games (30 rows), players (4,542) and teams (120) exactly.

## Versioning

- **`2010-2025_20261010T054343Z`** (2026-10-10, LD00): fit 2010–2025, 20 bootstraps; `production`. Retrain after each season (add the finished season to `live.train_seasons`, re-run the backtest, then `nfl live train --promote`). A new promoted bundle changes every stored decision review's stamp: the control room rebuilds a week on its next open, and `nfl live review --season S --weeks 1-N` re-writes the files (the season just trained on becomes in-sample).
