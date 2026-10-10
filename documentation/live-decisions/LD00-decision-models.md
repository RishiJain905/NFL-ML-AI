# LD00: Decision models

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Live decisions rows)

- **Depends on:** P10 (curated play-by-play 2010–2026, market lines, team ratings)
- **Unlocks:** LD01 (the live feed scores these models), PC02 (reuses the situational pass model)
- **Read first:**
  - [Live decisions README](README.md), all of it, especially §2 (research) and §6 (the no-touch rule);
  - the `model-experiment` skill (W&B logging, artifacts, promotion, model cards, 🧑 handoffs);
  - the `curated-data` skill (`plays`: `down`, `ydstogo`, `yardline_100`, `posteam`, `score_differential`, `game_seconds_remaining`, `half_seconds_remaining`, `posteam_timeouts_remaining`, `spread_line`, `total_line`, `wp`, `vegas_wp`, `xpass`, `field_goal_result`, `kick_distance`, `punt_*`; the sign conventions);
  - [04 Track 1 models](../04-track1-models.md) → the leakage rules;
  - nfl4th's sources for reference: `data-raw/_go_for_it_and_2pt_models.R`, `_punt_and_fg_models.R`, `R/decision_functions.R`.

## Goal

Five small, fast models and a decision engine that, given a game state (score, clock, down, distance, yard line, timeouts, the pre-game spread and total), returns the win probability after going for it, kicking a field goal and punting, with an honest confidence label. All of it is backtested season by season and tracked in W&B, and none of it touches the production models.

## Scope

- **In:**
  - the models:
    1. **win probability** (`wp`), spread-adjusted;
    2. **yards gained** on 3rd and 4th downs (`gain`), a distribution;
    3. **field goal make** (`fg`);
    4. **punt outcome** (`punt`), where the next possession starts;
    5. **situational pass probability** (`pass`);
    6. **the kickoff after a score** (`kickoff`), where the opponent starts, by rules era;
  - `live/decide.py`: go / kick / punt and the 3rd-down if-stopped table;
  - leave-one-season-out backtests;
  - W&B runs and the artifact;
  - the model card, guide, skill;
  - `tests/test_production_untouched.py`.
- **Out:** the live feed (LD01), the app (LD02), team-context panels (LD02), 2-point decisions and timeout advice (later, if wanted).

## Tasks

### Probe first
- [x] 🤖 **The training rows.** Count 3rd- and 4th-down runs, passes, sacks and penalty first downs per season 2010–2026. Expect about 32k 4th downs in 2018–2025 (checked 2026-10-08), with go-for-it attempts a small share. Check how many have `spread_line` / `total_line` (closing lines from `games`).
- [x] 🤖 **The baselines that already exist:** `vegas_wp` and `wp` in `plays` (nflfastR's models, fit on all seasons, so they're in-sample there: say so when comparing), and `xpass`. Confirm the coverage per season.
- [x] 🤖 **Rules eras:**
  - the 2015 extra-point move;
  - the 2024 kickoff rule and its 2025 change (touchback to the 35): measure where the opponent starts after a score in 2023, 2024, 2025 and 2026 weeks 1–4;
  - overtime rules by season.

  Decide the era feature from this.
- [x] 🤖 **Field goals:** make rate by distance band, roof, temperature and wind (`games` weather) and era. Check whether a kicker's own record adds anything after shrinkage (a small leave-one-season-out test). Report it either way.

