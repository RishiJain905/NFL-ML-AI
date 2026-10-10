# Game day mockup (the visual spec for LD02)

**Live mockup:** <https://claude.ai/artifact/QpALHDapDHPhLTHLNCQDsW> (private to Rishi's account; agents read it with the Artifact tool's `read` action). **Approved by Rishi on 2026-10-10** ("Approved, build it"; the toss-up as drawn). To update it, rebuild, check it, and publish to the same URL. The spec that goes with it is [`../LD02-game-day-tab.md`](../LD02-game-day-tab.md) and [`../README.md`](../README.md) §3.

The Game day tab is drawn as a tab of the control room: the same shell (sidebar, header, week tabs with **Game day** after **Graph**), the same tokens and both themes × modes under the gear. This folder holds its source, so the design is versioned with the docs:

| File | What it is |
|---|---|
| `template.html` | The page skeleton with four placeholders (`{{CSS}}`, `{{GDCSS}}`, `{{JS}}`, `{{DATA}}`) and the striped "Mockup controls" strip |
| `gameday.css` | The tab's own styles (game list, game panel, call card, option bars, if-stopped table, team context). It uses the control room's tokens only; the control room's [`styles.css`](../../control-room/mockup/styles.css) is inlined unchanged before it |
| `gameday.js` | The whole mockup in plain JavaScript: the shell, the board, the panel, the Check this play button's states, the 4th- and 3rd-down cards, the team context, tooltips on hover and focus |
| `dump_payloads.py` | Writes the payloads: the real app API, in-process, on ESPN's replayed week-4 play logs (`nfl app --live-replay`) |
| `build_mockup.py` | Reads the payloads and the curated team colours and stadiums, embeds them, and writes one HTML file |

## Rebuild it

```powershell
uv run python documentation/live-decisions/mockup/dump_payloads.py                    # the reader's answers -> payloads.json
uv run python documentation/live-decisions/mockup/build_mockup.py                     # payloads.json -> game-day.html
uv run python documentation/live-decisions/mockup/build_mockup.py --payloads <file>  # any other payloads file
```

- **What the build reads:** `{NFL_DATA_ROOT}/cache/live-decisions-mockup/payloads.json` (or `--payloads`), the control room's `styles.css`, and `curated/teams.parquet` and `curated/games.parquet` (team colours, nicknames, stadiums) for the weeks the payloads name.
- **What it writes:** `{NFL_DATA_ROOT}/cache/live-decisions-mockup/game-day.html`. Nothing else is written, and no data goes into git.
- **Viewing it locally:** Python's `http.server` can't serve files from D: on this machine (`os.fstat` fails, WinError 87). Copy the HTML to a folder on C: (a scratchpad), serve that, and open `http://127.0.0.1:<port>/game-day.html`.

## The payloads file

`{"built_at", "source", "moments": {name: {...}}}`. Each moment has `at` (the mock clock), `note`, `games` (`LiveGamesResponse`) and, for a play, `call` (`LiveCallResponse`) and `context` (`LiveContextResponse` for the offense). These are the shapes in `web/src/api/types.ts` (LD02 block), so the React port can reuse the mockup's structure.

| Moment | What |
|---|---|
| `board` | Sun 2026-10-04, 3:57 PM ET: the 1 PM games late in the 4th, the late games not started |
| `fourth_close` | NYG 4th & 5 at ARI 34, 2:11 left, up 3. It also carries `call_repeat` (a second check with no new play) and `call_stale` (ESPN failing after a good answer: HTTP 503) |
| `fourth_tossup` | PHI 4th & Goal at LA 2, 7:31 left, up 10 (3:41 PM) |
| `third` | PHI 3rd & 4 at PHI 46, 3:10 left, up 3 |
| `behind` | ESPN 45 s behind: it still shows NYG's 3rd & 5 while the TV is at the 4th down |
| `none` | NE at BUF, 2nd & 5: "2nd & 5: checks are for 3rd and 4th downs." |
| `between` | Sun 7:50 PM ET: the afternoon games final, SNF not started |
| `final` | Tue 12:00 AM ET: every week-4 game final |
| `before` | Thu 4:00 PM ET, week 5: the slate before the first kickoff |
| `past` | 2026 week 3, standing in for a past week |

`build_mockup.py` stops and names any key that's missing. A play's `call.game` replaces its game on that moment's board.

## What the mockup shows

The striped **Mockup controls** strip (not part of the app) picks the state:

- **Game day:**
  - Before kickoff: week 5's slate, "Game day opens at the first kickoff";
  - Games on: the board and the picked play;
  - No game on: "Next: DET at CAR, 8:20 PM";
  - All final: the decision review, "coming in LD03";
  - Past week: "Decision review: coming in LD03" and the finals;
  - No models: "The decision models aren't trained yet (LD00)", with the button off.
- **The play** (with Games on):
  - 4th · close;
  - 4th · toss-up;
  - 3rd down: the if-stopped table;
  - ESPN behind: the 4th & 5 row is the 4th-down call;
  - Stale feed: the last good answer;
  - Repeat check: "No new play since your last check";
  - Not 3rd / 4th.
- **Check button:** Idle · Loading (a spinner and a skeleton card) · Answer · Error. Pressing the real button in the mockup shows a 0.7 s loading state, then the answer.

Clicking another tab, or a season page, shows a note pointing to the control room mockup: those pages don't change in LD02.

## What's real and what's mockup-only

- **Real (the reader's answers):** every board, call and context on the page.
- **Mockup-only (each says so on the page):**
  - the **No models** state (the real bundle is loaded);
  - the **Loading** and **Error** cards, and the 0.7 s load after a press;
  - the **Before kickoff** board (ESPN's real week-5 board with every game set back to not started: the dump ran after Thursday's game);
  - **week 3 as the past week** (in the app, weeks 1–3 are before go-live and aren't listed in the sidebar).

## Design choices (for the ✋ review)

- **One honest axis:** the three bars all start at 0%, so 93.0 vs 93.3 look the same. The gap is stated in words ("Worth 1.4 more points of win chance than the field goal").
- **The accent marks the best option only.** On a Toss-up no option gets the full accent:
  - the big line reads **Toss-up**;
  - the options within a point share a half-strength accent;
  - the sub-line names the edge ("Field goal by 0.3 points over going for it").
- **Labels:** a three-pip chip (Confident ▮▮▮, Lean ▮▮▯, Toss-up ▮▯▯, the last dashed) in ink colours, not status colours. It comes with one plain line ("The call held in 20 of 20 re-checks of the models") and a key of all three labels with the thresholds in a tooltip. Lean never appears in week 4's data, so the key is the only place it shows.
- **The game list** sits on the left on a desktop and becomes a swipeable row on a phone. Cards with a 3rd or 4th down next show it in bold.
- **"Is this team good at this?"** shows:
  - the team's rates next to last season and the league, with a small dot plot on a 0–100% scale (hidden on a phone);
  - the coach's go rate where the bot says go, by distance, with this play's band highlighted;
  - the kicker by distance, with this kick's band highlighted and his career long;
  - the punter's net.
- **On a phone** the if-stopped table drops the "If they gain" column and shows the labels as pips only; each row's tooltip still has the call, the label and the win chances.

## Changing the design

Design changes start here:
1. Edit the source and rebuild.
2. Republish to the same URL.
3. Update `../LD02-game-day-tab.md` (and `../README.md` §3 if a screen changed).
4. Change the code.
