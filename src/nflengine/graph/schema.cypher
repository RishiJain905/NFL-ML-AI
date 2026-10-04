// Knowledge-graph constraints and indexes (documentation/05 -> Constraints and indexes).
// Applied idempotently before every load (`graph/load.py::apply_schema`).
CREATE CONSTRAINT team_id IF NOT EXISTS FOR (t:Team) REQUIRE t.team_id IS UNIQUE;
CREATE CONSTRAINT player_id IF NOT EXISTS FOR (p:Player) REQUIRE p.player_id IS UNIQUE;
CREATE CONSTRAINT game_id IF NOT EXISTS FOR (g:Game) REQUIRE g.game_id IS UNIQUE;
CREATE CONSTRAINT coach_id IF NOT EXISTS FOR (c:Coach) REQUIRE c.coach_id IS UNIQUE;
CREATE CONSTRAINT venue_id IF NOT EXISTS FOR (v:Venue) REQUIRE v.stadium_id IS UNIQUE;
CREATE CONSTRAINT official_id IF NOT EXISTS FOR (o:Official) REQUIRE o.official_id IS UNIQUE;
// Model outputs and the novelty log use one string key each ("KC:2026:4", ...).
CREATE CONSTRAINT teamweek_key IF NOT EXISTS FOR (tw:TeamWeek) REQUIRE tw.key IS UNIQUE;
CREATE CONSTRAINT prediction_key IF NOT EXISTS FOR (gp:GamePrediction) REQUIRE gp.key IS UNIQUE;
CREATE CONSTRAINT projection_key IF NOT EXISTS FOR (pp:PlayerProjection) REQUIRE pp.key IS UNIQUE;
CREATE CONSTRAINT published_key IF NOT EXISTS FOR (pi:PublishedInsight) REQUIRE pi.key IS UNIQUE;
CREATE INDEX game_season_week IF NOT EXISTS FOR (g:Game) ON (g.season, g.week);
CREATE INDEX teamweek_team_season_week IF NOT EXISTS FOR (tw:TeamWeek) ON (tw.team_id, tw.season, tw.week);
CREATE INDEX player_pos IF NOT EXISTS FOR (p:Player) ON (p.position_group);
CREATE INDEX player_name IF NOT EXISTS FOR (p:Player) ON (p.name);
CREATE INDEX published_insight_id IF NOT EXISTS FOR (pi:PublishedInsight) ON (pi.insight_id);
CREATE INDEX played_for_season IF NOT EXISTS FOR ()-[r:PLAYED_FOR]-() ON (r.season);
CREATE INDEX threw_to_season IF NOT EXISTS FOR ()-[r:THREW_TO]-() ON (r.season);
CREATE INDEX depth_chart_season_week IF NOT EXISTS FOR ()-[r:DEPTH_CHART]-() ON (r.season, r.week);
