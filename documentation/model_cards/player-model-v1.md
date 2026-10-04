# Model card: player model v1 (P06)

The overview card for all 11 player models. Per-family details (pools, settings, top drivers, football sense checks): [QB](player-qb.md) · [RB](player-rb.md) · [WR/TE](player-wrte.md) · [defense: EDGE/DL + LB/S](player-defense.md). A plain-language walkthrough of how projections reach the digest: [player projections guide](../guides/player-projections.md). Decisions: D64–D69.

## What it is

A projection, with an 80% range, of each player's **main stats for next week**, on offense and defense:

| Group | Pool (who is projected and scored) | Targets (model key) |
|---|---|---|
| QB | The team's main QB in the game (most dropbacks); live: the P03 expected starter | passing yards (`pass_yds-qb`), EPA per dropback (`pass_epa-qb`) |
| RB | Running backs with offensive snaps | rushing yards (`rush_yds-rb`), carries (`carries-rb`), receptions (`receptions-rb`), scrimmage yards (`scrim_yds-rb`) |
| WR/TE | Wide receivers and tight ends with offensive snaps (one model, a TE flag) | receiving yards (`rec_yds-wrte`), targets (`targets-wrte`), receptions (`receptions-wrte`) |
| EDGE/DL | Defensive linemen **and linebackers** with defensive snaps (nflverse labels many edge rushers LB) | pressures (`pressures-edge`, PFR, 2018+) |
| LB/S | Linebackers and safeties with defensive snaps | tackles (`tackles-lbs`, solo + assisted) |

The main stat of each group (passing yards, rushing yards, receiving yards, pressures, tackles) feeds **Players to watch**. Every projection feeds the **accuracy scoreboard**, the `PlayerProjection` nodes in the graph and `predictions_players.parquet`.

**Grain:** one row per player × regular-season game × target. **Scored only when he played** (documentation/11). **Training window:** 2013 (the first season with snap counts) to the week before the projection; pressures from 2018. **Final backtest runs** (after the Sol review: same metrics, refreshed watch-list role flags): the W&B column below; the earlier final-round runs (`5osf2x54` …) are superseded. **Production version:** `player-model:2026-w04` (the first live fit), refit every week.

## How it works, in plain language

1. **What he usually does.** For each player and stat, the **baseline** ("player rolling") is half his last-4-games average and half his season-to-date average, pulled toward last season's per-game average as if last season were 3 extra games. That pull matters most early in the year. With no game this season it is last season's average; with no history at all it is the average for players in his role (his snap share in his previous game), learned from earlier seasons only.
2. **What's different this week.** 126 features (123 without the market ones) describe him and his situation as of the Tuesday before the game: his recent output and usage (snap share, target / carry / air-yard / red-zone shares), his efficiency (yards per target, NGS separation and rush yards over expected, PFR drops and broken tackles, FTN play action and blitzes), his team (pass rate over expected, pace, P03's predicted points and margin for this game, the market's implied team total), the opponent (yards and targets it allows to his position group beyond what those offenses usually gain; pressure and blitz rates; its ratings), ripple effects (usage left by teammates who missed the last game or are ruled out; how often he has caught passes from this week's QB), and his own injury-report status.
3. **The projection.** For yards and EPA, three LightGBM models learn the 10th, 50th and 90th percentile of the outcome; the 50th is the projection. For counts (carries, targets, receptions, pressures, tackles), a LightGBM Poisson model learns the expected value, and a negative-binomial distribution around it gives the median (the projection) and the range.
4. **An honest range.** The raw ranges are then calibrated on the model's own earlier walk-forward misses (the last 2 seasons): yards ranges are widened or narrowed by the amount that makes 80% of past outcomes fall inside (conformal calibration), and count ranges pick the tail level (10–25%) that does the same.
5. **Why.** LightGBM's SHAP values split each projection into feature contributions; the top 3 become "drivers" phrased from `features/descriptions.yaml`.
6. **Refit every week**, from scratch, on everything before the week, current season weighted 3×, last season 1.5× (D28).

## The math, step by step (with worked examples from the live 2026 week-4 fit)

Every step below names the function that does it (`file.py:line` as of commit `2ab11b4`; the function name is the stable pointer if lines move). The worked numbers come from `models/player-model/2026-w04/meta.json` and `runs/2026/week04/predictions_players.parquet`.

### 0. Notation and the time rule

- A row is one player × one game × one target; `y` is the target stat (e.g. his rushing yards in that game).
- Time is one continuous week index, `t = (season − 2000) × 22 + week` (`features/player_data.py:175` `tkey`), so week 1 of 2026 follows week 18 of 2025.
- **As-of rule:** every number used for a row at time `t` comes from rows with `t' < t`. PFR / FTN inputs and the pressures label use `t' < t − 1` because they publish a week late (D44). One function does every look-up: `asof_join` (`features/player_data.py:182`), "the latest history row strictly before this row" (with `lag=1`: strictly before last week).

### 1. The rolling baseline `B` (what he usually does)

For one player and one target, from his earlier games **in that target's pool** (`own_features`, `features/player.py:267`):
- `n` = his games this season so far; `L4` = mean of his last 4 of them; `S` = his season-to-date mean;
- `LS` = last season's per-game mean (only if he played 2+ games then, `MIN_LAST_GAMES`);
- `R` = the **role average**: the mean of `y` over every pool row in *earlier seasons* whose previous-game snap share fell in the same 20%-wide bucket (`role_table`, `features/player.py:337`; buckets `SNAP_BUCKETS`, line 83).

