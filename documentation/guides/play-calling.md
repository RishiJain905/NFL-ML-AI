# Play calling guide: team tendencies

**What this is:** a plain-language guide to the Play calling track's data: what play-calling tendencies are (pass rate over expected, play-action, screens, blitzes, coverage shells: a primer), which data covers what and when, the exact definitions, the tables `nfl playcalling build` writes and how to read them, how they refresh each week, and real numbers from 2025 and 2026 (PC00). PC01 adds the control room's pages, PC02 the forecast, PC03 the play diagrams.

**Related docs:** the spec is the [Play calling README](../play-calling/README.md) and the phase files ([PC00](../play-calling/PC00-tendency-data.md), [PC01](../play-calling/PC01-play-calling-pages.md), [PC02](../play-calling/PC02-tendency-forecast.md), [PC03](../play-calling/PC03-play-diagrams.md)). Decisions: D107 (the no-touch rule), D108 (where the pages go), D110 (diagrams), D122 (PC00 as built: the definitions, the tables, the refresh) in the [decisions log](../10-decisions-log.md). The W&B side is in the [W&B guide §6.9](weights-and-biases.md). The weekly commands are in the [runbook → Play calling](../runbook.md). Agents use the `play-calling` skill.

---

## 1. Play-calling tendencies, in plain words

A **tendency** is how often a team does something, compared with what the league does in the same spot. "The Rams use play-action on 35% of dropbacks; the league, 23%" is a tendency. The numbers are **rates** over a set of plays (a season so far, the last four games), always shown next to the **league rate** and the team's **percentile** among the 32 teams.

The terms the pages and tables use:

| Term | What it means | Why it matters |
|---|---|---|
| **Dropback** | A play where the quarterback drops back to pass: a pass attempt, a sack or a scramble. The opposite is a **designed run** | "Pass rate" means dropbacks, not just throws: a sack was still a pass call |
| **Expected pass rate** (`xpass`) | nflverse's model of how often an average team drops back in this exact spot (down, distance, field position, score, time, timeouts) | 3rd & 10 is a pass almost always; 1st & 10 up 14 in the 4th is mostly a run. Raw pass rates mostly measure game situations |
| **Pass rate over expected** (PROE) | Dropback rate minus expected pass rate, in percentage points. +5 = this team passes 5 points more often than an average team would have in the same spots | The cleanest single number for "pass-first or run-first", because the situation is taken out |
| **Neutral situation** | Win probability 20–80%, downs 1–3, not the last 2 minutes of a half | Removes garbage time and hurry-up, where every team passes or runs for the clock |
| **Play-action** | The QB fakes a handoff before throwing | Freezes linebackers; some offenses (the Rams, the 49ers) are built on it |
| **Screen** | A short pass behind the line with blockers set up in front | A substitute for the run game, and a way to punish blitzes |
| **RPO** (run-pass option) | The QB decides after the snap whether to hand off or throw, reading one defender | Counts on both run and pass plays |
| **Motion** | A player moves before the snap | Shows the defense's coverage (a defender following = man) and gives a head start; league use has risen every year |
| **Shotgun / under center / pistol** | Where the QB stands: 5–7 yards back, right behind the center, or ~4 yards back with a back behind him | Under center hides run vs play-action better; shotgun helps the pass |
| **Deep shot / aDOT** | A pass thrown 20+ yards past the line of scrimmage / the average air yards of a team's throws (average depth of target) | How vertical an offense is |
| **Run direction and gap** | Where a designed run goes: left or right **end** (outside the tight end), **tackle**, **guard**, or the **middle** | Which side and which blocks an offense trusts; drawn on an offensive-line diagram |
| **Blitz** | The defense sends extra rushers from the back seven (linebackers, defensive backs) | High-risk pressure; a blitz-heavy defense invites screens and quick throws |
| **Pass rushers** | How many defenders rush the passer (4 is standard; 5+ usually means a blitz) | |
| **Box count** | Defenders near the line before the snap. 6 or fewer = **light** (good for the run), 8+ = **heavy** (stacked against the run) | |
| **Coverage shell** | The pass-coverage scheme. **Cover 0**: all man, no deep safety (an all-out blitz). **Cover 1**: man with one deep safety. **Cover 2**: two deep safeties, zone underneath. **2-man**: two deep safeties, man underneath. **Cover 3**: three deep zones. **Cover 4** (quarters) and **Cover 6** (quarter-quarter-half): four deep zones. **Cover 9**, **combo**: newer labels (2023+) | The defense's answer to the pass; how much **man vs zone** a team plays |
| **Personnel** | Who's on the field for the offense, as two digits: running backs, then tight ends. **11** = 1 RB, 1 TE (so 3 WRs); **12** = 1 RB, 2 TEs; **21** = 2 backs (a fullback), 1 TE | Heavier groups (12, 13, 21, 22) usually mean more runs |
| **Defensive package** | How many defensive backs: **base** (4 or fewer), **nickel** (5), **dime** (6+) | The defense's answer to personnel |

