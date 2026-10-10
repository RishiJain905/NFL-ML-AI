# LD02: The Game day tab

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Live decisions rows)

- **Depends on:** LD01 (the live layer), CR03 (the finished control room)
- **Unlocks:** LD03; game nights with the bot
- **Read first:**
  - [Live decisions README](README.md) §1, §3 (screens), §4 (architecture), §5 (safety);
  - the `control-room` skill and the [control room README](../control-room/README.md) §5 (safety rules), §7 (quality gates);
  - [guides/control-room.md](../guides/control-room.md) "how to extend it safely".

## Goal

On a game night Rishi opens the current week, clicks **Game day**, picks the game he's watching and, when a 3rd or 4th down matters to him, presses **Check this play**. In about a second he gets the call card: the call, the win probability after each choice, the confidence label, the odds behind it and whether this team is good at it. Nothing is computed or fetched until he asks, apart from a slow refresh of the game list.

## Scope

- **In:**
  - a mockup (✋ first);
  - the API endpoints;
  - the team-context builder;
  - the tab (game list, game panel, call card for 3rd and 4th down, the feed's "as of" and lag notices);
  - tests;
  - a guide section;
  - the first live use.
- **Out:** finished weeks' decision review (LD03); automatic alerts (not wanted, D109); anything on other tabs.

## Tasks

### Mockup first
- [x] 🤖 **Mockup** of the Game day tab in both themes (Turf & Pylon dark, Playbook light), with real data: Published 2026-10-10: [Game day mockup](https://claude.ai/artifact/QpALHDapDHPhLTHLNCQDsW), data from `mockup/dump_payloads.py` (the real API on replayed 2026 week-4 moments).
  - a replayed 2026 game for the live states;
  - the week's slate;
  - a 4th-down card (a close call and a toss-up);
  - a 3rd-down card with the if-stopped table;
  - the "ESPN is behind" state;
  - the "no game on" state.

  Publish it as an artifact; source in `documentation/live-decisions/mockup/`.
- [x] ✋ **Rishi approves the mockup** (or asks for changes: mockup first, then this file, then code). **Approved by Rishi 2026-10-10** ("Approved, build it"; the toss-up as drawn: TOSS-UP big).

### Backend (`src/nflengine/app/`)
- [x] 🤖 **`readers/live.py`** + endpoints:
  - `GET /api/live/{season}/{week}/games`: the week's games from the scoreboard (cached 30 s), each with our `game_id`, status, score, clock, possession, down and distance, our pre-game win % (the week's `predictions_games.parquet`, primary row) and the market spread;
  - `GET /api/live/games/{event}/call`: a fresh `game(event)` (subject to the client's 2 s limit), the parsed state, then:
    - on a 3rd down: the conversion chance, pass chance and if-stopped table;
    - on a 4th down: go / FG / punt with WP, gap, confidence and the odds;
    - otherwise: the state and "not a 3rd or 4th down";

    always with `as_of`, the seconds since ESPN's last play, and ESPN's own win % as a reference;
  - `GET /api/live/games/{event}/context?offense=XXX`: "is this team good at this?".

  The event must be in the requested week's scoreboard; season and week are validated as elsewhere.
- [x] 🤖 **`live/context.py`**, from curated data as of the start of the week, read-only and cached per week:
  - the team's 4th-down go rate and conversion rate (this season, last season);
  - 3rd / 4th & ≤ 2 conversion;
  - red-zone touchdown rate;
  - the kicker's makes by distance band this season and his career long;
  - the punter's net average;
  - the coach's go rate in "go" spots (where the bot says go) last season and this.
- [x] 🤖 **Models in memory:** load `live-decision-models:production` once at app start (lazy, on the first Game day request). If missing: the tab shows "the decision models aren't trained yet (LD00)".
- [x] 🤖 **Tests:** the endpoints on a fake ESPN transport (live 3rd down, 4th down, timeout, pre / post game, ESPN down, a bad event id); the outbound-host allowlist; `scrub()` on ESPN text; nothing written outside `cache/control-room/live/`; the existing endpoints' answers unchanged (the CR test suite passes as is).

### Web (`web/`)
- [x] 🤖 **Add `game-day` to `TABS`**, after `graph`. No other tab changes.
- [x] 🤖 **The tab:**
  - **Game list:** cards with score, clock, down and distance and a live dot; refreshed every 30 s only while the tab is visible; a manual refresh.
  - **Game panel:** situation, timeouts, our pre-game %, the market spread, ESPN's last-play text.
  - **The Check this play button.**
  - **Call card:** a big call, three option bars with WP after each, gap, Confident / Lean / Toss-up, the odds line, the team-context block.
  - **If-stopped table** on 3rd down.
  - **"As of" stamp:** "ESPN as of 8:41:07 PM · last play 12 s ago". When ESPN is behind, the card says "ESPN still shows 3rd & 4: the 4th down hasn't posted. Here's the if-stopped table".
  - Keyboard accessible; the dataviz rules for the bars.
- [x] 🤖 **States:** before the first kickoff (the slate and "Game day opens at kickoff"); every game final (a link to the review, LD03); a past week (LD03's review, or "coming in LD03").
- [x] 🤖 **Web tests** (Vitest): the card for each state, the button's loading and error states, no request until the click (apart from the list refresh).

### Verify and close
- [x] 🤖 **Rehearse on a replayed game:** a test-only flag (`nfl app --live-replay <event>`) serves a finished game's plays as if live, so the whole tab can be exercised outside game times.
- [ ] ⏭ 🧑 **First live use:** Rishi uses the tab during a real game (TNF or a Sunday window) and checks a few 3rd and 4th downs. Note what felt slow, confusing or wrong. **Dated: Sunday 2026-10-11** (PROGRESS → Season calendar; Rishi: "close now, use it Sunday").
- [x] 🤖 **Mockup parity:** screenshots next to the mockup; differences fixed or logged in README §8.
- [x] 🤖 **Review:** a Codex / Sol review of the change (the new outbound call and endpoints especially). Fixes with tests; a decision-log entry.
- [x] 🤖 **Docs:**
  - the guide `live-decisions.md` §"The Game day tab" (a walkthrough with screenshots, what each number means, the TV-delay advice);
  - `guides/control-room.md` (the new tab, linked);
  - the `control-room` skill (the live endpoints and the outbound rule).
- [x] 🤖 Production-unchanged check; the CR test suite passes unchanged.
- [x] ✋ **Close LD02** (Rishi's verdict from the first live use). **Waived by Rishi at the kickoff** ("marking the phase complete"); his first-live-use verdict comes on Sunday and any fixes go in a small `[LD02]` commit.

## Rishi-in-the-loop moments (what to look for)

- **The mockup:** is the call readable at a glance from the couch? Is the team-context block the right "is this team good at this?" Is the toss-up label clear?
- **The first live game:** how often was ESPN behind your TV? Did the if-stopped table cover those moments? Did any call look plainly wrong? (Note the game, quarter and down.)

## Exit criteria (+ how to verify)

- The tab works on a real live game (Rishi's verdict) and on a replayed one (tests).
- The existing tabs, pages and Run button are unchanged (the CR tests pass as is).
- Nothing is fetched until a click, apart from the 30 s list refresh while the tab is visible (a network-call test).
- Tests and ruff pass; the web checks pass (`lint`, `typecheck`, `test`, `build`); the production-unchanged check passes.

## Handoff to next phase

LD03 fills the tab for finished weeks and adds the season view of the bot's own accuracy.

## Pitfalls / notes

- **Several live games at once:** only the chosen game is fetched on a click; the list uses one scoreboard call for all of them.
- **A check during a commercial or replay review** returns the same state as last time. That's fine; say "no new play since your last check".
- **Don't block the event loop:** scoring is fast, but the ESPN call isn't. Use the async client or a worker thread.

## As built: deviations from the task list (2026-10-10; D118, D119)

- **Waivers at the kickoff (Rishi):** the ✋ mockup approval asked mid-session (approved); the 🧑 first live use moved to **Sunday 2026-10-11** (Season calendar), the phase closes now; the ✋ close waived ("marking the phase complete").
- **Endpoints:** season and week are in every live URL (`/api/live/{season}/{week}/games/{event}/call`, `.../context?offense=XXX`), so "the event must be in the requested week's scoreboard" is checked against the week asked for; the list takes `?refresh=1` (the Refresh button: re-asked when the server's copy is over 10 s old) and carries `live_week`; no `/situation` endpoint (the list carries every game's situation).
- **Which week is live:** the first week whose last kickoff + 6 h is still ahead, not the calendar's week (it moves to N+1 at week N's last kickoff, which would end Monday night's Game day at kickoff).
- **The summary (snap times, the opening kickoff):** fetched at once only for a first-half check before the opening receiver is known (~2 s once per game) or when ESPN shows no down; otherwise one background refresh per game after a check, at most every 30 s. "Last play N s ago" is timed from the snap when the server has the play log, else from the app's first sighting of that play.
- **"ESPN may be behind"** fires past 55 s (the phase file's example wording kept; 35 s fired on ordinary gaps between snaps).
- **No "win chance now" on 4th-down cards:** the wp model's read of a 4th-down state can sit above every option late in close games (NYG 80.6% vs 73.3%; +0.06 points on average over 2025's 3,997 4th downs). The 3rd-down card shows it.
- **The team context:** red-zone drives with a snap only (kickoff / extra-point rows carry the previous drive's number); field-goal bands from `kick_distance`, else yards to goal + 18; the kicker's long counts only kicks before the week; the coach's go-spot rate scored by the production bot (no bootstrap). Cached per week in `cache/control-room/live/context/` (a stamp: the two seasons' plays files, games, the coach fixes, the bundle's version).
- **`nfl app --live-replay`** takes `--replay-at` (ISO; no zone = Eastern), `--replay-speed`, `--replay-lag`; it serves the event's whole week. `nfl app` exits 3 when the replay can't start.
- **The tab label's live dot / count** comes only from the cached list (no fetch from other tabs). The one control-room test that changed is `shell.test.tsx`'s list of tab names.
- **Review:** a cross-review (sonnet-xhigh, read-only) found 3 security and 12 correctness items, all fixed with 55 regression tests (`tests/app/test_app_live_review.py`), incl. the ESPN client holding its lock through a 2 s wait (`live/espn.py`, an LD01 file) and raw ESPN ids / team codes reaching the browser; then the Sol review (D119).
- **Production-unchanged check:** `tests/test_production_untouched.py` passes; `nfl weekly rehearse --season 2026 --week 5 --steps game,player --at 2026-10-07T00:28:30Z` reproduced the published games (30), players (4,542) and teams (120) rows exactly (`baseline_role` 5e-18, float order).
