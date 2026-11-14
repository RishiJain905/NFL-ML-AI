# Forecast lab: availability, outcome curves and two simulators

Status → see the **Forecast lab** rows in [plans/PROGRESS.md](../plans/PROGRESS.md). Planned 2026-11-14 (Rishi). Decision: D158 in the [decisions log](../10-decisions-log.md).

> **Where it stands (2026-11-14):** this document is written; no code yet. The data probes that fix each model's details could not run: the data drive (D:) stopped answering during the planning session. Sections marked **to confirm in the probe** are the plan, not findings.

The goal is four additions that answer questions the engine can't answer today, each one a **new model or layer beside the existing ones**. The existing models stay exactly as they are (D107).

| # | Addition | The question it answers | Phase | Status |
|---|---|---|---|---|
| 1 | **Availability and role** | Will he play, how much, and who gets the work if he doesn't? | FL00 | Building first |
| 2 | **Outcome curves** | What is the chance he clears 50 yards, or lands between 60 and 90? | FL01 | Building first |
| 3 | **Season simulator** | What are each team's playoff, division and seed chances, and how much does this week's game move them? | FL03 | Planned |
| 4 | **Game-script simulator** | How is this game likely to be played (shootout, blowout, one-score), and what does each player's line look like in each? | FL04 | Planned |

FL02 is the shared plumbing for 1 and 2: the update command, the link to the Run button, and the two new week tabs.

## 1. What was agreed (Rishi, 2026-11-14)

| Topic | Decision |
|---|---|
| Scope | NFL only. Curated additions, not more stat targets: each one must be useful to someone researching the week |
| Order | Build 1 and 2 now. Keep 3 and 4 in this document for later |
| Refresh | Anything that needs a weekly refresh is tied to **Run week N** in the control room, so one press refreshes everything |
| How it's tied | **Chained after the run** (asked once): the weekly run goes exactly as today and publishes; then the new models refresh as a follow-up. The same after the Saturday injury update. `weekly.py` is untouched |
| Screens | **Separate tabs**, so nothing is crowded: one week tab per model |
| Runs and ship decisions | The agent runs the backtests and training; a rule written down before the reported runs decides what ships. A model that misses its rule goes back to Rishi |
| Mockup | Built first on real data, as for every control-room change; approval waived |
| Documentation | When 1 and 2 are built, a plain-language guide and model cards, written by the sonnet-xhigh subagent with the existing model cards as context |

## 2. The four additions

### 2.1 Availability and role (FL00)

**The gap.** Every player projection assumes we know who plays and how much. [Doc 11](../11-prediction-targets.md) says so directly: availability is an input, not a target. Tuesday's run has no injury report for the week, and the biggest misses come from a player who sat, or from a teammate who took his work.

**What it predicts**, per player and week:

| Output | Meaning | Baseline it must beat |
|---|---|---|
| Chance he plays | A probability that he takes at least one snap | The historical rate for his report status (Out, Doubtful, Questionable, not listed) |
| Snap share if he plays | His share of his side's snaps, with an 80% range | His average over his last 2 games (the `snap_share_l2` the player model already publishes) |
| Opportunity share | His share of the team's targets (WR, TE, RB) and carries (RB) | His rolling share |
| Who inherits | For each player unlikely to play: the teammates whose expected share rises, and by how much | None: it's a view built from the outputs above |

**Two views of the same week.**
- **Tuesday** (after Run week N): no injury report yet. Inputs are the last game's snaps, whether he left a game early, reserve lists, and last week's status.
- **Saturday** (after the injury update): the final report is in. Inputs add the game status and practice participation. This is where the model gains most.

**Data** (all curated already): `injuries`, `snaps`, `rosters_weekly`, `depth_charts`, `player_games`, `plays`.

**Leakage rules specific to it.**
- `rosters_weekly.status == 'INA'` is the game-day inactive list. It is never an input.
- `injuries.date_modified` is null for 2025 and 2026, so a report can't be time-scoped. The Tuesday view never reads week N's report; the Saturday view reads the final report, which is what a Saturday run has.
- "Probable" existed until 2015, so "listed at all" changes meaning across eras. Use Out, Doubtful, Questionable and practice participation.

**To confirm in the probe:** how far back practice participation is usable, the play rate by status and position, how much of a snap-share miss a returning or exiting teammate explains, and the tuning and reporting windows.

### 2.2 Outcome curves (FL01)

**The gap.** The player model publishes three points per projection: the 10th, 50th and 90th percentile. To get "the chance he clears 50 yards" today you have to assume a bell curve and do the math by hand, and yardage isn't bell-shaped (it has a floor at zero and a long right tail).

**What it predicts.** For each projected player and stat, the whole curve: the chance of reaching any number and of landing in any band. The tab lets you type a number or a band and read the chance.

**How.** It reads the player model's published projection files only. It learns the shape of the misses from the walk-forward history: where the actual result landed relative to the projection and its range, by stat, by projection size and by role. No feature or setting of the player model changes.

**What it must beat.**

| Baseline | Why it's the bar |
|---|---|
| A bell curve through P10 and P90 | What you'd compute by hand today |
| The player's own past games as a distribution | The simplest honest curve |

**Scored on:** how close the whole curve is to what happened (CRPS, lower is better), the Brier score of threshold chances at standard marks, and whether a "70% chance" happens 70% of the time.

