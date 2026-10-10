---
name: live-decisions
description: How the live 3rd- and 4th-down bot is built, trained, backtested, scored and tested (LD00+, documentation/live-decisions/). Use when touching src/nflengine/live/ or tests/live/, the six decision models (wp, gain, fg, punt, kickoff, pass), the decision engine (go / field goal / punt, the 3rd-down if-stopped table, confidence labels and the bootstrap gate), `nfl live train | backtest | call`, the `live-decision-models` W&B artifact and its own production alias, the `live:` block of settings.yaml, or when a call looks wrong. Also covers the no-touch rule (D107) and the production-unchanged check every LD / PC / AE phase closes with.
---

# Live decisions (the 3rd / 4th-down bot)

Spec: `documentation/live-decisions/README.md` (+ phase files LD00-LD03). Plain-language guide: `documentation/guides/live-decisions.md`. Model card: `documentation/model_cards/live-decision-models.md`. Decisions: D107-D109, D113-D115.

## 1. The no-touch rule first (D107)

- New code lives in `src/nflengine/live/`; it **never imports** from `nflengine.models` or `nflengine.features` (copy small helpers instead) and never writes to production folders, aliases or the weekly run.
- It may **read** curated data and published outputs (`features/team_ratings.parquet`, market lines).
- Shared files change only by adding: a CLI group (`nfl live`), the `live:` block in `config/settings.yaml`, `AppConfig.live`.
- `tests/test_production_untouched.py` hashes every protected file (`src/nflengine/{models,features,digest,graph,ingest,curate,ops}/**`, `weekly.py`, `schedule.py`) and the parsed pre-existing settings blocks against `tests/fixtures/production_manifest.json`. A failure reads "a protected file changed: production models are off limits (D107)". **Regenerating the manifest needs Rishi's OK** and its own commit (`uv run python tests/test_production_untouched.py --write-manifest`).
- Every LD / PC / AE phase closes with the **production-unchanged check**: that test passes + a rehearsal of the newest published week reproduces its predictions (`weekly-ops` skill §5c; see §7 below for how LD00 did it).

## 2. Map of the package

| File | What it holds |
|---|---|
| `schema.py` | `GameState` (offense's side; `home` +1/0/−1; `spread` from the offense's side; validation in `check_state`), `era_of` (0 = 2010-14, 1 = 2015-17, 2 = 2018-20, 3 = 2021-23, 4 = 2024+), `kickoff_era` (tb20 / tb25 / tb30 / tb35), `pat_era`, OT rules, feature lists, the 77 gain classes, FG constants (distance = yards to goal + 18; a miss → spot of the kick, 8 yards back, or the 20), `state_features` |
| `data.py` | `load_plays` (REG + POST, 2010+, game order, `neutral_site` from `games`), `with_state` (the offense-side columns, same formulas as `state_features`), the frames `wp_frame` / `gain_frame` / `fg_frame` / `punt_frame` / `kickoff_frame` / `pass_frame` / `fourth_frame`, `with_ratings`, `state_of_row` |
| `models.py` | `settings()` (code `DEFAULTS` < `live.models`), `WPModel` (logistic base on 5 smooth terms + LightGBM boosted from it via `init_score`; `_fit_wp_base` drops a monotone term whose coefficient comes out negative), `fit_wp` / `fit_gain` / `fit_fg` / `fit_punt` / `fit_kickoff` / `fit_pat` / `fit_pass`, `fit_all` (**filters every frame to the given seasons itself**), `LiveModels`, `save` / `load` (LightGBM text + JSON), `legal_gain`, `conversion_prob`, `fg_distance_only` |
| `decide.py` | `Engine(models, cfg, threads=4)`: `fourth_down(state, bootstrap=True|False|"all")` → `Decision`, `third_down(state)` → `ThirdDown` (convert %, pass %, the if-stopped table), `fourth_downs(states)` for backtests; `kneel_out`, `wp_row`, `compress` |
| `train.py` | `run_train` (`nfl live train`), `run_checks` (hand-made calls, timing, the bootstrap gate), `production_folder` / `load_production` |
| `backtest.py` | `run_backtest` (`nfl live backtest`, leave one season out), `ship_verdict` |

