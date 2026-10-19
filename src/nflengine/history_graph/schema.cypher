// The history graph's constraints and indexes (AE02; history_graph/schema.py lists every
// property). Run after each bulk import (the import itself creates none) and before an append;
// every statement is idempotent. Names start with `history_` so they never look like the
// weekly graph's (a different server: nfl-neo4j-history, Bolt 7688).
CREATE CONSTRAINT history_season IF NOT EXISTS FOR (n:Season) REQUIRE n.season IS UNIQUE;
CREATE CONSTRAINT history_team IF NOT EXISTS FOR (n:Team) REQUIRE n.team_id IS UNIQUE;
CREATE CONSTRAINT history_player IF NOT EXISTS FOR (n:Player) REQUIRE n.player_id IS UNIQUE;
CREATE CONSTRAINT history_coach IF NOT EXISTS FOR (n:Coach) REQUIRE n.coach_id IS UNIQUE;
CREATE CONSTRAINT history_game IF NOT EXISTS FOR (n:Game) REQUIRE n.game_id IS UNIQUE;
CREATE CONSTRAINT history_drive IF NOT EXISTS FOR (n:Drive) REQUIRE n.drive_key IS UNIQUE;
CREATE CONSTRAINT history_play IF NOT EXISTS FOR (n:Play) REQUIRE n.play_key IS UNIQUE;
CREATE INDEX history_play_season_week IF NOT EXISTS FOR (n:Play) ON (n.season, n.week);
CREATE INDEX history_play_season IF NOT EXISTS FOR (n:Play) ON (n.season);
CREATE INDEX history_drive_season IF NOT EXISTS FOR (n:Drive) ON (n.season);
CREATE INDEX history_play_offense IF NOT EXISTS FOR (n:Play) ON (n.offense);
CREATE INDEX history_play_defense IF NOT EXISTS FOR (n:Play) ON (n.defense);
CREATE INDEX history_player_name IF NOT EXISTS FOR (n:Player) ON (n.name);
CREATE INDEX history_game_season_week IF NOT EXISTS FOR (n:Game) ON (n.season, n.week);
CREATE INDEX history_drive_game IF NOT EXISTS FOR (n:Drive) ON (n.game_id);
