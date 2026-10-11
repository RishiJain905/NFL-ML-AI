# Model card: the play-calling tendency forecast (PC02)

Each team's play-calling **rates in its next game, against that opponent**: pass rate over expected, dropback rate, deep shots, runs up the middle and outside, play-action, screens and motion on offense; blitz rate, pass rushers and heavy boxes on defense. Built in PC02 (2026-10-11); the W&B artifact `playcall-model` (its own `production` alias), version **`2019-2025_20261011T230330Z`**. Spec: [PC02 phase file](../play-calling/PC02-tendency-forecast.md), [Play calling README](../play-calling/README.md); plain-language guide: [guides/play-calling.md §9](../guides/play-calling.md#9-the-forecast-pc02); decisions D129 (as built), D130 (the ship decision), D131 (the review fixes); the W&B runs in [W&B guide §6.10](../guides/weights-and-biases.md#610-the-play-calling-forecast-group-play-calling-pc02).

## In one paragraph

For every game of a week, each team's rate on 11 play-calling targets is forecast as of that week (only what a Tuesday-night run could see) by an **empirical-Bayes** model on the rate scale: the team's own rate this season, shrunk toward last season (less so after a coordinator change) and the league; **plus** what this opponent allows beyond the teams it faced (for run direction: what this **stadium's stats crew** charts); **plus**, for PROE and dropback rate, the **game script** from the pre-game spread (scored through LD00's `pass` model) and the total line. In a walk-forward backtest over 2019–2025 (FTN targets 2022–2025) it beat the team's season-to-date rate on game-level error in **every season for every target** (11 of 11 shipped by a rule fixed before the run), by 3–28%: most in weeks 1–4, least by week 11+. The gains are real and modest: one game's rate is mostly noise (about 60 plays), so a forecast's 80% range is wide (about ±13 points for dropback rate, ±11 for PROE) and a forecast 1–2 points from the season's rate is inside it.

## The targets (probed on the real tables, 2026-10-11)