The **side** matters. An `offense` row describes what the team does with the ball. A `defense` row describes **what offenses do against that defense**: the defense's own choices (blitz, rushers, box, coverage) plus what opponents call against it (their pass rate over expected, play-action, deep shots, explosive plays allowed). Situations are always read from the offense's side, so a defense row's `lead_9plus` means the opponent was up 9+.

## 2. Which data covers what, and when

| Source | What it gives | Seasons on D: | During 2026? |
|---|---|---|---|
| nflverse play-by-play (curated `plays`) | run / pass, `xpass`, pass depth (short / deep) and direction (left / middle / right), air yards, run location and gap, shotgun, no-huddle, EPA, success | 2010–2026 (the tables start in 2016) | **Yes**, refreshed by Tuesday's weekly run |
| FTN charting (curated `ftn_plays`) | play-action, screen, RPO, motion, QB alignment (shotgun / under center / pistol), backfield count, box, blitzers, pass rushers, hash, QB out of the pocket | 2022–2026 | **Yes**: ~2 days after each game (see below) |
| PFR (curated `pfr_pass`) | blitzes each passer faced: the blitz **cross-check** | 2018–2026 | About a week behind |
| nflverse participation (**research only**, `research/nflverse/participation`) | formation, personnel, defensive personnel, coverage shell, man / zone, the targeted receiver's route, pressure, time to throw | 2016–2025 | **No.** 2026's file arrives about February 2027 |

