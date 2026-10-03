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

## The accuracy scoreboard

One row per target per week, logged to a W&B Table (`accuracy_scoreboard`) and kept season-long:

`season, week, target, position_group, n_scored, mae_model, mae_baseline, improvement_pct, coverage_80, brier_model, brier_baseline, calibration_ece`

Views:
- **W&B dashboard panel:** improvement % by target over the season, plus season-to-date cumulative numbers.
- **Digest report card** ([06](06-weekly-digest.md)): 2–3 highlights each week (e.g. "receiving-yards projections beat the rolling average by 9% so far this season"), always including at least one weak spot.
- **Per-player look-back:** for the watch list, predicted vs actual with the range ("projected 84, range 52–118 → actual 97 ✓ inside the range").

## Priority order

1. **P03:** game win probability, margin, predicted score.
2. **P06:** the main target for each position group (QB passing yards and EPA, RB rushing yards and carries, WR/TE receiving yards, targets and receptions, EDGE pressures, LB tackles) plus the scoreboard.
3. **P08:** TD/sack/INT probabilities, coverage stats for DBs, team stat totals, the consistency layer.