Then (`baselines`, `features/player.py:388`; `K_LAST = 3`, line 71):

```
mix = 0.5 × L4 + 0.5 × S
B   = (n × mix + 3 × LS) / (n + 3)     if n ≥ 1 and LS exists   ("rolling")
    = mix                               if n ≥ 2, no LS
    = (n × mix + 2 × R) / (n + 2)       if n = 1, no LS (a rookie after 1 game: 2/3 role average)
    = LS                                if n = 0 and LS exists  ("last_season")
    = R                                 no history at all       ("role")
```

**Worked example, Braelon Allen (Jets RB), rushing yards, week 4.** 2026 weeks 1–3: 40, 7, 14 yards, so `L4 = S = 20.33`, `mix = 20.33`; 2025: 19.0 per game over 4 games. `B = (3 × 20.33 + 3 × 19.0) / 6 = 19.67` (the digest's "baseline 20 rushing yards"). The 3 pseudo-games are why last season still counts half after 3 games and a quarter after 9.

### 2. Sample weights (D28)

Each training row gets a weight by season relative to the week being predicted: **3** for the current season, **1.5** for last season, **1** for older (`SampleWeights.for_rows`, `models/backtest.py:62`; values in `config/settings.yaml:53` `training.sample_weights`). Older seasons teach *how much* form, matchups and injuries matter; current-season rows carry the form itself.

### 3. Amounts (yards, EPA per dropback): three quantile models

LightGBM builds an ensemble `F(x) = Σ_m η · f_m(x)`: `M` small regression trees (`n_estimators`), each with at most `num_leaves` leaves of at least `min_data_in_leaf` rows, each fitted to what the previous trees still get wrong, scaled by the learning rate `η = 0.05`. Every tree sees a random 80% of the features and of the rows (`feature_fraction`, `bagging_fraction`), and leaf values are shrunk by an L2 penalty `λ = 1` (`PlayerModelConfig.lgb_params`, `models/player_model.py:75`).

For a quantile model the loss is the **pinball loss** at level `τ`:

```
ρ_τ(y, q) = τ × (y − q)          if y ≥ q     (under-predicting costs τ per yard)
          = (1 − τ) × (q − y)    if y < q     (over-predicting costs 1 − τ per yard)
```

The value that minimizes the average pinball loss is the `τ`-quantile of `y`. Three separate models are fitted per target, `τ = 0.1, 0.5, 0.9` (`QUANTILES`, `models/player_model.py:42`; `fit_player_model`, line 223): **q50 is the projection** (the median: the MAE-optimal single guess for skewed yardage), q10 and q90 the raw range. They are sorted so `q10 ≤ q50 ≤ q90` (`FittedPlayerModel.raw`, line 177).

### 4. The conformal shift (an honest 80% range for amounts)

On the model's **own earlier walk-forward predictions** of the last 2 seasons (`H`, at least 300 rows; `_recent_history`, `models/player_model.py:311`), score each by how far outside its raw range the actual value fell:

```
E_j = max(q10_j − y_j,  y_j − q90_j)          (E ≤ 0 means inside the range)
s   = the ⌈0.8 × (n + 1)⌉-th smallest E_j      (the 80% "conformal" quantile)
P10 = q10 − s,   P90 = q90 + s
```

(`conformal_shift`, `models/player_model.py:148`; applied in `FittedPlayerModel.predict`, line 189.) A positive `s` widens the range, a negative one narrows it; by construction about 80% of the history falls inside, and it holds out of sample (80–82% in the backtests).

**Worked example, Trevor Lawrence, passing yards.** The live fit's shift is `s = 5.4` yards (`meta.json` → `pass_yds-qb.range_param`): raw quantiles 172.1 / 258.0 / 342.7 → published **258, range 167–348**. For receiving yards `s = 0.0`: the raw quantile models already covered 81%.

### 5. Counts (carries, receptions, targets, pressures, tackles): Poisson + negative binomial

**The mean.** One LightGBM model with the Poisson objective: `μ(x) = exp(F(x))` (log link), trained on the Poisson negative log-likelihood `μ − y × log μ` per row. `μ` is the expected count (`fit_player_model`, the `"mean"` booster).

**The spread.** Real counts are more spread out than Poisson (variance = mean). The outcome is a **negative binomial** with mean `μ` and dispersion `r`: `Var(Y) = μ + μ² / r` (large `r` → Poisson). `r` is fitted by the method of moments on the walk-forward history `H`:

```
mean((y − μ)²) = mean(μ) + mean(μ²) / r     ⇒     r = mean(μ²) / (mean((y − μ)²) − mean(μ))
```

(`nb_dispersion`, `models/player_model.py:126`; `r` is capped at 10,000 = Poisson when there's no excess spread.) Quantiles come from `scipy.stats.nbinom(n = r, p = r / (r + μ))` (`count_quantiles`, line 139). **P50 is the median** (the projection the scoreboard grades); the reader sees `μ` (one decimal), because a small count's median is too coarse.

**The range.** Whole-number quantiles at exactly 10% / 90% hold more than 80% (88% of tackles in the smoke run). So the tail level `a` is chosen from 0.10, 0.125, …, 0.25 (`TAILS`, line 46) as the one whose `[Q(a), Q(1 − a)]` covered closest to 80% of the history (`count_tail`, line 294).

**Worked example, Jake Hansen (Texans LB), tackles.** `μ = 5.29`; the live tackles model has `r = 8.74`, so `Var = 5.29 + 5.29² / 8.74 = 8.49` (sd 2.9). Median **5**; at the chosen tail 0.15 the range is **2–8** (at 0.10 it would be 2–9). The digest shows "5.3 tackles, range 2–8".

### 6. The fair baseline for counts: `baseline_p50` (D65)

A median beats any mean on MAE for a skewed, zero-heavy stat, by itself. So a count's projection (a median) is compared with the baseline turned into the same kind of number: the **median of the same negative binomial placed at the baseline's mean**, `baseline_p50 = median NB(μ = B, r)` (`baseline_p50`, `models/player_model.py:385`). For amounts `baseline_p50 = B`.

**Worked example, Abdul Carter (Giants), pressures.** `B = 1.6`, `r = 2.81` (pressures are very spread out) → `baseline_p50 = 1`. Hansen: `B = 3.17` → 3. Comparing Carter's median projection (2) with 1.6 instead of 1 would credit the model with a gain that is only the median-vs-mean effect: that is how pressures first looked 16% better instead of 3%.

### 7. How each projection is scored (the scoreboard)

On the rows where he played and the label is known (`scoreboard_rows`, `models/player_runs.py:324`; pooled: `summarize`, line 377):

```
MAE_model    = mean |y − P50|              MAE_baseline = mean |y − baseline_p50|
improvement  = 100 × (MAE_baseline − MAE_model) / MAE_baseline
coverage_80  = share of rows with P10 ≤ y ≤ P90
rank corr.   = Spearman( projection − B ,  y − B )
```

### 8. The gap to baseline and its z-score (the watch list's ranking)

`gap = projection − B` (projection = P50 for amounts, `μ` for counts) and `z = gap / scale`, where `scale` = the target's typical baseline miss, `mean |y − B|` over the training rows of the last 2 seasons (`_scale`, `models/player_model.py:217`; `assemble`, `models/player_runs.py:217`). Dividing by `scale` puts yards and pressures on one footing.

**Worked example, Hansen:** `(5.29 − 3.17) / 1.98 = 1.07`: the week's biggest jump among role players, so he's the first pick. The watch list then ranks role players by `z` (×1.15 for a followed team) under the caps of D69 (`select_watchlist`, `models/player_watch.py:150`).

### 9. The drivers (SHAP)

TreeSHAP splits each prediction exactly: `F(x) = φ₀ + Σ_j φ_j`, where `φ₀` is the model's average output over its training rows and `φ_j` is feature `j`'s contribution (`FittedPlayerModel.contributions`, `models/player_model.py:207`, LightGBM `pred_contrib=True`). For amounts `φ_j` is already in yards. For counts it is on the log scale; it is converted to counts at first order, `φ_j × μ`, because `dμ/dF = μ`. The 3 largest `|φ_j|` are kept (`top_drivers`, line 400) and phrased from `features/descriptions.yaml`.

**Worked example, Braelon Allen, rushing yards (gap +17.3):** `rip_out_car` (carries left open by teammates ruled out: Breece Hall's 0.70 share) **+11.3**, `own_ewm` (his recent form) **−4.8**, `use_snap_l1` (52% snaps last game) **+2.5**. Because `φ` is measured against `φ₀` (a typical player in his group), the digest words it that way ("puts the projection 11 rushing yards above a typical player in his group") and shows a driver only if it is at least 20% of the gap (`driver_texts`, `digest/players.py:78`; D69).

### 10. Walk-forward training and testing

For every week key `k` from 2017 week 1 to 2025 week 18 (158 keys per target, pressures from 2018; `run_backtest`, `models/player_runs.py:523`):
1. `train` = every labelled row with `t < t_k` (pressures: `t < t_k − 1`, `Target.label_lag`), with its weight;
2. `history` = this run's earlier predictions (for the shift / dispersion / tail);
3. fit the boosters (§3 or §5) and predict week `k`'s rows;
4. the harness raises `LeakageError` if `train` or `history` reach `k`, or `test` leaves week `k` (`walk_forward`, `models/backtest.py:99`; `_check_boundary`, line 89).

2017–2018 are burn-in (they build the history the ranges are calibrated on); 2019–2025 are reported.

### 11. Tuning (D68)

For each target, 12 settings (`TUNE_GRID`, `models/player_runs.py:777`: `num_leaves` 7 / 15 / 31 × `min_data_in_leaf` 50 / 200 × `n_estimators` 150 / 300). Each is scored by a walk-forward over **every other week (odd weeks) of 2017–2018 only**: fit on all earlier rows, predict that week with the q50 model (amounts) or the NB median (counts), and average `|y − prediction|` (`tune_objective`, line 784; one W&B sweep per target via `run_tune`, line 834). The lowest MAE wins and goes into `config/settings.yaml:78–88` (`player_model.per_target`). Weeks with under 2,000 training rows are skipped (pressures, a 2018+ PFR stat, is tuned on late 2018), and the pressures label lag applies here too.

### 12. The weekly (live) fit

`run_train` (`models/player_runs.py:911`): rebuild every feature with the live week's expected players added (`upcoming_rows`, `features/player.py:126`), then for each target run the same walk-forward over weeks 1..N of the current season, **seeded with the canonical backtest's last 2 seasons as history** (`_seed_history`, line 899), so the shift / dispersion / tail come from real earlier misses. The fit at key N predicts the live rows. The kickoff cutoff and `created_at` are taken when the projections are saved; a game that already kicked off gets no new projection. Boosters go to `models/player-model/<season>-w<NN>/<target-key>-<q10|q50|q90|mean>.txt` and the W&B artifact `player-model`.

## Where each model lives in the code

All 11 models share the same code path; what differs per model is one line in the target registry, its pool, its label column and its settings line.

| Model | Target registry (`models/player_schema.py`) | Pool (`pool_expr`, line 91) | Label column (`player_history`) | Settings (`config/settings.yaml`) | Boosters per weekly fit |
|---|---|---|---|---|---|
| `pass_yds-qb` | line 59 | QB, main QB of the game | `passing_yards` | line 78 | `pass_yds-qb-q10/q50/q90.txt` |
| `pass_epa-qb` | line 60 | QB, main QB | `epa_per_db` (`qb_epa` sum / dropbacks) | line 79 | `pass_epa-qb-q10/q50/q90.txt` |
| `rush_yds-rb` | line 61 | RB, offensive snaps | `rushing_yards` | line 80 | `rush_yds-rb-q10/q50/q90.txt` |
| `carries-rb` | line 62 | RB | `carries` | line 81 | `carries-rb-mean.txt` |
| `receptions-rb` | line 63 | RB | `receptions` | line 82 | `receptions-rb-mean.txt` |
| `scrim_yds-rb` | line 64 | RB | `scrimmage_yards` (rushing + receiving) | line 83 | `scrim_yds-rb-q10/q50/q90.txt` |
| `rec_yds-wrte` | line 65 | WR + TE, offensive snaps | `receiving_yards` | line 84 | `rec_yds-wrte-q10/q50/q90.txt` |
| `targets-wrte` | line 66 | WR + TE | `targets` | line 85 | `targets-wrte-mean.txt` |
| `receptions-wrte` | line 67 | WR + TE | `receptions` | line 86 | `receptions-wrte-mean.txt` |
| `pressures-edge` | line 68 (start 2018, `label_lag` 1) | DL + LB, defensive snaps | `pressures` (PFR `def_pressures`) | line 87 | `pressures-edge-mean.txt` |
| `tackles-lbs` | line 69 | LB + S, defensive snaps | `tackles` (solo + assists) | line 88 | `tackles-lbs-mean.txt` |

The pipeline, in order:

| Step | File : line | Function |
|---|---|---|
| Load the curated tables | `features/player_data.py:250` | `load_inputs` |
| One row per player × game, labels, `pgroup`, `main_qb`, PFR zero-fill | `features/player_data.py:484` | `player_history` |
| Prediction rows (past games / the live week's expected players) | `features/player.py:98`, `:126` | `history_rows`, `upcoming_rows` |
| Shared features (usage, team, ripple, availability, efficiency, opponent) | `features/player.py:909` | `build_features` (+ `player_efficiency.py:407`, `player_opponent.py:332`) |
| One target's frame: pool rows, label `y`, `own_*`, baselines | `features/player.py:958` | `target_frame` |
| Fit the boosters, the shift / dispersion / tail | `models/player_model.py:223` | `fit_player_model` |
| A harness model (one weekly refit) | `models/player_model.py:329` | `PlayerWeekModel` |
| Walk-forward | `models/backtest.py:99` | `walk_forward` |
| Prediction rows → `predictions_players.parquet` columns, confidence, drivers | `models/player_runs.py:217` | `assemble` |
| Backtest / tune / weekly fit / scoring | `models/player_runs.py:523`, `:834`, `:911`, `:1166` | `run_backtest`, `run_tune`, `run_train`, `score_weeks` |
| Watch list, tough spots | `models/player_watch.py:150`, `:174` | `select_watchlist`, `tough_spots` |
| Digest wording (drivers, notes, highlights, look-back) | `digest/players.py:78`, `:113`, `:484`, `:346` | `driver_texts`, `baseline_note`, `scoreboard_highlights`, `lookback_text` |
| Graph nodes | `graph/projections.py:116` | `write_projections` |
| The weekly step | `weekly.py:119` | `_player` |

## How the 11 models were trained and tuned (every round)

All of P06 happened on 2026-10-04. Every round is a full walk-forward backtest of all 11 models (158 weekly refits each), logged to W&B (group `track1-player`, job type `backtest`, run name `backtest-<model>`).

| Round | What changed (and where) | Settings | How counts were scored |
|---|---|---|---|
| Smoke (`xoahaq4o`, tackles, 2025 only) | First check of the W&B logging. Count ranges held 88% → `count_tail` added (`models/player_model.py:294`) | default 15 / 100 / 300 | raw mean |
| **Round 1** (first backtests) | First full run; it showed the count gains were inflated → D65 (`baseline_p50`, `models/player_model.py:385`) | default 15 leaves / 100 rows per leaf / 300 trees, all models | raw mean (superseded) |
| Review fixes (no new round) | sonnet-xhigh's review: `avail_listed` removed (era break), vacated share only for recent absences (`RECENT_WEEKS`), returning starters projected live (`RETURN_GAMES`, `RETURN_SNAP`), pressures labels lag a week (`Target.label_lag`); feature table rebuilt (`nfl features player`) | – | – |
| Tuning (D68) | 11 grid sweeps on 2017–2018 → `config/settings.yaml:78–88` | per model, table above | median |
| **Final** (tuned) | Tuned settings, `baseline_p50`, the review fixes, the new confidence rule (`assemble`) | per model | median |
| No-market check | Same as final without `team_mkt_*` (`--no-market`, leakage rule 5) | per model | median |
| **Final v2** (after the Sol review) | `open_tgt` / `open_car` for the watch list's role change (`ripple_features`, `features/player.py:657`); identical metrics; pressures re-tuned with the label lag (same settings) | per model | median |

Per model, the improvement over the baseline in each round (W&B run ids under the numbers):

| Model | Round 1 (default settings, raw mean) | Final, tuned (median baseline) | Final v2 (published numbers) | No market lines |
|---|---|---|---|---|
| `pass_yds-qb` | +4.3% `uri858o1` | +5.0% `5osf2x54` | +5.0% `5t3sgsfb` | +4.6% `b5ih5plm` |
| `pass_epa-qb` | +3.6% `q448e9cd` | +4.7% `opw6jalf` | +4.7% `hmeyyis1` | +3.9% `8bcl12h4` |
| `rush_yds-rb` | +8.1% `l8ackyb0` | +7.9% `sygypd87` | +7.9% `sbvbkqcj` | +8.1% `crppcfkc` |
| `carries-rb` | +8.4%\* `86mpdcx2` | +6.9% `qtrkdlyq` | +6.9% `sp9b4l9w` | +6.9% `ea8gjgan` |
| `receptions-rb` | +8.4%\* `ytzjlzxr` | +2.7% `93ov3uz8` | +2.7% `xjgpuxqf` | +2.3% `ultxr45c` |
| `scrim_yds-rb` | +7.3% `znunyf6c` | +7.4% `cwhxrmev` | +7.4% `l9oko9q2` | +7.3% `kvg3x7g1` |
| `rec_yds-wrte` | +9.1% `gu5b2wxv` | +9.2% `wpwv6zd2` | +9.2% `xzmsy4ac` | +9.2% `clfrac2z` |
| `targets-wrte` | +8.0%\* `1yq342zo` | +5.2% `hqmugbgz` | +5.2% `q0xk19h9` | +5.2% `vc1nnqax` |
| `receptions-wrte` | +7.7%\* `6vz8zisd` | +3.4% `h1vbygqw` | +3.4% `es0ls811` | +3.4% `ls6t4bxm` |
| `pressures-edge` | +16.0%\* `z35epws7` | +3.0% `ek0sc1ld` | +3.0% `z2mwdzna` | +3.0% `b1nhvc9a` |
| `tackles-lbs` | +6.8%\* `r18wcwa2` | +4.8% `7a77jo45` | +4.8% `m1iw9pyp` | +4.7% `xo7ckwaw` |

\* Measured against the raw rolling mean, the comparison D65 replaced: the drop to the next column is mostly the fairer yardstick, not a worse model (the model's own MAE barely moved, e.g. pressures 0.5546 → 0.5557, tackles 1.790 → 1.796). For yardage models the yardstick didn't change, so the round-1 → tuned difference is the tuning plus the review's feature fixes (−0.2 to +1.1 points).

Live fits (job type `train`, `train-2026-w04`): `u5k0ivia` (first live pass), `kl4fzvl8` (after the Sol fixes; behind the published digest). Each weekly fit is the same code as one walk-forward key, so it needs no separate tuning.

## Settings (tuned on 2017–2018 only, D68)

A 12-point grid per target (`num_leaves` 7 / 15 / 31, `min_data_in_leaf` 50 / 200, `n_estimators` 150 / 300; learning rate 0.05, feature and row subsampling 0.8, L2 1.0), scored by a walk-forward over every other week of 2017–2018. The grid is flat (best to worst 0.9–3.1% in MAE), so the choice matters little; small, heavily regularized trees win for QBs and pressures (7 leaves, 200 rows per leaf), deeper ones for rushing (31 leaves).

| Model | Leaves | Rows per leaf | Trees | Sweep |
|---|---|---|---|---|
| `pass_yds-qb`, `pass_epa-qb` | 7 | 200 | 150 | `rcyuv6pl`, `cygm4hbw` |
| `rush_yds-rb`, `scrim_yds-rb` | 31 | 200 | 150 | `glu9cmj8`, `ywgzufw3` |
| `carries-rb`, `receptions-rb` | 15 | 50 | 300 | `4yqdpr6c`, `0z30t3eb` |
| `rec_yds-wrte` | 15 | 200 | 150 | `awk1hndy` |
| `targets-wrte` | 31 | 200 | 300 | `3w91p7lu` |
| `receptions-wrte` | 7 | 50 | 300 | `lhxih9f7` |
| `pressures-edge` | 7 | 200 | 150 | `upmglalz` (re-run with the label lag after the Sol review; `qnn35nz6` picked the same) |
| `tackles-lbs` | 15 | 50 | 150 | `uvajbbn7` |

Last tuned 2026-10-04. Hyperparameters stay fixed for the season (doc 04).

## Results: walk-forward 2019–2025 (every regular-season week, refit weekly)

**MAE** is the average miss, in the stat's units. **Improvement** compares the model's projection with the rolling baseline **on the same rows**. For counts the baseline is turned into the same kind of projection (the median of the same distribution at the baseline's mean, D65), because a median beats any mean on MAE by itself for skewed, zero-heavy stats. "vs season mean" is the improvement over the plain season-to-date average (rows where one exists). "80% range held" is the share of outcomes inside P10–P90. "Rank corr." is the Spearman correlation between the projected and the real gap to baseline: does the model know *who* will beat his usual?

| Model | Player-games | MAE model | MAE baseline | Improvement | vs season mean | 80% range held | Rank corr. | Seasons better | No market lines | W&B |
|---|---|---|---|---|---|---|---|---|---|---|
| `pass_yds-qb` | 3,733 | 56.6 | 59.5 | **+5.0%** | +10.7% | 81% | 0.29 | 7 / 7 | +4.6% | `5t3sgsfb` |
| `pass_epa-qb` | 3,733 | 0.230 | 0.241 | **+4.7%** | +10.3% | 80% | 0.30 | 7 / 7 | +3.9% | `hmeyyis1` |
| `rush_yds-rb` | 10,382 | 19.0 | 20.7 | **+7.9%** | +7.5% | 81% | 0.31 | 7 / 7 | +8.1% | `sbvbkqcj` |
| `carries-rb` | 10,382 | 3.39 | 3.64 | **+6.9%** | +7.6% | 80% | 0.36 | 7 / 7 | +6.9% | `sp9b4l9w` |
| `receptions-rb` | 10,382 | 1.09 | 1.12 | **+2.7%** | +9.8% | 81% | 0.28 | 7 / 7 | +2.3% | `xjgpuxqf` |
| `scrim_yds-rb` | 10,382 | 23.5 | 25.3 | **+7.4%** | +7.4% | 80% | 0.30 | 7 / 7 | +7.3% | `l9oko9q2` |
| `rec_yds-wrte` | 29,266 | 16.5 | 18.2 | **+9.2%** | +9.1% | 82% | 0.30 | 7 / 7 | +9.2% | `xzmsy4ac` |
| `targets-wrte` | 29,266 | 1.62 | 1.70 | **+5.2%** | +7.5% | 81% | 0.30 | 7 / 7 | +5.2% | `q0xk19h9` |
| `receptions-wrte` | 29,266 | 1.20 | 1.24 | **+3.4%** | +7.7% | 80% | 0.27 | 7 / 7 | +3.4% | `es0ls811` |
| `pressures-edge` | 45,041 | 0.556 | 0.573 | **+3.0%** | +17.0%\* | 85% | 0.33 | 7 / 7 | +3.0% | `z2mwdzna` |
| `tackles-lbs` | 33,949 | 1.80 | 1.89 | **+4.8%** | +7.4% | 81% | 0.31 | 7 / 7 | +4.7% | `m1iw9pyp` |

\* The season mean is a mean, not a median: against it every count target looks better than it is (pressures most). The "Improvement" column is the fair comparison. Against the raw rolling mean the count models look better still (pressures 0.556 vs 0.660), which is the trap D65 avoids.

**Every model beats its baseline in every one of the 7 seasons** (the smallest season gains: RB receptions +0.9% in 2020, passing yards +1.7% in 2020, pressures +2.3% in 2020):

| Model | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|
| `pass_yds-qb` | +4.6 | +1.7 | +6.3 | +7.0 | +3.6 | +5.6 | +6.2 |
| `pass_epa-qb` | +6.6 | +3.9 | +5.6 | +3.5 | +4.1 | +3.4 | +5.8 |
| `rush_yds-rb` | +7.3 | +7.5 | +8.5 | +8.9 | +9.4 | +6.2 | +7.6 |
| `carries-rb` | +6.6 | +6.7 | +7.2 | +5.8 | +9.1 | +6.3 | +6.8 |
| `receptions-rb` | +1.5 | +0.9 | +3.4 | +3.0 | +3.2 | +3.2 | +4.1 |
| `scrim_yds-rb` | +7.0 | +6.9 | +7.0 | +8.2 | +9.7 | +6.5 | +6.8 |
| `rec_yds-wrte` | +8.9 | +8.3 | +9.1 | +9.5 | +10.2 | +9.7 | +8.9 |
| `targets-wrte` | +5.3 | +4.9 | +4.1 | +5.3 | +4.7 | +6.8 | +5.2 |
| `receptions-wrte` | +3.0 | +3.3 | +3.9 | +2.0 | +3.3 | +5.3 | +2.8 |
| `pressures-edge` | +2.8 | +2.3 | +3.1 | +2.7 | +2.7 | +3.6 | +3.9 |
| `tackles-lbs` | +4.1 | +6.0 | +3.9 | +4.6 | +5.2 | +5.0 | +4.7 |

**Where the gain comes from.**
- **Early in the season the gain is largest** (weeks 1–4: rushing yards +11.2%, receiving yards +11.1%, scrimmage yards +10.2%; weeks 10+: +6.6%, +8.3%, +6.8%): the baseline leans on last season, while the model also sees role changes, new teammates and the team's context.
- **Thin history is where the model helps most.** For players whose baseline is the role average (rookies, new starters: 5,015 of 215,782 scored rows), the model is far better than that crude baseline (receiving yards +63%, rushing yards +49%, carries +39%, tackles +35%; QBs +8–11%, pressures +4%); with a full rolling baseline the gains are 2–7%. Ranges stay calibrated in every part of the season (79–86%).
- **The closing-line effect is small.** Historical market lines are closing lines, better informed than what a Tuesday run sees (doc 04, leakage rule 5). Without the market features (`--no-market`, runs `b5ih5plm`, `8bcl12h4`, `crppcfkc`, `ea8gjgan`, `ultxr45c`, `kvg3x7g1`, `clfrac2z`, `vc1nnqax`, `ls6t4bxm`, `b1nhvc9a`, `xo7ckwaw`) every model still beats its baseline in all 7 seasons. The QB models lose 0.4 and 0.9 points, RB receptions 0.4 and scrimmage yards 0.1; the rest move by under 0.1, and rushing yards gains 0.2 without them. The live run uses the current lines, so its true gain sits between the two columns.

**First round (before tuning and D65), for the record:** passing yards +4.3%, EPA per dropback +3.6%, rushing yards +8.1%, receiving yards +9.1%; counts looked far better against the raw rolling mean (pressures +16.0%, carries +8.4%, targets +8.0%), which D65 corrected.

## Players to watch (backtest)

The watch list (D69) picks 8 players a week by the projected gap to their baseline. In the 2019–2025 backtests (124 weeks, 992 picks), **69.5% of picks beat their baseline**, against a **42.2% base rate** for every eligible role player (exit criterion: > 50%). Every season is between 66.2% and 72.9%; every group beats its own base rate (QB 71.5 / 47.5, RB 68.1 / 44.7, WR/TE 68.3 / 40.2, EDGE/DL 61.1 / 37.3, LB/S 72.1 / 47.8). 77% of the picks' results fell inside their range. Caveat: backtest rows only exist for players who played, so a pick who sits out never happens there; live hit rates will be a little lower.

## Is a 3–9% gain good? (Why player stats barely beat a rolling average)

- **Doc 11's bar:** beating a good rolling average by 5–15% on yardage is a solid result; football player stats are very noisy. Receiving yards (+9.2%), rushing yards (+7.9%) and scrimmage yards (+7.4%) are in that band; passing yards (+5.0%) is at its edge.
- **The noise floor is high.** A receiver's yards swing with a handful of targets and one long catch: the receiving-yards MAE is 16.5 yards on a 26-yard average (median 13; a third of the games are 0). No model removes the play-to-play randomness; it can only move the center.
- **Counts are measured strictly.** Against the raw rolling mean, the count models look 6.5–15.8% better; against the fair median baseline (D65) they are 2.7–6.9% better. The smaller number is the honest one.
- **The ordering is real.** A rank correlation of 0.27–0.36 between the projected and the real gap to baseline means the model knows who is likely to beat his usual, which is what the watch list needs (69% hits vs a 42% base rate).

## Confidence labels (what they do and don't mean)

`confidence` is **low** when the history is thin (fewer than 3 games, no game this season, or the role-average baseline) or the range is wide for the projection's size (more than 1.5× the week's median relative width); **high** when he has 4+ games this season and a relatively narrow range; else **medium**. The labels mark thin history and wide ranges; they are **not** an accuracy ranking: relative errors are similar across labels, and the low-confidence rows are where the model gains most over its (weak) baseline. The digest uses them to make the writer hedge. Recalibrating them is a P08 item.

## Reading the W&B charts

All player-model runs are in W&B group **`track1-player`**, tagged `group:<qb|rb|wrte|edge|lbs>` and `target:<name>`.

### The metrics in one minute

| Metric | What it measures | Better | Our 2019–2025 values |
|---|---|---|---|
| **MAE** | Average absolute miss, in the stat's units | Lower | Receiving yards 16.5 (baseline 18.2), tackles 1.80 (1.89) |
| **Improvement %** | 100 × (baseline MAE − model MAE) / baseline MAE, on the same rows | Higher; > 0 = beats the baseline | +2.7% to +9.2% |
| **Coverage (80% range)** | Share of outcomes inside P10–P90 | ≈ 0.80 (ship bar 0.72–0.88) | 0.80–0.85 |
| **Spearman (rank corr.)** | Correlation of the projected and real gaps to baseline | Higher | 0.27–0.36 |
| **Range parameter** | Yards: the conformal shift (yards added to each end; can be negative or 0). Counts: the negative-binomial dispersion r (smaller = more spread) | Stable | Receiving yards shift ≈ 0 (raw quantiles are already calibrated); tackles r ≈ 9 |

### Backtest runs (`backtest-<target>-<group>`; job type `backtest`)

The x-axis of every `bt/*` curve is **`bt/step`**: the count of reported weeks so far (1 = 2019 week 1, about 124 = 2025 week 18).

| Chart | What it shows | How to read it |
|---|---|---|
| `bt/mae_model`, `bt/mae_baseline` | That week's MAE (about 30 QB rows, 300–400 for the big pools) | Jumpy. The model line should usually sit below |
| `bt/cum_mae_model`, `bt/cum_mae_baseline` | MAE of every row so far | **The main chart.** The gap opens in 2019 week 1 and stays: the final values are the table's |
| `bt/improvement_pct`, `bt/cum_improvement_pct` | Weekly and cumulative improvement % | The cumulative line settles within a few weeks; a weekly value below 0 happens and is noise |
| `bt/coverage_80`, `bt/cum_coverage_80` | Share of outcomes inside the range | Weekly 0.7–0.9; cumulative ≈ 0.80 (pressures 0.85: whole-number ranges of a stat that is often 0 or 1 can't be exactly 80%) |
| `bt/range_param` | The conformal shift (yards) or NB dispersion (counts) used that week | Should drift slowly; a jump means the earlier misses changed a lot |
| `bt/n`, `bt/season`, `bt/week` | Rows scored, and which week a step is | Lookups |
| `lgb/curve_<season>` (web only) | The season's opening fit replayed with the previous season held out: training vs validation loss per boosting round (quantile loss for yards, Poisson deviance for counts) | Validation should fall and flatten by the last round. A validation line that turns up while training keeps falling would mean overfitting (more trees than useful). With 150–300 trees at learning rate 0.05 it flattens, not turns |

**End-of-run panels:** `by_season` (the table above, per season), `mae_by_season` (model vs baseline by season), `accuracy_scoreboard` (one row per week), `feature_importance` (gain of the last fit, top 25), `shap_summary` (mean |SHAP| over 2025 in target units, top 20; the family cards say which features lead), `predictions` (2025 rows).

**Summary keys:** `n_scored`, `mae_model`, `mae_baseline` (counts: the median baseline), `mae_baseline_mean` (the raw rolling mean), `improvement_pct`, `mae_season_mean_same_rows`, `improvement_vs_season_mean_pct`, `coverage_80`, `spearman_outperformance`, `share_role_baseline`, `seasons_beating_baseline` of `seasons`.

**Research variant:** `backtest-<key>_nomarket` (tag `no-market`): the same run without the market features; saved to `runs/backtests/player/<key>_nomarket/`, never read by the digest.

### Sweeps (`tune-<target>-<group>`; job type `tune`)

One run per grid point, summary `tune/mae_model` (the objective), `tune/mae_baseline`, `tune/improvement_pct`, `tune/n`. On the sweep page, the parallel-coordinates chart shows a flat landscape (best to worst within 1–3%); parameter importance is not meaningful at that size. Pressures has three sweeps: `twoo1qm2` crashed (no 2017 training rows), `qnn35nz6` trained on the latest week's labels (a PFR leak the Sol review found), `upmglalz` is the final one; all valid ones picked the same settings.

### Weekly fit (`train-<season>-w<NN>`; job type `train`) and scoreboard (`scoreboard-<season>-w<NN>`; job type `eval`)

See the [W&B guide §4.7](../guides/weights-and-biases.md): `projections_main`, `projections_per_target`, `top_outperformance`, `low_confidence_share`; the `player-model` artifact (boosters + `meta.json`, description = this card); the scoreboard's `scoreboard/improvement_<target>_<group>` and `scoreboard/coverage_<target>_<group>` curves and the `accuracy_scoreboard` table.

## Outputs: `predictions_players.parquet`

One row per player × game × target (`models/player_schema.py` `PRED_SCHEMA`): identity (season, week, game, kickoff, player, team, opponent, home, position, pool group), the target (name, label, kind, unit, `is_main`), the projection (`p10`, `p50`, `p90`, `mean`), the baselines (`baseline`, `baseline_source`, `baseline_season_mean`, `baseline_role`, `baseline_p50`), the gap (`outperformance` = projection − baseline, `outperf_z` = gap / the target's typical baseline miss), context (`games_history`, `games_season`, `snap_share_l2`, `role_ok`, `role_change`, `vacated_share`, `injury_status`), `confidence`, `drivers` (top 3: feature, phrase, value, SHAP contribution), versioning (`model_version`, `feature_hash`, `trained_through`, `created_at`) and, once played, `actual` / `played`. Live: `runs/<season>/week<NN>/`; backtests: `runs/backtests/player/<key>/`.

## Known biases and limits

- **Only games he played.** Projections are scored only when he plays; availability itself isn't predicted (doc 11). The watch list's backtest hit rate is therefore a little optimistic.
- **Closing lines** in the historical market features (above; leakage rule 5).
- **The "Friday view" (D66):** the week's injury report is a feature. A Tuesday run has no report yet and sees "not listed".
- **Pressures** are PFR's count, a week late: the model trains only on weeks a Tuesday run would have, and its features lag one more week.
- **EDGE/DL includes off-ball linebackers** (their history keeps them near 0). A clean edge / off-ball split would sharpen the pool.
- **QBs:** the pool is the game's main QB, so a starter hurt early isn't scored and his backup is; live, the expected starter can be wrong (P03 resolver).
- **SHAP drivers explain the projection, not the gap to his baseline** (D69): a backup QB can be projected far above a thin baseline while every top driver points down. The digest lists agreeing drivers first and adds a baseline note.
- **Ripple effects:** a mid-season mover's season share mixes his teams (small, rare; sonnet-xhigh's review); the vacated share only counts regulars gone within the last 4 weeks.
- **Rookies and new starters** fall back to the role baseline and get `confidence: low`. Their carries ranges under-cover (60–69% for rookies and first games, vs 80% overall).
- **Range calibration is pooled per model:** WR/TE ranges run a little wide for tight ends (84%) and a little narrow for wide receivers' counts (78%); pressures ranges hold 85% because whole-number counts with P10 = 0 can't hit 80% exactly.
- **Injury designations:** the model captures about half of a player's own questionable / limited-practice effect, and misses the drop in pressures right after a missed game (sonnet-xhigh's sense check, family cards).
- **A proxy driver:** "carries the opponent allows to running backs" is a top-3 driver in 6% of 2025 passing-yards rows and always pushes up, but the real gap doesn't rise with it (rank correlation −0.001): a candidate to prune in v2.
- **Consistency across targets** (receptions ≤ targets, receivers' yards ≈ the QB's) isn't enforced yet (P08).

## Code review and verification

- **Leakage:** future-invariance tests for every feature family on a synthetic league (`tests/test_player_core.py`, `test_player_efficiency.py`, `test_player_opponent.py`); sonnet-xhigh's independent truncation check on the real data (inputs cut before 2024 week 10: 102 feature columns and every `own_*` / baseline column identical for earlier rows); the walk-forward harness's own boundary check.
- **Independent recomputation** (sonnet-xhigh): baselines 1,500 / 1,500 rows, ripple shares 400 / 400, the NB and conformal math in simulation.
- **Sol review:** see PROGRESS.md (P06 session log).

## Versioning

- Artifact `player-model` (type `model`), alias `<season>-w<NN>` per weekly fit; `production` = the fit the digest used (moved with `--promote`).
- Model version string in every projection: `player-model-v1:<season>-w<NN>` (backtests: `player-model-v1:backtest`).
- Settings: `config/settings.yaml` → `player_model` (+ `per_target`); feature hash per target in each run's config.