Checked on 2026-10-10 (PC00's probes):
- **FTN has a row for every 2022–2025 scrimmage play** (regular season and playoffs), but a few are blank placeholders for plays it didn't chart (QB location '0', box 0, every flag off: 2024's BAL at TB has 97 of 131), so **99.3–99.9% are charted**. On Tuesday 2026-10-06 the curated 2026 file had weeks 1–3 complete and 15 of week 4's 16 games: everything but Monday night's ATL at NO (98.4% of 2026's plays).
- **When FTN lands:** nflverse pulls FTN at least twice a day (about 11:00 and 17:00 UTC; 07:00 and 13:00 ET), and FTN's own target is 48 hours after a game. In the snapshots, Sunday's games were in by Tuesday 13:00 ET (40–51 hours after kickoff), and Thursday 2026-10-08's TB at DAL was in by Saturday 07:00 ET (35 hours); week 4's Monday game was in by Thursday 2026-10-08 at the latest. So Tuesday evening's build has every game but Monday night's, which should arrive **Wednesday** (to be confirmed on 2026-10-14); a Wednesday rebuild after 13:00 ET completes the week's files (§5).
- **FTN revises past weeks:** every pull re-stamps the whole season (`date_pulled`), and the 2025 file was republished on 2026-09-23. Rebuild from the newest snapshot; a page can move slightly after a re-pull.
- **Participation joins 100%** of 2016–2025 scrimmage plays, but what's filled varies. On dropbacks: formation and personnel ~99–100% every season; coverage 0% in 2016–2017, ~89% in 2018–2022, ~99.8% in 2023–2025; the targeted receiver's route 85–90%; pressure 89–100%. On designed runs, coverage and routes are empty (the denominators are pass plays).
- **Two eras in participation:** 2016–2022 is NGS-sourced, 2023–2025 FTN-sourced, with different vocabularies: formations SINGLEBACK / I_FORM / EMPTY / JUMBO / WILDCAT before, UNDER CENTER after; routes FLAT / HITCH / CROSS / OUT / IN / ANGLE before, QUICK OUT / HITCH-CURL / IN-DIG / SHALLOW CROSS-DRAG / DEEP OUT / SWING / TEXAS-ANGLE after; coverage adds COVER 9, COMBO, BLOWN in 2023 and drops PREVENT. The tables keep each era's own labels (a metric exists only in the seasons that use its label). `formation_shotgun` and `formation_pistol` span both eras, but NGS lists EMPTY as its own formation and FTN folds empty sets into shotgun, so a history chart steps between 2022 and 2023.
- **Play-by-play quirks that shaped the definitions:** `shotgun` = 1 on pistol snaps too (pbp marks 1,646 of FTN's 1,647 pistol snaps of 2025 as shotgun); the gamebook's `pass_length` "deep" starts at 16 air yards (not 20); `run_gap` is empty on middle runs; `xpass` is filled on every run and pass.

So **in season the tables carry play-by-play and FTN tendencies**; personnel, formations, coverage shells and routes are **history** (labelled `history_only`) until 2026's participation lands.

## 3. The definitions

All of them live in one file, `src/nflengine/playcalling/labels.py`, and every table, page and forecast uses them.

| Term | Rule here | Source |
|---|---|---|
| **Plays counted** | `play_type` run or pass, no two-point tries: sacks and scrambles in, kneels, spikes and penalty-only (`no_play`) rows out. Regular season **and** playoffs | pbp |
| **Dropback** | nflverse `pass == 1` (pass attempts, sacks, scrambles) | pbp |
| **Designed run** | a run that isn't a dropback (scrambles out) | pbp |
| **Pass attempt** | `play_type` pass and not a sack (throwaways in) | pbp |
| **PROE** | the mean of (dropback − `xpass`) over the plays. Shown in the pages ×100 as percentage points | pbp |
| **Neutral situation** | win probability (the offense's `wp`) 20–80% inclusive, downs 1–3, more than 120 seconds left in the half (overtime counts by its own clock) | pbp |
| **Deep shot** | a pass attempt with 20+ air yards; per attempt | pbp |
| **aDOT** | mean air yards per pass attempt | pbp |
| **Pass zone** | the gamebook's depth (short / deep, deep ≈ 16+) × direction (left / middle / right): six zones for the field diagram | pbp |
| **Run direction** | `run_location` × `run_gap`: left / right end, tackle, guard, and middle (no gap); per designed run | pbp |
| **Explosive play** | a dropback gaining 20+ yards or a designed run gaining 10+ | pbp |
| **Shotgun rate** | pbp `shotgun` (pistol counts as shotgun here); FTN's three-way `qb_*_share` splits them | pbp / FTN |
| **Play-action, screen** | FTN `is_play_action`, `is_screen_pass`; per FTN-charted **dropback** | FTN |
| **RPO, motion** | FTN `is_rpo`, `is_motion`; per FTN-charted play | FTN |
| **Blitz** | FTN `n_blitzers > 0`; per FTN-charted dropback. PFR's count (`pfr_pass.times_blitzed` per opponent dropback) is shown as **`blitz_rate_pfr`, a cross-check** | FTN (PFR) |
| **Pass rushers** | mean FTN `n_pass_rushers` on dropbacks with at least one rusher charted | FTN |
| **Box** | FTN `n_defense_box` on every charted play (0 = not charted, dropped); light ≤ 6, heavy ≥ 8 | FTN |
| **Personnel** | backs (RB + FB) then tight ends: "11", "12" ...; groups other than 11 / 12 / 13 / 21 / 22 / 10 count as "other"; special-teams units dropped | participation |
| **Defensive package** | defensive backs on the field: base ≤ 4, nickel 5, dime 6+ | participation |
| **Coverage, man / zone, pressure** | per dropback with a value | participation |
| **Target's route** | per pass attempt with a route (only the targeted receiver's route exists) | participation |

**Two things to know about PROE:**
- **The league isn't at 0.** nflverse's `xpass` was fit on 2006–2019 and isn't re-centred each season, and teams now pass a little less than it expects: league neutral PROE was −0.2 to −1.9 points in 2016–2021 and −1.2 to −2.6 since (2025: −2.0; 2026 so far: −1.9). rbsdm.com shows the same thing (its 2025 league mean is −1.7). So read a team **against the league**: the tables' `diff` column is the centred number.
- **rbsdm's PROE tab uses a wider filter** (every down and every second of the half, penalty plays included); ours is the phase's neutral rule (downs 1–3, not the last two minutes, rbsdm's "neutral pass rate" convention). On 2025's regular season the two rank the teams almost identically (§7).

**Blitz sources disagree.** FTN and PFR chart blitzes differently: per team-game they correlate 0.69 (P06), per team-season 0.86 (2025). Both name Minnesota the most blitz-heavy defense of 2025 (51% FTN, 42% PFR).

## 4. What the build produces

`uv run nfl playcalling build --season S` writes `{NFL_DATA_ROOT}/playcalling/<S>/` (each file written to a temporary name, then swapped in, so a crash never leaves half a file; `build.json` goes last):

| File | One row per | What's in it | 2025 size |
|---|---|---|---|
| `plays_enriched.parquet` | scrimmage play | keys, teams (offense / defense / home), the situation (down, distance, yards to goal, field zone, score state, quarter, time bucket, neutral, two-minute), the call (dropback, designed run, attempt, sack, scramble), `xpass` / `pass_oe` (nflverse: percentage points), pass depth / direction / zone / air yards / deep shot, run location / gap / direction, yards, EPA, success, explosive, shotgun, no-huddle, the players' ids, the play text, the FTN labels (`ftn_charted`, play_action, screen, rpo, motion, qb_alignment, backfield, box, blitzers, rushers, blitz, hash, out_of_pocket) and the participation labels (`part_charted`, formation, personnel, extra_ol, def_package, coverage, man_zone, target_route, pressure, time_to_throw, part_box, part_rushers) | 34,502 rows × 82 columns |
| `team_game_tendencies.parquet` | team × game × side × metric × situation | `n` (plays counted), `value` (the rate or mean): the week-by-week view, and the actuals PC02 grades a forecast against | ~234k rows |
| `team_tendencies.parquet` | team × side × **as-of week** × window × metric × situation | `n`, `games`, `value`, `league_value`, `diff`, `pct`, plus `family`, `unit`, `source`, `history_only` | ~944k rows |
| `league_tendencies.parquet` | as-of week × window × metric × situation | the league's pooled rate (`n`, `value`, `teams`), over the offense rows. In `season` and `last_season` it equals every row's `league_value`; for `last4` each side pools its own teams' last four games, so use `team_tendencies.league_value` for a defense's `last4` comparison | ~15k rows |
| `build.json` | the build | as-of weeks, last complete week, FTN coverage by week and the games without FTN yet, participation coverage, row counts, the definitions and the metric catalogue, notes | |

**As of a week.** A row with `as_of_week = W` is **what a run on the Tuesday night before week W could see** (the leakage rule, D123): games before week W, and only data published by the **cutoff**, the end of the Tuesday (US Eastern) after the previous week's last game (or week W's first kickoff, if that comes first). Play-by-play has every earlier game by then. **FTN** counts a game once 48 hours have passed since its kickoff (FTN's target), so **Monday night's game counts from the following week**. The **PFR** cross-check runs a week behind (PFR publishes about a week late; so even the row after the Super Bowl lacks the Super Bowl's PFR blitzes). A game without a kickoff time fails closed (its FTN waits a week). The tables come out the same whether they're built on Tuesday or after Wednesday's refresh, and the same as a backtest of that week would see. Three windows:
- `season`: every game of the season before week W;
- `last4`: the team's last 4 games before week W (fewer early in the season);
- `last_season`: every game of the previous season (the same for every W; at week 1 it's the only window).

> Example (2025, KC's play-action rate, offense): as of week 10 the season rate was 16.8% (368 dropbacks, 9 games) but the last 4 games only 11.5%; as of week 15, 15.0% and 11.4%; last season (2024) 21.5%. The league stayed at 22–25%.

The as-of weeks run from 1 to one past the last **complete** week: a week counts only when all its games are final **and** in the curated play-by-play. On Saturday 2026-10-10 that's weeks 1–5 for 2026 (week 5's Thursday game isn't curated yet). Past seasons run to 23 (2021+) or 22 (2016–2020): the as-of row after the Super Bowl is the whole season.

**Situations.** Every metric has `all` and `neutral`. Six key metrics (dropback rate, PROE, shotgun rate, deep-shot rate, play-action, blitz) also come by:
- **down & distance**: `1st_down`, `2nd_short` (1–3 yards) / `2nd_medium` (4–6) / `2nd_long` (7+), the same for 3rd, `4th_down`;
- **field zone**: `own_deep` (own 1–20), `own_half` (own 21–50), `opp_half` (opponent's 49–21), `red_zone` (20–1);
- **score**: `trail_9plus`, `trail_1to8`, `tied`, `lead_1to8`, `lead_9plus` (from the offense's side);
- **time**: `two_minute` (the last two minutes of either half).

History metrics (participation) come only for `all`.

**The metrics** (the full list with labels is in `build.json → metrics` and `labels.METRICS`):

| Family | Metrics |
|---|---|
| Play-by-play (2016+) | `dropback_rate`, `proe`, `shotgun_rate`, `no_huddle_rate`, `deep_shot_rate`, `adot`, `pass_left/middle/right`, `pass_short_left` … `pass_deep_right`, `run_left_end` … `run_right_end` (7), `explosive_rate`, `epa_per_play`, `success_rate` |
| FTN (2022+) | `motion_rate`, `play_action_rate`, `screen_rate`, `rpo_rate`, `qb_under_center_share`, `qb_shotgun_share`, `qb_pistol_share`, `blitz_rate`, `rushers_avg`, `box_avg`, `light_box_rate`, `heavy_box_rate` |
| PFR (2018+) | `blitz_rate_pfr` (the cross-check; `all` only) |
| Participation, `history_only` (2016–2025) | `personnel_11` … `personnel_other`, `formation_*` (per era), `def_base/nickel/dime_share`, `cov_*` (2018+, per era), `man_rate` (2018+), `route_*` (per era), `pressure_rate`, `time_to_throw_avg` |

`history_only` rows are research data: the pages label them "History", and **a 2026 forecast never reads them** (PC02's leakage rule; participation is published after the season). They never reach a later season's rows: a `last_season` window carries only the metrics that exist in the season being built, so 2026's has no participation; read 2023–2025 history from those seasons' own folders.

**Percentiles** rank a team among the teams with plays in that window (0 = lowest value, 100 = highest, 50 when only one team): a high percentile is "more of it", not "better". Every team with at least one counted play is ranked, so a narrow cell (red-zone deep shots on 1 attempt) can sit at 0 or 100: read `n` (PC01's pages grey out small samples).

A `--through-week W` build (a simulation of an earlier moment) is written to its own subfolder, `playcalling/<S>/through_weekWW/`, so it never replaces the tables the pages read.

## 5. Running it, and the weekly refresh

```bash
uv run nfl playcalling build --season 2026                       # the current season (W&B on)
uv run nfl playcalling build --season 2026 --history all         # + 2016-2025 (settings: playcalling.history_seasons)
uv run nfl playcalling build --season 2025 --through-week 10     # as if only weeks 1-10 had been played: into 2025/through_week10/
uv run nfl playcalling build --season 2026 --no-wandb            # no W&B run
```

It prints one line per season (plays, as-of weeks, tendency rows, FTN and participation join rates, seconds), a summary table, the games still waiting for FTN, and the W&B link. A season takes under a second to compute; the whole 2016–2026 history took 22 seconds end to end on 2026-10-10, W&B run included.

**The weekly refresh (D122, Rishi chose a runbook line, not a button or a weekly-run step):**
- **Tuesday, after the weekly run:** `uv run nfl playcalling build --season 2026`. The weekly run has just refreshed the curated data; FTN has every game of the week just played except Monday night's (which the as-of tables wouldn't count yet anyway: §4).
- **Wednesday after 13:00 ET (or Thursday):** pick up Monday night's FTN, then rebuild:
  ```bash
  uv run nfl ingest --sources nflverse --datasets ftn_charting
  uv run nfl curate
  uv run nfl playcalling build --season 2026
  ```
  Only FTN is re-pulled (no Odds API credits); `nfl curate` rebuilds the curated tables from the newest snapshots (the others are unchanged, so it's the same as Tuesday's plus FTN). It doesn't touch any model: the production player features lag FTN a week anyway. `build.json → coverage.games_without_ftn` should be empty afterwards. What it changes: the per-game files (`plays_enriched`, `team_game_tendencies`: the week-by-week view, the play browser, PC02's grading). The as-of tables hold Monday night's FTN until the next week by design (`build.json → availability.ftn_held_games` names the games).

The tables aren't a weekly-run step on purpose (D107: the weekly run stays as it is). Each file is replaced in one step, so none is ever half-written, but a build that fails part-way can leave a mix of new and old tables. `build.json` is removed before the first table is written and written after the last, so **a folder without `build.json` means "run the build again"**.

## 6. Looking at the tables

Python (Polars), the current season's identity for one team:

```python
import polars as pl
from nflengine.paths import ensure_data_root

d = ensure_data_root().playcalling / "2026"
t = pl.read_parquet(d / "team_tendencies.parquet")
w = t["as_of_week"].max()  # the newest as-of week (next week to be played)
kc = t.filter(
    (pl.col("team") == "KC")
    & (pl.col("side") == "offense")
    & (pl.col("as_of_week") == w)
    & (pl.col("window") == "season")
    & (pl.col("situation") == "all")
    & ~pl.col("history_only")
)
print(kc.select("metric", "n", "value", "league_value", "pct").sort("pct"))
```

A defense against the league, by down and distance:

```python
t.filter(
    (pl.col("team") == "MIN")
    & (pl.col("side") == "defense")
    & (pl.col("as_of_week") == w)
    & (pl.col("window") == "season")
    & (pl.col("metric") == "blitz_rate")
    & (pl.col("family") == "down_distance")
).select("situation", "n", "value", "league_value")
```

DuckDB works straight on the files; `window` is an SQL keyword, so quote it: `SELECT * FROM 'D:/nfl-ml-data/playcalling/2026/league_tendencies.parquet' WHERE "window" = 'season' AND situation = 'all'`. For one play's labels, filter `plays_enriched.parquet` on `game_id` and `play_id`.

## 7. Real numbers

**The league, season by season** (the whole season incl. playoffs; 2026 through week 4):

| Season | Neutral PROE | Shotgun | No-huddle | Motion | Play-action | Screen | RPO | Deep shot | aDOT | Blitz | Rushers | Explosive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2016 | −0.0 | 64.3% | 11.4% | | | | | 11.8% | 8.39 | | | 9.1% |
| 2019 | −0.6 | 64.5% | 7.8% | | | | | 12.4% | 8.26 | | | 9.5% |
| 2022 | −2.4 | 68.5% | 10.4% | 37.5% | 20.8% | 8.6% | 4.9% | 11.4% | 7.78 | 24.0% | 4.16 | 9.2% |
| 2023 | −1.0 | 72.2% | 9.1% | 45.9% | 20.3% | 9.9% | 5.3% | 11.8% | 7.76 | 27.4% | 4.29 | 9.0% |
| 2024 | −2.1 | 70.8% | 12.6% | 49.7% | 22.8% | 9.0% | 3.6% | 11.5% | 7.71 | 29.1% | 4.31 | 9.2% |
| 2025 | −2.0 | 65.9% | 9.8% | 55.3% | 23.0% | 7.7% | 6.1% | 11.8% | 7.85 | 29.5% | 4.31 | 8.8% |
| 2026 (w1–4) | −1.9 | 60.0% | 6.2% | 58.6% | 24.1% | 7.9% | 4.6% | 12.1% | 8.02 | 31.1% | 4.38 | 8.6% |

The story in it: motion went from 37% of plays (2022) to 59% (2026); shotgun peaked in 2023 and is falling fast (under center is back: FTN has it on 34% of 2025's snaps and 40% so far in 2026, from 27% in 2023); blitzes rose every year.

**2025 leaders and laggards** (regular season):

| Metric | Most | Fewest | League |
|---|---|---|---|
| Play-action (per dropback) | LA 34.6%, CHI 31.4%, IND 28.1% | TB 16.9%, CIN 16.0%, KC 15.3% | 23.2% |
| Motion | MIA 70.7%, SF 68.4%, ATL 66.7% | ARI 44.0%, CAR 42.9%, NYG 38.5% | 55.2% |
| Under center | LA 59.1%, SEA 53.4%, BUF 49.6% | KC 18.8%, CIN 17.3%, WAS 11.5% | 33.7% |
| Deep shots (per attempt) | LA 15.2%, CHI 14.5%, GB 14.3% | NYJ 9.2%, CAR 8.4%, DET 6.7% | 11.5% |
| Blitz (defense) | MIN 51.2%, KC 39.0%, TB 37.5% | LV 22.0%, CIN 21.3%, HOU 21.2% | 29.4% |
| Man coverage (defense, history) | DEN 44.4%, PHI 43.6%, CHI 41.2% | LV 20.2%, GB 19.4%, MIN 18.3% | 30.8% |

**Two team pictures (2025):**
- **Kansas City's offense**: PROE +3.7 (90th percentile, league −2.0); shotgun 81% (under center only 19%, 6th percentile); the league's most RPOs (14.4% vs 6.2%); the fewest play-action (15.3%) and almost no no-huddle (2.5%). Personnel 11 on 58% of snaps, 12 on 31% (71st percentile).
- **Minnesota's defense** (Brian Flores): blitzed on 51% of dropbacks (FTN; 42% by PFR), 4.6 rushers on average (league 4.3), yet mostly **zone** (man 18%, the league's lowest) and **Cover 2** on 37% of dropbacks (league 23%, the highest); base defense 57% of snaps (league 31%). Offenses ran on it: their PROE against Minnesota was −8.9 points over all plays (league −2.2), the league's lowest.
- **Baltimore's runs** go wide: 15% to each end (league 10–11%) and only 17% up the middle (league 27%).

**By situation, the 2025 league** (dropback rate / play-action per dropback / blitz per dropback): 1st down 50% / 39% / 25%; 2nd & long 73% / 17% / 27%; 3rd & short 48% / 17% / 35%; 3rd & long 92% / 1% / 36%; two-minute 76% / 5% / 23%. Play-action lives on early downs; blitzes rise on 3rd down.

**2026 so far (as of week 5):** PROE leaders CIN +7.7, CLE +7.2, PIT +4.8, laggards NYG −10.5, ATL −11.3, TEN −11.5; play-action KC 36.6% (from 15% last season), SEA 34.6%, CHI 32.7%; blitz MIN 72.5% (!), WAS 47.4%, TB 41.9%. These rest on ~140 neutral plays per team, so a percentile can swing a lot from one game.

**Checked against public numbers (2025 regular season):**
- **rbsdm.com** (Ben Baldwin and Sebastian Carl, queried directly): our neutral PROE ranks the 32 teams with Spearman **0.975** against rbsdm's PROE (Pearson 0.988; mean gap 0.7 points, from the wider filter); the **top 5 are the same** (ARI, KC, LA, CIN, NE) and **4 of the bottom 5** (NYJ, BAL, SEA, DET; rbsdm has ATL, we have GB). With rbsdm's own filter on our plays: Spearman 0.988.
- **FTN** (computed from the public FTN file, the same source): play-action 23.1% of dropbacks, blitz 29.4%, matching ours. PFF, with its own charting, has play-action 25.7% and "around 33%" blitz, motion 64% (a broader "shifts and motion"), RPO 8.0%; Sportradar has under center at 34.7% (ours 33.7%). The leader and laggard teams agree across sources even where the levels don't.

## 8. How to change it safely

- **A new metric:** add a `Metric` to `labels.py` (a `den` expression saying which plays count, a `num` saying what each counts as, a `unit`, a `source`, `situational=True` if it should come by situation, `first_season` / `last_season` if its data has an era). If it needs a new play label, add the column in `build.enrich_plays`. Rebuild; the tables pick it up. Add a test with a hand-counted value.
- **A new situation:** add a `Situation` to `labels.SITUATIONS` (a family and an expression over the enriched columns).
- **Changing a definition** (neutral, deep shot, explosive, the box edges): change the constant in `labels.py`, bump `build.SCHEMA_VERSION`, update §3 here, log a decision, and rebuild every season (`--history all`): old and new numbers must never mix.
- **The no-touch rule (D107):** nothing here may change the production models, their features, settings, artifacts, the digest, the weekly graph or the weekly run. `tests/test_production_untouched.py` checks it.

## 9. Limits and what's next

- **Small samples early in a season:** a team has ~140 neutral plays after four games; the `n` and `games` columns are there to show it, and PC02's forecast blends in last season.
- **FTN lags 2 days** and revises; **PFR** about a week; **participation** a whole season. The as-of tables model the first two (§4: FTN 48 hours, PFR a week); FTN's real lag in 2022–2025 isn't recorded, so the 48-hour rule is FTN's own target, checked on 2026's snapshots (35–51 hours).
- **Charting is human:** FTN's and PFR's blitz counts disagree (r 0.69 per game); coverage labels changed in 2023.
- **The league's PROE drifts** below 0 (§3): compare teams with `diff`, not with 0.
- **Next:** PC01 draws these tables (the Play calling pages, the Play calls week tab); PC02 forecasts the next game's rates against the opponent and grades them with `team_game_tendencies`; PC03 draws the plays.
