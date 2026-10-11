# Play calling mockup (the visual spec for PC01)

**Live mockup:** <https://claude.ai/artifact/KFwYaMJnxjpPdXEz7MsxPQ> ("Play Calling Mockup", published 2026-10-10; private to Rishi's account, agents read it with the Artifact tool's `read` action; Rishi waived the ✋ approval step for PC01). To update it: rebuild, check it, and publish the rebuilt HTML to that URL (a copy in the session scratchpad: the Artifact tool doesn't publish from D:). The spec that goes with it is [`../PC01-play-calling-pages.md`](../PC01-play-calling-pages.md) and [`../README.md`](../README.md) §3; what every number means is in the [guide](../../guides/play-calling.md).

The pages are drawn inside the control room's shell: the same sidebar (with the new **Explore** group between Season and System), header, week tabs (with **Play calls** after **Game day**), tokens and both themes × modes under the gear. This folder holds the source, so the design is versioned with the docs:

| File | What it is |
|---|---|
| `template.html` | The page skeleton with four placeholders (`{{CSS}}`, `{{PCCSS}}`, `{{JS}}`, `{{DATA}}`) and the striped "Mockup controls" strip |
| `playcalling.css` | The pages' own styles (rate bars, tiles, the heat table, the field and the line, the week sparklines, history, the Play calls tab). It uses the control room's tokens only; the control room's [`styles.css`](../../control-room/mockup/styles.css) is inlined unchanged before it |
| `playcalling.js` | The whole mockup in plain JavaScript: the shell, the teams grid, a team page, the Play calls tab, the states, tooltips on hover and focus |
| `dump_payloads.py` | Writes the payloads: the real app API's answers, in-process, on the real tables (see its docstring) |
| `build_mockup.py` | Reads the payloads and the curated team colours, embeds them, and writes one HTML file. It stops and names any missing key |

## Rebuild it

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run python documentation/play-calling/mockup/dump_payloads.py                     # the reader's answers -> payloads.json
uv run python documentation/play-calling/mockup/build_mockup.py                      # payloads.json -> play-calling.html
uv run python documentation/play-calling/mockup/build_mockup.py --payloads <file>   # any other payloads file
```

- **What the build reads:** `{NFL_DATA_ROOT}/cache/play-calling-mockup/payloads.json` (or `--payloads`), the control room's `styles.css`, and `curated/teams.parquet` (team names, nicknames, colours).
- **What it writes:** `{NFL_DATA_ROOT}/cache/play-calling-mockup/play-calling.html`. Nothing else is written, and no data goes into git.
- **Viewing it locally:** Python's `http.server` can't serve files from D: on this machine (`os.fstat` fails, WinError 87). Copy the HTML to a folder on C: (a scratchpad), serve that, and open `http://127.0.0.1:<port>/play-calling.html`.

## The payloads file

The reader's answers in the shapes of `web/src/api/types.ts` (PC01 block), so the React port reuses the mockup's structure:

| Key | What |
|---|---|
| `meta`, `weeks`, `team_info` | The shell's answers (`/api/meta`, `/api/weeks`, `/api/team-info`): the sidebar's week rows are the real ones |
| `teams.<season>` | `GET /api/playcalling/teams?season=` (`PlaycallTeamsResponse`): the grid |
| `team.<season>.<TEAM>.<side>` | `GET /api/playcalling/teams/{team}?season=&side=` (`PlaycallTeamResponse`): a team page, offense and defense |
| `history.<TEAM>.<side>` | `GET /api/playcalling/teams/{team}/history?side=` (`PlaycallHistoryResponse`) |
| `play_calls.<season>_<week>` | `GET /api/weeks/{S}/{W}/play-calls` (`PlayCallsResponse`): 2026 week 5 (the current week), week 1 (last season's numbers), week 6 (`not_yet`), 2025 week 10 |
| `states` | The reader's own empty answers: `not_built.{teams,team,week}` (a season without tables: 2015), `not_yet.week`, `unknown_team` (a 404). Without one the page draws a stand-in and says so |

## What the mockup shows

The striped **Mockup controls** strip (not part of the app) picks the screen (Teams grid · Team page · Play calls tab), the team (the teams the dump has pages for: KC, MIN, CIN, LA, BAL, PHI, NYG, DAL; KC, MIN, LA, BAL also for 2025), the week (the dumped weeks) and the state (Ready · Not built · Not yet · Loading). The sidebar's **Play calling** and the team links work too.

- **The teams grid** (Explore → Play calling): 32 tiles, each with the two signature numbers (`columns[].signature`: offense pass rate over expected, defense blitz rate) as a big number, a bar on **one scale per column** (so the tiles compare), the league marker, the percentile and n. **Sort by** any column (a select plus a direction button); the sorted column shows on each tile when it isn't a signature one. **Tiles / Table**: the table has every column (offense and defense groups), sortable headers, the league row, cells tinted by the difference from the league. A season switch in the header (a select when the answer lists more than three seasons; the mockup has 2026 and 2025); an "FTN waiting" chip names the games FTN hasn't charted.
- **A team page**, with **Offense / Defense** in the header (remembered in the browser) and the season switch:
  - **In five seconds:** the server's `summary` sentences, big; on defense, a line saying a defense's page mixes its own calls with what offenses do against it.
  - **Window:** Season · Last 4 · Last season, one switch (sticky while scrolling) for Identity, By situation and Where the ball goes.
  - **Identity:** the server's groups; each metric a row: the value (big) with n, a **rate bar** (the team's bar from 0 with the league tick; PROE's bar runs from the league tick, since the league isn't 0), the league's value and the **percentile** (a 0–100 dot meter, "more of it", not "better"). Small samples are greyed and the bar hatched. EPA per play, which can be negative and has a league near 0, is drawn from the league tick like PROE.
  - **By situation:** a heat table, rows = the family's buckets under an **All plays** baseline row, columns = the six situational metrics; each cell the value and n, tinted by the difference from the league (diverging: `--s2` less → the panel → `--s1` more), **small cells hatched and never tinted** (most 2026 4th-down cells). Switches: Down & distance · Field zone · Score & clock.
  - **Where the ball goes** (on defense: "Where offenses go against it"): a **half-field** drawn like a whiteboard (5-yard stripes, yard numbers, hash marks, sidelines, the line of scrimmage, the line as circles with the center a square, the QB), six zones short / deep (the gamebook's 16+ air yards) × left / middle / right, a route from the QB to each zone **as thick as its share**, each zone's share and the league's in a box tinted by the difference; the three directions over every depth below it, then deep shots and aDOT as rate rows. Beside it the **offensive line**: the five linemen (tackles and guards as circles, the center a square, the ends dashed), the QB and the back, a run path to each of the seven lanes (left end … right end) as thick as its share, the lane's share and the league's above.
  - **Week by week:** a small chart per key metric, one point per game (hover / focus: the week, the opponent, the value, n), the league's season rate as a dashed line; small games hollow.
  - **History, 2023–2025** (behind **Show history**; in the app it loads only when opened): the source note (research data), then a table per group (personnel, formation, routes of targets on offense; coverage shells, man vs zone, packages on defense): rows × seasons, each cell the value, n and a rate bar with the league tick.
- **The Play calls week tab** (after Game day): the tab's note (descriptive; the forecast is PC02's), the **Biggest matchup shifts** (`shifts[].text`, a bar of the size in SDs, the SDs), then each game as a card with two columns: each offense against the other defense, one row per metric: what the offense does, what the defense allows (or, for a defense's call such as the blitz, what it calls and what the offense has faced), the league, and a dot strip (offense `--s1`, defense `--s2`, the league a tick; legend above). Rows 2+ SD from the league's defenses carry an accent edge (at 1.5 SD half the real rows were marked). Buttons open each team's page.
- **States:** Not built (the reader's message and the command to run), Not yet (a future week's tab), Loading (a skeleton).

## What's real and what's mockup-only

- **Real (the reader's answers):** every number, sentence and message on the pages.
- **Mockup-only (each says so on the page):** the Loading skeleton; any empty state the dump has no answer for (drawn with a stand-in message); the grid's "Not yet" (a season's grid never is: before week 1 it shows last season).

## What the real data changed

- **EPA per play** is in Identity (Results) and goes negative (MIN's defense allows −0.18): a 0-based bar would vanish, so means near 0 or below are drawn from the league tick.
- **"Pass rate over expected"** is the metric's `short` too: on a tile it wraps to two lines under the side, and the heat table's headers wrap.
- **The not-built answer lists every built season** (2016–2026): the season switch becomes a select past three seasons.
- **The heat table's "All plays" row** reads every play, while Identity's PROE is neutral (KC 2026: +2.3 vs +2.0): both are labelled, the tooltip names the situation.
- **Shift accents** at 1.5 SD marked about half of a game's rows; 2 SD keeps them for the real outliers.

## Design choices

- **Five seconds:** the summary sentences come first, in the largest body type; the identity rows put the value, the bar and the league tick on one line, so "more or less than the league" is a glance.
- **Compare with the league, never with 0:** PROE is drawn from the league tick (the league sits near −2), shown ×100 as signed points ("+3.7"); shares from 0 with the league tick.
- **A percentile is "more of it":** a neutral dot meter in ink, no good / bad colours; the tooltip says "not better".
- **Small samples can't pass for tendencies:** n under every rate; n < `min_n` (20) greys the number, hatches the bar or the cell and drops the tint (the heat table's 4th-down cells in 2026 have n of 2–6).
- **One colour job per view:** the team (or the offense) is `--s1`, the defense `--s2`; the diverging tint is `--s2` → panel → `--s1` with a legend; text stays in ink. The field and the line use the board's own tokens (`--field`, `--chalk`), so Turf is a green field with chalk and Playbook a whiteboard with marker by day, a chalkboard by night.
- **Defense "allowed" rates** say in their tooltip (and the group note) that they depend on the offenses faced.
- **History is labelled research data** with its source note and sits behind its own button, so 2026's in-season numbers never mix with it.
- **On a phone:** the identity rows keep the value, n and the bar and move the league and the percentile under the name; the heat table scrolls inside its card; the field and the line stack.

## Changing the design

Design changes start here:
1. Edit the source and rebuild.
2. Republish to the same URL.
3. Update `../PC01-play-calling-pages.md` (and `../README.md` §3 if a screen changed).
4. Change the code.
