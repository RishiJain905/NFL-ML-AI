# 11: Prediction Targets and the Accuracy Scoreboard

This is the core of the fun: **how accurate can we get?** This doc lists every quantity we predict for **games, teams and players on both offense and defense**, the baseline each must beat, how it's scored, and realistic ceilings, so "good" means something.

## Principles

- **Every target has a baseline** (usually the player's or team's own recent average) and is judged by **improvement over that baseline**, not by raw error alone.
- **Every prediction has a stated uncertainty:** a P10–P50–P90 range for amounts, a calibrated probability for yes/no events.
- **Only scored when the player plays.** Players who are inactive or didn't play are excluded from accuracy. Predicting availability is a separate problem (injury status is an input, not a target, in v1).
- **Walk-forward only** (see the leakage rules in [04](04-track1-models.md)).
- Everything feeds the **accuracy scoreboard** (end of this doc), in W&B and in the digest's report card.

## Game and team targets

| Target | Model type | Baseline(s) | Metrics | Phase |
|---|---|---|---|---|
| Home win probability | Classifier / from the margin | Home team always, Elo, ESPN FPI, market-implied | Brier, log loss, accuracy, ECE | P03 |
| Expected margin | Regression | Elo margin, market spread | MAE, RMSE | P03 |
| **Points for each team (predicted score)** | Two regression heads (or margin + total split) | Team's rolling points, market-implied team total | MAE per team, total-points MAE | P03 |
| Team passing yards / rushing yards | Regression (quantile) | Rolling team average adjusted for opponent | MAE, coverage | P08 |
| Team sacks (made and taken) | Count (Poisson) | Rolling average | MAE, Poisson deviance | P08 |
| Team takeaways | Count (Poisson) | League average (takeaways are mostly noise, so be honest) | Deviance, calibration | P08 |

## Player targets: offense

| Position | Target | Model type | Phase |
|---|---|---|---|
| QB | **Passing yards** (main) | Quantile LightGBM | P06 |
| QB | EPA per dropback | Quantile | P06 |
| QB | Pass TDs, interceptions | Count (Poisson / Tweedie) → expected value + P(≥1), P(≥2) | P08 |
| QB | Rushing yards | Quantile | P08 |
| RB | **Rushing yards** (main), carries | Quantile / count | P06 |
| RB | Receptions, scrimmage yards | Count / quantile | P06 |
| WR / TE | **Receiving yards** (main) | Quantile | P06 |
| WR / TE | Targets, receptions | Count | P06 |
| All skill players | **Chance of scoring a TD** | Classifier (calibrated) | P08 |

## Player targets: defense

| Position | Target | Model type | Phase |
|---|---|---|---|
| EDGE / DL | **Pressures** (main) | Count | P06 |
| EDGE / DL | Sacks: expected and P(≥1) | Count / classifier | P08 |
| EDGE / DL | QB hits | Count | P08 |
| LB / S | **Tackles** (main) | Count | P06 |
| CB / S | Targets allowed, completions allowed, yards allowed in coverage (PFR) | Count / quantile | P08 |
| CB / S | P(interception), P(pass defended) | Classifier | P08 |

Kickers and punters are out of scope for now.

## How each kind of target is modeled

- **Amounts (yards, EPA):** LightGBM with quantile objectives (P10 / P50 / P90). P50 is the projection; P10–P90 is the 80% range.
- **Counts (receptions, tackles, pressures, TDs):** LightGBM with a Poisson or Tweedie objective for the expected value. The count distribution gives P(≥k), and quantiles come from that distribution.
- **Yes/no events (TD scored, sack, INT):** a calibrated classifier (isotonic or Platt calibration on walk-forward predictions).
- **Consistency (v2, P08):** check and lightly adjust predictions so they agree: receptions ≤ targets; the sum of a team's player receiving yards ≈ the team's passing yards; QB passing yards ≈ receivers' total. Log how inconsistent things are as a quality signal.
- **Features:** the player-model feature families in [04](04-track1-models.md) section C, plus position-specific additions (pass rusher: opponent offensive-line pressure-allowed rate, ESPN pass rush win rate if collected; DB: opponent passing volume and target depth, NGS cushion; LB: opponent rush rate and pace).

## Baselines (what "beating the baseline" means)

| Baseline | Definition |
|---|---|
| **Player rolling** | Weighted mix of the last 4 games and season to date, pulled toward last season's rate early in the year. The main baseline for every player stat |
| **Season-to-date mean** | A simpler check |
| **Position-average-for-role** | For players with little history (rookies, new starters), matched by snap share |
| **Market** | For games: market-implied probability, spread and total (strongest public bar) |

## Realistic ceilings (rough public reference points)

These set expectations. **Our own measured baselines from P03 and P06 replace them** once we have them.