| Target | What | Per | Source, seasons | Second term | Script |
|---|---|---|---|---|---|
| `proe` | pass rate over expected, neutral situations (WP 20–80%, downs 1–3, not the last 2 minutes) | neutral play | play-by-play, 2019–2025 | opponent | yes |
| `dropback_rate` | dropbacks (passes, sacks, scrambles), every situation | play | play-by-play | opponent | yes |
| `deep_shot_rate` | passes thrown 20+ air yards | pass attempt | play-by-play | opponent | |
| `run_middle` | designed runs up the middle | designed run with a direction | play-by-play | **venue** (the home stats crew) | |
| `run_outside` | designed runs to the left or right end (outside the tackles) | designed run with a direction | play-by-play | **venue** | |
| `play_action_rate` | play-action | dropback | FTN, 2022–2025 | opponent | |
| `screen_rate` | screens | dropback | FTN | opponent | |
| `motion_rate` | pre-snap motion | play | FTN | opponent | |
| `blitz_rate` (defense) | 1+ blitzer (FTN's charting) | dropback faced | FTN | opponent (the offense) | |
| `rushers_avg` (defense) | pass rushers | dropback faced | FTN | opponent | |
| `heavy_box_rate` (defense) | 8+ defenders in the box | play faced | FTN | opponent | |

Grain: one row per team-game (regular season + playoffs), the PC00 team-game sums (`team_game_tendencies`): `k` (the count, or the sum for a mean) over `n` plays. Defense targets are the defense's own calls; their "opponent" is the offense (how much defenses blitz a given offense beyond their own habits).

**How noisy are they?** (2019–2025; FTN 2022–2025; SD of a team-game's rate split into sampling noise, real week-to-week movement and real differences between teams.)

| Target | SD of a game's rate | of it: sampling noise | week to week | between teams | season-to-season r (same / new coordinator) | 1st half → 2nd half r |
|---|---|---|---|---|---|---|
| PROE (neutral) | 10.0 pts | 8.8 (78% of the variance) | 2.2 | 4.2 | 0.50 (0.57 / 0.40) | 0.57 |
| dropback rate (all) | 10.6 | 6.2 (35%) | 7.6 | 3.9 | 0.45 (0.55 / 0.33) | 0.41 |
| deep shots | 6.5 | 5.7 (78%) | 2.4 | 1.8 | 0.29 | 0.41 |
| runs up the middle | 16.7 | 9.3 (31%) | 12.1 | 6.9 | 0.70 | 0.63 |
| runs outside | 13.0 | 8.9 (47%) | 7.5 | 5.7 | 0.51 | 0.65 |
| play-action | 10.3 | 7.0 (46%) | 6.3 | 4.1 | 0.31 (0.51 / −0.07) | 0.53 |
| screens | 5.7 | 4.8 (71%) | 2.3 | 2.0 | 0.15 | 0.46 |
| motion | 16.3 | 6.3 (15%) | 9.4 | 11.8 | 0.75 (0.84 / 0.59) | 0.87 |
| blitz | 12.7 | 7.5 (34%) | 7.9 | 6.6 | 0.60 (0.81 / 0.25) | 0.70 |
| pass rushers | 0.21 | 0.12 (31%) | 0.14 | 0.10 | 0.50 (0.70 / 0.19) | 0.64 |
| heavy box | 8.1 | 3.4 (17%) | 6.7 | 3.1 | 0.32 (0.49 / 0.05) | 0.48 |

No target was dropped as "almost all noise": PROE, deep shots and screens are ~75% sampling noise in one game, but the backtest still beats season-to-date on them (below), and the pages show the range. **A coordinator change breaks the carry-over** (blitz r 0.81 → 0.25, play-action 0.51 → −0.07), which is why the prior has two carry-over weights. Dropback rate in neutral situations was 90% noise and is covered by PROE, so it isn't a target; light boxes and box count barely carry over (r 0.12–0.17) and aren't targets either.

## The model, step by step

For team T facing O in week W of season S (everything as of W):

1. **The league rate** L: this season's league rate on games before W, blended with last season's (worth 2,000 plays, about a week of the league).
2. **The team's prior** m0 = L + ρ × (T's last-season rate − last season's league), with ρ = `rho_same`, or `rho_new` when T's head coach or the seed's coordinator on that side changed since last season (`config/coaching_seed.csv` OC / DC, `config/head_coach_fixes.csv` for the head coach).
3. **The team term** = (κ × m0 + k) / (κ + n), k / n = T's count and plays before W (a beta-binomial posterior mean: no games → the prior; many → T's own rate). After a **mid-season head-coach change**, games before it count half (fixed, `pre_change_weight`).
4. **The opponent's effect**: for each game of O before W, what the team facing O did beyond its own team term (k − n × its team term), summed and shrunk: (κ_opp × ρ_opp × last season's effect + Σ residuals) / (κ_opp + Σ n). Using each team's own rate is the "adjusted for the offenses it faced" part: a defense that met three pass-happy offenses isn't charged for their passing. **For run direction the group is the venue**, not the opponent: the residuals of both teams' runs at that stadium (the home team's stats crew charts run location; below).
5. **The game script** (PROE and dropback rate): `b_spread` × (the league's expected dropback rate at this game's spread − its average) + `b_total` × (total line − the league's average total) / 10. The expected rate at a spread: the plays of the last three seasons, weighted by how close their game's spread was (Gaussian kernel, 2.5 points), scored through **LD00's `pass` model** (its own `wp` model as an input); for PROE, the pass model minus `xpass` on neutral plays. Every observed game is script-adjusted the same way before it enters the team term, so a favourite's season of favourite games isn't counted twice.
6. **The forecast** = team term + λ × opponent effect + script, clipped to the target's range.
7. **The range**: the variance of a game's rate around the forecast = c × u × (1/n + 1/(κ + n so far)) + b (u = f(1 − f) for a share, 1 for a mean): the game's sampling noise, the posterior's own uncertainty (the beta posterior's variance) and real game-to-game movement b; then the 10th and 90th percentiles of the tuning games' errors in those units (a skewed rate gets a lopsided range). n = the team's average plays per game so far.

