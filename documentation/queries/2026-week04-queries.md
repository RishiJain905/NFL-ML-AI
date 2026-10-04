# 2026 week 4: graph queries

**The digest these go with:** `D:/nfl-ml-data/reports/2026/week04-digest.md` (live, written 2026-10-04 02:50 UTC; W&B digest run `o0skjazq`, graph build `8txvvr92`). The graph currently in Neo4j was rebuilt for week 4 afterwards (`binb1iho`, same counts and picks).

**What "as of week 4" means:** the graph holds results only from games of weeks 1–3. Week-4 games are there with their predictions and expected QBs but no scores. Thursday's Steelers at Browns had already kicked off when the digest ran, so the digest left it out of its predictions table and graph sections even though it's in the graph.

How to run these: [README](README.md). Every query below was run against the week-4 graph; "You should see" gives what it returned.

---

## 1. Orientation

### 1.1 The map of the whole graph · Graph view
```cypher
CALL db.schema.visualization()
```
**You should see** one circle per node type (Player, Team, Game, TeamWeek, GamePrediction, Coach, Official, Venue, PublishedInsight) and one arrow per relationship type. That's the shape of all 588,282 relationships in one picture.

### 1.2 How big it is · Table view
```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC
```
```cypher
MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS relationships ORDER BY relationships DESC
```
**You should see** 7,079 Player, 5,664 TeamWeek, 2,291 Game, 294 Official, 86 Coach, 44 Venue, 32 GamePrediction, 32 Team, 3 PublishedInsight; and 253,704 DEPTH_CHART, 209,733 APPEARED_IN, 46,365 ON_INJURY_REPORT, 27,292 PLAYED_FOR, 16,154 OFFICIATED, 7,803 THREW_TO, 5,664 HAS_WEEK, 5,632 NEXT, 4,582 PLAYED_IN, 4,582 COACHED_IN, 3,601 DRAFTED_BY, 2,291 AT, 545 TRADED_TO, 302 HEAD_COACH_OF, 32 HAS_PREDICTION.

---

## 2. The digest's graph sections, traced

### 2.1 Matchup / risk: the Buccaneers' QB change · Graph view

Digest: *"Jalon Daniels is expected to start at QB for the Buccaneers this week; Baker Mayfield is listed out for this week (thumb)."*

```cypher
MATCH (g:Game {game_id: '2026_04_GB_TB'})<-[pi:PLAYED_IN]-(t:Team {team_id: 'TB'})
MATCH (p:Player)-[pf:PLAYED_FOR {season: 2026}]->(t)
WHERE p.name IN ['Baker Mayfield', 'Jalon Daniels']
OPTIONAL MATCH (p)-[i:ON_INJURY_REPORT]->(g)
OPTIONAL MATCH (p)-[th:THREW_TO {season: 2026}]->(r:Player)
RETURN g, pi, t, p, pf, i, th, r
```
**You should see** the Buccaneers and this week's game in the middle, Mayfield with an `ON_INJURY_REPORT` arrow into the game (click it: status Out, Thumb, did not practice) and `THREW_TO` arrows fanning out to his receivers, and Daniels with just two thin `THREW_TO` arrows (one target each to Ted Hurst III and Kenny Gainwell). That picture is the whole story: a starter out, a backup with almost no history with these receivers.

### 2.2 The same item, as numbers · Table view

Digest: *"Baker Mayfield started 3 of the Buccaneers' 3 games ... Emeka Egbuka has 20 targets from Baker Mayfield this season; he has none from Jalon Daniels."*

```cypher
MATCH (p:Player)-[a:APPEARED_IN]->(g:Game {season: 2026})
WHERE a.team_id = 'TB' AND p.position_group = 'QB'
RETURN g.week AS week, p.name AS qb, a.dropbacks AS dropbacks, a.qb_started AS started
ORDER BY week, dropbacks DESC
```
**You should see** Mayfield the starter in weeks 1–3 (36, 40, 42 dropbacks) and Daniels with 4 relief dropbacks in week 3. "Started" means the most dropbacks for his team in that game.

