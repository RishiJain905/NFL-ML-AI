# FL02 tab mockups (the visual spec for the Outcomes and Availability week tabs)

**Live mockups** (published 2026-11-14; private to Rishi's account; the ✋ approval is waived, D158):
- Outcomes: <https://claude.ai/artifact/GtsLqoMb8Y5mQVaiXy7HDz> ("Outcomes Tab Mockup").
- Availability: <https://claude.ai/artifact/NiZt8wAJn6jqL1RWFwxDZU> ("Availability Tab Mockup"), the same files with `availability` in their names, built with `--page availability`.
 To update it: re-dump, rebuild into a scratch folder, check it, and publish the rebuilt HTML to that URL. The spec is [`../README.md`](../README.md) §3 and [`../FL02-update-chain-and-tabs.md`](../FL02-update-chain-and-tabs.md).

The tab is drawn inside a slim copy of the control room's shell (the header and the week tabs; the sidebar is the app's own, unchanged). It uses the control room mockup's [`styles.css`](../../control-room/mockup/styles.css) unchanged and both palettes × modes.

| File | What it is |
|---|---|
| `template.html` | The page skeleton with four placeholders (`{{CSS}}`, `{{OCCSS}}`, `{{JS}}`, `{{DATA}}`) and the striped "Mockup controls" strip |
| `outcomes.css` | The tab's own styles, tokens only. The React port copies it to `web/src/pages/week/forecastlab/outcomes.css` |
| `outcomes.js` | The whole mockup in plain JavaScript. Its arithmetic (`cdfAt`, `simpleAt`, `chanceAbove`, `chanceBetween`, `oneIn`) is the same as `web/src/lib/forecastlab.ts` |
| `dump_payloads.py` | Writes `payloads.json`: the real app API's answers, in-process and read-only, for 2026 week 10 (see its docstring) |
| `payloads.json` | The trimmed answers: four stats (`rec_yds-wrte`, `carries-rb`, `pass_yds-qb`, `tackles-lbs`) with the first 24 rows each, 33 whole curves (the first 8 rows of each stat plus four featured players: a star receiver, a low-projection tight end, a lead back's carries, a quarterback), and the real empty answers |
| `template_availability.html`, `availability.css`, `availability.js` | The Availability tab's page, styles (copied to `web/src/pages/week/forecastlab/availability.css`) and script |
| `payloads_availability.json` | The real Availability answer for 2026 week 10 (`base`: the week, Tuesday's view; `team.<T>`: the `team` and `players` of MIN, DET, WAS) |
| `build_mockup.py` | Embeds everything into one HTML file at `--out` (`--page outcomes` or `availability`). It stops and names any missing key |

## Rebuild it

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run python documentation/forecast-lab/mockup/dump_payloads.py            # the real answers -> payloads.json
uv run python documentation/forecast-lab/mockup/build_mockup.py --out <scratch>/outcomes-mockup.html
uv run python documentation/forecast-lab/mockup/build_mockup.py --page availability --out <scratch>/availability-mockup.html
```

Before publishing, scan the built file for `nfl-ml-data` and key-shaped strings (the dump also refuses a data-root path).

## What it shows

- **The strip** (when the curves were made, the curves model, the player model they're built on) and three tiles.
- **One note, once:** a curve is the whole spread behind a projection, *if he plays*; ranges are wide because football is (a receiver lands within 7 yards of his projection about 1 game in 9); "the simple curve" is the hand method (a bell curve through the projection's 10th and 90th percentiles; for counts, a plain Poisson).
- **The stat picker:** the ten stats grouped by QB · RB · WR/TE · LB/S (the mockup has data for four).
- **The curve panel** (the centrepiece): two boxes, *Chance of more than [x] yards* (counts: *Chance of [k] or more*) and *Within ± [n] of the projection* or *Between [a] and [b]*, each with the chance, "about 1 in 9" and the simple curve's chance. The band box opens on the stat's own band around the projection. Below, the chart: for yardage the chance of more than each number (the curve solid, the simple curve dashed), for counts a bar per exact number with the simple curve as dots; the band shaded, the typed number marked, hover or the arrow keys read it. Then the facts: "No yards at all (0 or fewer)", where half and 8 in 10 of such games land, how many past projections the curve was built from; after the games, what happened.
- **The list:** every player for the stat, sorted by projection: the projection, the curve's 80% range as a bar, the chance within ±band and the chance of a standard mark (picked in the header) for the curve and the simple curve. A search box filters by player or team.
- **Where the curve and the simple curve disagree most:** the answer's `gaps` (two per stat), each with a Show button.
- **After the games** (synthetic in the mockup): the actuals in the list and on the chart, and "How the curves did" per stat (the whole-curve miss, the threshold chances, the band said vs happened; this week or season to date). A value is bold only when it is clearly the lowest (2% under the next), so passing yards' tiny gap doesn't look like a win.
- **States:** before the games (real), after the games (synthetic), nothing yet (week 11's real answer), no model (the real sentence), loading.

## The Availability mockup

- **The strip** (each view and when it was made, the view shown, the model, the games made after kickoff), four tiles (players, might sit by group, this week's report, the view) and one note: Tuesday has no injury report yet, so the chance leans on last week; Saturday's is the sharp one; shares are *if he plays*; a share's change is in points.
- **Might sit**, in the reader's three groups and order (the most playing time at stake first): *On this week's injury report* (empty on Tuesday, said plainly), *Regulars with a low chance* (15 rows, then Show all), *On a reserve list* (collapsed). Each row: the player, chips (this week's status, the practice line, last week's status, the roster list), the chance he plays with the usual rate for his status as a tick, Tuesday → Saturday once both views exist, his snap share if he plays against his usual, and "If he sits": each teammate's gains joined on one line, in points of share. After his game: played and his snaps, or didn't play.
- **Biggest role changes:** more snaps, fewer snaps, more of the targets, more of the carries.
- **Every player:** a team picker, then that team's table, offense then defense; the rows on the might-sit list are marked.
- **How it did** (once the week is graded) and **How good is this** (the 2019–2025 backtest).
- **States:** Tuesday (real), Saturday (synthetic, from the model worker's scratch examples), after the games (synthetic, except the Thursday game, which is real), nothing yet, loading.