Data on D:: `models/live-decisions/<first>-<last>_<UTC stamp>/` (wp.txt, gain.txt, pass.txt, gain_boot/NN.txt, fg.json with the bootstrap coefficients, punt.json, kickoff.json, pat.json, meta.json, checks.json) and `models/live-decisions/production.json` (the pointer the live code loads; the app reads files, not the alias). Backtests: `runs/backtests/live-decisions/<label>/`.

## 3. Commands

```bash
uv run nfl live backtest                 # LOSO 2014-2025, W&B group live-decisions / live-backtest (~2.5 min)
uv run nfl live backtest --smoke --no-wandb --no-save   # two seasons, a code check
uv run nfl live train --promote          # fit 2010-2025, checks, artifact live-decision-models:production
uv run nfl live call --state '{"season": 2025, "score_diff": 0, "game_seconds": 2400, "half_seconds": 600, "down": 4, "ydstogo": 1, "yardline_100": 40}'
uv run pytest -q tests/live              # 294 unit tests (stub models, no data, ~6 s)
uv run pytest -q -m integration tests/live/test_bundle_integration.py   # the promoted bundle
```

## 4. How a call is computed (keep these rules when changing the engine)

- Every state after a choice is a **first down for someone** at a spot, scored by `wp` from that team's side (`1 - p` when the defense has the ball). Each play takes 6 s (`SECONDS_PER_PLAY`).
- Rules before the model (`Engine._rule`, after `_next_period`): time up in the second half or overtime → the score decides (tie = 0.5), except a **tied playoff OT period, which goes on** into a new 15-minute period (`schema.ot_minutes(season, playoffs)`); first half over → the second-half kickoff to the `receive_2h_ko` team with **both teams back to 3 timeouts** (`possession(..., fresh=True)`); **kneel-out** (second half, a lead, a first down, `gs <= 6 + 40 * (3 - other team's timeouts)`) → 1 / 0.
- Touchdown (ours, or a kickoff / punt return touchdown) → +7 with the era's extra-point rate, else +6 (`_try_parts`) → kickoff (`_then_kick` handles time up / halftime first) from the season's kickoff distribution (`kickoff.at(season)`: tb35 from 2025, the receiver's mean start ~ own 30.5). Field goal → +3 → kickoff. Miss → their ball at the spot of the kick. Punt → `punt.at(yard line)`. Safety → their ball at `SAFETY_START` (60 yards to goal).
- Distributions are compressed to 12 receiving spots (`compress`) and all states of a call are deduplicated and scored in **one** `wp` predict (`_Batch`). The go branch is vectorised over the 77 classes (`_go_plan` / `_go_values`).
- **Confidence:** the 20 bootstrap refits of `gain` + `fg` re-score the call; Confident >= 90% wins, Lean 60-90%, Toss-up < 60% or gap < 1 point. **The gate (D113):** only calls closer than `live.decide.boot_gap` (5 points) are bootstrapped; wider ones are Confident because none flipped in 1,498 real 2025 4th downs; `run_checks` re-validates this on ~400 real 4th downs at every train (`checks.json` → `gate`), and `--promote` refuses if it fails.
- **Speed:** a 4th-down call ~1-6 ms; a 3rd-down check (the if-stopped table, bootstraps included) must stay under 20 ms (`TIMING_BUDGET_MS`; checked at train time). The cost is the bootstrap gain predictions (77 classes x 50 rounds = 3,850 trees per model), memory-bound: more threads don't help past 4. **Time it on an idle machine** (a parallel model fit doubled the numbers once).

## 5. Gotchas

- `spread` in a `GameState` is from the **offense's** side (nflverse `spread_line` is home-side, + = home favored). With the spread in the model, `home` has a *negative* effect at a pick'em (the spread already holds home field): don't "fix" it.
- `game_seconds = half_seconds + 1800` in the first half; 1800 / 0 is the end of Q2, 1800 / 1800 the second half's start (`first_half` uses the difference, never `game_seconds > 1800`).
- In overtime nflverse sets both clocks to the OT clock (max 900 to 2016, 600 from 2017).
- `kick_distance` = yards to goal + 18 on 90% of kicks (17 or 19 otherwise); the miss spot matches "+8" not nfl4th's "+7".
- The gain model's ratings variant (`--ratings`) needs `off_epa` / `def_epa_opp`, which a live `GameState` doesn't carry: only a research variant unless shipped with new state fields.
- LightGBM `predict(..., num_threads=1)` for small batches; `deterministic=True` everywhere.
- Bootstrap refits of `fg` share the main model's `wind_fill` (one design matrix for all; `Engine` refuses otherwise).

## 5b. What LD00 shipped (2026-10-10; D113, D114, D115)

- `production` = `2010-2025_20261010T054343Z` (W&B `hxmgioka`): wp 7 leaves x 150 rounds boosted from the logistic base; gain 4 leaves x 50 rounds at lr 0.15; pass 31 x 600; 20 bootstraps.
- Backtest `m70hg3pa` (final, after the Sol fixes; the ship decision was made on `p3y90nh0`, identical model metrics): wp Brier 0.1481 vs `vegas_wp` 0.1469, calibration 0.0048; gain 12 / 12; fg 10 / 12; pass 6 / 12 (loses only inside `xpass`'s 2006-2019 training window; Rishi: ship all six). The ratings variant lost (1 / 12).
- **The bot is aggressive** (go on 51% of 4th downs vs coaches' 17%). It comes from the 4th-down conversion chances (between the 3rd-down rate and the attempted-4th-down rate), not the WP model: an engine valuing the same futures with nflfastR's `ep` was *more* aggressive. Don't "fix" it inside LD01 / LD02; LD03 measures the bot's chances against real attempts.
- Timing on this machine: 3rd & 6 14.6 ms, 3rd & 10 19.6 ms (budget 20): any engine change must re-run `run_checks` on an idle machine.

## 6. Tests

- `tests/live/test_live_{schema,data,models,decide,train_cli}.py` (unit, stubs in `tests/live/live_stubs.py`): schema types and edges, data frames on hand-made plays, models (monotone wp, legal_gain, leakage in `fit_all`, save / load), the engine's rules worked by hand (game over, halftime, kneel-out, miss spot, safety, the table, labels, the gate), CLI.
- `tests/live/test_live_backtest.py`: the LOSO harness and `ship_verdict` on synthetic frames.
- `tests/live/test_live_sol_fixes.py`: the Sol review's edge cases (playoff OT, halftime timeouts, whole-number floats, zero refits, the try after a return TD; D115).
- **Test basenames must be unique across `tests/`** (no `__init__.py`: two `test_backtest.py` files break collection). Prefix new files `test_live_`.
- `tests/live/test_bundle_integration.py` (`-m integration`): the hand-made calls, timing and meta on the promoted bundle.

## 7. The production-unchanged check (how LD00 ran it)

1. `uv run pytest -q tests/test_production_untouched.py`.
2. `uv run nfl weekly rehearse --season 2026 --week 5` (pinned to the published run's moment, scratch copy, W&B off) and compare its prediction files with the published `runs/2026/week05/` files (join on keys; max abs diff must be 0). See the `weekly-ops` skill §5c and the phase-workflow skill's "old code vs new code" notes if a shared input moved after publication.

## Improving this skill

Update it in the same commit as any change to `src/nflengine/live/` that changes how things work, and note it in the PROGRESS session log. Never weaken §1.