```cypher
MATCH (w:Player)-[:PLAYED_FOR {season: 2026}]->(:Team {team_id: 'TB'})
WHERE w.position_group IN ['WR', 'TE', 'RB']
OPTIONAL MATCH (:Player {name: 'Baker Mayfield'})-[m:THREW_TO {season: 2026}]->(w)
OPTIONAL MATCH (:Player {name: 'Jalon Daniels'})-[d:THREW_TO]->(w)
WITH w, coalesce(m.targets, 0) AS from_mayfield_2026, sum(coalesce(d.targets, 0)) AS from_daniels_career
WHERE from_mayfield_2026 > 0 OR from_daniels_career > 0
RETURN w.name AS receiver, w.position AS pos, from_mayfield_2026, from_daniels_career
ORDER BY from_mayfield_2026 DESC
```
**You should see** Egbuka 20 / 0, Otton 17 / 0, Bucky Irving 15 / 0, Hurst 12 / 1, Godwin 11 / 0, Gainwell 9 / 1, then smaller numbers. Daniels' column adds up to 2, the "2 career targets to the Buccaneers' current receivers" in the digest's small-sample note.

### 2.3 The Buccaneers' injury report for this game · Table view
```cypher
MATCH (p:Player)-[i:ON_INJURY_REPORT]->(:Game {game_id: '2026_04_GB_TB'})
WHERE i.team_id = 'TB' AND i.status IS NOT NULL
RETURN p.name AS player, p.position AS pos, i.status AS status, i.body_part AS injury,
       i.practice_status AS practice
ORDER BY status, player
```
**You should see** four Out (Mayfield thumb, Benjamin Morrison quadricep, Ko Kieft elbow, Rueben Bain Jr. groin) and three Questionable. Morrison is also an injury-ripple candidate the digest didn't use (§3).

### 2.4 Non-obvious: Kaden Elliss vs his former team · Graph view

Digest: *"Kaden Elliss (Saints LB) faces the Falcons, his former team ... played 34 games for the Falcons in 2024 and 2025 ... 100% of the Saints' defensive snaps in the 3 games he played."*

```cypher
MATCH (p:Player {name: 'Kaden Elliss'})-[r:PLAYED_FOR]->(t:Team)
WHERE r.season >= 2024
MATCH (g:Game {game_id: '2026_04_ATL_NO'})<-[pi:PLAYED_IN]-(t)
RETURN p, r, t, pi, g
```
**You should see** a small triangle: Elliss with `PLAYED_FOR` arrows to the Falcons (2024, 2025) and the Saints (2026), and both teams pointing into Monday's game.

### 2.5 Elliss's whole career, as numbers · Table view
```cypher
MATCH (p:Player {name: 'Kaden Elliss'})-[r:PLAYED_FOR]->(t:Team)
RETURN r.season AS season, t.team_id AS team, r.games AS games
ORDER BY season
```
```cypher
MATCH (:Player {name: 'Kaden Elliss'})-[a:APPEARED_IN]->(g:Game {season: 2026})
RETURN g.week AS week, a.team_id AS team, a.def_snaps AS defensive_snaps, a.off_snaps AS offensive_snaps,
       round(a.snap_pct, 2) AS share, a.tackles AS tackles
ORDER BY week
```
**You should see** a fuller story than the digest's 2-season window: Saints 2019–2022, **Falcons 2023, 2024, 2025 (17 games each)**, back with the Saints in 2026. So he's facing the team he just left after 3 seasons, and he was originally a Saint. This season: 77, 58 and 70 defensive snaps, every one of the Saints' (share 1.0), 9 / 6 / 8 tackles.

### 2.6 Non-obvious: Panthers up, Lions down · Graph view

Digest: *"The Panthers' net rating is up 0.04 EPA per play over the last 3 weeks; the Lions' net rating is down 0.05."*

```cypher
MATCH (t:Team)-[h:HAS_WEEK]->(tw:TeamWeek {season: 2026})
WHERE t.team_id IN ['CAR', 'DET']
OPTIONAL MATCH (tw)-[n:NEXT]->(nx:TeamWeek {season: 2026})
RETURN t, h, tw, n, nx
```
**You should see** each team with four rating nodes (as of weeks 1–4) linked into a chain by `NEXT` arrows. Click a TeamWeek node to see its `net`, `trend_delta` and `trend_dir`.

### 2.7 The same walk, as the query does it · Table view
```cypher
MATCH (t:Team)-[:HAS_WEEK]->(now:TeamWeek {season: 2026, week: 4}),
      (before:TeamWeek)-[:NEXT*3]->(now)
WHERE t.team_id IN ['CAR', 'DET']
RETURN t.team_id AS team, round(before.net, 3) AS net_3_weeks_ago, round(now.net, 3) AS net_now,
       round(now.net - before.net, 3) AS change, now.trend_dir AS direction
```
**You should see** CAR −0.071 → −0.026 (change +0.045, up) and DET 0.064 → 0.015 (change −0.049, down). `NEXT*3` means "walk three NEXT arrows back", which is exactly what query Q8 does.