**An honest note on tight ranges.** A band of plus or minus 7 yards around a typical receiving projection holds roughly 1 game in 9. That's the sport, not a model weakness: the published 80% ranges do cover 80–85% of games. The curve doesn't make ranges tighter. It makes any range you choose come with a chance you can check.

**To confirm in the probe:** which targets get curves first (the yardage targets, then counts), how the misses' shape changes with projection size, and the windows.

### 2.3 Season simulator (FL03, planned)

Simulate the rest of the season many times from the published game model (this week) and the team ratings (later weeks), with the NFL's tiebreakers.

- **Outputs:** each team's chance of the playoffs, the division, each seed and a first-round bye; and each game's **leverage**: how far a team's playoff chance moves between a win and a loss.
- **Check:** replay it from every week of past seasons and score "made the playoffs" for calibration.
- **Kind of work:** simulation on top of published outputs, not a new trained model. Cheap to build.
- **Screen:** decided at its kickoff (a season page, plus the week's leverage on a week tab).

### 2.4 Game-script simulator (FL04, planned)

Simulate each game many times to get the chance of each kind of game (shootout, blowout, one-score finish) and what each player's line looks like in each.

- **Builds on:** the team totals, the play-calling forecast, availability and role (2.1) and the outcome curves (2.2). Build it after those.
- **The hard part:** player outcomes inside a game move together (a quarterback and his receivers), and validating joint outcomes needs more care than validating one stat.

## 3. Screens (FL02)

Two new **week tabs**, appended after Play calls (D108's rule for weekly views). Existing tabs keep their order.

| Tab | Before the games | After the games |
|---|---|---|
| **Availability** | Players at risk of sitting (the chance he plays, Tuesday and Saturday side by side once both exist); expected snap and opportunity shares against each player's baseline; **who inherits** for every player unlikely to play | Who played and how much, against the forecast; the model against its baselines for the week |
| **Outcomes** | Pick a player and stat: the curve, with a number or band you choose and its chance; the week's biggest gaps between the curve and a plain bell curve | The actual marked on each curve; how the threshold chances did against the baselines |

Each tab has a designed empty state before its first refresh, and says when it was refreshed and from which run.

## 4. How it refreshes: chained after the run (D158)

1. **Run week N** starts `nfl weekly run --auto` exactly as today.
2. When that run ends with the week published, the app starts `nfl forecastlab update --auto --expect-week N` as a follow-up. It has its own progress on the new tabs and its own events file.
3. The same follow-up starts after the **Saturday injury update**, which gives the availability model its Saturday view.
4. A failure in the follow-up never touches the published week. The tabs then show an **Update** button (the same command), which is also the way to refresh after a run started from a terminal.

Rules:
- The follow-up holds the weekly-run lock, so it never overlaps a run, a resume or an injury update.
- It only starts from the press that started the run. No page load starts anything, and over Tailscale every write stays refused (D157).
- No follow-up in rehearsal mode.

## 5. Architecture

- **`src/nflengine/forecastlab/`** (new package): `availability.py` and `curves.py` (the models, pure), `runs.py` (files, backtests, W&B), `update.py` (what's due and the update command).
- **Files** under `{NFL_DATA_ROOT}/forecastlab/<S>/week<NN>/` (`availability.parquet`, `curves.parquet`, `update.json`), season scoreboards beside them, backtests under `forecastlab/backtests/`, models under `models/forecastlab/`.
- **CLI:** `nfl forecastlab backtest | predict | grade | update`.
- **W&B:** group `forecast-lab` with its own job types and artifacts (`availability-model`, `curves-model`), each with its own `production` alias.
- **App:** a fifth runner kind `forecastlab_update`, a pre-flight gate, `GET /api/weeks/{S}/{W}/availability` and `/outcomes`.

## 6. The no-touch rule (D107)

- New code only in `src/nflengine/forecastlab/` and the app; a `forecastlab:` block in `settings.yaml`.
- It reads curated data and published outputs (the week's `predictions_players.parquet`, the walk-forward backtest predictions). It never writes to them.
- The digest, the weekly graph and the weekly run's steps are unchanged.
- **One recorded exception (D158):** D107 also said the Run button stays as it is. Rishi asked for the refresh to be tied to Run week N, so the app now starts a follow-up after a successful run. The run itself, its command and `weekly.py` don't change.
- The production-unchanged check runs at every close.

## 7. Phases

| Phase | Title | Depends on | Delivers |
|---|---|---|---|
| FL00 | Availability and role | Curated data | The model, its walk-forward backtest, the ship rule, the model card |
| FL01 | Outcome curves | The player model's published projections | The model, its backtest against the bell curve, the ship rule, the model card |
| FL02 | The update, the Run-week chain and the tabs | FL00, FL01 | `nfl forecastlab update`, the chain, the two tabs (mockup first), the guide |
| FL03 | Season simulator | The game model's outputs, team ratings | Planned |
| FL04 | Game-script simulator | FL00, FL01, team totals, the play-calling forecast | Planned |

## 8. Not in scope

- Betting picks, odds or prices (the brief's non-goals). A threshold chance is a property of the projection, shown in the control room only; the digest is unchanged.
- Fantasy scoring.
- Other sports.
- Sportsbook lines beside the curves: that's the separate [player market lines plan](../player-market-lines.md), still waiting for approval.
