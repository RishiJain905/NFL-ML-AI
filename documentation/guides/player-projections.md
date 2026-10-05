# Player projections guide (player model v1, plus the P08 targets)

**What this is:** a plain-language guide to the player model: what it predicts, the handful of modelling ideas it rests on (LightGBM, quantile regression, the negative binomial, conformal ranges, SHAP), how one week's projections flow into the digest and the knowledge graph, what files and W&B runs it leaves behind, and how to run and change it. Built in P06; P08 added twelve more targets (chance of a touchdown, sacks, interceptions, coverage stats for defensive backs and more), described in §2b and in the [P08 targets model card](../model_cards/player-p08.md).

**Related docs:** the spec is [04 → C. Player model](../04-track1-models.md#c-player-model) and [11 Prediction targets](../11-prediction-targets.md) (targets, baselines, the accuracy scoreboard). The results live in the model cards, [player-model-v1](../model_cards/player-model-v1.md) (P06) and [player-p08](../model_cards/player-p08.md) (the P08 targets). Decisions are in the [decisions log](../10-decisions-log.md) (D64 the model design, D65 count baselines, D66 availability as of Friday, D67 the weekly `player` step). The W&B side is in the [W&B guide](weights-and-biases.md); the graph side in the [knowledge-graph guide](knowledge-graph.md); how the digest's LLM writes about the picks in the [LLM writer guide](llm-digest-writer.md). Agents use the `model-experiment` and `digest-checks` skills.

---

## 1. What it does, in one example

Every week the model answers, for each player expected to play: *how much of his main stat will he produce, how sure are we, and is that more or less than his own normal?*

> **Puka Nacua** (Rams WR) vs the 49ers: projected **85 receiving yards**, likely range **60–135**, baseline **60** (his own rolling average). That's 25 yards above his baseline. Main driver: his share of the team's targets lately (puts the projection 15 receiving yards above a typical player in his group).

Three numbers carry the whole idea:

| Number | What it is | Where it comes from |
|---|---|---|
| **Projection** | the model's central estimate: the median for yards, the average for counts | P50 of the quantile model (yards) or the Poisson mean (counts) |
| **Range** | where 80% of outcomes should land (10th to 90th percentile) | P10 and P90, calibrated on the model's own past misses |
| **Baseline** | what he usually does: last 4 games blended with his season so far, pulled toward last season early in the year | `features/player.py` (documentation/11 → Baselines) |

The model is only useful if it beats the baseline: "projected 85 yards" means nothing if "he averages 84" would have been just as accurate. The **accuracy scoreboard** checks that every week, per target (§7).

## 2. The targets

One model per target × position group (`models/player_schema.py` → `TARGETS`). The **main** stat of each group drives *Players to watch*.

| Group | Who's in the pool | Main target | Other targets |
|---|---|---|---|
| QB | the team's main QB in the game (most dropbacks) | passing yards | EPA per dropback |
| RB | running backs with offensive snaps | rushing yards | carries, receptions, scrimmage yards |
| WR/TE | wide receivers and tight ends with offensive snaps | receiving yards | targets, receptions |
| EDGE/DL | defensive linemen **and linebackers** with defensive snaps (nflverse labels many edge rushers LB) | pressures (PFR, 2018+) | |
| LB/S | linebackers and safeties with defensive snaps | tackles | |

A linebacker can be in two pools (pressures and tackles): he gets both projections. Only games a player actually played count as targets ("scored only when he plays", documentation/11).

## 2b. The P08 targets (all 12 shipped, live from 2026 week 5; D82)

Twelve more targets use the same machinery (`phase="p08"` in `TARGETS`). **A P08 target is refit and projected by the weekly run only when it is listed in `config/settings.yaml` → `player_model.live_targets`**, which is how a target ships: it must first clear the pre-registered rule in §7. All 12 did (every one in 7 of 7 seasons), so the list now holds 23 targets. Next to them, the weekly `player` step also refits the **team stat totals** (passing / rushing yards, sacks made / taken; [team stats card](../model_cards/team-stats-v1.md)) and runs the **consistency layer** (WR/TE receptions capped at targets; the receivers-vs-QB-vs-team passing-yards gaps logged).

| Group | Target (key) | Kind | What it answers |
|---|---|---|---|
| QB | passing TDs (`pass_tds-qb`) | event count | expected passing TDs, chance of at least 1 and 2 |
| QB | interceptions thrown (`ints-qb`) | event count | expected interceptions, chance of at least 1 and 2 |
| QB | rushing yards (`rush_yds-qb`) | amount | like any yardage target (median and 80% range) |
| RB | chance of a touchdown (`td-rb`) | probability | calibrated chance of a rushing or receiving TD |
| WR/TE | chance of a touchdown (`td-wrte`) | probability | the same for receivers |
| EDGE/DL | sacks (`sacks-edge`) | event count | expected sacks, chance of at least 1 credited sack (a half-sack counts) |
| EDGE/DL | QB hits (`qb_hits-edge`) | count | like pressures |
| CB/S | targets, completions allowed in coverage (`cov_tgt-cbs`, `cov_cmp-cbs`) | counts | PFR coverage data, 2018+, a week late |
| CB/S | yards allowed in coverage (`cov_yds-cbs`) | amount | PFR coverage data, 2018+, a week late |
| CB/S | chance of an interception (`int-cbs`) | probability | calibrated chance of at least 1 |
| CB/S | chance of a pass defended (`pd-cbs`) | probability | calibrated chance of at least 1 |

**Three kinds of target** (`Target.kind`):
- **amount** and **count**: exactly as in P06 (§3).
- **event count** (a count with `event_probs`): also gets `p_ge1` and `p_ge2`, the chances of at least one and at least two. They come from the same negative binomial; `p_ge1` is then **recalibrated** on the model's own earlier walk-forward values, because a distribution fitted for the mean, the median and the range isn't exactly right about zero (passing TDs: 75% predicted vs 78% seen: a QB's TD count is more regular than a Poisson; sacks 15.5% vs 16.7%; the raw interception chances were too spread out, Platt slope 0.44).
- **probability** (`prob`): a yes/no event. A LightGBM *binary* model gives a raw probability; a **calibration layer** (Platt scaling: a logistic regression of the real outcome on the raw probability's log-odds, fitted on the model's own earlier walk-forward predictions, never on its own training rows) turns it into the probability shown. There is no range and no median: `p10` / `p50` / `p90` are empty, `mean` = `p_ge1` = the chance, and the baseline is his own rolling rate of the event. While the history is too thin (the first weeks of the burn-in) the raw probability is used.

**CB/S pool:** cornerbacks and safeties with defensive snaps (a safety is in the LB/S pool *and* this one). Their own family of features, `cvg_*` (`features/player_coverage.py`), describes what a back faces: how many passes, how deep, how often completed and for how many yards, for the opposing offense and for his own defense, plus his own defense's pressure rate and pass-defense rating. Only the CB/S models read it, so the P06 models' features are untouched.

**Labels** (`features/player_data.py`): `any_td` = rushing + receiving TDs ≥ 1 (return and passing TDs don't count); `def_int_any` = at least 1 interception; `pd_any` = at least 1 pass defended; `pfr_completions_allowed` from PFR. A half-sack is a credited sack (`def_sacks` 0.5): the "at least one" event for sacks means any credit, because that is what the negative binomial's probability matches (16.6% of EDGE/DL player-games, against 13.9% for a full sack or more).

## 3. The ideas it rests on (a short primer)

The formulas, worked examples with real week-4 numbers, the `file:line` of every step and the history of every training round are in the [player model card → The math, step by step](../model_cards/player-model-v1.md#the-math-step-by-step-with-worked-examples-from-the-live-2026-week-4-fit). This section is the plain-language version.

**Gradient-boosted trees (LightGBM).** A decision tree splits players into groups by asking yes/no questions about features ("target share over 22%?", "opponent allows many yards to WRs?") and predicts each group's typical outcome. *Boosting* fits hundreds of small trees one after another, each correcting what the previous ones got wrong. LightGBM is a fast implementation. The settings that matter: `num_leaves` (how many groups one tree can make: bigger = more detail, more overfitting), `min_data_in_leaf` (no group smaller than this), `n_estimators` (how many trees) and `learning_rate` (how much each tree corrects). They live in `config/settings.yaml` → `player_model`.

**Quantile regression (yards, EPA).** A normal model predicts the average. A *quantile* model predicts a percentile: trained with the "pinball" loss at 0.1, it learns the value that 10% of outcomes fall below. We fit three per target (0.1, 0.5, 0.9): the 0.5 model is the projection (the median), the other two are the raw range. Why the median for yards: yardage is skewed (a few 150-yard games, many 40-yard ones), and the median is the better single guess for "what will he do" on such a distribution.

**Poisson and negative binomial (counts: carries, targets, receptions, pressures, tackles).** Counts are whole numbers, often small and zero-heavy (most pass rushers get 0–3 pressures). LightGBM with a *Poisson* objective predicts the expected count (the `mean`). Real counts are more spread out than a Poisson allows, so the outcome is modelled as a *negative binomial* with that mean and a dispersion fitted on earlier misses (`Var = mean + mean² / r`). Its quantiles give the range (whole numbers). The digest shows the mean ("5.3 tackles") because a small count's median is too coarse ("5").

**Conformal calibration (the 80% range).** Raw quantile models are often over-confident. Conformal quantile regression fixes that empirically: look at the model's own walk-forward predictions over the last 2 seasons, measure how far outside its range each outcome fell, and widen (or narrow) both ends by the 80th percentile of those misses. Afterwards the range holds about 80% of outcomes by construction. Counts do the equivalent by picking the tail level (0.10–0.25) whose range covered closest to 80% on the same history.

**SHAP (the drivers).** SHAP splits one prediction into contributions, one per feature, that add up to "the prediction minus the model's average prediction". LightGBM computes them exactly for trees (`pred_contrib`). We keep the 3 largest and turn each feature name into a phrase (`features/descriptions.yaml`): "his share of the team's targets lately (puts the projection 9 receiving yards above a typical player in his group)". For counts the contributions are on the log scale and are converted to counts by multiplying by the mean. **What drivers can't say:** SHAP explains the projection against the *model's average player* in the pool, not against *this player's baseline or form*. Two consequences the P06 fact-check of the first live digest found:
- "His recent form raises the projection by 1.8 pressures" read as "he's in form" for a pass rusher whose recent games were *below* his own baseline: his form is above a typical player in the pool (off-ball linebackers included), not above his own normal. So every driver is worded against that reference: "puts the projection 1.8 pressures **above a typical player in his group**", and prompt rule 22 says a driver is never his form and never the reason for the gap to his baseline.
- A backup QB projected 61 yards above his thin baseline may have three drivers that all *lower* his projection, and a starter 29 yards above his may have a 3-yard "main driver": the gap comes from the baseline, not from the drivers. So a driver is shown only when it is at least **20% of the gap** to baseline (drivers pushing the same way as the gap first); otherwise the digest says "no single factor stands out". A baseline note says when the baseline rests on little (§6).

**Walk-forward (how it's tested).** To test week 7 of 2023, the model is fit only on rows before week 7 of 2023, predicts week 7, then moves to week 8 and refits. Nothing from the future leaks in. Backtests run 2017–2025: 2017–2018 are burn-in (their predictions calibrate the range and dispersion), 2019–2025 are reported. Hyperparameters were tuned on a 2017–2018 walk-forward only, never on the reported years.

## 4. How it's wired

```
curated Parquet (D:) ──► features/player_data.py   player_history: one row per player x game he played
                         features/player.py        features (own_, use_, team_, eff_, opp_, rip_, avail_)
                                                   + baselines (rolling, season mean, role average)
                    ──► models/player_model.py     LightGBM quantile / Poisson / binary fits, NB ranges,
                                                   conformal shift, calibration layer, SHAP drivers
                    ──► models/player_runs.py      walk-forward backtests, tuning, the weekly fit,
                                                   the scoreboard; assembles PRED_SCHEMA rows
                    ──► runs/<season>/week<NN>/predictions_players.parquet
                          │
                          ├──► models/player_watch.py   players to watch, tough spots
                          ├──► digest/players.py        payload items, look-back, scoreboard highlights
                          └──► graph/projections.py     PlayerProjection nodes in Neo4j
```

**Feature families** (prefixes; every feature of a week-N row uses only games before week N, and PFR / FTN columns lag one more week):

| Prefix | What | Examples |
|---|---|---|
| `own_` | his own target stat over earlier games | last game, last 4, season to date, last season, recent form (EWM) |
| `use_` | usage | snap share (last game / last 3 / trend), target, air-yards, carry, dropback and red-zone shares |
| `team_` | team context | pass rate over expected, pace, the game model's predicted team points and expected margin, the market-implied team total |
| `eff_` | efficiency | yards per target / carry, EPA, NGS separation, YAC and rush yards over expected, time to throw, PFR broken tackles and drops |
| `opp_` | opponent | what the opponent allows to this position group (schedule-adjusted), pressure rate vs pressure allowed, blitz rate |
| `rip_` | ripple effects | usage left open by teammates who are out, a new QB, his history with this week's QB |
| `avail_` | availability | his injury status and practice participation (the Friday view, D66), games of the last 3 he missed |
| `cvg_` | coverage context (P08, CB/S models only) | passes per game, depth, completion rate and yards per pass of the offense he faces and of his own defense; his defense's pressure rate and pass-defense rating |

**Who gets a live projection:** players on the team who played in one of its last 3 games and aren't listed Out / Doubtful or on a reserve list in the week's injury snapshot; for QBs, only the expected starter (the game model's QB resolver).

**Confidence** (`player_runs.assemble`): **low** when he has fewer than 3 games of history in the pool, no game this season, a role-average baseline, or a range more than 1.5× the week's median width (relative to the projection's size); **high** when he has 4+ games this season and a range no wider than the median; otherwise **medium**. Low confidence makes the digest hedge.

## 5. Players to watch, tough spots, look-back

All three are code, not the LLM (`models/player_watch.py`, `digest/players.py`).

**Players to watch: 10 on offense + 10 on defense** (D70, Rishi's request; `digest.watchlist_offense` / `watchlist_defense`). From the week's projections:
1. **Pool:** main targets only; a real role (snap share ≥ 50% over his last 2 games, or a *role change*: regular teammates who missed the team's last game or are ruled out this week, each counted once, leaving ≥ 15% of the targets for a WR/TE or ≥ 25% of the carries for an RB (`open_tgt` / `open_car`)); a game not yet kicked off when the digest runs; not Out / Doubtful.
2. **Score:** `outperf_z` = (projection − baseline) / the target's typical baseline miss, so a 25-yard jump and a 1.5-pressure jump compare. Only positive scores qualify. Followed teams get ×1.15.
3. **Pick each side on its own, greedily:** offense (QB, RB, WR/TE) and defense (EDGE/DL, LB/S) fill separate lists of 10, at most 2 per team per side, with soft group caps for variety (QB 3, RB 4, WR/TE 4, EDGE/DL 6, LB/S 6) that are relaxed only if a side would come up short. Why two lists: defenders' count targets move further from their baselines in z than receiving yards do, so one combined ranking crowds offense out (P06's single list of 8 needed a 3-defender cap; without it 5–6 of 8 picks were defenders).
4. **Minimum projected volume** (`MIN_VOLUME`, on the projection as shown: the average for counts): at least **1.0 pressures** or **2.0 tackles**. A rotation lineman projected 0.3 pressures read oddly, and on 2019–2020 pressures picks under 0.8 hit only 33% (58% above). Chosen on 2019–2020 only, confirmed on 2021–2025 unchanged: defense 62.9% → 65.3% and 64.2% → 65.7%, EDGE/DL 55.4% → 59.9% and 56.0% → 59.8%, and no week came up short of 10. Never relaxed: a side that can't fill 10 shows fewer (the table title says "Defense (9 picks)"). No offensive floor: picks under 20 receiving or rushing yards hit as often as the others, and a 20-yard floor lowered WR/TE's hit rate (65.6% → 62.8% on 2019–2020). Tough spots don't use it.

**Tough spots (5, `digest.tough_spots`).** Regular starters (snap share ≥ 50%, not low confidence) projected at least a quarter of a typical miss *below* their own baseline, one per team, never a watch-list player. A code-written list under *Matchup / risk to watch*.

**Look-back.** Next week's report card grades the list exactly as it was saved (`watchlist.parquet` in the run folder, `created_at` = when the digest ran; picks made after kickoff are never graded): "projected 85 receiving yards, range 60–135 receiving yards → actual 97 receiving yards: inside the range, above his baseline of 60 receiving yards". A **hit** = actual above his baseline. Unpublished stats (a PFR pressure count not out yet) aren't scored; the weekly step re-scores every earlier week, so they fill in a week later on the scoreboard (the look-back itself is written once, in the next week's digest).

**Is the list any good?** `watchlist_backtest` replays the selection on every backtest week and compares the hit rate with the **base rate**: the share of all eligible role players who beat their baseline. The base rate is well under 50% (medians sit below means, so most players finish below their own average), so a hit rate only means something next to it.

| 2019–2025 backtests (124 weeks, 2,480 picks: 10 + 10 a week, volume floor on) | Hit rate | Base rate | Picks per week |
|---|---|---|---|
| **All picks** | **65.3%** | 42.2% | 20 |
| Offense | 65.0% | 42.0% | 10 |
| Defense | 65.6% | 42.3% | 10 |
| QB | 70.7% | 47.5% | 3.0 |
| RB | 62.0% | 44.7% | 3.6 |
| WR/TE | 63.3% | 40.2% | 3.5 |
| EDGE/DL | 59.8% | 37.3% | 4.3 |
| LB/S | 69.9% | 47.8% | 5.7 |

Exit criterion (P06): hit rate > 50%. Every season clears it (60.8% in 2022 is the lowest, 69.4% in 2023 and 2024 the highest), 20–27 points above that season's base rate; every group too (EDGE/DL is lowest, 59.8% against a 37.3% base rate). Neither side came up short of 10 in any week. 78% of the picks' actual results fell inside their 80% range. Confidence mix: 35% high, 51% medium, 14% low (low-confidence picks hit 61%, the others 65–66%). Among *all* pool players projected above their baseline, 47.9% beat it: the list's edge comes from picking the biggest projected jumps, not from the direction alone. That's also why the 20-pick list hits less often than P06's 8-pick list did (69.5%): picks 9–20 have smaller projected jumps.

## 6. Reading the projections in the digest

**Players to watch tables** (code-written, one for Offense and one for Defense, 10 rows each; the prose covers the 2–3 most notable per side, offense first):

| Column | Meaning |
|---|---|
| Projection | the central estimate (median yards, average count) |
| Range (80%) | 10th to 90th percentile; about 8 in 10 outcomes land inside |
| Baseline | his own rolling average (what "beat his baseline" is measured against) |
| Confidence | high / medium / low (§4); low → the prose must hedge |
| Main driver / note | the top SHAP driver that is at least 20% of the gap to baseline (same direction first), worded against a typical player in his group, or "no single factor stands out"; plus a **baseline note** when the baseline rests on little ("his baseline comes from 2 games this season", "... (pressures data arrives a week late)", "mostly from last season", "the average for players in his role") |

How to read a pick: the projection's distance from the baseline is the story; the range says how wide the plausible outcomes are; the driver says what the model is reacting to; a baseline note says "his normal is uncertain too". Every number in the table and the prose comes from `digest/format.py` and is checked against the payload (the `digest-checks` skill).

**Report card additions:**
- **Last week's watch list:** one compact table, one row per pick (side, player, stat, projected, range, actual, ✓ / ✗ for "in range" and "above baseline", "did not play" / "no result yet" when it wasn't scored), and on the numbers line "watch list 15 of 20 above baseline, 17 inside their range (offense 6 of 10, defense 9 of 10)". Season totals are unchanged.
- **Player projections:** 2–3 highlights from the accuracy scoreboard, always including the weakest target: "receiving yards projections beat the rolling baseline by 9% so far this season (3 weeks scored)", "pressures: not yet better than the rolling baseline (2% worse)", "the 80% ranges held 79% of results". A live digest uses only live scoreboard rows. Until a live week has been scored it says so ("no live week of player projections has been scored yet") and quotes the walk-forward backtests, **labelled as backtests**.

**When projections are missing or the refit failed**, the digest falls back to the P04 usage heuristic. A failed refit writes `player_status.json` = `degraded` in the run folder (`player_schema.write_status`), and the digest checks it before reading `predictions_players.parquet`, so an older projection file left by an earlier run of the week is never used. The fallback is labelled "Heuristic picks (no player-model projections this week)", and the footer's model version says `heuristic-v0`.

## 7. The accuracy scoreboard

One row per (season, week, target, group): projections scored, MAE of the model and of the baseline, improvement %, 80%-range coverage, and `mode` (live / backtest). Live rows go to `runs/<season>/accuracy_scoreboard.parquet` (the week's *saved pre-kickoff* projections). The weekly `player` step re-scores **every** earlier week of the season on each run (`score_weeks`): PFR pressures arrive about a week late, so a week's pressures row appears or changes one run later. `nfl scoreboard` scores one week (`score_week`); backtest rows to `runs/backtests/player/scoreboard.parquet`. The season file also holds walk-forward re-runs of the current season's earlier weeks with `mode = backtest`; nothing quotes them as live.

**What the baseline is when scoring counts (D65).** A count's projection is a negative-binomial median, and a median beats *any* mean on MAE for skewed, zero-heavy counts. Scoring pressures against the raw rolling mean made them look 16% better than they were (3% against a fair bar). So counts are scored against `baseline_p50`, the median of the same distribution placed at the baseline mean. Yards are scored against the baseline itself.

| Target (2019–2025 backtests, pooled) | Improvement vs baseline (MAE) | 80% range coverage |
|---|---|---|
| passing yards (QB) | +5.0% | 81% |
| EPA per dropback (QB) | +4.7% | 80% |
| rushing yards (RB) | +7.9% | 81% |
| carries (RB) | +6.9% | 80% |
| receptions (RB) | +2.7% | 81% |
| scrimmage yards (RB) | +7.4% | 80% |
| receiving yards (WR/TE) | +9.2% | 82% |
| targets (WR/TE) | +5.2% | 81% |
| receptions (WR/TE) | +3.4% | 80% |
| pressures (EDGE/DL) | +3.0% | 85% |
| tackles (LB/S) | +4.8% | 81% |

Counts are compared with the baseline's median (D65). All 11 targets beat their baseline in every one of the 7 seasons; every range covers 80–85% (the exit band is 72–88%). Pressures are the weakest target, and their ranges run slightly wide.

Realistic bar (documentation/11): beating a good rolling average by 5–15% on player yardage is a solid result; usage counts (targets, carries) should improve most.

**P08 targets on the scoreboard.** Probability targets and event counts add three columns, scored on `p_ge1` against the real "at least one" event: `brier_model` and `brier_baseline` (the average squared miss of the probability; 0 is perfect, and always guessing the base rate scores `rate × (1 − rate)`) and `calibration_ece` (how far, on average, the stated chances are from the real rates: when it says 30%, does it happen 30% of the time?). A probability target has no MAE, range or coverage (those columns stay empty).

**The pre-registered ship rule** (documentation/plans/P08; `player_runs.ship_rule` computes it into every backtest summary as `ship_*` keys, and the lead decides): amounts and counts must beat the baseline's MAE pooled over 2019–2025, in at least 5 of 7 seasons, with 80% range coverage between 0.75 and 0.88; event counts must have a Brier of `p_ge1` below the baseline's pooled and in 5 of 7 seasons, an ECE no higher than max(0.02, the baseline's), and an MAE no worse than the baseline's by more than 0.5%; probability targets the same Brier and ECE conditions. Results: [player-p08 model card](../model_cards/player-p08.md).

## 8. Files it produces

| File | What |
|---|---|
| `features/player_features.parquet` | the feature table, one row per player-game (`nfl features player`) |
| `runs/<season>/week<NN>/predictions_players.parquet` | the week's projections, one row per player × game × target (`PRED_SCHEMA`) |
| `runs/<season>/week<NN>/watchlist.parquet` | the picks as published (graded next week) |
| `runs/<season>/accuracy_scoreboard.parquet` | the season's scoreboard (live + walk-forward rows) |
| `runs/backtests/player/<target-key>/predictions_players.parquet`, `summary.json` | walk-forward backtest predictions (with actuals) and summary, e.g. `rec_yds-wrte` |
| `runs/backtests/player/scoreboard.parquet` | backtest scoreboard rows (parallel chains upsert it under a lock file) |
| `runs/backtests/player/<key>_nomarket/` | the research variant without closing-line features (leakage rule 5); never read by the digest |
| `models/player-model/<season>-w<NN>/` | the week's fitted boosters |
| `runs/digest-backtests/<season>/week<NN>/predictions_players.parquet` | a backtest digest's copy of that week's backtest projections (outcomes blanked) |

Key `PRED_SCHEMA` columns: `p10` / `p50` / `p90` (empty for probability targets), `mean`, `p_ge1` / `p_ge2` (chances of at least 1 / 2: event counts and probability targets), `baseline`, `baseline_p_ge1`, `baseline_p50`, `baseline_source` (rolling / last_season / role), `outperformance`, `outperf_z`, `role_ok`, `role_change`, `injury_status`, `confidence`, `drivers` (feature, phrase, value, contribution), `model_version`, and `actual` / `played` once the game is played.

## 9. W&B runs

All in project `nfl-analytics-engine`, group **`track1-player`**, tagged with the position group (details and screenshots of each chart in the [W&B guide](weights-and-biases.md)).

| Job type | Command | What to look at |
|---|---|---|
| `backtest` (one run per target) | `nfl backtest player` | `bt/*` lines over the backtest weeks: weekly and cumulative MAE (model vs baseline), improvement %, range coverage; `lgb/curve_<season>` training curves (validation loss should flatten without a growing gap); tables `by_season`, `accuracy_scoreboard`, feature importance, the SHAP summary bar chart, the predictions. Probability targets and event counts log Brier curves instead of (or next to) MAE (`bt/brier_*`, `bt/cum_brier_*`), a `brier_by_season` chart and a decile `reliability_diagram` (predicted chance vs how often it happened: the points should sit on the diagonal) |
| `tune` | `nfl tune player` | the sweep's MAE per setting (2017–2018 walk-forward); probability targets: Brier (`tune/brier_model`), with their own grid (`TUNE_GRID_PROB`: smaller trees, bigger leaves, fewer rounds) |
| `train` (weekly) | `nfl train player` / the weekly `player` step | `projections_main` table, biggest projected jumps, projections per model; artifact `player-model` (type `model`, the boosters, aliased by week; `production` with `--promote`) |
| `eval` | `nfl scoreboard` / the weekly step | `scoreboard/*` curves per target over the season; the `accuracy_scoreboard` table |

## 10. The graph side

The weekly `player` step writes every projection into Neo4j after the graph build (which wipes the database first):

`(:Player)-[:HAS_PROJECTION]->(:PlayerProjection)-[:FOR_GAME]->(:Game)`

One node per player × game × target × model version (key `player_id|game_id|rec_yds-wrte|player-model-v1:2026-w05`), with `p10`, `p50`, `p90`, `mean`, `p_ge1`, `p_ge2`, `baseline`, `baseline_p50`, `baseline_p_ge1` (the P08 ones only when set), `outperformance`, `outperf_z`, `confidence`, `top_drivers` and more. The write is idempotent (MERGE) and fail-soft: if Neo4j is down the step logs it and carries on. Examples for Neo4j Browser:

```cypher
// this week's biggest projected jumps (main stats)
MATCH (p:Player)-[:HAS_PROJECTION]->(pp:PlayerProjection {season: 2026, week: 5, is_main: true})-[:FOR_GAME]->(g:Game)
RETURN p.name, pp.target_label, pp.p50, pp.baseline, pp.outperf_z, g.game_id
ORDER BY pp.outperf_z DESC LIMIT 10

// one receiver's projection next to his history with this week's QB
MATCH (r:Player {name: 'Puka Nacua'})-[:HAS_PROJECTION]->(pp:PlayerProjection {target: 'rec_yds'})
MATCH (q:Player)-[t:THREW_TO]->(r)
RETURN pp.week, pp.p50, pp.p10, pp.p90, q.name, t.season, t.targets ORDER BY pp.week DESC
```

## 11. Operating it

| Task | Command |
|---|---|
| Build the feature table | `uv run nfl features player` |
| Backtest one target (or `all`) | `uv run nfl backtest player --target rec_yds --seasons 2019-2025` (`--group WR/TE`, `--smoke` for a throwaway run) |
| Tune | `uv run nfl tune player --target rec_yds` → copy the best settings into `player_model.per_target` (P08 targets have defaults in `player_runs.TARGET_DEFAULTS`; `per_target` wins) |
| Project a week | `uv run nfl train player --season 2026 --week 5` (prints the biggest projected jumps; refits `live_targets` only) |
| Score a played week | `uv run nfl scoreboard --season 2026 --week 4` (the weekly step re-scores every earlier week by itself) |
| The whole weekly cycle | `uv run nfl weekly run --season 2026 --week 5` (the `player` step: score week N−1, refit and project week N, write the graph) |
| A digest from backtest projections | `uv run nfl digest --season 2025 --week 8 --backtest --llm placeholder --no-wandb` |

A 🧑 run passes `--launched-by rishi` (agents: `--launched-by agent`).

## 12. Changing it safely

- **A new feature:** add it in `features/player*.py` under a family prefix (it's picked up automatically), add a leakage test (a row may only see earlier games), and add its phrase to `features/descriptions.yaml` (no digits: a test enforces it; phrases name what the feature measures, the driver's sign and size come with it, and a phrase with a betting word is dropped by the digest).
- **Model settings:** `config/settings.yaml` → `player_model` (global) and `per_target` (from a tuning sweep). Tune only on 2017–2018; re-run the backtests and compare on the scoreboard before shipping.
- **A new target:** add a `Target` to `TARGETS` (column, kind, label, unit; `event_probs` for a count whose "at least one" matters, `phase`) and a pool if needed; backtest it (the summary ends with the ship rule); to ship it add its key to `player_model.live_targets`. It reaches the digest tables only through the P08 columns (TD chance, sack chance, passing TDs) or if it is the group's `main` target.
- **A feature for one group only:** give it its own prefix and register the prefix in `player_model.GROUP_PREFIXES`; a feature under a shared prefix changes every model's feature set (and so every P06 projection).
- **The calibration layer:** `player_model.calibration` (`platt` | `isotonic` | `none`) and `calibration_seasons` (3). Platt was chosen over isotonic on the 2017–2018 burn-in seasons only.
- **The watch-list rule:** `models/player_watch.py`. Check any change on 2019–2020 with `watchlist_backtest`, then confirm on 2021–2025 without changing it again; report the hit rate next to the base rate.
- **What the digest shows:** `digest/players.py` (items, notes, look-back, highlights), `digest/render.py` (tables), `digest/prompt/` (rules 21–22). New numbers go through `digest/format.py`.
- **Never re-run a past live week** to "improve" it: the report card grades the projections saved before kickoff.

## 13. Limits and what's next

- Player stats are noisy: a good projection still misses by a lot on any one game. The range is the honest part.
- SHAP drivers explain the projection against a typical player in his group, not against the player's baseline (§3); the digest words them that way and hides the small ones.
- **A pick can be mostly baseline lag.** The rolling baseline pulls toward last season with 3 pseudo-games, so a backup who became a starter (the live 2026 week-4 list: Jake Hansen, 12% of snaps in week 1, then 88% and 100%, after a 1.7-tackle backup season) is projected well above a baseline that hasn't caught up with his new role. The projection is sensible; the "jump" is partly the role change. A baseline note appears only for fewer than 3 games this season, not for a changed role.
- The prose names players without their team or opponent (the table carries both); the prompt doesn't ask for the matchup string in each sentence.
- **Deep offensive picks can be small yardage.** The defensive list has a volume floor (§5); the offensive list doesn't, because low-yardage picks hit as often as the others. A pick like 13 projected receiving yards (a 3-yard baseline) can appear late in the Offense table.
- Backtest rows only include games the player played, so a pick who sits out never appears there; live hit rates will run a little lower.
- Pressures depend on PFR, which publishes about a week late: last week's pressures may not be scorable on Tuesday.
- Shipped in P08 ([card](../model_cards/player-p08.md)): touchdown, sack and interception chances, coverage stats for corners and safeties, passing TDs and interceptions, QB rushing yards, QB hits; plus the team stat totals and the consistency layer (receptions ≤ targets on; receivers' yards vs the QB's and the team's logged, not adjusted, D81). Still open: tying passing TDs to the receivers' TD chances, goal-line usage as a touchdown feature, a clean CB / safety split.
