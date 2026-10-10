# Game day mockup (the visual spec for LD02 and LD03)

**Live mockups** (private to Rishi's account; agents read them with the Artifact tool's `read` action): **LD03, current:** <https://claude.ai/artifact/4fv6KpgdftSgWToDTAB4uB> ("Game Day Review Mockup": every LD02 state plus the decision review; published 2026-10-10, the approval waived by Rishi). **LD02:** <https://claude.ai/artifact/QpALHDapDHPhLTHLNCQDsW> (approved by Rishi on 2026-10-10, "Approved, build it"; the toss-up as drawn; kept as approved). To update the current one, rebuild, check it, and publish to its URL (an update from another conversation reads it first: about 900 KB). The spec that goes with it is [`../LD02-game-day-tab.md`](../LD02-game-day-tab.md), [`../LD03-decision-review.md`](../LD03-decision-review.md) (the finished-week decision review, added 2026-10-10) and [`../README.md`](../README.md) §3.

The Game day tab is drawn as a tab of the control room: the same shell (sidebar, header, week tabs with **Game day** after **Graph**), the same tokens and both themes × modes under the gear. This folder holds its source, so the design is versioned with the docs:

| File | What it is |
|---|---|
| `template.html` | The page skeleton with four placeholders (`{{CSS}}`, `{{GDCSS}}`, `{{JS}}`, `{{DATA}}`) and the striped "Mockup controls" strip |
| `gameday.css` | The tab's own styles (game list, game panel, call card, option bars, if-stopped table, team context). It uses the control room's tokens only; the control room's [`styles.css`](../../control-room/mockup/styles.css) is inlined unchanged before it |
| `gameday.js` | The whole mockup in plain JavaScript: the shell, the board, the panel, the Check this play button's states, the 4th- and 3rd-down cards, the team context, tooltips on hover and focus |
| `review.js` | LD03's decision review: a finished week (summary, highlights, every 4th down) and the season switch (leaderboard, calibration, conversion, field goals, the weekly trend). The build puts it inside `gameday.js`'s scope at the `/* @review.js */` line, so it shares the helpers |
| `review.css` | The review's styles, after `gameday.css` (the same tokens only) |
| `dump_payloads.py` | Writes the payloads: the real app API, in-process, on ESPN's replayed week-4 play logs (`nfl app --live-replay`), and the decision reviews from curated plays (`--review-only` redoes just those) |
| `build_mockup.py` | Reads the payloads and the curated team colours and stadiums, embeds them, and writes one HTML file. It stops and names any missing moment or review key |