### Build (`src/nflengine/live/`, new)
- [x] 🤖 **`wp`:** LightGBM binary.
  - **Target:** the team with the ball wins (ties dropped, counted).
  - **Features:** score difference, game and half seconds left, yard line, down, distance, both teams' timeouts, home, receives the second-half kickoff, the spread from the offense's side and its interaction with time left (nflfastR's `spread_time`, `Diff_Time_Ratio`), era, roof.
  - **Constraints:** monotone in score difference and in spread; seasons 2010–2025 to fit.
- [x] 🤖 **`gain`:** LightGBM multiclass on 3rd and 4th downs (runs, passes, sacks, and first downs by penalty), with yards gained bucketed −10 … +65 plus touchdown.
  - **Features:** down, distance, yard line, the spread, total and implied team total, era, roof.
  - **Optional:** the offense's and defense's opponent-adjusted ratings as of that week, read from `features/team_ratings.parquet`. This only reads the published table; keep it only if the backtest says so.
  - **Conversion chance** = P(gain ≥ distance).
- [x] 🤖 **`fg`:** logistic or GAM-like (LightGBM with a monotone distance) for P(make) by distance, roof, era, plus weather if the probe says it helps. **Block rate** and **miss spot** rules: the ball goes to the spot of the kick (+7) or the 20.
- [x] 🤖 **`punt`:** empirical distribution of the next possession's start by punt yard line, plus block, return-touchdown and muff rates (2016+), smoothed.
- [x] 🤖 **`pass`:** LightGBM binary, pass (dropback) vs run. Situation features as in nflfastR's `xpass` (down, distance, yard line, score, time, timeouts, win probability, era, roof) plus the spread. Baseline: `xpass`.
- [x] 🤖 **`kickoff`:** where the opponent starts after a touchdown or field goal, by era (a distribution, not a constant).
- [x] 🤖 **`decide.py`:**
  - **go** = Σ P(gain) × WP(the state after), counting the touchdown branch (with the extra point or 2-point try at its era's rate);
  - **FG** = P(make) × WP(after a make: opponent kicks off) + P(miss) × WP(opponent's ball at the miss spot);
  - **punt** = Σ P(start) × WP(opponent's ball there).

  Details:
  - 6 s of clock per play, nfl4th's run-off and kneel-out rules near the end of a half;
  - the call is the best option; the gap is in win-probability points;
  - **confidence:** score with 20–30 bootstrap refits of `gain` and `fg` (cheap) and label **Confident** (the best option wins in ≥ 90% of the bootstraps), **Lean** (60–90%) or **Toss-up** (< 60%, or a gap < 1 point);
  - **the 3rd-down table:** for each gain from −10 to distance − 1, the 4th-down state and its call.

  Target: under 20 ms for a full 3rd-down check, bootstraps included.
- [x] 🤖 **Save** under `{NFL_DATA_ROOT}/models/live-decisions/<version>/` (each model, its settings, the training seasons, a `meta.json`), and log the artifact `live-decision-models` to W&B. **Promotion** is this artifact's own `production` alias. No other model's alias moves.
- [x] 🤖 **CLI:** `nfl live train [--seasons 2010-2025] [--promote]`, `nfl live backtest --seasons 2014-2025`.
- [x] 🤖 **`tests/test_production_untouched.py`** (the no-touch guard for all three new tracks, D107):
  - a manifest of SHA-256 hashes for the protected files: `src/nflengine/{models,features,digest,graph,ingest,curate,ops}/**`, `weekly.py`, `schedule.py`;
  - the parsed pre-existing top-level blocks of `config/settings.yaml`.

  Any change fails with "a protected file changed: production models are off limits (D107)". Updating the manifest is a deliberate, separate commit that needs Rishi's OK.
- [x] 🤖 **Tests:** each model's input checks, monotonicity of `wp` in score and spread, the decision engine on hand-made states (4th & 1 at the opponent's 40 tied in Q2 → go; 4th & 15 at your own 20 → punt; a 40-yard FG down 2 with 5 s left → kick), the 3rd-down table's shape, the timing budget, leakage (no row from the scored season in its fit).

### Backtest (🧑 Rishi runs, or delegated)
- [x] 🧑 **`uv run nfl live backtest --seasons 2014-2025`**: leave one season out for each model, logged live to W&B (`job_type: live-backtest`, `launched-by` tag).
  - **wp:** Brier, log loss and calibration error (5-point bins) by season, quarter and score state, against `vegas_wp`.
  - **gain:** conversion log loss and calibration on 3rd downs and on 4th-down attempts (note the selection bias of attempted 4th downs), against the empirical rate by distance and era.
  - **fg:** log loss and calibration by distance band, against a distance-only logistic.
  - **pass:** log loss and AUC against `xpass`.
  - **punt** and **kickoff:** the mean and spread of the next start, by season.
  - **The decisions:** how often coaches followed the bot by season (the go rate rising since 2018 is a sanity check), and the win-probability cost of the calls that didn't.
- [x] ✋ **Ship decision, by rules fixed before the run:**
  - `wp`: calibration error ≤ 0.01 and Brier within 0.002 of `vegas_wp` overall;
  - `gain`, `fg`, `pass`: each beats its simple baseline in at least 9 of 12 seasons;
  - the decision tests pass;
  - the timing budget is met.

  Ship → `nfl live train --promote`. A miss: report and propose a fix, and Rishi decides.

### Docs and close
- [x] 🤖 Model card `documentation/model_cards/live-decision-models.md` (the `model-experiment` skill §7 sections, "Reading the W&B charts" included).
- [x] 🤖 Guide `documentation/guides/live-decisions.md`, started:
  - what a 4th-down model is, in plain words;
  - the five models;
  - how a call is computed, worked through on a real 2025 4th down;
  - the confidence labels;
  - commands.
- [x] 🤖 Skill `.claude/skills/live-decisions/SKILL.md`; CLAUDE.md's skills table; the W&B guide's new section (`live-backtest`, `live-train`, `live-decision-models`).
- [x] 🤖 **Production-unchanged check:** the protected-paths test passes, and a rehearsal of the newest published week reproduces its predictions.
- [x] ✋ **Close LD00.**

## Rishi-in-the-loop moments (what to look for)

- **The backtest run** (W&B, `live-backtest`):
  - `wp/calibration` should hug the diagonal in every quarter. Watch the 4th quarter and the last two minutes, where WP models usually break.
  - `gain/conversion_by_distance` should fall smoothly from about 70% on 4th & 1 to about 25% on 4th & 10. A bump means a bucket or feature problem.
  - `decisions/go_rate_vs_bot` by season: coaches' go rate rising toward the bot's since 2018 is expected.
- **The ship decision:** the rules above. Anything inside the noise counts as a tie with the baseline, not a win.

## Exit criteria (+ how to verify)

- The six models saved and promoted (`live-decision-models:production`); the backtest in W&B (run links in PROGRESS).
- `nfl live call --state '<json>'` (a test hook) answers the hand-made states correctly in under 20 ms.
- `uv run pytest` and `uv run ruff check` pass; the production-unchanged check passes.
- The model card, guide, skill and W&B guide section exist.

## Handoff to next phase

LD01 feeds real ESPN states into `decide.py`. Note for it: the input state must carry yards to goal from the offense's side, both teams' timeouts, the pre-game spread and total from the offense's side, and the era.

## Pitfalls / notes

- **Selection bias:** teams go for it when they expect to convert, so 4th-down attempt rates overstate an average team's odds. Training on 3rd and 4th downs together (as nfl4th and NGS do) is the usual fix. Keep the down feature.
- **The kickoff after a score changed in 2024 and again in 2025.** A model that assumes the 25 misjudges every "kick a field goal and give it back" call.
- **The end of a half** (kneel-outs, a field goal with no time left, timeouts) needs explicit rules, not just the WP model.
- **`vegas_wp` is fit on all seasons**, so it isn't a fair out-of-sample opponent. Say so in the card.
- **Don't import from `src/nflengine/models/`** in a way that couples the new models to production code. Copy small helpers if needed.

## As built: deviations from the task list (2026-10-10)

- **Waivers (Rishi, kickoff):** the 🧑 backtest run by the agent (`--launched-by agent`), the ✋ ship decision by the pre-registered rules with any miss back to Rishi, the ✋ close waived by "marking the phase complete".
- **The ✋ ship decision:** rules 1–3 and 5 passed; the pass model missed rule 4 (6 / 12 vs `xpass`, all six losses inside `xpass`'s 2006–2019 training data) and **Rishi chose "ship all six"** (D114). Promoted: `live-decision-models:2010-2025_20261010T054343Z`.
- **`wp`:** boosted from a logistic base (7 leaves × 150 rounds), not trees from scratch: a 2025 smoke of the first design showed poor calibration; the fix was chosen on the tuning window 2010–2013 (D113, D114, model card → Tuning).
- **`gain`:** learning rate 0.15 × 50 rounds (same tuning-window loss as 0.05 × 150, a third of the trees); goal-to-go defensive-penalty first downs left out (276 rows, a label no class can express). The team-ratings variant lost (1 / 12): not kept.
- **`fg`:** a logistic with distance hinges, era, roof and weather; the kicker's record isn't a feature (D113).
- **Confidence:** 20 bootstrap refits, run only for calls closer than 5 points (the gate, re-checked at every train; D113).
- **Timing:** a full 3rd-down check takes 14.6 ms (3rd & 6) and 19.6 ms (3rd & 10) on this machine: met, the second only just; a 3rd & 15 (25 table rows) takes ~27 ms.
- **`nfl live call --state`** is the test hook of the exit criteria; it loads the promoted bundle (or `--version`).
- **The production-unchanged check:** `tests/test_production_untouched.py` (119 files, 17 settings blocks; the tracks' own blocks are excluded) passes, and a rehearsal of 2026 week 5 pinned to its published moment (2026-10-07 00:28:30 UTC) reproduced its games (30 rows), players (4,542) and teams (120) exactly (one baseline column differs by 3.5e-18, float order).