### 2.8 What the digest published · Graph view (or Table)
```cypher
MATCH (pi:PublishedInsight) RETURN pi
```
**You should see** 3 nodes: `qb_change:TB:...`, `revenge:...:ATL`, `trend_mismatch:2026_04_DET_CAR`. These keep the same stories out of the next three weeks' digests.

---

## 3. What else the graph found (not used this week)

The query library returned 43 rows this week, and each became a candidate. The digest used the top QB change and the top two non-obvious items. The strongest ones left out, by strength:

| Strength | Item | Why it wasn't picked |
|---|---|---|
| 0.80 | Talanoa Hufanga (Broncos S) faces the 49ers, his former team | Revenge slot already taken by Elliss (0.90); the second non-obvious slot went to a different kind of item |
| 0.78 | Jackson Powers-Johnson (Raiders C) out: the Raiders' offense with and without him | Matchup / risk takes one item; the QB change ranked higher |
| 0.77 | Michael Penix Jr. expected to start for the Falcons | Lower than the Buccaneers' change (Penix already started last week) |
| 0.77 | Marcus Mariota for the Commanders; Jayden Daniels listed out (elbow) | Same |
| 0.77 | Azeez Al-Shaair (Texans LB) out | Same |
| 0.75 | Case Keenum for the Bears; Caleb Williams listed out (hamstring) | Same |
| 0.70 | Nick Bosa (49ers DE) out | Same |