## Rebuild it

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run python documentation/live-decisions/mockup/dump_payloads.py                    # the reader's answers -> payloads.json
uv run python documentation/live-decisions/mockup/dump_payloads.py --review-only      # only the review block (keeps the moments)
uv run python documentation/live-decisions/mockup/build_mockup.py                     # payloads.json -> game-day.html
uv run python documentation/live-decisions/mockup/build_mockup.py --payloads <file>  # any other payloads file
```

- **What the build reads:** `{NFL_DATA_ROOT}/cache/live-decisions-mockup/payloads.json` (or `--payloads`), the control room's `styles.css`, and `curated/teams.parquet` and `curated/games.parquet` (team colours, nicknames, stadiums) for the weeks the payloads name.
- **What it writes:** `{NFL_DATA_ROOT}/cache/live-decisions-mockup/game-day.html`. Nothing else is written, and no data goes into git. The dump's app keeps what it computes in its own cache (`cache/control-room/live/`: the team contexts and the reviews); a first dump of the review block takes up to half a minute per season the app hasn't reviewed, later ones seconds.
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

**The review block (LD03):** `review.week_<S>_<W>` is `GET /api/live/{S}/{W}/review` (`LiveReviewResponse`) and `review.season_<S>_<W>` is `GET /api/live/{S}/season-review?through=W` (`LiveSeasonReviewResponse`), the shapes in `web/src/api/types.ts` (LD03 block):

| Key | What |
|---|---|
| `2026_4` | Week 4 2026 (243 decisions; costliest GB punt on 4th & 2 at TB 44; boldest TB 4th & 11 at GB 47, Q4 1:34, down 3) and the season through week 4 |
| `2026_1` | Week 1 2026 and the season through week 1: the small-sample case |
| `2026_5` | Week 5 2026: `no_plays` (Tuesday's run brings its plays); its season clamps to week 4 |
| `2025_18` | Week 18 2025, a full regular-season week, and the season through it: **in-sample** |
| `2025_22` | The Super Bowl (20 decisions, no go-for-it, so no boldest call) and the whole 2025 season |
| `final_no_plays` | Week 4 in its `final` phase before Tuesday's run: the reader's own `no_plays` wording (`no_plays_text`) |

## What the mockup shows

The striped **Mockup controls** strip (not part of the app) picks the state:

- **Game day:**
  - Before kickoff: week 5's slate, "Game day opens at the first kickoff";
  - Games on: the board and the picked play;
  - No game on: "Next: DET at CAR, 8:20 PM";
  - All final: "Every week-4 game is final" with **Open the decision review** (works when the review is ready; off with "Ready after Tuesday's run" when the plays aren't in);
  - Past week: the decision review (LD03, below);
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

- **Review week** (with Past week): 2026 week 4 · week 1 · week 5 (no plays) · 2025 week 18 · the 2025 Super Bowl.
- **Review** (Past week and All final): Ready · Plays not in yet · Loading · No models.

Clicking another tab, or a season page, shows a note pointing to the control room mockup: those pages don't change in LD02 or LD03.

### The decision review (LD03)

A finished week's Game day tab. A **This week / Season** switch sits in its header (no sidebar page); in week view a stepper goes to the previous / next week (the app steps one week; the mockup jumps between the dumped weeks), which is how weeks 1–3 (not in the sidebar) and past seasons are reached besides a link.

- **The header:** the week, the models' version, "nflverse play-by-play, not ESPN", out of sample or the **in-sample** warning (2025: the models were trained on 2010–2025, so its calibration flatters the bot; 2026 is the first season they haven't seen), and where the answer came from (`nfl live review`'s file, the app's cache, computed on first open).
- **This week:**
  - the numbers: 4th downs decided (wiped / fakes), coach and bot agree (toss-ups), went for it in go spots ("33 of 81"), expected wins given up against the bot, and **who calls what** (coaches' go / field goal / punt against the bot's, two bars with a legend);
  - **Boldest call** and **Costliest call** cards (the situation big, the clock and score, the convert chance or the cost, coach vs bot with the label, the result, nflverse's play text as text) with "Show it in the list";
  - **The five costliest** as a ranked list (cost bars, one ink colour), each a link to its row;
  - **Every 4th down**, grouped by game in kickoff order (final score, each team's wins given up): quarter and clock, the offense and the score (offense first), the situation, the coach's call, the bot's call with its label, the edge (**+ gained** over the next best / **− cost** against the bot, in points, a bar around 0) and the result, with **fake** / **wiped** tags. A row opens to the three options on one 0–100% axis (the bot's best in the accent, "coach" / "bot" marks), the odds and the play text; hovering it shows the same in a tooltip. **All / Disagreements only** filters the list.
- **Season** (through the week shown; the server clamps a week without plays to the newest reviewed one, and says so):
  - a small-sample note while there are 6 weeks or fewer;
  - the league's numbers (go spots, kick spots, wins given up with the timid / bold split, agreement);
  - **Coach aggressiveness**: ranked by the go rate in go spots (a bar with the league's line), with go spots, went, the go rate on all 4th downs, went in kick spots, wins given up (timid · bold) and agreement; sortable by rank, go spots, wins given up and agreement; the league row at the foot; coaches with under 5 go spots dimmed; on a phone the team and counts move into sub-lines;
  - **Is the bot's win chance right?**: the calibration curve (the bot `--s1` and nflfastR's `vegas_wp` `--s3` on the same snaps, against the dashed diagonal; snaps per bin under the axis; a tooltip per bin) with Brier and ECE for both;
  - **Does the bot's conversion chance match?**: 3rd downs next to 4th-down tries by yards to go (1 … 10+; the bot's line `--s1`, what happened as hollow ink circles, dashed under 10 plays, n under each distance), with the selection-bias note ("Tries convert more often than the bot says": coaches go when they like their chances);
  - **Field goals: made vs predicted** by distance band;
  - **Week by week**: the bot's go rate (`--s1`) against the coaches' (`--s2`) on one 0–100% axis, a tooltip per week (agreement, wins given up, tries converted vs predicted).
- **States:** Plays not in yet (the reader's message and "See the season so far"); Loading (a skeleton and "the first open of a week or season the server hasn't reviewed yet can take up to half a minute"); No models (the reader's `no_models` message).

## What's real and what's mockup-only

- **Real (the reader's answers):** every board, call and context on the page.
- **Real (LD03):** every review, season table, chart and message (the `review` block).
- **Mockup-only (each says so on the page):**
  - the **No models** state (the real bundle is loaded);
  - the **Loading** and **Error** cards, and the 0.7 s load after a press;
  - the **Before kickoff** board (ESPN's real week-5 board with every game set back to not started: the dump ran after Thursday's game);
  - **the review's Loading and No models states** (the real answers were cached; the bundle is loaded);
  - **All final → Plays not in yet** uses week 4's `final_no_plays` (week 4's plays were already curated when the dump ran);
  - **the week stepper** jumps between the weeks the dump has.

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

## Design choices for the review (LD03)

- **One colour job per view.** In the week view the hues mean go / field goal / punt (the "who calls what" bars); the edge bars and the five costliest stay in ink (cost solid, gain light), so nothing else competes with them. In the season view the hues mean who: the bot `--s1`, coaches `--s2`, nflfastR's `vegas_wp` `--s3`; "what happened" is always ink (hollow circles), never a series colour.
- **Numbers as the LD02 cards:** win chance as %, gains and costs in points with one decimal (0.049 → 4.9), expected wins with two decimals.
- **The boldest call** is the longest shot a coach chose when kicking was still a real option (a go, not wiped out, the game in doubt: the bot's win chance 10–90%, going within 5 points of kicking); it's shown even when the bot agreed, and its card says so (week 4: TB 4th & 11 at GB 47, +2.6).
- **Selection bias is drawn, not just told:** attempted 4th downs sit next to 3rd downs on the same axis, the small samples dashed.
- **In-sample is a warning, not a footnote**, at the top of both views for 2025.

## Changing the design

Design changes start here:
1. Edit the source and rebuild.
2. Republish to the same URL.
3. Update `../LD02-game-day-tab.md` (and `../README.md` §3 if a screen changed).
4. Change the code.
