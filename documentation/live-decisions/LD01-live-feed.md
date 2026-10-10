# LD01: The live feed and replay

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Live decisions rows)

- **Depends on:** LD00 (the promoted `live-decision-models`)
- **Unlocks:** LD02 (the Game day tab calls this layer)
- **Read first:**
  - [Live decisions README](README.md) §2 (ESPN's fields, quirks and timing) and §5 (safety rules);
  - `src/nflengine/ingest/espn.py` (how the weekly ingest already calls ESPN; reuse its HTTP helper's habits, not its module);
  - the `curated-data` skill (team codes, `plays` columns for the parity check).

## Goal

Turn ESPN's live JSON into the exact game state LD00's decision engine expects, reliably and politely, and prove it against nflverse's play-by-play on games already played. Make the whole thing usable from the terminal (`nfl live …`) before any UI exists. Measure ESPN's real lag on a live game.

## Scope

- **In:**
  - `live/espn.py`, the client;
  - `live/state.py`, the parser;
  - mapping to our `game_id` and team codes;
  - the pre-game spread and total for the state;
  - `live/replay.py`;
  - the CLI commands;
  - the parity check;
  - the latency measurement;
  - the decision-log entry for the feed.
- **Out:** the app (LD02); recording every live game (only what a measurement needs).

## Tasks

### Probe first
- [x] 🤖 **Fetch and save** (into `{NFL_DATA_ROOT}/live/probe/`) the full scoreboard, one game's `scoreboard/{eventId}` and its `summary` for a finished 2026 game. Confirm every JSON path in README §2 still holds; note any new field.
- [x] 🤖 **Event id → our `game_id`:** match on date and teams (ESPN codes → canonical with `normalize_team`, adding WSH and LAR if missing). Check all 2026 weeks 1–5. `espn_scoreboard` in curated data already carries `espn_event_id` + `game_id`: prefer it.

### Build (`src/nflengine/live/`)
- [x] 🤖 **`espn.py`:**
  - a client that only talks to `https://site.api.espn.com`;
  - a 5 s timeout and at most one request per game every 2 s (a per-event clock);
  - exponential back-off after errors (2 s → 60 s);
  - a fixed User-Agent naming the project;
  - gzip;
  - answers kept in memory with their fetch time.

  Functions: `scoreboard(season, week)`, `game(event_id)`, `summary(event_id)`.
- [x] 🤖 **`state.py`:** ESPN JSON → `GameState`:
  - offense and defense (canonical codes), home / away;
  - score difference from the offense's side;
  - quarter, game and half seconds left;
  - down, distance;
  - **yards to goal** from `yardLine` + possession, never `yardsToEndzone`;
  - both teams' timeouts;
  - the spread and total from the offense's side (curated `lines`: the newest pre-kickoff row; nflverse convention, + = home favoured);
  - roof / indoor, era;
  - `as_of` (ESPN's fetch time and the last play's `wallclock`).

  Handle:
  - `down = -1` during timeouts (use the next real snap state);
  - goal-to-go;
  - "No Play" penalties;
  - kickoffs and PATs (no decision);
  - overtime;
  - the two-minute warning;
  - missing `situation` (pre / post game).

  Each oddity gets a test fixture from the saved JSON.
- [x] 🤖 **`replay.py`:** step through a finished game's `summary` plays as if live (each play's start state is the "situation"), so the decision path can be exercised on any past game, at any time.
- [x] 🤖 **CLI:**
  - `nfl live games [--week N]`: this week's games, status, score, down and distance;
  - `nfl live call --event ID`: fetch now, print the state and the call (3rd or 4th down) or "not a 3rd / 4th down";
  - `nfl live replay --event ID [--downs 4]`: every 3rd / 4th down of a finished game with the bot's call next to what happened;
  - `nfl live latency --event ID --minutes M`: poll one live game every 2 s and log, per play, the first time it appeared against its `wallclock`, to `{NFL_DATA_ROOT}/live/latency/<event>.jsonl`.
- [x] 🤖 **Tests:** the parser on the saved fixtures (every quirk), team-code mapping, the host allowlist (another host is refused), the rate limit, back-off, and that no test makes a real network call (a fake transport).

### Verify
- [x] 🤖 **State parity:** for every 2026 game in weeks 1–5, replay ESPN's summary and compare each 3rd / 4th-down start state with nflverse `plays` (offense, down, distance, yards to goal, score difference, quarter, clock within 5 s, timeouts). **Target ≥ 98% of states equal**; list and explain the rest (usually penalties and corrected spots).
- [x] 🤖 **Decision replay:** `nfl live replay` on five 2026 games; the calls agree with LD00's backtest scoring of the same plays from nflverse states (same engine, different source).
- [ ] ⏭ 🤖 **Latency on a live game** (moved to Sun 2026-10-11, PHI vs JAX in London, event 401872981, 09:30 ET; Rishi at the kickoff: close now, measure Sunday; PROGRESS → Season calendar): run `nfl live latency` through one live game (Thursday night or a Sunday window; an agent may run it in the background). Report the median and p90 lag and the worst case, and update README §2.

### Docs and close
- [x] 🤖 **Decision-log entry** (D109 is the planning one; add the as-built one): the endpoints, the polling rules, the parser's rules, the parity result and the measured lag.
- [x] 🤖 Guide `live-decisions.md`: the live feed (where the data comes from, how fresh it is, the TV-delay table, what "as of" means), the CLI, replaying a game. Runbook: a short "Game day" section.
- [x] 🤖 Production-unchanged check.
- [x] ✋ **Close LD01.**

## Rishi-in-the-loop moments (what to look for)

- **The latency log:** the lag column should be mostly under 15 s. If the median is over 30 s, the 3rd-down table matters even more, and README §3's "as of" wording must say so plainly.
- **Optional, any game night:** run `uv run nfl live call --event <id>` on a 4th down you're watching and compare it with what you'd expect (and with ESPN's own win probability in the feed).

## Exit criteria (+ how to verify)

- State parity ≥ 98% on 2026 weeks 1–5 (the numbers in the session log).
- `nfl live call` works on a live game, and `nfl live replay` works on any finished 2026 game.
- The measured lag is in README §2.
- Tests and ruff pass; the production-unchanged check passes.

## Handoff to next phase

LD02 calls `espn.game()`, `state.parse()` and `decide()` from the API. The rate limit and back-off live in the client, so the app inherits them.

## Pitfalls / notes

- **ESPN can change or block the feed without notice** (D109). Fail soft everywhere and say so on screen.
- **Never trust `yardsToEndzone`.**
- **The spread to use is the pre-game one**, not ESPN's live line. Live lines would leak the game state into the "pre-game strength" feature.
- **Don't store ESPN data in curated tables.** Live data is a view, not a source. The probe and latency files live under `live/` only.

## As built: deviations from the task list (2026-10-10; D116, D117)

- **The latency run is a dated step.** No NFL game was live during the session (Saturday morning); Rishi chose to close LD01 now and measure on Sunday 2026-10-11 (PROGRESS → Season calendar). `nfl live latency` was smoke-tested live on a pre-game event (3 requests in a minute) and on a scripted game in the tests. The live `nfl live call` exit check moves with it; the parser was proven on real archived live scoreboards instead (Wayback captures of ESPN's scoreboard during 2026 games: 4th & 10, 4th & Goal, 3rd & Goal at a neutral site, `down = -1` timeouts, halftime).
- **`nfl live call --event`** extends LD00's `call` (`--state` still works); **`nfl live parity`** was added so the check can be re-run.
- **Probes:** saved under `{NFL_DATA_ROOT}/live/probe/` (the full scoreboard, one game, TB@DAL's summary, 13 Wayback captures); every 2026 summary of weeks 1–5 under `live/summaries/`. The path helper is `paths.live_data` (`paths.live` is taken by rehearsal paths).
- **Event id → `game_id`:** curated `espn_scoreboard` (all 93 2026 events through week 6); date-and-teams matching is the fallback. WSH and LAR were already in `normalize_team`.
- **The client** adds 0.5 s between any two requests (a bulk replay stays polite), and serves the last good answer marked stale while ESPN fails or the client backs off.
- **Spread / total:** curated `lines` keeps only the newest snapshot per source, so "the newest pre-kickoff row" = nflverse's schedule line, else the Odds API median, else ESPN's, the last two only if saved before kickoff day.
- **Extra state inputs:** a null `games.roof` (retractable roofs before the game) uses the stadium's usual roof, then ESPN's `venue.indoor`; outdoor wind and temperature come from nflverse (played) or the pre-game forecast (the field-goal model reads them).
- **Replay** needed rules the phase file didn't foresee. Found by the parity check: scores only from `scoringPlay` rows, timeouts lost on challenges, and an estimated snap clock for scoring plays (ESPN's is the clock at the score). Found by the tests: what the offense did is read from the play's full text before ESPN's type (a penalty-wiped play typed "Rush", a fumbled punt return typed "Fumble Recovery (Opponent)", and after a reversed review only the final ruling); `decision_plays` leaves out kneels and spikes. `choice_of` now agrees with nflverse's `play_type` on 8,366 of 8,368 2026 snaps (the other two have ESPN's short-form text).
- **No W&B** for the parity check: it's a data check, not a model run.