**Baselines** (the phase file's four, on the same rows): **b1** the league rate (for dropback rate plus `xpass` on the expected game script: the same kernel over `xpass`); **b2** the team's season-to-date rate (in week 1, last season's; with neither, the league's); **b3** the shrunk team rate (steps 1–3, its own fit); **b4** b3 + the opponent (steps 1–4, its own fit). For targets without a script term the model is b4.

**Not inputs:** participation data (research only, D14); the production models (D107); anything from week W or later.

## Data and availability

- **As of a week** = what a run on the Tuesday night before it could see (D123): play-by-play of every earlier game; FTN once 48 hours have passed since a game's kickoff (Monday night's FTN counts from the next week); a game without a kickoff time holds its FTN a week. The forecast's sums are built from the team-game rows with that rule; on the real 2025 data they equal PC00's as-of tables exactly (the test worker's integration test).
- **Lines:** backtests use nflverse's closing spread and total (leakage rule 5: optimistic); a live forecast uses the lines on hand when it's made (nflverse, then the Odds API median, then ESPN). b4 (no script) is the "no market" variant: the script term's gain is the part the closing line could flatter, about 0.04 points on PROE and dropback rate.
- **The script curves** are rebuilt per season: for a finished season LD00's `wp` + `pass` refit on 2010 .. S−1 with LD00's own code and settings (~4 s each), for the live season the promoted LD00 bundle (fit 2010–2025). At a 7-point spread the 2025 curve expects 62.7% dropbacks for the underdog and 58.8% for the favourite.

## Tuning

- **Where:** every parameter fit on **2019–2023** (FTN targets 2022–2023), by minimizing the per-play squared error (for a share, the per-play Brier score), L-BFGS-B from two starts; b3, b4 and the model each fit their own. Then fixed for every reported season (2024–2025 never touched by the fit). Production refits on all reported seasons (2019–2025, FTN 2022–2025).
- **The venue choice** (pre-registered, D130): the backtest's first pass showed run direction gaining far more from the "opponent" than anything else; a probe showed why: visiting teams' middle-run gaps at a stadium correlate 0.80 between halves of a season, while a defense's effect from its away games alone is about zero (r −0.02); the stadium effect's SD is 9.3 points on middle runs, 5.1 on outside runs. By a rule written down before the venue variant was scored, run direction takes whichever context has the lower tuning-window loss: the venue (middle runs 0.0146 vs 0.0225, outside 0.0122 vs 0.0138). With the venue in, the team's own carry-over almost vanishes (ρ ≈ 0.15): run direction as charted is mostly the crew.

**The fitted parameters (tuning window; production refit in `params.json`):**

| Target | κ | ρ same / new | κ_opp | ρ_opp | λ | b_spread | b_total |
|---|---|---|---|---|---|---|---|
| PROE | 199 | 0.54 / 0.35 | 449 | 0.36 | 0.91 | 0.21 | 0.045 |
| dropback rate | 490 | 0.43 / 0.35 | 834 | 0.40 | 0.76 | 0.17 | 0.034 |
| deep shots | 528 | 0.31 / 0.31 | 977 | 0.41 | 0.78 | | |
| runs up the middle (venue) | 1,093 | 0.15 / 0.08 | 80 | 0.99 | 1.04 | | |
| runs outside (venue) | 269 | 0.13 / 0.10 | 179 | 0.89 | 1.03 | | |
| play-action | 238 | 0.53 / 0.09 | 110 | 0.36 | 0.45 | | |
| screens | 250 | 0.08 / 0.20 | 229 | 0.30 | 0.64 | | |
| motion | 95 | 0.91 / 0.62 | 457 | 0.36 | 0.26 | | |
| blitz | 125 | 1.00 / 0.13 | 2,311 | 0.16 | 2.66 | | |
| pass rushers | 268 | 0.74 / 0.00 | 3,721 | 0.09 | 4.29 | | |
| heavy box | 564 | 0.61 / 0.22 | 1,104 | 0.43 | 1.34 | | |

How to read them: κ is how many of the team's own plays it takes to outweigh the prior (blitz: 125 dropbacks, about 3–4 games); ρ is how much of last season's gap to the league survives (blitz with the same DC: all of it; with a new DC: 13%); λ with κ_opp sets how far an opponent moves a forecast (their ratio trades off: blitz's large κ_opp and λ mean "a little per play, adding up over a season").

## Results: walk-forward 2019–2025

Every team-game forecast as of its week; game-level MAE (each game counts once) in points (rushers in rushers); lower is better. W&B [`5zttxdnw`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/5zttxdnw) (the final, after the Sol review's fixes, D131: the same numbers as the pre-review [`610301ws`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/610301ws); run direction with the venue). The first reported run, [`mx6acp91`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/mx6acp91), had run direction with the opponent (middle 11.71, outside 9.35; every other number identical).

| Target | Games | **Forecast** | Season to date (b2) | Shrunk (b3) | + opponent (b4) | League (b1) | vs b2 | Seasons won | 80% range covers |
|---|---|---|---|---|---|---|---|---|---|
| PROE | 3,919 | **7.21** | 7.69 | 7.31 | 7.24 | 7.83 | −6.3% | 7 / 7 | 79.7% |
| dropback rate | 3,920 | **7.98** | 8.58 | 8.16 | 8.02 | 8.39 | −7.0% | 7 / 7 | 80.4% |
| deep shots | 3,920 | **4.94** | 5.25 | 4.96 | 4.94 | 5.05 | −5.9% | 7 / 7 | 79.7% |
| runs up the middle | 3,920 | **9.53** | 13.26 | 12.45 | 9.53 | 13.42 | −28.1% | 7 / 7 | 81.1% |
| runs outside | 3,920 | **8.99** | 10.14 | 9.72 | 8.99 | 10.44 | −11.3% | 7 / 7 | 79.5% |
| play-action | 2,241 | **7.62** | 8.07 | 7.69 | 7.62 | 8.04 | −5.6% | 4 / 4 | 81.4% |
| screens | 2,241 | **4.28** | 4.49 | 4.30 | 4.28 | 4.40 | −4.7% | 4 / 4 | 78.9% |
| motion | 2,246 | **9.65** | 9.97 | 9.65 | 9.65 | 12.00 | −3.2% | 4 / 4 | 79.5% |
| blitz | 2,241 | **8.88** | 9.37 | 8.98 | 8.88 | 9.91 | −5.2% | 4 / 4 | 81.3% |
| pass rushers | 2,241 | **0.147** | 0.157 | 0.150 | 0.147 | 0.164 | −5.9% | 4 / 4 | 83.6% |
| heavy box | 2,246 | **5.47** | 5.78 | 5.61 | 5.47 | 5.77 | −5.4% | 4 / 4 | 83.5% |

**The test window alone** (2024–2025, never seen by the fit): PROE 7.30 vs 7.71, dropback rate 7.86 vs 8.58, deep shots 5.01 vs 5.26, middle 9.30 vs 11.36, outside 8.95 vs 9.39, play-action 7.44 vs 7.91, screens 4.28 vs 4.46, motion 9.69 vs 10.04, blitz 8.67 vs 9.25, rushers 0.133 vs 0.143, heavy box 4.42 vs 4.60 (forecast vs season-to-date): every target holds out of sample.

**By part of season** (forecast / season-to-date): the prior and the opponent help most early, and by week 11 the team's own rate has caught up, as the plan expected.

| Target | Weeks 1–4 | Weeks 5–10 | Weeks 11+ |
|---|---|---|---|
| PROE | 7.48 / 8.71 | 6.98 / 7.31 | 7.22 / 7.45 |
| dropback rate | 7.97 / 9.35 | 8.08 / 8.46 | 7.92 / 8.27 |
| deep shots | 4.87 / 5.51 | 4.95 / 5.28 | 4.95 / 5.09 |
| runs up the middle | 10.13 / 14.94 | 9.31 / 12.96 | 9.38 / 12.64 |
| runs outside | 9.46 / 11.53 | 8.82 / 9.58 | 8.87 / 9.84 |
| play-action | 7.90 / 9.17 | 7.67 / 8.00 | 7.45 / 7.62 |
| screens | 4.19 / 4.76 | 4.45 / 4.62 | 4.20 / 4.30 |
| motion | 9.65 / 10.71 | 9.76 / 10.09 | 9.58 / 9.57 |
| blitz | 8.85 / 10.14 | 9.02 / 9.38 | 8.80 / 9.01 |
| pass rushers | 0.15 / 0.17 | 0.15 / 0.16 | 0.14 / 0.15 |
| heavy box | 4.88 / 5.37 | 5.21 / 5.45 | 5.90 / 6.18 |

**By season** (forecast / season-to-date): PROE 7.25/7.89, 6.99/7.47, 7.35/8.05, 7.13/7.54, 7.12/7.50, 7.54/7.71, 7.06/7.71 (2019 → 2025); dropback rate 7.97/8.39, 7.77/8.27, 8.28/9.01, 8.30/8.79, 7.81/8.38, 7.89/8.55, 7.83/8.61; deep shots 5.14/5.57, 4.62/4.92, 4.91/5.14, 4.92/5.22, 4.94/5.34, 5.02/5.32, 5.01/5.21; middle 9.86/14.88, 9.46/14.72, 10.07/13.84, 9.14/13.04, 9.57/13.82, 8.98/11.64, 9.61/11.08; outside 8.98/10.81, 9.07/10.63, 9.03/10.68, 8.75/10.15, 9.20/10.02, 9.38/9.93, 8.51/8.85; play-action 8.51/8.87, 7.12/7.63, 7.63/8.19, 7.25/7.62 (2022 → 2025); screens 4.16/4.38, 4.38/4.67, 4.51/4.70, 4.04/4.22; motion 9.84/10.32, 9.38/9.51, 9.73/10.16, 9.65/9.92; blitz 8.88/9.23, 9.30/9.73, 9.02/9.55, 8.31/8.94; rushers 0.16/0.17, 0.16/0.17, 0.14/0.15, 0.13/0.14; heavy box 5.32/5.80, 7.71/8.15, 4.41/4.42, 4.43/4.78. The closest call: heavy box 2024 (4.41 vs 4.42).

**Per-play scores.** For a share, every play of a game scored at the forecast rate: Brier (forecast vs season-to-date) dropback 0.2367 vs 0.2383, deep shots 0.1029 vs 0.1035, middle 0.1804 vs 0.1933, outside 0.1752 vs 0.1791, play-action 0.1689 vs 0.1702, screens 0.0803 vs 0.0807, motion 0.2371 vs 0.2383, blitz 0.1960 vs 0.1975, heavy box 0.0696 vs 0.0703; log loss lower for every one. **PROE per play** (each neutral play's pass chance = its `xpass` + the game's PROE forecast): Brier 0.1970 vs 0.1982 (season to date), 0.1987 (league); log loss 0.5705 vs 0.5751; per-play calibration error 0.007 (10 equal-count bins).

**Calibration of the rates** (10 equal-count bins of the forecast, play-weighted gap to the actual rate): 0.2–1.0 points for every share (motion the widest at 1.0: the league's motion rate rose every season, so forecasts made from last season run low early). The 80% range covered 79–84% of games (heavy box and rushers 84%: their errors are skewed and heavy-tailed; the range errs wide).

## The ship decision (D130)

The rule, written down at 2026-10-11 21:49 UTC before any reported run: a target ships if the forecast's game-level MAE is lower than season-to-date's in at least 5 of the 7 seasons 2019–2025 (FTN targets: 3 of the 4 seasons 2022–2025; Rishi at the kickoff), ties at 4 decimals not counting, **and** its pooled MAE is no worse than the shrunk team rate's. Rishi delegated the backtest and the decision to the rule ("agent runs, rules decide"). **All 11 ship**: every target won every season, and the model is better than b3 on every target at full precision (motion only just: 0.09648 vs 0.09650; λ is small and the opponent barely matters for motion). The pooled gate is compared exactly since the Sol review (D131); it was written "at 4 decimals", which changed no verdict. The venue addendum (written at 22:14 UTC, before the venue variant was scored) chose the context for run direction on the tuning window and re-applied the same rule in the second reported run.

## The live record (2026)

2026 is out of sample for the production parameters (fit on 2019–2025). Weeks 1–4 were forecast on 2026-10-11 from the as-of tables (a **backfill**: exactly what each Tuesday could see, but made after the games, with closing lines) and graded the same day: see the guide §9 and the Play calls tab. The first forecast made before its games is week 6 (Tuesday 2026-10-13); week 5's grade comes with that Tuesday's build (Monday night's FTN on Wednesday).

**2026 weeks 1–4** (1,396 graded team-games x targets; 12 more wait for ATL at NO's FTN), game-level MAE, forecast vs season-to-date:

| Target | Graded | Forecast | Season to date | Closer than season-to-date | In the 80% range |
|---|---|---|---|---|---|
| PROE | 128 | **6.51** | 7.71 | 82 of 128 | 82% |
| dropback rate | 128 | **7.02** | 8.21 | 80 of 128 | 86% |
| deep shots | 128 | **5.39** | 6.03 | 71 of 128 | 80% |
| runs up the middle | 128 | **9.77** | 11.60 | 75 of 128 | 78% |
| runs outside | 128 | **8.56** | 10.02 | 72 of 128 | 76% |
| play-action | 126 | **6.96** | 8.04 | 75 of 126 | 90% |
| screens | 126 | **3.85** | 4.41 | 72 of 126 | 83% |
| motion | 126 | **10.40** | 11.77 | 75 of 126 | 74% |
| blitz | 126 | **9.70** | 10.37 | 67 of 126 | 75% |
| pass rushers | 126 | **0.15** | 0.17 | 67 of 126 | 76% |
| heavy box | 126 | **5.18** | 5.84 | 69 of 126 | 79% |

All targets in points (shares and PROE): 7.34 vs 8.40; closer than season-to-date in 805 of 1,396 (58%); 80% of rows inside the range. By week (points): 7.26 vs 7.55 (week 1, season-to-date = last season), 7.98 vs 10.27, 6.92 vs 8.15, 7.18 vs 7.61. Four weeks is a small sample; the early-season weeks are where the backtest's gains were largest.

## Is this good? (honest notes)

- **The bar is low and the noise is high.** Season-to-date is what a careful fan would quote; beating it by 3–7% on most targets (28% on middle runs, where the stadium crew dominates) is what the noise allows: a team's pass rate over ~60 plays has a sampling SD of ~6 points even if its true rate were known. The forecast's own MAE (8 points on dropback rate) is mostly that noise.
- **Most of the gain is shrinkage early in the season**, as the research notes expected: a 3-game rate is a poor guide, and last season plus the league fix most of it. The opponent term is worth 0.1–0.2 points on most targets (b3 → b4), more on dropback rate and PROE (0.14, 0.07) and blitz (0.10); the game script is worth ~0.04 points on PROE and dropback rate (and it uses closing lines in the backtest).
- **The tuning window is inside the reported seasons** (2019–2023 of 2019–2025, as the phase file asked): about seven parameters per target, and the test window 2024–2025 alone holds every gain.
- **"Is the forecast right?" is a range question.** In 2026 week 4 a forecast of PIT's PROE at −1.4 against CLE with a range of −12.7 to +9.3 was "right" when PIT came in at +3.5, while season-to-date (+5.1) was closer: one game can't settle it; a season of them can.

## Reading the W&B charts

### The metrics in one minute

| Metric | What it is | Good looks like (this backtest) |
|---|---|---|
| game-level MAE | the average |forecast − the game's actual rate|, each team-game once, in points | below the season-to-date line in every season: 7.21 vs 7.69 (PROE), 8.88 vs 9.37 (blitz) |
| play-weighted MAE (`wmae`) | the same, weighted by the game's plays | |
| per-play Brier / log loss | every play of a game scored at the forecast rate | below season-to-date's, by a little (0.2367 vs 0.2383 dropback) |
| calibration (`ece`) | the play-weighted gap between forecast and actual in 10 equal-count bins | under 1 point |
| 80% range coverage | the share of games whose rate fell inside the range | 0.78–0.84 |
| wins vs b2 | the seasons the forecast beat season-to-date | 7 / 7, 4 / 4 |

### `playcall-backtest` runs (group `play-calling`; `playcall-backtest-2019-2025`, `-smoke`)

- **Live curves** (x-axis `bt/step`: one step per week of 2019–2025 in time order, `bt/season` and `bt/week` alongside): `bt/<target>/mae_<kind>` (the cumulative game-level MAE of the forecast `model` and each baseline `b1`–`b4`: the model line should settle below `mae_b2` and next to or below `mae_b3`) and `bt/<target>/week_mae_model` vs `week_mae_b2` (one week's MAE: noisy; early-season weeks show the biggest gaps).
- **Charts:** `mae_vs_baseline/<target>` (MAE by season for the forecast, season-to-date, shrunk, + opponent and league: the forecast's line under season-to-date's in every season), `calibration/<target>` (forecast vs actual in 10 bins against the diagonal), `calibration/proe_per_play` (each neutral play's `xpass` + forecast vs dropbacks).
- **Tables** (named `*_rows`, so W&B's own `<chart>_table`s don't overwrite them): `backtest/by_season_rows` and `backtest/by_part_rows` (every metric for every target, season / part and predictor), `backtest/ship_rows` (the verdicts), `backtest/params_rows` (the fitted parameters per target and predictor, with any at a bound), `backtest/coverage_rows` (the range's coverage by season), `backtest/script_curve_rows` (per season: which LD00 fit scored the curve, its reference seasons and the expected dropback rate at −7 / 0 / +7).
- **Summary:** `<target>/mae_model`, `mae_b1`–`mae_b4`, `improvement_vs_b2_pct`, `wins_vs_b2`, `seasons`, `ships`, `coverage_80`, `ece`, `games`, `test_mae_model`, `test_mae_b2`, `brier_model`, `brier_b2` (shares); `proe/per_play_brier_<kind>`, `proe/per_play_logloss_<kind>`; `shipped`.
- **Artifact** `playcall-model` (type `model`, with `--promote`): the version folder (`params.json`, `MODEL_CARD.md`); aliases `<version>` and `production`; description = this card.
- **Runs:** [`5zttxdnw`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/5zttxdnw) (final, after the review fixes, version `2019-2025_20261011T230330Z`), [`610301ws`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/610301ws) (before the review fixes, identical metrics; version `2019-2025_20261011T221755Z` superseded), [`mx6acp91`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/mx6acp91) (the first reported run: run direction with the opponent; its version `2019-2025_20261011T220450Z` is superseded), smoke [`z594wk90`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/z594wk90) (group `play-calling-smoke`, 3 targets, 2023–2025).

### `playcall-forecast` and `playcall-grade` runs

One `playcall-forecast-<season>-w<NN>` run per forecast (`nfl playcalling forecast`): the table `forecast/rows` (the shipped forecasts: game, team, opponent, target, forecast, range, season-to-date, shrunk, the opponent and script pieces, whether the game had kicked off), the summary `games`, `rows`, `shipped_rows`, `games_after_kickoff`; tags `season:S`, `week:NN`, `backfill` when made after kickoffs. One `playcall-grade-<season>-w<NN>` run per grading (`nfl playcalling grade`): `grade/targets_rows` (per target: graded rows, MAE of the forecast and every baseline, closer than season-to-date, the range's hit rate) and `grade/rows` (every row), the summary `graded`, `rows`, `shipped/*` (the overall MAE is over the targets in points only: rushers are in rushers). Chart `shipped/mae_model` and `shipped/mae_b2` across the grade runs with `week` on the x-axis for the season's record.

## Known biases and limits

- **Run direction is charted by each stadium's stats crew**, and crews differ by ~9 points on middle runs: the forecast models the venue; the PC01 pages' run lanes don't (a team's run lanes include its own crew in half its games).
- **League trends:** motion, play-action and heavy boxes drift during and across seasons; a forecast from last season's level runs low early (motion bias −0.75 points pooled, heavy box −0.56).
- **Closing lines** in the backtest's script term (rule 5); the live forecast uses Tuesday's lines.
- **Coordinators** come from the seed's final staff per season: a mid-season coordinator firing marks the whole season "new"; only head-coach changes are dated (half weight before the change), from nflverse's schedule (interims listed through 2023) and `config/head_coach_fixes.csv` (2025 on): 2024's NYJ and NO firings are in neither, so two team-seasons of the backtest aren't down-weighted (the fixes file feeds the weekly graph: not edited, D107).
- **No personnel, formations or coverage** (no 2026 participation until about February 2027); **no single plays** (out of scope).
- **FTN targets have four seasons** (2022 with no prior); 2022 week 1 has no forecast for them (no league rate yet).

## Outputs

- `{NFL_DATA_ROOT}/models/playcall/<version>/params.json` (per target: every predictor's parameters, the range, shipped, the tuning-window fit, the backtest summary) + `MODEL_CARD.md`; `models/playcall/production.json` (the promoted version and the shipped list).
- Backtests: `playcalling/backtests/<first>-<last>/predictions.parquet` (every team-game, every predictor, the range and the pieces) + `summary.json` (every metric, the curves); `playcalling/backtests/smoke/`.
- Weekly: `playcalling/<S>/week<NN>/forecast.parquet` + `forecast.json`; `playcalling/<S>/playcall_scoreboard.parquet`.

## Verification

- `uv run pytest -q tests/playcalling -k forecast` and `tests/app -k playcall` (synthetic leagues with hand-worked values; an independent plain-Python reference of every predictor; leakage by future scrambling; the FTN 48-hour rule; the ship rule; the reader's answers and the JSON-vs-TypeScript contract); `-m integration`: the as-of sums equal PC00's tables on the real 2025 data.
- The production-unchanged check (D107): `tests/test_production_untouched.py`; and old code vs new code on the same inputs for 2026 week 5's game and player steps (identical).

## Versioning

- **`2019-2025_20261011T230330Z`** (2026-10-11, PC02, after the Sol review): all 11 targets shipped; run direction with the venue; `production`.
- `2019-2025_20261011T221755Z` (the same day, superseded): the same parameters and verdicts before the review fixes (the range's fallback plays, D131).
- `2019-2025_20261011T220450Z` (the same day, superseded): run direction with the opponent.
- **Refit after each season:** add the finished season to `playcalling.forecast.report_seasons`, re-run `uv run nfl playcalling backtest --promote` (the rule re-applied), then re-forecast any open week.
