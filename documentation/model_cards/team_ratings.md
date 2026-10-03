# Model card: team ratings, Elo and trend (P02)

**Family:** Track 1 model A ([04 → A](../04-track1-models.md#a-team-ratings-and-trend)) · **Last tuned:** 2026-10-03 (approved by Rishi, D46) · **Code:** `models/ratings.py`, `elo.py`, `trend.py`, `trend_evidence.py` · **Tables:** `features/team_ratings`, `team_elo`, `team_trends`, `team_trend_drivers` (rebuilt by `nfl ratings build`; `_meta/team_ratings.json` holds the parameters, git commit and data version of the last build).

## What it is

Opponent-adjusted team strength in **EPA per play** (and success rate), for offense and defense, split into all plays / dropbacks / designed runs. Every row is **as of** a (season, week): built only from games in earlier weeks.

- **Method:** weighted ridge `y = mu + off[team] + def[opponent] + hfa·home` (D41).
  - Plays are recency-weighted with a 12-week half-life, and garbage time weighs 0.25.
  - Coefficients shrink toward a target with strength 250 weighted plays. The target starts each season at last season's full-season rating × 0.9 and fades at the same half-life.
  - Home field is held at the trailing 3-season estimate (D42).
- **Not a learned label:** it's a calculation, validated by how well it predicts the next games.
- **Elo** (baseline for P03): 538-style, K 20, home field 48, 1/3 reversion, margin-of-victory multiplier, from 2002 (D43).

## How it works, in plain language

New to EPA? Start with [the math, step 1](#1-expected-points-and-epa-the-raw-material) below.

**Nothing here is "trained" like a neural net or LightGBM.** There are no saved weights and no model file. Every `nfl ratings build` recomputes all of history from the data and three fixed settings, in about 0.2 seconds.

- **Ratings: a regression solved fresh for every week.**
  - The equation: `EPA of a play = league average + offense strength + defense strength + home field`. It is solved over this season's plays from earlier weeks, for each of 3 play types × 2 metrics.
  - Solving it across every play untangles schedule strength. Gaining 0.2 EPA/play against a great defense earns more credit than doing it against a bad one.
  - Each week's plays are summarized once into a 66×66 matrix, so any week's ratings are a single small solve.
- **Elo: a running score, updated game by game from 2002.**
  - Before a game it gives a win probability.
  - After the game, the winner takes points from the loser. Upsets and blowouts move it more.
  - Each offseason every team is pulled 1/3 back toward average.
- **Trend: arithmetic on the ratings.**
  - The change over 3 weeks, plus performance vs what the ratings expected over the last 3 games.
  - Which part moved most (pass/rush, offense/defense), and evidence: QB change, key injuries, pressure-rate shifts.

**What was actually learned: three settings, chosen once by a backtest.** For every week of 2015–2025, the backtest took the ratings as of that week, predicted that week's games, and measured the error (sweep `dwyj31wk`, 175 combinations). The settings stay fixed for the season (D17) and are retuned before 2027 (P10).

| Setting | Value | Plain meaning |
|---|---|---|
| Half-life | 12 weeks | How fast old games stop counting: a game 12 weeks older than the latest one counts half as much, 24 weeks older a quarter. Within a ~17-week season, early games keep real weight. Short half-lives (4) chase noise; anything 8+ was about equally good |
| Prior pull | 0.1 (10%) | In week 1, every team starts at last season's rating moved 10% toward average, a "regression to the mean" for the offseason. 0–20% were about equal; 50% threw away useful information and hurt weeks 1–3 |
| Shrinkage | 250 plays | How much evidence it takes to move a team away from its starting point: the prior counts like 250 plays. Too little (100) chases noise; too much (650) reacts too slowly |

**How fast the preseason prior fades** (average share of last season's rating in the current rating, 2015–2025):

| Week | 1 | 2 | 4 | 6 | 10 | 18 |
|---|---|---|---|---|---|---|
| Prior share | 100% | 78% | 52% | 38% | 24% | 11% |

**How it's applied each week.** Every table row is keyed "as of week w": it is built only from games before week w. A week-5 game therefore joins its week-5 rows directly, with no look-ahead (D44, covered by tests). From P07 on, each Tuesday runs ingest → curate → `nfl ratings build` with the fixed settings, and the current week's rows are the ratings going into this week's games.

**Where the outputs are used:**
- **P03 game model:** rating differences are the core features (net, pass offense vs pass defense, rush offense vs rush defense), with the Elo difference and home field. For example, net +0.10 vs −0.02 plus home field 0.01 gives an expected margin of +0.13 EPA/play, which the game model turns into a win probability and score. Elo's Brier score (0.2214) is the bar it has to beat.
- **P04 digest:**
  - "Team trend shifts" reads `team_trends` (direction, drivers, evidence), worded as form, never as a forecast (D47).
  - `prior_weight` flags low confidence early in the season.
- **P05 graph:** ratings and trends become weekly attributes of team nodes.
- **P06 player model:** opponent pass and rush defense ratings become matchup features.

## The math, step by step

Every formula comes with what it means and a worked example. Real values are 2021–2025 regular-season averages unless the text says otherwise.

| Symbol | Meaning |
|---|---|
| $i$ | one play |
| $t$ | one team |
| $w$ | the **as-of week**: ratings "as of week $w$" use only games from weeks before $w$ |
| $k_i$ | the week play $i$ happened |
| $h$ | the half-life in weeks (12) |
| $\alpha$ | the shrinkage strength in plays (250) |
| $p$ | the prior pull toward average (0.1) |

### 1. Expected points and EPA (the raw material)

**Expected points.** For a game situation $s$ (down, distance, field position, time left, score), EP is the average value of the **next score** in the game, from the offense's point of view:

$$
EP(s) = \sum_{k} P(\text{next score is } k \mid s)\cdot v_k ,\qquad v_k \in \{+7,\,+3,\,+2,\,0,\,-2,\,-3,\,-7\}
$$

- **Reading it:** the values are our touchdown, our field goal, our safety, no more scoring, then the opponent's safety, field goal and touchdown.
- **Where the probabilities come from:** nflverse's nflfastR model, estimated from decades of plays. We use its numbers as they are.

| Situation | EP |
|---|---|
| 1st-and-10 at your own 25 | 1.21 |
| 1st-and-10 at your own 45 | 2.46 |
| 1st-and-10 at the opponent's 35 | 3.74 |
| 1st-and-goal at the 2 | 6.33 |

**EPA (Expected Points Added)** is how much one play changed expected points:

$$
EPA_i = EP(\text{after play } i) - EP(\text{before play } i)
$$

- **Scoring plays:** "after" is the points actually scored.
- **Turnovers:** "after" is the opponent's EP, counted negative.

| Play | EPA |
|---|---|
| A 20-yard completion from your 25 to your 45 | $2.46 - 1.21 = +1.25$ |
| A 4-yard gain on 3rd-and-3 (first down) | $+1.06$ on average |
| The same 4 yards on 3rd-and-10 (now you punt) | $-0.75$ on average |
| The average 3rd-down sack | $-1.95$ |
| The average interception | $-4.43$ |

The 3rd-down rows are why EPA beats yards: identical yardage, opposite value. Across the league, EPA averages about $0$ per play with a standard deviation of $1.38$; a single play is very noisy, so we need many plays.

**Success rate:**

$$
\text{success}_i = \mathbb{1}[EPA_i > 0],\qquad \text{success rate} = \frac{1}{n}\sum_{i=1}^{n}\text{success}_i
$$

A play is a success when it improved the offense's position. The league average is 43.7%.

### 2. How much each play counts

Each play gets a weight $\omega_i$ built from two parts:

$$
\omega_i = \underbrace{d^{\,(w-1)-k_i}}_{\text{recency}} \times \underbrace{g_i}_{\text{garbage time}},
\qquad d = 0.5^{1/h},
\qquad g_i = \begin{cases} 0.25 & \text{if win prob.} < 0.05 \text{ or } > 0.95 \\ 1 & \text{otherwise} \end{cases}
$$

**Recency.** The most recent week's plays count fully, and every week older multiplies the weight by $d$. With $h = 12$, $d = 0.5^{1/12} = 0.944$:

| Weeks older than the latest week | 0 | 1 | 4 | 8 | 12 | 16 |
|---|---|---|---|---|---|---|
| Weight | 1.00 | 0.94 | 0.79 | 0.63 | **0.50** | 0.40 |

That's what "12-week half-life" means: a play counts half as much after 12 more weeks.

**Garbage time.** About 15% of plays happen when the game is basically decided. They still count, but only a quarter as much, because a team up 28 points plays differently.

### 3. The rating equation

Every play is explained as a sum of who had the ball, who was defending, and where:

$$
EPA_i = \mu + o_{\,\text{off}(i)} + \delta_{\,\text{def}(i)} + h_{\text{home}}\cdot \text{home}_i + \varepsilon_i
$$

| Term | Meaning |
|---|---|
| $\mu$ | league-average EPA per play |
| $o_t$ | team $t$'s **offense** strength: + means it gains more EPA than average |
| $\delta_t$ | team $t$'s **defense**, as EPA **allowed**: − means it allows less, which is good |
| $h_{\text{home}}$ | the home-field bump per play (home is 1 if the offense is the home team at a non-neutral site) |
| $\varepsilon_i$ | everything else: luck, play call, weather, ... |

Worked example: KC's offense ($o = +0.10$) at home against DEN's defense ($\delta = -0.05$), with $\mu = 0$ and $h_{\text{home}} = 0$:

$$
\text{expected EPA per KC play} = 0 + 0.10 - 0.05 + 0 = +0.05
$$

**This is where schedule strength gets untangled.** If KC gains +0.15 per play against a defense that's −0.05, the model credits the offense with about +0.20. The same +0.15 against a bad defense (+0.10) earns only about +0.05.

**Scale and centering.**
- Team effects are shifted so $\sum_t o_t = 0$ and $\sum_t \delta_t = 0$, so 0 always means league average.
- A team's net rating is
  $$
  net_t = o_t - \delta_t
  $$
- At season's end a typical team sits within $\pm 0.09$ of average (one standard deviation). The best and worst are around $\pm 0.22$.

The same equation is solved separately for all plays, dropbacks only, and designed runs only, which gives the pass and rush ratings. It is also solved with $\text{success}_i$ in place of $EPA_i$, which gives the success-rate ratings.

### 4. Solving it: weighted ridge regression with a moving target

With 66 unknowns, $\beta = (\mu,\, h_{\text{home}},\, o_1..o_{32},\, \delta_1..\delta_{32})$, we choose the values that minimize:

$$
\underbrace{\sum_i \omega_i\,\big(EPA_i - \text{predicted}_i\big)^2}_{\text{fit this season's plays}}
\;+\;
\underbrace{\alpha \sum_j \big(\beta_j - \tau_j\big)^2}_{\text{don't stray from the target without evidence}}
$$

$\tau_j$ is the target for each coefficient (step 5).
- **The first part** wants the ratings to match the plays.
- **The second part** charges a penalty for moving away from the target.
- **$\alpha = 250$ means the target is worth 250 plays of evidence.**

**One team, simplified.** Pretend every other team is known. Then a team's rating is just a weighted average of what its plays say and its target:

$$
o_t \;\approx\; \frac{N_t\,\bar{y}_t + \alpha\,\tau_t}{N_t + \alpha}
$$

- $N_t$ is the team's weighted play count.
- $\bar{y}_t$ is its schedule-adjusted average from its plays.

Worked example: plays say $\bar{y} = +0.15$, last season says the target is $+0.05$.

| Point in the season | $N_t$ (weighted plays) | Target, faded | Rating |
|---|---|---|---|
| As of week 4 (3 games) | ≈ 176 | $0.05 \times 0.84 = 0.042$ | $\dfrac{176(0.15) + 250(0.042)}{176 + 250} = +0.087$ |
| As of week 13 (12 games) | ≈ 552 | $0.05 \times 0.50 = 0.025$ | $\dfrac{552(0.15) + 250(0.025)}{552 + 250} = +0.111$ |

Early in the season the rating sits between the evidence and the prior. Later it moves most of the way toward the evidence, never all the way: the shrinkage keeps one hot stretch from producing an extreme rating.

**The exact solution** solves all 66 values at once. This is the textbook ridge formula with a target:

$$
\hat\beta = \big(X^\top W X + \alpha I\big)^{-1}\big(X^\top W y + \alpha\,\tau\big)
$$

- $X$ marks which teams were on the field for each play.
- $W$ holds the weights $\omega_i$.
- $y$ holds the EPA values.

**Why it's fast.** Each week's plays are boiled down once into a 66×66 matrix $S_k = \sum_{i \in k} g_i x_i x_i^\top$ and a 66-vector. Moving one week forward is then just:

$$
A_w = d \cdot A_{w-1} + S_{w-1},\qquad b_w = d \cdot b_{w-1} + s_{w-1}
$$

Each week's ratings are one small solve, $\hat\beta_w = (A_w + \alpha I)^{-1}(b_w + \alpha\tau_w)$, and all 2010–2026 history takes about 0.2 seconds.

### 5. The preseason prior, and how it fades

Week 1 has no data, so each team starts at its target. That is last season's rating, pulled 10% toward average:

$$
\tau_t(\text{week }1) = (1-p)\, r_t^{\text{last}} = 0.9\, r_t^{\text{last}}
$$

- **Why pull toward average at all:** over an offseason, good teams tend to slip and bad teams tend to improve (roster turnover, coaching changes, luck evening out).
- **Why only 10%:** $r_t^{\text{last}}$ is last season's **full-season** rating. That is the same ridge fit over all of last season's plays with no recency weighting, which is already shrunk toward average by $\alpha$, so only a small extra pull is needed.
- Example: a team at $+0.20$ last season starts at $0.9 \times 0.20 = +0.18$.

The target then **fades toward average** at the same half-life as the data:

$$
\tau_t(w) = (1-p)\, r_t^{\text{last}} \cdot d^{\,w-1}
$$

The share of last season still inside a rating is about:

$$
\text{prior share}_t(w) \approx \frac{\alpha\, d^{\,w-1}}{\alpha + N_t(w)}
$$

This is the `prior_weight` column. It averages 100% in week 1, 52% in week 4, 38% in week 6, 24% in week 10 and 11% in week 18 (table above).

### 6. Home field

Inside a season the home-field term is **held fixed**: the average of the previous three seasons' full-season estimates:

$$
h_{\text{home}}(s) = \tfrac{1}{3}\big(\hat h_{s-1} + \hat h_{s-2} + \hat h_{s-3}\big)
$$

- **Why held fixed:** a weekly estimate swung by ±0.07 EPA/play, which is pure noise.
- **Its value is about zero:** −0.004 for 2026. Home teams do win more, but the play-level edge is hidden by game script: teams that lead, more often the home team, run many low-EPA clock-killing plays.
- **At the game level** (each game's EPA/play margin) home teams average about +0.01.
- **What it means downstream:** this term matters little for team ratings. P03's game model learns its own home-field edge in points.

### 7. From ratings to a game prediction (how the ratings are scored)

For a game between home team $H$ and away team $A$:

| Offense | Expected EPA per play |
|---|---|
| $H$ | $\mu + o_H + \delta_A + h_{\text{home}}$ |
| $A$ | $\mu + o_A + \delta_H$ |

Subtracting, $\mu$ cancels and the difference is exactly the net ratings:

$$
\widehat{m} = (o_H - \delta_H) - (o_A - \delta_A) + h_{\text{home}} = net_H - net_A + h_{\text{home}}
$$

At a neutral site $h_{\text{home}} = 0$. Example: $net_H = +0.10$, $net_A = -0.02$, $h_{\text{home}} = 0.01$ gives $\widehat{m} = +0.13$ EPA per play. Over about 62 plays a side, that's roughly an 8-expected-point edge.

**The tuning score** compares that prediction with what happened. $m_g$ is the home offense's EPA/play minus the away offense's, with the same garbage-time weights:

$$
\text{MSE} = \frac{1}{N}\sum_{g=1}^{N}\big(\widehat{m}_g - m_g\big)^2
$$

Lower is better. The ratings score $0.1041$, a typical miss of $\sqrt{0.1041} = 0.32$ EPA/play. They correlate 0.36 with what happens, so they explain about 13% of game-to-game variation; most of a single game is noise.

The baselines use the same formula with simpler inputs:

$$
net^{\text{raw}}_t = \frac{\sum_{\text{offense plays}} \omega_i\, EPA_i}{\sum \omega_i} \;-\; \frac{\sum_{\text{defense plays}} \omega_i\, EPA_i}{\sum \omega_i}
$$

That is the raw EPA/play gained minus allowed, with no schedule adjustment and no prior:
- **last-season baseline:** computed over all of last season (0.1194);
- **season-to-date baseline:** computed over this season so far (0.1249).

### 8. Elo

Elo is one number per team, updated after every game.

**Before the game**, the home team's win probability is:

$$
P(\text{home win}) = \frac{1}{1 + 10^{-D/400}},\qquad D = R_H - R_A + 48\cdot(1-\text{neutral})
$$

| Rating edge $D$ | 0 | 48 (equal teams, home field) | 100 | 200 | 400 |
|---|---|---|---|---|---|
| $P(\text{home win})$ | 50% | 56.9% | 64.0% | 76.0% | 90.9% |

**After the game**, the winner takes points from the loser:

$$
R_H \leftarrow R_H + K\,M\,(S - P),\qquad R_A \leftarrow R_A - K\,M\,(S - P),\qquad K = 20
$$

- $S$ is the result: 1 for a home win, 0.5 for a tie, 0 for a loss.
- $S - P$ is the **surprise**. A big favorite that wins gains little; an upset swings a lot.
- $M$ is the margin-of-victory multiplier:

$$
M = \ln(|\text{margin}| + 1)\times\frac{2.2}{0.001\,D_{\text{winner}} + 2.2}
$$

- $\ln(|\text{margin}|+1)$: blowouts count more, with diminishing returns.
- The fraction: $D_{\text{winner}}$ is the winner's edge from $D$. It damps wins that were expected, so favorites can't run away with the scale.
- A tie uses $M = \ln 2$.

**Offseason:** every team moves a third of the way back to average, $R \leftarrow 1505 + \tfrac{2}{3}(R - 1505)$.

**Worked example.** Two equal teams at 1505, and the home team wins by 7:
1. $D = 48$, so $P = 0.5686$.
2. $M = \ln 8 \times \frac{2.2}{0.048 + 2.2} = 2.079 \times 0.979 = 2.035$.
3. The shift is $20 \times 2.035 \times (1 - 0.5686) = +17.6$.
4. Home goes to 1522.6 and away to 1487.4.

**How Elo is scored:** the Brier score, the average squared error of the probability:

$$
\text{Brier} = \frac{1}{N}\sum_g \big(P_g - \text{outcome}_g\big)^2
$$

| Case | Brier |
|---|---|
| Said 70% and the team won | $(0.7-1)^2 = 0.09$ |
| Said 70% and the team lost | $(0.7-0)^2 = 0.49$ |
| Always saying 50% | 0.25 |
| Elo, 2015–2025 | 0.2214, the bar P03 must beat |

### 9. Trend

**Change over 3 weeks:**

$$
\Delta_t(w) = net_t(w) - net_t(w-3)
$$

**Performance vs expectation:** the average gap between what happened and what the ratings predicted before each of the team's last $n \le 3$ games (prediction from step 7, taken from the team's side):

$$
\text{pve}_t(w) = \frac{1}{n}\sum_{g \in \text{last } n \text{ games}}\big(m_{t,g} - \widehat{m}_{t,g}\big)
$$

**Direction.** $Q_{0.2}$ and $Q_{0.8}$ are the 20th and 80th percentiles of $\Delta$ in earlier seasons only. Each label covers about 20% / 60% / 20% of team-weeks.

| Label | Condition |
|---|---|
| up | $\Delta > Q_{0.8}$ |
| down | $\Delta < Q_{0.2}$ |
| stable | otherwise |

**Drivers.** Each part's contribution to the change is weighted by how much of the game it covers. $\pi$ is the league's share of dropbacks (about 0.6), measured as of the same week:

| Part | Contribution |
|---|---|
| pass offense | $\pi \cdot \Delta o^{\text{pass}}$ |
| rush offense | $(1-\pi) \cdot \Delta o^{\text{rush}}$ |
| pass defense | $\pi \cdot (-\Delta\delta^{\text{pass}})$ |
| rush defense | $(1-\pi) \cdot (-\Delta\delta^{\text{rush}})$ |

Defense is sign-flipped because allowing less is better.

| Example change | Contribution |
|---|---|
| Pass offense +0.10 | $0.6 \times 0.10 = +0.06$, the top driver |
| Rush defense allows 0.10 less | $0.4 \times 0.10 = +0.04$ |
| Rush offense −0.05 | $0.4 \times -0.05 = -0.02$ |

**Does trend predict? (the validation).** $y$ is how well the team actually played over the next 3 weeks, adjusted for opponents. Two models are compared, each fit only on earlier seasons:

$$
\text{A: } y = a + b\cdot net_t(w) \qquad\qquad \text{B: } y = a + b\cdot net_t(w) + c\cdot \Delta_t(w)
$$

They are scored by out-of-sample $R^2$, the share of variation explained:

$$
R^2 = 1 - \frac{\sum (y - \hat y)^2}{\sum (y - \bar y_{\text{train}})^2},\qquad \Delta R^2 = R^2_B - R^2_A
$$

The result: $\Delta R^2 = -0.0002$. Knowing the trend adds nothing once you know the rating, so trends are reported as **descriptive** (D47).

## Parameters (`config/settings.yaml` → `ratings`)

| Parameter | Value | How chosen |
|---|---|---|
| `half_life_weeks` | 12 | W&B sweep `dwyj31wk`: 4 → MSE 0.1051, 12 → 0.1041, flat from 8 to 52 |
| `prior_regression` | 0.1 | 0.0–0.2 about equal; 0.5 hurts weeks 1–3 (0.1109 vs 0.1079) |
| `ridge_alpha` | 250 | 100 → 0.1063, 250 → 0.1041, 650 → 0.1048 |
| `qb_change_regression` | 0 | Sweep `pbpcouy1`: every extra pull is worse (0.5 → 0.1043) |
| `garbage_weight` / `garbage_wp` | 0.25 / 0.05 | Fixed, not tuned |

## Evaluation (walk-forward 2015–2025, 2,895 regular-season games)

Target: week-w per-play EPA margin (home offense − away offense), predicted from ratings as of week w as `net[home] − net[away] + home field`. Lower MSE is better. Eval run: [`hcir0po5`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/hcir0po5).

| | Ratings | Last season's raw net (baseline) | Season-to-date raw net (baseline) |
|---|---|---|---|
| MSE, pooled | **0.1041** | 0.1194 (−12.9%) | 0.1249 (−16.7%) |
| MSE, weeks 1–3 | **0.1079** | 0.1094 | 0.1909 |
| MSE, weeks 4–8 | **0.1042** | 0.1171 | 0.1195 |
| MSE, weeks 9+ | **0.1027** | 0.1240 | 0.1054 |
| Correlation with final point margin | **0.366** | 0.239 | 0.301 |

The ratings beat both baselines in **all 11 seasons**:

| Season | Games | Ratings MSE | Last-season MSE | To-date MSE | Elo Brier |
|---|---|---|---|---|---|
| 2015 | 256 | 0.1050 | 0.1274 | 0.1385 | 0.2242 |
| 2016 | 256 | 0.0943 | 0.1050 | 0.1079 | 0.2173 |
| 2017 | 256 | 0.0993 | 0.1132 | 0.1280 | 0.2168 |
| 2018 | 256 | 0.1244 | 0.1364 | 0.1452 | 0.2222 |
| 2019 | 256 | 0.1131 | 0.1296 | 0.1481 | 0.2217 |
| 2020 | 256 | 0.0950 | 0.1052 | 0.1101 | 0.2144 |
| 2021 | 272 | 0.1131 | 0.1232 | 0.1334 | 0.2310 |
| 2022 | 271 | 0.0817 | 0.0976 | 0.0933 | 0.2215 |
| 2023 | 272 | 0.1117 | 0.1184 | 0.1296 | 0.2326 |
| 2024 | 272 | 0.1081 | 0.1305 | 0.1239 | 0.2107 |
| 2025 | 272 | 0.0993 | 0.1272 | 0.1171 | 0.2223 |

**Elo:** walk-forward Brier **0.2214** over all game types (0.2217 regular season), log loss 0.635, accuracy 64%. It slightly over-rates home teams: mean probability 0.562 vs a 0.550 home win rate.

## Trend (D47: descriptive)

Validation run [`ae30tzkf`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ae30tzkf) asks whether `trend_delta` predicts a team's opponent-adjusted play over the next 3 weeks beyond its current rating. The answer is no:
- ΔR² is −0.0002 (90% CI −0.0013 to +0.0008), and it helps in only 5 of 11 seasons.
- `perf_vs_expected` is the same (ΔR² −0.0002).

So the digest describes form ("has improved") and never forecasts it.

## Known limits and biases

- **Early season:** weeks 1–3 are mostly the prior (`prior_weight` ≈ 1 in week 1). The digest shows lower confidence there.
- **Home field in EPA terms is small and noisy.**
  - It is held at a trailing 3-season value, so a sudden change (2020's empty stadiums) is adopted with a lag.
  - At the play level it comes out about 0 (−0.004 for 2026), because leading teams, often the home team, run low-EPA clock-killing plays.
  - The game-level home edge is about +0.01 EPA/play, so predicted margins understate home teams slightly (about 0.0002 of the 0.104 MSE).
  - P03's game model learns its own home-field term, which covers this.
- **QB change:**
  - The week-1 QB-change flag uses depth charts published by Tuesday of week 1.
  - The curated 2025+ week-1 charts are game-day snapshots, so the flag is unknown (False) for 2025–2026. That doesn't matter while `qb_change_regression` is 0. P03's QB-status feature needs Tuesday-dated depth charts.
- **In-sample tuning:** the parameters were tuned on 2015–2025 walk-forward, the same seasons P03 evaluates on. That's 3 tuned numbers on a flat surface, so the optimism is small but real.
- **Playoff weeks:** as-of rows exist for all 32 teams. Eliminated teams keep their last ratings, drifting slowly toward average as their data ages.
- **Postponed games** count from the week after their scheduled week, matching live runs, which wait for the week to finish (D44).
