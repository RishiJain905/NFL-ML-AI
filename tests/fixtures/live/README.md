# ESPN fixtures (LD01 tests)

Trimmed copies of **real** ESPN answers, saved on 2026-10-10 under `D:/nfl-ml-data/live/` (the
LD01 probe, archived scoreboards and the finished games' summaries). The live-feed tests
(`tests/live/test_live_espn.py`, `test_live_state.py`, `test_live_replay.py`) read them through
`tests/live/live_fakes.py`; no test reads the data drive or the network. The pre-game facts of
each game (spread, total, roof ...) are written as literals in `live_fakes.py`, copied from
curated `games`.

## What each file is

Scoreboard answers (`scoreboard/{eventId}` shape: an event at the top level; the week's
scoreboard has the same events under `events`):

| File | Source | Event | What it covers |
|---|---|---|---|
| `scoreboard_week5.json` | `probe/scoreboard_current.json` (4 of its 15 events) | TB@DAL (final), PHI vs JAX (London, neutral site, pre-game), NYG@WSH and BUF@LAR (pre-game) | A week's scoreboard; ESPN's `WSH` and `LAR` codes (ours `WAS`, `LA`); pre-game and final events have **no `situation`** |
| `event_401872980_tb_dal_final.json` | `probe/scoreboard_event_401872980.json` | TB@DAL | A finished game (`state` post), `venue.indoor` true |
| `event_401872973_ten_bal_4th_and_10.json` | `wayback/20261004191835_scoreboard.json` | TEN@BAL, Q3 2:10 | 4th & 10 at TEN 14, the **away** team on offense (`yardLine` 86 = yards to goal as is); `lastPlay` with ESPN's win probability; `weather` without wind |
| `event_401872966_ari_nyg_timeout_after_td.json` | same file | ARI@NYG, Q3 1:44 | **`down` -1, `distance` 0, a junk `yardLine` 35 and no `possession`**: an "Official Timeout" after a touchdown (a kickoff is next) |
| `event_401872657_sf_lar_neutral_3rd_and_goal.json` | `wayback/20260911004916_scoreboard.json` | SF vs LAR, Melbourne, Q1 7:04 | **Neutral site**; 3rd & Goal at LAR 2 for ESPN's away team (`yardLine` 2); first half (the opening kickoff matters) |
| `event_401872960_bal_dal_neutral_3rd_and_goal.json` | `wayback/20260927234021_scoreboard.json` | BAL vs DAL, neutral site, Q4 0:14 | 3rd & Goal at BAL 5 for ESPN's **home** team (`yardLine` 95 -> 5 to go); `homeTimeouts` 0 after "Timeout #3 by DAL" |
| `event_401872970_lar_phi_4th_and_goal.json` | `wayback/20261004194149_scoreboard__-1791142909895.json` | LAR@PHI, Q4 7:31 | 4th & Goal at LAR 2, home team on offense (`yardLine` 98), `homeTimeouts` 2 |
| `event_401872930_dal_nyg_halftime.json` | `wayback/20260914015933_scoreboard__-1789351173920.json` | DAL@NYG | **Halftime**: status still `in` ("Halftime"), `lastPlay` "End of Half", the situation keeps a stale 1st & 10 and has no `possession` |
| `event_401872948_atl_gb_timeout_after_td.json` | `wayback/20260925022323_scoreboard__-1790303003457.json` | ATL@GB, Q3 3:38 | A second TV timeout after a touchdown (same shape as ARI@NYG) |
| `event_401872925_tb_cin_3rd_and_1.json` | `wayback/20260913170730_scoreboard.json` | TB@CIN, Q1 14:23 | 3rd & 1 at TB 37 (`yardLine` 63) right after "Timeout #1 by TB" (`awayTimeouts` 2) |

Summaries (`summary?event=ID` shape: `header`, `gameInfo`, `drives.previous[].plays[]`):

| File | Source | Game | What it covers |
|---|---|---|---|
| `summary_401872980_tb_dal.json` | `probe/summary_401872980.json` | TB@DAL, 24-16, 183 plays | A whole game: 9 kickoffs, 15 TV timeouts, 6 team timeouts, 2 two-minute warnings, "End Period", "End of Half", "End of Game"; 18 "No Play" penalties; 7 punts; 2 field goals; 2 kneel-downs; a touchdown with a failed two-point try; **`yardsToEndzone` wrong on 3 home punts** (mirrored) and 0 on the TV-timeout rows; a post-play penalty that left the play standing |
| `summary_401872945_ind_kc_ot.json` | `summaries/401872945.json.gz` | IND@KC, 33-30 in **overtime**, 213 plays | Overtime (2 timeouts each, the overtime clock, a two-minute warning in overtime), "End of Regulation", field goals in overtime, a delay-of-game "No Play" on 4th down; one stretch of Q4 where ESPN's feed **lost a timeout row** (play 3402, text "Daniel Jones Pass Complete for 5 Yds to Keenan Allen"); nflverse has this game, so replay states can be checked against it |
| `summary_401872966_ari_nyg_cut_at_timeout.json` | `summaries/401872966.json.gz`, **cut** | ARI@NYG up to the "Official Timeout" at Q3 1:44 (play 4018729663058) | What ESPN's summary looked like at that moment: state `in`, the unfinished drive as `current`; its last real play is a touchdown, so no snap is pending; contains a successful (REVERSED) challenge that costs no timeout |

## How they were trimmed

Written once by a script that is not in the repo: it reads the saved JSON and writes compact,
ASCII-only JSON (`separators=(",", ":")`, exact bytes). Kept, because a parser reads it or a
test explains it:

- events: `id`, `date`, `name`, `shortName`, `season`, `week`, the competition's `id`, `date`,
  `neutralSite`, `venue` (`id`, `fullName`, `indoor`), `competitors` (`id`, `homeAway`, `winner`,
  `score`, `team` with `id`, `abbreviation`, `displayName`, `location`, `name`, `nickname`),
  `status`, the whole `situation` (the `lastPlay` cut to `id`, `type`, `text`, `scoreValue`,
  `team`, `probability`, `start`, `end`), the event-level `status` and `weather`;
- summaries: `header` (id, season, week, competition, competitors, status), `gameInfo.venue`
  (without `indoor`: **ESPN's summary never has it**, only the scoreboard's venue does), `meta`
  (`gameState`, first and last `wallclock`), every drive (`id`, `team`, `result`) and every play
  (`id`, `sequenceNumber`, `type`, `text`, `awayScore`, `homeScore`, `scoringPlay`, `isPenalty`,
  `wallclock`, `period.number`, `clock` and the `start` / `end` spots with `down`, `distance`,
  `yardLine`, `yardsToEndzone`, `team.id`).

Dropped: logos, links, athletes, `teamParticipants`, leaders, broadcasts, headlines, news,
`boxscore`, odds / `pickcenter`, `winprobability`, videos, standings, injuries, article text.

The cut summary keeps the plays up to the named play, moves the last drive to `current`, sets
the game state to `in`, and zeroes the competitors' scores (the live scores come from the
scoreboard entry, not the summary).

## Using them

Tests load a fresh copy with `live_fakes.fixture(name)`, so a test may edit what it gets (for
example, remove a key or change `distance`) without touching the file. Edits that build a
situation ESPN really shows but no fixture holds are written out in the test (the TV timeout
after a punt in `test_live_state.py` is TB@DAL's final entry with the status and situation of a
TV timeout, the shape copied from ARI@NYG).
