# FL00: availability and role

**Track:** [Forecast lab](README.md) · **Status:** see [PROGRESS](../plans/PROGRESS.md) · **Decision:** D158

**Read first:** the track [README](README.md) §2.1, [doc 11](../11-prediction-targets.md) (principles), the leakage rules in [doc 04](../04-track1-models.md), the `curated-data` and `model-experiment` skills, `src/nflengine/forecastlab/schema.py` (the contract).

## What it delivers

A new model family, `availability`, that says for every skill and defensive player on a team's roster, per week and per view (Tuesday / Saturday):

1. the chance he plays (takes a snap on his side of the ball);
2. his snap share if he plays, with an 80% range;
3. his share of the team's targets (WR, TE, RB) and carries (RB, QB) if he plays;
4. who inherits: for each player who might sit, the teammates whose share rises.

It never changes a production model (D107) and never enters the digest.

## Tasks

- [x] 🤖 Probe the real data: play rate by report status and position group, by era; how practice participation changes it; the snap-share baseline's error; how much a missing teammate moves a share. Record the findings below.
- [x] 🤖 `forecastlab/availability.py`: the as-of universe (who could play for the team this week), the features per view, the three models, the inherit view. Pure functions on frames.
- [x] 🤖 Leakage tests with the features (the game-day inactive list never read; the Tuesday view never reads week N's report; future rows never move a past row).
- [x] 🤖 `forecastlab/availability_runs.py`: `run_backtest`, `run_predict`, `run_grade`, files per `schema.py`, W&B through `tracking.init_run`.
- [x] 🧑 → 🤖 (waived, D158) The tuning-window run, then the reported walk-forward backtest, `--launched-by agent`.
- [x] ✋ → rule (waived, D158) The ship decision by the rule below: all four outputs ship in both views (see "Results").
- [ ] 🤖 The model card.

## Windows (fixed before any run)

| Window | Seasons | Used for |
|---|---|---|
| Training starts | 2014 | Snap counts start in 2013; one season of lags |
| Tuning | 2017–2018 | Every setting is chosen here |
| Reported | 2019–2025 | The numbers in the model card; touched once the settings are fixed |

Walk-forward: a week's rows are predicted by a model fit only on earlier weeks.

## The ship rule (pre-registered 2026-11-14, before any reported run)

Each output ships on its own, per view. An output that misses its rule is not shown in the tab and goes to Rishi.

| Output | Baseline | Ships when, on 2019–2025 |
|---|---|---|
| Chance he plays | The historical play rate for what the view knows (status, practice line, roster status) by position group, fit on earlier seasons | Brier below the baseline's pooled **and** in at least 6 of 7 seasons; calibration error by deciles at most 0.02 pooled |
| Snap share if he plays | His mean snap share over his last 2 games | MAE below the baseline's pooled **and** in at least 6 of 7 seasons; the 80% range covers 72–88% pooled |
| Target share, carry share | His rolling share (last 4 games) | MAE below the baseline's pooled **and** in at least 6 of 7 seasons |

Model and baseline are always scored on exactly the same rows. Share outputs are scored only for players who played.

## Findings from the probe

Probed 2026-11-14 on the curated data (regular season, 2014-2025 unless noted). The universe is the one the model uses: every QB RB WR TE DL LB CB S who took a snap on his side in one of the team's last 3 games, whose latest appearance was for the team and whose latest roster row before the week doesn't place him elsewhere.

**The universe.** 34-39 players per team-game, 17,500-21,200 rows a season; 73-78% of them play (2014-2019: 77-78%, 2020-2025: 73-77%). Without a current roster, **week 1 was broken**: 15,000 week-1 rows played only 49.5% of the time (other weeks 78.1%), because last season's roster keeps every player who left in the offseason. With week 1's own roster row for membership (see "As built") week 1 plays 78.5% (2014-2018).

**Play rate by Saturday report status** (by era):

| Status | 2014-15 | 2016-19 | 2020-22 | 2023-25 |
|---|---|---|---|---|
| Out | 0.2% | 0.0% | 0.0% | 0.0% |
| Doubtful | 0.7% | 1.5% | 0.6% | 0.9% |
| Questionable | 59.4% | 67.7% | 67.0% | 65.8% |
| On the report, no game status (Probable before 2016) | 92.3% | 93.1% | 90.1% | 93.1% |
| Not on the report | 80.9% | 80.3% | 76.6% | 77.5% |

- Questionable by position group (2016+): QB 52%, CB 59%, RB 62%, S 64%, LB 69%, TE 69%, WR 70%, DL 74%.
- **Practice participation changes a Questionable a lot** (2016+): did not practice 43%, limited 69%, full 83%. On the report without a game status it barely matters (full 93-94%, limited 93-95%, did not practice 73-82%).
- "Not on the report" is the biggest group and the least certain (77-81%): healthy scratches, practice-squad players back on the practice squad, players cut or moved to a reserve list during the week.

**What a Tuesday run knows.** The report of the team's last game and whether he played it: listed Out last week and missed the game 29% play; not listed but missed the last game 25%; not listed and played it 85%. Missed none of the team's last 3 games 90%, one 61%, two 45%. Last week's roster: ACT 79%, reserve 29%, practice squad 12.5%.

**The snap-share baseline** (his mean over his last 2 games, players who played): mean absolute error CB 0.175, S 0.142, LB 0.136, WR 0.135, TE 0.122, RB 0.121, DL 0.107, QB 0.102 (last game only or last 4 games: within 0.01 of it). 13% of snap shares are exactly 1.0 (QB 76%, S 36%, CB 23%).

**The opportunity baselines** (rolling last 4 games, players who played): target share WR 0.058, RB 0.043, TE 0.042; carry share RB 0.123, QB 0.037.

**A missing teammate moves a share, a little on average.** The miss (actual minus baseline) against the share left open by teammates who didn't play (their baseline share, summed): targets +0.016 when 0.4 or more is open vs -0.004 when nothing is (r 0.09); carries +0.058 vs -0.013 (r 0.19); snaps (same position group) +0.038 vs -0.019 (r 0.15). Real, but most of a share's miss is game-to-game noise.

**Week W's roster row (what a run can know).** nflverse publishes week W's roster rows before the week: the raw snapshots of 2026 have week 5's rows on Tuesday 10-06 and week 6's on Wednesday 10-14, and the Saturday injury update (`ops/injury_update.py`) re-ingests `rosters_weekly`. Comparing each pre-game snapshot with the final row, universe rows only (ACT / RES / practice squad / gone / another team):

| Snapshot | Week | Universe rows | Differ from the final row | Of which |
|---|---|---|---|---|
| Tue 10-06 | 5 | 1,106 | 45 | 15 practice-squad elevations, 18 moved to reserve, 8 cut / gone / another team, 3 back to the practice squad, 1 re-signed |
| Wed 10-14 | 6 | 1,035 | 29 | 14 elevations, 9 to reserve, 5 cut / gone |
| Thu 10-29 | 8 | 1,051 | 18 | 10 elevations, 5 to reserve, 3 gone / another team |
| Fri 11-06 | 9 | 1,119 | 36 | 15 elevations, 12 to reserve, 4 cut / gone, 4 signed to the practice squad, 1 back to it |
| Sat 10-03 | 4 | 1,180 | 13 | 8 elevations, 3 to reserve, 1 cut, 1 signed to the practice squad |
| Sun 10-04 / 10-11 | 4 / 5 | 1,180 / 1,106 | 0 / 2 | (the game-day `INA` list, folded away) |

- Its value: of the 41,579 rows (2016+) that didn't play without being Out or Doubtful on the final report, 7,656 are on a reserve list in week W's final row, 5,226 have no row or another status there, 4,251 are on the practice squad.
- **What the backtest can't know:** the final row stands in for Saturday's snapshot. With practice-squad status folded into `ACT` (the elevations can't leak), about 5 of 1,180 Saturday rows (0.4%) carry information from after a Saturday run (Saturday 10-03: 3 moved to reserve, 1 cut, 1 signed). In week 1, the Tuesday view's membership comes from the final week-1 row; on the Tuesdays measured, 5-8 of ~1,100 universe rows (0.5-0.7%) changed membership by game day (no week-1 snapshot exists to measure week 1 itself).

## As built: deviations from the task list

**Code.** `forecastlab/availability.py` (pure: `prepare`, `build_rows`, `add_features`, `walk_forward`, `fit_view` / `predict_view`, `inherit_table`, `score` / `verdicts`) and `forecastlab/availability_runs.py` (`run_backtest`, `run_predict`, `run_grade`, `promote_model`, `load_production`). Tests: `tests/forecastlab/test_fl_avail_*.py` on a synthetic league (`fl_avail_fixtures.py`).

**The universe** follows the plan, built from snap counts (not box scores): his side's snaps in one of the team's last 3 games with snap counts, his latest appearance for the team, and his latest roster row before the week not placing him off the team (cut, traded, retired, released ...) or on another team's roster. QB RB WR TE DL LB CB S by the snap file's position label (`DE DT NT DL` -> DL, `LB OLB ILB MLB` -> LB, `CB DB` -> CB, `S FS SS` -> S; fullbacks are left out). One row per player per view; both views share the universe.

**Week W's roster row (decided with the lead, 2026-11-14, on the evidence above).**
- `INA` is folded into `ACT` everywhere (it is never an input, in any week or view); a test pins that replacing every `INA` changes no row.
- **Tuesday** never reads week W's roster row, **except week 1**, where the row decides membership only (on the team, any status but a departure: the player is in and reads `ACT`; otherwise he is out of the universe). Last season's roster can't know the offseason; a Tuesday run before week 1 has the week-1 rows (the cutdown is 10+ days earlier).
- **Saturday** reads week W's row as one folded value: `ACT` (ACT, INA and the practice squad), `RES` (reserve lists), `OFF` (cut, traded, another team, no row); null when the team has no rows for the week yet. Practice-squad status is folded in so Saturday's late elevations can't leak. Live runs fold the same way on any day (a Sunday run with the inactive list out reads the same values).
- The Saturday baseline's table uses the same folded value (the model and the baseline see the same information).

**Labels and shares.** `played` = at least one snap on his side (offense for QB RB WR TE, defense for DL LB CB S). Target share is `player_games.target_share` (0 when he played without a box-score row); carry share is designed runs (`rush == 1`, no two-point tries) per rusher over the team's.

**Models.**
- Chance he plays: LightGBM binary, then a Platt layer fitted on the model's own walk-forward predictions of the last 2 seasons before the block (identity in the burn-in).
- Snap share if he plays: three quantile LightGBM models (0.1 / 0.5 / 0.9). **P90 is fit reflected** (1 - P10 of 1 - share): with 13% of shares at exactly 1.0, LightGBM's 0.9 model started at 1.0 and never moved (every row predicted 1.0 in the first probe run). The range ends then move by the 90th percentile of the last 2 seasons' misses at each end (conformal), so each tail holds about 10%.
- Target share (WR TE RB) and carry share (RB QB) if he plays: one LightGBM regression each, pooled over groups.
- **Teammates' risk** (`vac_tgt`, `vac_car`, `vac_snap`): the share left open by teammates, (chance he sits) x (his baseline share), summed over the team's receivers / rushers / the same position group. Training rows use who actually sat; forecast rows use 1 - `p_play` at the same key. Who inherits uses the same models: each at-risk player's teammates are re-forecast with him in (weight 0) and out (weight 1).
- Features (Tuesday): position group, week, snap share last game / last 2 / last 4 / season, games this season and in his career, weeks since his last game, left his last game early, played the team's last game, team games missed of the last 3, his snap share in the team's last game, last week's report status and practice line, Out / Doubtful in the last 3 reports, last week's roster status, practice-squad weeks among his last 4, his group's size, his rank in it and the others' snap shares. Saturday adds this week's report status, practice line and folded roster value. The share models add his target / carry share (last game, last 4, season) and the three `vac_*` columns.

**Walk-forward.** Predictions start in 2015 (training from 2014) so every scored season has calibration history. Each week is predicted by models refit on every earlier week (`refit: week`); see "Tuning".

**Tuning (2017-2018 only, before any reported run).** A local scan without W&B (walk-forward from 2015, refit per season for speed, 17 settings; `scratchpad` scripts, numbers below are 2017-2018 pooled):

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

- The grid is flat for the chance he plays and the snap share (best to worst within 2%): the features carry the model, not the settings.
- The share models gain most from an L1 loss (the outputs are scored on absolute error: -2% targets, -3% carries) and smaller trees.
- **Weekly refits** cut the Saturday Brier by 0.0009 and the decile calibration error from 0.017 to 0.008 (Tuesday 0.012 to 0.007): a season-old model drifts within the season. They cost ~20x the fitting time (8 minutes for 2015-2018), which a backtest affords; the weekly predict refits anyway.
- Saturday's folded roster value: 2017-2018 Saturday Brier 0.0649 with it, 0.0741 without (refit per season), a 0.009 gain: kept. The 2019-2025 numbers with and without it are in "Results".
- The W&B tuning run (`h4g8948l`, group `forecast-lab`, the settings above): Tuesday Brier 0.0960 vs the baseline's 0.1434, Saturday 0.0641 vs 0.1091; decile calibration error 0.0077 / 0.0079; snap MAE 0.1185 / 0.1160 vs 0.1288 with the 80% range covering 81.1% / 81.2%; target share 0.0469 / 0.0467 vs 0.0497; carry share 0.0902 / 0.0885 vs 0.0964. Every output won both seasons.

## Results (2019-2025, the reported window)

Run once, after the settings were fixed: W&B `hklag5k1` (group `forecast-lab`, job type `availability-backtest`, `launched-by: agent`, 26 minutes, weekly refits 2015-2025). Smoke run first: `bv1f42pi` (group `forecast-lab-smoke`, 2025 only). Same rows for model and baseline (138,214 player-weeks; shares only for players who played). Play rate 77.2%; always saying 77.2% scores a Brier of 0.1759.

**Tuesday view**

| Season | Plays: Brier model / baseline | Calibration error (deciles) | Snap MAE model / baseline | 80% range covers | Target share MAE | Carry share MAE |
|---|---|---|---|---|---|---|
| 2019 | 0.0983 / 0.1426 | 0.0065 | 0.1212 / 0.1323 | 79.9% | 0.0473 / 0.0502 | 0.0889 / 0.0930 |
| 2020 | 0.1137 / 0.1533 | 0.0164 | 0.1234 / 0.1336 | 80.0% | 0.0458 / 0.0484 | 0.0963 / 0.1004 |
| 2021 | 0.1246 / 0.1646 | 0.0169 | 0.1268 / 0.1369 | 80.4% | 0.0462 / 0.0489 | 0.0967 / 0.1030 |
| 2022 | 0.1040 / 0.1456 | 0.0236 | 0.1174 / 0.1276 | 80.9% | 0.0463 / 0.0497 | 0.0915 / 0.0948 |
| 2023 | 0.1016 / 0.1438 | 0.0121 | 0.1177 / 0.1280 | 80.8% | 0.0449 / 0.0480 | 0.0864 / 0.0922 |
| 2024 | 0.1020 / 0.1425 | 0.0050 | 0.1178 / 0.1290 | 80.2% | 0.0463 / 0.0491 | 0.0916 / 0.0971 |
| 2025 | 0.1038 / 0.1464 | 0.0063 | 0.1185 / 0.1289 | 79.7% | 0.0459 / 0.0485 | 0.0858 / 0.0905 |
| **Pooled** | **0.1070 / 0.1485** | **0.0031** | **0.1204 / 0.1309** | **80.3%** | **0.0461 / 0.0489** | **0.0911 / 0.0959** |

**Saturday view**

| Season | Plays: Brier model / baseline | Calibration error (deciles) | Snap MAE model / baseline | 80% range covers | Target share MAE | Carry share MAE |
|---|---|---|---|---|---|---|
| 2019 | 0.0640 / 0.1060 | 0.0069 | 0.1185 / 0.1323 | 80.1% | 0.0469 / 0.0502 | 0.0874 / 0.0930 |
| 2020 | 0.0716 / 0.1255 | 0.0086 | 0.1198 / 0.1336 | 80.7% | 0.0452 / 0.0484 | 0.0933 / 0.1004 |
| 2021 | 0.0729 / 0.1274 | 0.0061 | 0.1219 / 0.1369 | 81.2% | 0.0456 / 0.0489 | 0.0939 / 0.1030 |
| 2022 | 0.0682 / 0.1136 | 0.0074 | 0.1142 / 0.1276 | 81.6% | 0.0460 / 0.0497 | 0.0903 / 0.0948 |
| 2023 | 0.0698 / 0.1148 | 0.0051 | 0.1150 / 0.1280 | 81.6% | 0.0446 / 0.0480 | 0.0849 / 0.0922 |
| 2024 | 0.0684 / 0.1123 | 0.0067 | 0.1142 / 0.1290 | 81.4% | 0.0457 / 0.0491 | 0.0898 / 0.0971 |
| 2025 | 0.0685 / 0.1130 | 0.0077 | 0.1151 / 0.1289 | 81.0% | 0.0454 / 0.0485 | 0.0843 / 0.0905 |
| **Pooled** | **0.0691 / 0.1162** | **0.0061** | **0.1169 / 0.1309** | **81.1%** | **0.0456 / 0.0489** | **0.0892 / 0.0959** |

**The verdicts (the rule above):** every output ships in both views. The chance he plays: Brier below the baseline pooled and in 7 of 7 seasons, calibration error 0.0031 (Tuesday) / 0.0061 (Saturday) against the 0.02 limit. Snap share: 7 of 7, range 80.3% / 81.1% (72-88% required). Target share and carry share: 7 of 7. Promoted: `availability-model:2019-2025_20261114T224537Z` (alias `production`), `shipped` = all four outputs per view.

- Gains over the baseline, pooled: Brier -28% (Tuesday) / -41% (Saturday); snap MAE -8% / -11%; target share -6% / -7%; carry share -5% / -7%.
- 2020-2021 (the COVID-era seasons, with their reserve/COVID lists and more practice-squad call-ups) are the hardest for the Tuesday chance; 2022 Tuesday has the worst calibration of a single season (0.024; pooled 0.003). The rule is pooled, and one season of ~20,000 rows has more noise in 10 bins than the pool.
- **Like with like:** the share models are fit on absolute error (a median), the rule's baseline is the rolling mean. The rolling median of the last 4 games does worse than the mean (Tuesday target share 0.0494 vs 0.0489; carries 0.0962 vs 0.0959), so the comparison doesn't flatter the model.
- **How much of Saturday rests on week W's roster row (research ablation, W&B `bikjocnb`, label `saturday-2019-2025`, never used for the decision):** without the folded value, Saturday's Brier is 0.0827 vs its baseline's 0.1251 (7 of 7, calibration error 0.0039); snap MAE 0.1177, target 0.0458, carry 0.0897, all 7 of 7. The play output passes its rule without the feature; the feature is worth 0.0136 Brier (0.0827 -> 0.0691).

**The live path (a scratch run of 2026 week 10, 2026-11-14).** Timed in a scratch folder on C: (the real curated data and the promoted model; nothing written on D:): `run_predict` takes 9-10 s per view (load, refit on 2014 to week 9, forecast, who inherits). Pinned to Tuesday 2026-11-10, the Tuesday view made 1,073 rows for 14 games; the Saturday view, run after Thursday's WAS at NYG, kept that game's Tuesday rows and made 995 rows for the other 13. The curated week-10 report was still Friday's early pull (18 game statuses), so the Saturday view had little to add yet. **The real week-10 file is not that scratch run:** it holds only the Tuesday view, made by the first update at 2026-11-14 23:15 UTC (after WAS at NYG had kicked off, so that game's rows are marked as made after kickoff).

**Files.** Backtests: `forecastlab/backtests/availability/<view>/` (canonical reported run), `<view>-tune`, `<view>-smoke`, `<view>-<first>-<last>` (research). The model folder `models/forecastlab/availability/<version>/` holds `model.json` (settings, feature lists, each view's Platt layer and range shifts for the next season, the shipped outputs) and the boosters refit on every labelled row (for inspection: the weekly predict refits on every earlier week with these settings). `availability.json` holds `views.<view>` (`made_at`, `rows`, `games`, `trained_through`, `version`, `games_after_kickoff`, `games_kept_after_kickoff`), `version`, `trained_through`, `shipped` (per view), `games_after_kickoff` (every game whose rows were made after its kickoff, any view).

**The kickoff rule** (lead, 2026-11-14): `run_predict` never re-makes a row whose game has kicked off at `now`. Rows already in the week's file stay exactly as they are; a game with no rows yet is made and listed in `games_after_kickoff` (overall, and per view under `views.<view>`).

**Forecasts and results are kept apart (review, 2026-11-14).**
- `run_predict` always writes `played` and every `actual_*` empty, even for a game already played (the first live Tuesday view had filled them for WAS at NYG): results come only from `run_grade`.
- `run_grade` fills the results of every row whose game has snap counts (facts, including games made after kickoff), but the scoreboard leaves out every game a view made after its kickoff (`excluded_after_kickoff` in its summary): a forecast made after kickoff isn't a forecast.
- A week can be graded in pieces: snap counts can lag a result (2026-11-06's snapshot lacked the played 2026_09_JAX_BAL). Rows without snap counts stay empty (`waiting` in the summary); a later grade fills them and replaces the week's scoreboard rows.
- JSON files are replaced with the repo's retrying replace (`fsutil.replace_file`, Windows readers). `availability.json` is written after the two parquet files; if it can't be written, the predict raises an error saying the meta is stale, never a silent success.

**Live calibration is frozen between promotions.** Live, the calibration layer and the range shifts are the ones saved at promotion (fit on the last two reported seasons, 2024-2025) and stay fixed all season, while the backtest re-fit them every week from earlier weeks. The models are refit every week live, but their calibration isn't, so live calibration can drift a little from the backtest's until the next promotion. (The live grades will show it: the season's decile calibration error against the backtest's 0.003 / 0.006.)
