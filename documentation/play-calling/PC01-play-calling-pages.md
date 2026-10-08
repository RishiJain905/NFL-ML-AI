# PC01: The Play calling pages

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Play calling rows)

- **Depends on:** PC00 (the tables), CR03 (the finished control room)
- **Unlocks:** PC02 (fills the forecast parts), PC03 (the play browser hangs off the team page)
- **Read first:**
  - [Play calling README](README.md) §1, §3 (screens), §4;
  - the `control-room` skill, the [control room README](../control-room/README.md) §5 and §7;
  - the dataviz rules the app's charts follow (`web/src/charts/`).

## Goal

A new **Explore** group in the sidebar with **Play calling**: a teams grid, then a team page showing how that team calls plays on offense and defense, drawn so it reads like a playbook rather than a spreadsheet. A new **Play calls** week tab shows each game's matchup of tendencies, descriptive for now (the forecast arrives in PC02).

## Scope

- **In:**
  - a mockup (✋ first);
  - the sidebar group;
  - the teams grid;
  - team pages (Offense / Defense / History);
  - the Play calls tab with descriptive matchups;
  - the endpoints, tests and guide section.
- **Out:** the forecast (PC02); diagrams and the play browser (PC03).

## Tasks

### Mockup first
- [ ] 🤖 **Mockup** with real 2026 data (through the newest FTN week) and 2025 history, in both themes:
  - the sidebar's new **Explore** group;
  - the teams grid;
  - a team page:
    - **Identity:** rate bars with the league marker and percentile;
    - **By situation:** a down × distance heat table with field zone and score state switches;
    - **Where the ball goes:** a half-field with pass direction × depth cells, and run lanes on an offensive-line diagram (left end … right end);
    - **Week by week:** sparklines;
    - **History 2023–2025:** personnel and formation shares, coverage shells on defense, routes of targets;
  - the Play calls week tab: two columns per game (each offense against the other defense) and the "biggest matchup shifts".
- [ ] ✋ **Rishi approves the mockup.** Changes go mockup first, then the README, then code.

### Backend
- [ ] 🤖 **`readers/playcalling.py`** + endpoints (all GET, read-only, short reads with no memory map, D100):
  - `GET /api/playcalling/teams?season=`: the grid's numbers;
  - `GET /api/playcalling/teams/{team}?season=&side=offense|defense`: identity, situations, field cells, run lanes, week-by-week series;
  - `GET /api/playcalling/teams/{team}/history?side=`: 2023–2025 shares, labelled `research`;
  - `GET /api/weeks/{S}/{W}/play-calls`: the week's games with each side's season-to-date tendencies against the other side's "allowed" rates.

  The team is validated against the 32 canonical codes and the season as elsewhere. No file paths.
- [ ] 🤖 **Tests:** the readers on fixture tables; an empty state when the tables aren't built ("run `nfl playcalling build`"); `scrub()` / NaN → null; the existing endpoints unchanged.

### Web
- [ ] 🤖 **Sidebar:** a new **Explore** group under System (or between Season and System, as the mockup settles), with **Play calling**. The existing groups are unchanged.
- [ ] 🤖 **Routes** `/explore/play-calling` and `/explore/play-calling/{team}`; the teams grid; the team page with an Offense / Defense switch (remembered in the browser) and History behind its own section.
- [ ] 🤖 **Chart components** (in-house SVG like the rest, D96's dataviz rules):
  - a rate bar with league marker and percentile;
  - a down × distance heat table;
  - a half-field direction × depth grid;
  - an offensive-line run-lane diagram;
  - reuse the sparkline.
- [ ] 🤖 **Week tab:** add `play-calls` to `TABS` after `game-day`; the descriptive matchup view.
- [ ] 🤖 **Web tests** for each component and page state.

### Verify and close
- [ ] 🤖 **Mockup parity**; a Codex / Sol review; fixes with tests.
- [ ] 🤖 **Spot checks:** three teams' identity numbers against public pages (rbsdm PROE, Sharp Football's tendencies) for 2025, within rounding or definition differences, which get documented.
- [ ] 🤖 **Docs:**
  - the guide `play-calling.md` (a page walkthrough with screenshots, how to read each chart, what History means);
  - `guides/control-room.md` (the Explore group and the new tab);
  - the `control-room` skill.
- [ ] 🤖 Production-unchanged check; the CR test suite passes unchanged.
- [ ] ✋ **Close PC01.**

## Rishi-in-the-loop moments (what to look for)

- **The mockup:** does a team page tell you its identity in five seconds? Is the field / line drawing playbook-like enough? What's missing that you'd look for before a game?

## Exit criteria (+ how to verify)

- The Explore → Play calling pages and the Play calls tab work on real data.
- The existing tabs and pages are unchanged.
- Tests, ruff and the web checks pass; the production-unchanged check passes.

## Pitfalls / notes

- **Small samples:** early in the season a team has 3–4 games. Show n on every rate and grey out rates with n < 20, so a 1-for-2 doesn't read as a tendency.
- **Defense "allowed" rates** depend on the offenses faced. Say so in a tooltip; PC02 adjusts for it.