### 3.1 Everyone ruled out this week · Table view
```cypher
MATCH (p:Player)-[i:ON_INJURY_REPORT]->(g:Game {season: 2026, week: 4})
WHERE i.status IN ['Out', 'Doubtful']
RETURN i.team_id AS team, p.name AS player, p.position AS pos, i.status AS status, i.body_part AS injury
ORDER BY team, player
```
**You should see** 72 rows. Includes Caleb Williams (CHI QB, hamstring), Nick Bosa (SF, knee), Aaron Banks (GB guard, knee: the Packers' trend evidence) and Jackson Powers-Johnson (LV center, groin).

### 3.2 Nick Bosa: the 49ers' defense with and without him · Table view
```cypher
MATCH (s:Player {name: 'Nick Bosa'})-[pf:PLAYED_FOR]->(t:Team {team_id: 'SF'})-[pi:PLAYED_IN]->(g:Game {season: pf.season})
WHERE g.season >= 2024 AND g.completed AND g.game_type = 'REG' AND g.week IN pf.roster_weeks
OPTIONAL MATCH (s)-[a:APPEARED_IN]->(g)
WITH g, pi,
     CASE WHEN a IS NULL OR a.snaps = 0 THEN 'without' WHEN a.snap_pct >= 0.5 THEN 'with' ELSE 'part-time' END AS bosa
RETURN bosa, count(*) AS games, round(avg(pi.epa_allowed), 3) AS sf_defense_epa_allowed,
       round(avg(pi.points_allowed), 1) AS points_allowed
ORDER BY bosa
```
**You should see** with him (16 games): −0.032 EPA allowed per play, 21.1 points allowed; without him (18 games): +0.112 and 24.3; plus 3 part-time games. This is the injury-ripple query's comparison done by hand (higher EPA allowed = worse defense).

### 3.3 The Bears' QB change · Table view
```cypher
MATCH (g:Game {game_id: '2026_04_NYJ_CHI'})<-[:PLAYED_IN]-(t:Team {team_id: 'CHI'})
MATCH (p:Player)-[:PLAYED_FOR {season: 2026}]->(t)
WHERE p.name IN ['Caleb Williams', 'Case Keenum']
OPTIONAL MATCH (p)-[i:ON_INJURY_REPORT]->(g)
OPTIONAL MATCH (p)-[a:APPEARED_IN {qb_started: true}]->(sg:Game {season: 2026})
RETURN p.name AS qb, i.status AS status, i.body_part AS injury, sg.week AS started_week
ORDER BY qb, started_week
```
**You should see** Caleb Williams Out (hamstring) after starting weeks 1–2, and Case Keenum, who started week 3.

### 3.4 Every "faces a former team" connection this week · Table view (or Graph with `RETURN p, t, opp`)
```cypher
MATCH (g:Game {season: 2026, week: 4})<-[:PLAYED_IN]-(t:Team),
      (g)<-[:PLAYED_IN]-(opp:Team),
      (p:Player)-[:PLAYED_FOR {season: 2026}]->(t),
      (p)-[old:PLAYED_FOR]->(opp)
WHERE opp <> t AND old.season >= 2024 AND old.games >= 4
RETURN p.name AS player, p.position AS pos, t.team_id AS now_with, opp.team_id AS faces_former_team,
       collect(old.season) AS seasons_there, sum(old.games) AS games_there
ORDER BY games_there DESC
```
**You should see** 26 players. The top of the list is Reggie Gilliam (NE vs BUF, 37 games), Kaden Elliss (NO vs ATL, 34), Nick Cross (WAS vs IND, 34) and Casey Kreiter (ARI vs NYG, 34). Gilliam, Kreiter (a long snapper) and others don't make the digest because query Q1 also requires half the snaps on offense or defense this season; Nick Cross was ruled out.

### 3.5 Common opponents this week · Table view (and a picture)
```cypher
MATCH (a:Team)-[ra:PLAYED_IN]->(g:Game {season: 2026, week: 4})<-[:PLAYED_IN]-(b:Team)
WHERE ra.home
MATCH (a)-[x:PLAYED_IN]->(ga:Game {season: 2026})<-[:PLAYED_IN]-(c:Team),
      (b)-[y:PLAYED_IN]->(gb:Game {season: 2026})<-[:PLAYED_IN]-(c)
WHERE ga.week < 4 AND gb.week < 4 AND c <> a AND c <> b
RETURN a.team_id AS home, b.team_id AS away, c.team_id AS common_opponent,
       ga.week AS home_week, x.margin AS home_margin, gb.week AS away_week, y.margin AS away_margin
ORDER BY home
```
**You should see** four pairs: LV and KC both beat MIA by 14; SEA beat ARI by 24 while LAC lost to ARI by 12; SF beat LA by 20 while DEN beat LA by 4; TB lost to MIN by 7 while GB lost to MIN by 17 (the game model still favors the Packers in that one).

Drawn as a picture (Graph view):
```cypher
MATCH (tb:Team {team_id: 'TB'})-[r1:PLAYED_IN]->(g1:Game {season: 2026})<-[r2:PLAYED_IN]-(c:Team {team_id: 'MIN'}),
      (gb:Team {team_id: 'GB'})-[r3:PLAYED_IN]->(g2:Game {season: 2026})<-[r4:PLAYED_IN]-(c)
WHERE g1.week < 4 AND g2.week < 4
RETURN tb, r1, g1, r2, c, gb, r3, g2, r4
```

---

## 4. Cross-checking the rest of the digest

### 4.1 Game outlook: the predictions behind the table · Table view
```cypher
MATCH (g:Game {season: 2026, week: 4})
OPTIONAL MATCH (g)-[:HAS_PREDICTION]->(gp:GamePrediction {is_primary: true})
RETURN g.away_team AS away, g.home_team AS home, g.kickoff AS kickoff_utc,
       round(1 - gp.home_win_prob, 2) AS away_win_prob, round(gp.home_win_prob, 2) AS home_win_prob,
       round(gp.pred_away_points, 1) AS away_pts, round(gp.pred_home_points, 1) AS home_pts,
       g.away_qb_source AS away_qb_from, g.home_qb_source AS home_qb_from
ORDER BY kickoff_utc, home
```
**You should see** the digest's game table in numbers: IND at WAS 0.64 / 0.36 (25.4–20.7), TEN at BAL 0.17 / 0.83, NE at BUF 0.27 / 0.73, GB at TB 0.62 / 0.38 (20.9–17.1), MIA at MIN 0.18 / 0.82, KC at LV 0.64 / 0.36, DET at CAR 0.64 / 0.36, ATL at NO 0.42 / 0.58, and so on. Every expected QB comes from `schedule` (confirmed) except Thursday's PIT at CLE (`last_game`), which kicked off before the digest ran.

### 4.2 Team trend shifts · Table view

Digest: Raiders up 0.12, Jets up 0.12, Packers down 0.11 (the payload also had the Eagles, down 0.10, which the prose left out for length).

```cypher
MATCH (t:Team)-[:HAS_WEEK]->(tw:TeamWeek {season: 2026})
WHERE t.team_id IN ['LV', 'NYJ', 'GB', 'PHI']
RETURN t.team_id AS team, tw.week AS as_of_week, round(tw.net, 3) AS net_rating,
       round(tw.trend_delta, 3) AS change_over_3_weeks, tw.trend_dir AS direction
ORDER BY team, as_of_week
```
**You should see** at week 4: LV net −0.026 (change +0.120, up), NYJ −0.102 (+0.118, up), GB −0.043 (−0.106, down), PHI −0.025 (−0.100, down).

### 4.3 "Kirk Cousins started their latest game (last season's main starter was Geno Smith)" · Table view
```cypher
MATCH (p:Player)-[a:APPEARED_IN {qb_started: true}]->(g:Game)
WHERE a.team_id IN ['LV', 'NYJ'] AND g.season >= 2025
RETURN a.team_id AS team, g.season AS season, p.name AS starter, count(*) AS starts
ORDER BY team, season, starts DESC
```
**You should see** LV 2025: Geno Smith 15 starts (plus Kenny Pickett and Aidan O'Connell 1 each), LV 2026: Kirk Cousins 3; NYJ 2025: Justin Fields 7, Tyrod Taylor 5, Brady Cook 5, NYJ 2026: Geno Smith 3. Geno Smith moved from the Raiders to the Jets, and both teams are trending up.

### 4.4 Brock Bowers: "recently without" him, yet a 43% target share · Table view

The trend section says the Raiders were recently without Brock Bowers, and Players to watch gives him a 43% target share. Both are true:

```cypher
MATCH (p:Player {name: 'Brock Bowers'})
OPTIONAL MATCH (p)-[i:ON_INJURY_REPORT]->(g:Game {season: 2026})
OPTIONAL MATCH (p)-[a:APPEARED_IN]->(g)
RETURN g.week AS week, i.status AS report_status, i.body_part AS injury, a.targets AS targets,
       round(a.target_share, 2) AS target_share
ORDER BY week
```
**You should see** week 1 Out (knee) and week 2 Doubtful with no appearance, then week 3 Questionable but played: 13 targets, 43% of the Raiders' targets. Week 4 has no status. So "recently without him" is weeks 1–2, and the 43% is his one game back. That's why the watch list marks it low confidence.

### 4.5 Players to watch: the usage behind the picks · Table view
```cypher
MATCH (p:Player)-[a:APPEARED_IN]->(g:Game {season: 2026})
WHERE p.name IN ['Bhayshul Tuten', 'Chuba Hubbard', 'Luther Burden III', 'Aaron Jones', 'Brock Bowers']
RETURN p.name AS player, g.week AS week, a.team_id AS team, a.carries AS carries, a.targets AS targets,
       round(a.target_share, 2) AS target_share, round(a.snap_pct, 2) AS snap_share,
       coalesce(a.rush_yds, 0) + coalesce(a.rec_yds, 0) AS scrimmage_yds
ORDER BY player, week
```
**You should see** the week-by-week usage the heuristic picks rest on: Chuba Hubbard's carries rising 10 → 12 → 19, Luther Burden III's targets 5 → 7 → 11 (target share 0.19 → 0.32), Aaron Jones at 23 and 17 carries in weeks 2–3, Bhayshul Tuten steady at 13–15 carries, Bowers' single game.

### 4.6 Last week under the hood: the QB lines · Table view

Digest: Mayfield "42 dropbacks", Geno Smith "37 attempts", Mariota "35 dropbacks".

```cypher
MATCH (p:Player)-[a:APPEARED_IN]->(g:Game {season: 2026, week: 3})
WHERE p.name IN ['Baker Mayfield', 'Geno Smith', 'Marcus Mariota']
RETURN p.name AS qb, a.team_id AS team, a.dropbacks AS dropbacks, a.pass_att AS attempts,
       a.completions AS completions, a.pass_yds AS yards, round(a.ngs_time_to_throw, 2) AS time_to_throw
ORDER BY qb
```
**You should see** Mayfield 42 dropbacks (34 attempts, 217 yards), Geno Smith 37 attempts (31 completions, 321 yards), Mariota 35 dropbacks (31 attempts). The blitz rate, pressure rate and completion % over expectation in the digest come from PFR, FTN and Next Gen Stats tables in DuckDB, not the graph. The graph holds the game lines and time to throw.

---

## 5. Just interesting

### 5.1 Everything around the Packers–Buccaneers game · Graph view
```cypher
MATCH (g:Game {game_id: '2026_04_GB_TB'})-[r]-(x) RETURN g, r, x
```
**You should see** the game in the middle with 29 neighbours: both teams, both head coaches, the venue, the two model predictions (model-only and market) and the 22 players on either team's injury report for it.

### 5.2 Patrick Mahomes' passing network this season · Graph view
```cypher
MATCH (q:Player {name: 'Patrick Mahomes'})-[t:THREW_TO {season: 2026}]->(r:Player)
RETURN q, t, r
```
**You should see** Mahomes with 11 receivers. Click an arrow for its targets, catches and yards. Switch to Table and add `ORDER BY t.targets DESC` to rank them.