| Target | Rough ceiling |
|---|---|
| Picking game winners | ~63–68% over a season; even the betting market rarely does better |
| Final-margin error | ~10–11 points MAE; the market spread is around 10 |
| Total-points error | ~10 points MAE |
| Player yardage | Beating a good rolling average by **5–15%** in MAE is a solid result; football player stats are very noisy |
| Player counts (receptions, tackles, pressures) | Similar relative gains; usage-driven stats (targets, carries) are the most predictable |
| TD / sack / INT probabilities | Small gains in Brier score; **calibration** is the main goal |

**Measured in P03** (walk-forward 2018–2025, 2,227 games; [game model card](model_cards/game-model-v0.md)). These replace the public ceilings for games:

| Target | Our model-only | Our market-informed | Closing market | Other baselines |
|---|---|---|---|---|
| Picking winners (accuracy) | 64.0% | 66.3% | 66.2% | Elo 63.5%, home team 54.3% |
| Win probability (Brier) | 0.2199 | 0.2102 | 0.2104 | Elo 0.2221, home team 0.2477 |
| Final-margin MAE | 10.19 | 9.86 | 9.83 (spread) | Elo 10.22 |
| Total-points MAE | 10.63 | 10.44 | 10.42 (total) | rolling team average 10.86 |
| Points MAE per team | 7.46 | 7.28 | 7.27 (implied team total) | rolling team average 7.65 |

