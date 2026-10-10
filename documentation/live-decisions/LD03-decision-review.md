# LD03: Decision review and season tracking

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Live decisions rows)

- **Depends on:** LD02
- **Unlocks:** a season-long record of the bot and of coaches' 4th-down habits
- **Read first:**
  - [Live decisions README](README.md) §3 (the finished-week row);
  - the `model-experiment` skill (W&B logging);
  - the [W&B guide](../guides/weights-and-biases.md) (where a new job type is documented).

## Goal

Once a week's games are final, its Game day tab becomes a **decision review**:
- every 4th down of the week;
- the bot's call against the coach's;
- the win probability each call gained or cost;
- the week's boldest and costliest decisions.

Over the season, the bot's own accuracy on real plays is tracked: are its win probabilities calibrated, and do its conversion chances match what happened?

## Scope

- **In:**
  - the review computed from curated play-by-play (no live feed needed);
  - the coach aggressiveness table;
  - the bot's season calibration;
  - a `decision-review` W&B run per week from a CLI command;
  - the tab's finished-week state;
  - a mockup of it (✋ first).
- **Out:** changing the weekly run (the review is its own command, D107); a season page in the sidebar unless Rishi asks (the season view sits inside the tab behind a "This week / Season" switch).

## Tasks

### Mockup first
- [x] 🤖 **Mockup:**
  - the finished-week review: a list of 4th downs (game, quarter, score, situation, coach's call, bot's call, WP gain or cost, confidence), "boldest call", "costliest call", the top 5;
  - the season switch: coach aggressiveness leaderboard, the bot's calibration curve, conversion predicted vs actual.

  Both themes.
- [x] ✋ **Rishi approves the mockup.** Waived by Rishi at the kickoff ("Waive it"); the mockup was still built first as the visual spec (D120 (10)).

### Build
- [x] 🤖 **`live/review.py`:** for a season and week, score every 4th down in curated `plays` with the promoted models.
  - **Inputs:** nflverse states, not ESPN, so it's reproducible.
  - **Output per play:** the bot's call and confidence, the coach's call (go / FG / punt / other), the gain or cost in WP, the result.
  - **Season tables:** each coach's go rate in "go" spots and total WP given up, and the bot's calibration (`wp` against outcomes on this season's plays; conversion chances against attempted 4th downs).
  - Written to `{NFL_DATA_ROOT}/live/<S>/week<NN>/decision_review.parquet` and `live/<S>/season_review.parquet`.
- [x] 🤖 **CLI:** `nfl live review --season S --week N [--wandb]` logs a `decision-review` run (`job_type: decision-review`, tags `season:S`, `week:NN`) with the week's table, the calibration chart and the leaderboard. **It's not a weekly-run step**: Rishi (or the runbook's Tuesday routine) runs it after the weekly run. ✋ Whether it should get a control-room button (a fourth allowlisted command) is decided at the close. **Decided at the kickoff: no button, CLI only** (Rishi; D120 (6)).
- [x] 🤖 **App:** `GET /api/live/{season}/{week}/review` reads the review files (or computes on the fly from curated plays when the file isn't there, cached), and `GET /api/live/{season}/season-review`. The tab's finished-week state and the season switch.
- [x] 🤖 **Backfill** the 2026 weeks played so far (1–5+) and the 2025 season as the example.
- [x] 🤖 **Tests:** the review on a fixture week (calls, costs, the leaderboard), the app's finished-week state, nothing written by the app.

### Verify and close
- [x] 🤖 **Sanity checks:** the 2025 leaderboard against public 4th-down aggressiveness rankings (for example nfl4th's / rbsdm's published season tables): the same coaches near the top and bottom.
- [x] 🤖 **Mockup parity;** Codex / Sol review; fixes with tests.
- [x] 🤖 **Docs:**
  - the guide (the review, how to read the leaderboard and the calibration);
  - the W&B guide (`decision-review`);
  - the runbook (the Tuesday command);
  - the model card (live calibration section).
- [x] 🤖 Production-unchanged check.
- [x] ✋ **Close LD03**, which closes the Live decisions track (and decides on the button). Waived by "mark the phase complete" in the kickoff; the button was decided at the kickoff (none).

## Rishi-in-the-loop moments (what to look for)

- **The review:** do the "costliest calls" match what you saw on TV? If the bot calls something clearly silly (for example go for it on 4th & 12 in Q1), note the play: it's a model or state bug, not a hot take.
- **The calibration chart:** the bot's live WP should sit on the diagonal like the backtest did. It needs about 4 weeks of plays before it says much.

## Exit criteria (+ how to verify)

- Every finished 2026 week has its review in the tab and in W&B.
- The 2025 leaderboard passes the sanity check.
- Tests, ruff and the web checks pass; the production-unchanged check passes.

## Pitfalls / notes

- **"The coach's call"** must read fakes (a fake punt is a go), penalties before the snap (no decision) and kneel-downs correctly. Reuse LD01's rules.
- **Selection bias again:** the conversion calibration on attempted 4th downs leans optimistic. Show 3rd downs next to it.

## As built: deviations from the task list (2026-10-10; D120, D121)

- **The ✋ checkpoints:** Rishi waived the mockup approval at the kickoff; the mockup was still built first (from the real API's answers) and published as the visual spec. The button question was decided at the kickoff too: **no button**, `nfl live review` stays a terminal command (the tab builds any week it needs by itself). The close was waived by "mark the phase complete".
- **Files:** `season_review.json` sits beside `season_review.parquet` (the parquet is the leaderboard; the JSON holds every season table and the stamp). Each file carries a stamp (schema, model version, a hash of the curated rows and the coach fixes); a week's JSON also carries its rows' hash.
- **Endpoints:** `season-review` takes `?through=N` (the tab's week; clamped to the newest week with plays). Statuses `ok` / `no_plays` / `no_models` are answers, not errors.
- **CLI:** `--weeks A-B` for a range, `--smoke` for a W&B logging check; exit 2 without a week, 3 when no week had curated plays; official write failures are errors.
- **Definitions fixed while building** (from real data): the boldest call is the lowest-convert go that counted (not wiped out), in a game in doubt (10–90%), where kicking was within 5 points (a desperation go is forced, not bold); timid / bold costs count only go spots / kick spots; the conversion calibration counts real attempts only.
- **The selection-bias pitfall reads the other way:** attempted 4th downs convert *more* often than the bot says (coaches pick their spots); 3rd downs are the fair test and match within a point. The tab says so.
- **The production-unchanged rehearsal** ran into the session scratchpad on C: (the data drive took seconds per small write; LD01 / LD02's had landed on F: through a relative `--out`).
- **Sanity check:** against FTN's 2025 Aggressiveness Index (the only full public coach table reachable): Spearman 0.66 / 0.71, half of each top 10 and 6 of each bottom 10 shared, LaFleur first in both ours and a report of rbsdm's; guide §10.5.
- **Reviews:** the test worker's hand-worked tests found 2 bugs, the UI worker's real-data mockup 2 wording faults; Sol 15 findings (1 high), 13 fixed with tests, 2 kept with a reason (D121).
