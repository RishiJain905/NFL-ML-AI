# PC02: The tendency forecast

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Play calling rows)

- **Depends on:** PC01 (the pages it fills), LD00 (the situational `pass` model it reuses)
- **Unlocks:** forecasts and their grading every week
- **Read first:**
  - [Play calling README](README.md) §1 and §3;
  - the `model-experiment` skill (walk-forward backtests, W&B, artifacts, promotion, model cards, 🧑 handoffs);
  - [04 Track 1 models](../04-track1-models.md) → leakage rules;
  - the LD00 model card (the `pass` model).

## Goal

For every game of the coming week, forecast each team's play-calling **rates** against that specific opponent, and grade the forecast after the games:
- **offense:** pass rate over expected in neutral situations, play-action, screens, deep shots, motion, run direction shares;
- **defense:** blitz rate, average rushers, heavy-box rate.

The bar is honest: beat "this team's season-to-date rate" by a margin the backtest can see. Not single-play predictions.

## Research notes (2026-10-08)

- **Run vs pass on a single play** tops out around 70–75% accuracy against a 58–60% "always pass" baseline, and the best models use pre-snap tells (shotgun, no-huddle) that a week-ahead forecast can't know ([Fernandes et al. 2020](https://journals.sagepub.com/doi/10.3233/JSA-190348); [Ötting](https://arxiv.org/abs/2003.10791)).
- **Noise floor:** with about 60 plays in a game, a team's pass rate has a standard deviation of about 6 points even if its true rate is known exactly; rates on about 35 dropbacks (blitz, play-action) about 7 points. Expect modest gains over the team's own rate.
- **Opponents move rates by a few points** (a defense shifts opponents' PROE by roughly ±2–5), and blitz rate is sticky year to year (r ≈ 0.6, higher with the same play-caller).
- nflfastR's `xpass` has **no team or opponent terms**, so it's the situational baseline, not a team forecast.

## Scope

- **In:**
  - the model;
  - its walk-forward backtest against four baselines;
  - W&B runs and the `playcall-model` artifact;
  - the model card;
  - `nfl playcalling forecast` / `grade`;
  - forecasts and grading in the Play calls tab and on team pages.
- **Out:** single-play predictions, coverage-shell forecasts for 2026 (no 2026 data until about February 2027), personnel forecasts.

## Tasks

### Probe first
- [ ] 🤖 **Targets and their noise:** for each rate, the game-level standard deviation and how much of it is sampling noise (binomial) against real week-to-week change, 2019–2025. Drop targets that are almost all noise, or keep them with that caveat shown.
- [ ] 🤖 **Stability:** the season-to-season and in-season (first half → second half) correlation of each rate, and whether a coordinator change breaks it (the coaching seed has OC / DC by season).

### Build (`playcalling/forecast.py`)
- [ ] 🤖 **The model**, for each team × rate × game:
  1. **The team's prior:** last season's rate, shrunk toward the league (more when the coordinator changed).
  2. **The season so far:** the beta-binomial / empirical-Bayes update of the prior with this season's plays (as of the week).
  3. **The opponent:** the opponent's "allowed" effect on the logit scale, shrunk and adjusted for the offenses it faced.
  4. **The game script:** expected situations from the spread and total, scored through LD00's `pass` model (for the PROE and dropback targets).

  Fit the shrinkage strengths and the opponent weight by walk-forward on 2019–2023, test on 2024–2025.
- [ ] 🤖 **Baselines**, in order:
  1. the league situational rate (`xpass` on the expected game script);
  2. the team's season-to-date rate;
  3. the shrunk team rate;
  4. the shrunk rate + opponent (the model without game script).
- [ ] 🤖 **CLI:**
  - `nfl playcalling backtest --seasons 2019-2025` (W&B `playcall-backtest`, live curves, the `launched-by` tag);
  - `nfl playcalling forecast --season S --week W` (writes `playcalling/<S>/week<NN>/forecast.parquet`, logs `playcall-forecast`);
  - `nfl playcalling grade --season S --week W` (forecast vs actual → `playcall_scoreboard.parquet`).

  Save the fitted settings under `models/playcall/<version>/` and log `playcall-model` with its own `production` alias.
- [ ] 🤖 **Tests:** as-of leakage (a week-W forecast uses nothing from week W), the shrinkage limits (no games → the prior; many games → the team's rate), the opponent adjustment's sign, grading.

### Backtest and ship
- [ ] 🧑 **`uv run nfl playcalling backtest --seasons 2019-2025`**. Metrics per target:
  - per-play log loss and Brier (where the rate is a per-play probability);
  - game-level rate MAE;
  - calibration;
  - each against the four baselines, by season and by part of season (weeks 1–4 / 5–10 / 11+).
- [ ] ✋ **Ship decision, by rules fixed before the run:** a target ships if the model beats baseline 2 (the team's season-to-date rate) on game-level MAE in at least 5 of 7 seasons and isn't worse than baseline 3. Targets that don't ship stay descriptive on the pages. Ship → promote `playcall-model`.

### Pages
- [ ] 🤖 **Play calls tab:** forecast rates (with a range from the beta posterior) next to the season-to-date rate; "biggest matchup shifts"; after the games, forecast vs actual and the week's grade.
- [ ] 🤖 **Team page → Next game card;** a season card "how the forecast has done for this team".
- [ ] 🤖 Endpoints extended (`/api/weeks/{S}/{W}/play-calls` gains `forecast` and `graded`); tests.

### Docs and close
- [ ] 🤖 Model card `documentation/model_cards/playcall-forecast.md` (the skill's §7 sections; "Reading the W&B charts"; the noise floor stated plainly).
- [ ] 🤖 Guide: the forecast, how to read a forecast with its range, and why gains are small. The W&B guide (`playcall-backtest`, `playcall-forecast`, `playcall-model`). The runbook: the weekly forecast and grade commands (or the button, per PC00's decision).
- [ ] 🤖 Codex / Sol review; production-unchanged check.
- [ ] ✋ **Close PC02.**

## Rishi-in-the-loop moments (what to look for)

- **The backtest** (W&B `playcall-backtest`):
  - `mae_vs_baseline/<target>` by season: the model's bar under the season-to-date bar in most seasons;
  - weeks 1–4 is where the prior and opponent terms should help most; by week 11+ everything converges on the team's own rate;
  - calibration of the per-play probabilities on the diagonal.
- **The ship decision:** by the rules; a target that only ties ships as descriptive.

## Exit criteria (+ how to verify)

- The backtest in W&B, the ship decision recorded (D-entry), shipped targets forecast for the current week, and the first week graded.
- Tests, ruff and web checks pass; the production-unchanged check passes.

## Pitfalls / notes

- **Don't oversell:** a forecast of "61% pass" against a season rate of 59% is inside the noise. Show the range and the baseline every time.
- **Game script is the biggest driver of pass rate.** Use the pre-game spread and total, never the result.
- **Coordinator changes mid-season** (firings) break priors. Read the coaching seed and the head-coach fixes file.