Why ~65% accuracy is near the ceiling (and 80%+ isn't reachable for winners over a season) is explained, with evidence, in the game model card's ["Is 64% accuracy good?"](model_cards/game-model-v0.md#is-64-accuracy-good-why-nfl-winners-top-out-around-6570) section.

**P08 game model v1** (LightGBM boosted from v0 with injury load, weather and a trailing home edge; [card](model_cards/game-model-v1.md)) on the same 2018–2025 games: model-only Brier 0.2193 (v0 0.2199), market-informed 0.2101 (v0 0.2102), accuracy 64.3% / 66.2%, margin MAE 10.19 / 9.87, total MAE 10.63 / 10.46. The gain is inside the noise and calibration is worse, so **v0 stays in production** (D79); the table above is still the production model.

**Measured in P06** (walk-forward 2019–2025, every regular-season week, only games the player played; [player model card](model_cards/player-model-v1.md)). These replace the public ceilings for player stats. Improvement is over the player-rolling baseline on the same rows; count targets are compared with the baseline's median (D65), because a median beats any mean on MAE by itself.

| Target | MAE model | MAE baseline | Improvement | Without market lines | 80% range held | Seasons better |
|---|---|---|---|---|---|---|
| QB passing yards | 56.6 | 59.5 | +5.0% | +4.6% | 81% | 7 / 7 |
| QB EPA per dropback | 0.230 | 0.241 | +4.7% | +3.9% | 80% | 7 / 7 |
| RB rushing yards | 19.0 | 20.7 | +7.9% | +8.1% | 81% | 7 / 7 |
| RB carries | 3.39 | 3.64 | +6.9% | +6.9% | 80% | 7 / 7 |
| RB receptions | 1.09 | 1.12 | +2.7% | +2.3% | 81% | 7 / 7 |
| RB scrimmage yards | 23.5 | 25.3 | +7.4% | +7.3% | 80% | 7 / 7 |
| WR/TE receiving yards | 16.5 | 18.2 | +9.2% | +9.2% | 82% | 7 / 7 |
| WR/TE targets | 1.62 | 1.70 | +5.2% | +5.2% | 81% | 7 / 7 |
| WR/TE receptions | 1.20 | 1.24 | +3.4% | +3.4% | 80% | 7 / 7 |
| EDGE/DL pressures | 0.556 | 0.573 | +3.0% | +3.0% | 85% | 7 / 7 |
| LB/S tackles | 1.80 | 1.89 | +4.8% | +4.7% | 81% | 7 / 7 |

Yardage lands in the 5–15% band above; usage counts (carries, targets) beat per-play efficiency stats as expected, but catches and pressures, which depend on what the defense allows on a few plays, gain least. The watch list (8 picks a week) beat its players' baselines 69.5% of the time against a 42.2% base rate.

**Measured in P08: the 12 new player targets** (walk-forward 2019–2025, vs the player's rolling baseline on the same rows; [P08 player targets card](model_cards/player-p08.md)). All 12 ship (D82); every one beats its baseline in **7 of 7** seasons, with and without market lines.

| Target | Kind | Model vs baseline | Improvement | 80% range / calibration | Base rate |
|---|---|---|---|---|---|
| QB passing TDs | count + chance of ≥ 1 | MAE 0.862 vs 0.892; Brier 0.164 vs 0.174 | +3.4% MAE, +5.5% Brier | range 80%; ECE 0.016 (baseline 0.050) | 78% throw ≥ 1 |
| QB interceptions | count + chance of ≥ 1 | MAE 0.680 vs 0.720; Brier 0.248 vs 0.255 | +5.5% MAE, +2.6% Brier | range 85%; ECE 0.016 | 51% (the chance is coin-flip quality: base-rate Brier 0.250) |
| QB rushing yards | amount | MAE 11.63 vs 11.87 | +2.0% | range 81% | |
| RB chance of a TD | probability | Brier 0.161 vs 0.176 | +8.4% | ECE 0.009 (0.067) | 24.5% |
| WR/TE chance of a TD | probability | Brier 0.116 vs 0.126 | +8.0% | ECE 0.003 (0.055) | 15.1% |
| EDGE/DL sacks (half credit counts) | count + chance of ≥ 1 | MAE 0.181 vs 0.186; Brier 0.125 vs 0.133 | +2.6% MAE, +6.0% Brier | range 89%; ECE 0.003 | 16.7% |
| EDGE/DL QB hits | count | MAE 0.384 vs 0.395 | +2.7% | range 86% | |
| CB/S targets allowed (PFR) | count | MAE 1.837 vs 1.934 | +5.0% | range 80% | |
| CB/S completions allowed | count | MAE 1.331 vs 1.393 | +4.4% | range 82% | |
| CB/S coverage yards allowed | amount | MAE 18.58 vs 20.58 | +9.7% | range 84% | |
| CB/S chance of an interception | probability | Brier 0.071 vs 0.077 | +8.5% | ECE 0.003 (0.056) | 7.8% |
| CB/S chance of a pass defended | probability | Brier 0.199 vs 0.218 | +8.8% | ECE 0.005 (0.096) | 30.7% |

As doc 11 expected for yes/no events, the gains are in Brier and above all in **calibration**: the models' ECE is 0.003–0.016 against the rolling rates' 0.05–0.10 (a player's rolling TD rate says exactly 0% for a fifth to a third of players). Coverage yards (+9.7%) is the biggest MAE gain of any player target so far; the counts' ranges run a little wide (85–89% for interceptions, sacks, QB hits: whole-number quantiles of small counts).

**Measured in P08: team stat totals** (walk-forward 2019–2025, 3,742 team-games each; [team stats model card](model_cards/team-stats-v1.md)). Baselines: yards and sacks = (the team's last-8-games average + the opponent's last-8-games average allowed) / 2; takeaways = the league's per-team-game average as of the week. Counts are compared with the baseline's median (D65) and by Poisson deviance.

| Target | MAE model | MAE baseline | Improvement | Seasons better | 80% range held | Poisson deviance (model / baseline) | Shipped |
|---|---|---|---|---|---|---|---|
| Team passing yards | 55.23 | 56.65 | +2.5% | 7 / 7 | 81% | | **yes** |
| Team rushing yards | 38.19 | 38.81 | +1.6% | 6 / 7 | 81% | | **yes** |
| Sacks made (defense) | 1.340 | 1.343 | +0.2% | 6 / 7 | 80% | 1.346 / 1.369 | **yes** (thin) |
| Sacks taken (offense) | 1.342 | 1.343 | +0.1% | 5 / 7 | 80% | 1.346 / 1.369 | **yes** (thin) |
| Takeaways | 0.870 | 0.869 | −0.1% | 2 / 7 | 82% | 1.218 / 1.229 | no |

As doc 11 expected, takeaways are mostly noise: the model's mean beat the league average on deviance but its median lost on MAE in 5 of 7 seasons, and it over-projected (1.35 vs 1.29 actual). The sack models are a tie on MAE (a whole-number median of a ~2.5 mean) but better on the proper scores: deviance −1.7%, Brier of P(≥ 1 sack) 0.107 vs 0.111. Without the closing-line features they lose their edge (+0.06% / −0.04%), so they lean on the market total (leakage rule 5).

## The accuracy scoreboard

One row per target per week, logged to a W&B Table (`accuracy_scoreboard`) and kept season-long:

`season, week, target, position_group, n_scored, mae_model, mae_baseline, improvement_pct, coverage_80, brier_model, brier_baseline, calibration_ece`

Views:
- **W&B dashboard panel:** improvement % by target over the season, plus season-to-date cumulative numbers.
- **Digest report card** ([06](06-weekly-digest.md)): 2–3 highlights each week (e.g. "receiving-yards projections beat the rolling baseline by 9% so far this season"), always including at least one weak spot.
- **Per-player look-back:** for the watch list, predicted vs actual with the range ("projected 84, range 52–118 → actual 97 ✓ inside the range").

## Priority order

1. **P03:** game win probability, margin, predicted score.
2. **P06:** the main target for each position group (QB passing yards and EPA, RB rushing yards and carries, WR/TE receiving yards, targets and receptions, EDGE pressures, LB tackles) plus the scoreboard.
3. **P08:** TD/sack/INT probabilities, coverage stats for DBs, team stat totals, the consistency layer.
