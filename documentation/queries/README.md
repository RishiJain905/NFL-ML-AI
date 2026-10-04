# Weekly graph queries

One file per live week: `<season>-week<NN>-queries.md`. Each holds the Neo4j Browser queries worth running after that week's live `nfl weekly run`: the ones that trace the week's digest back to the graph (so any line can be cross-checked), the strongest findings the digest didn't use, and a few that are just interesting that week.

**How these files get made:** after each live weekly run, Rishi asks for the week's file. The agent writes the queries, runs every one of them against that week's graph, and notes what each should show (the real values it returned), so a query that comes back different later means the graph changed.

## How to run one

1. Make sure Neo4j is up: `uv run nfl doctor` shows `neo4j OK` (start it with `docker compose up -d`).
2. Open **http://localhost:7474**, log in as `neo4j` with your `NEO4J_PASSWORD`.
3. Paste a query into the box at the top and press **Ctrl+Enter** (or the ▶ button).
4. Switch the result frame between **Graph** (the picture) and **Table** (rows) with the buttons on its left edge. Each query below says which view suits it.

Tip: players show as ids until you give them a caption: click the `Player` chip in the frame's legend and pick `name`.

## Which week is in Neo4j right now?

**Only one week at a time.** Every weekly run (and every `nfl graph build`) wipes the graph and rebuilds it for that week, so the database holds the most recently built week. To check:

```cypher
MATCH (g:Game) WHERE g.home_qb_expected IS NOT NULL
RETURN DISTINCT g.season AS season, g.week AS week
```

To look at an older week's file again, rebuild that week first (about 1.5 minutes), then rebuild the current week when you're done:

```bash
uv run nfl graph build --season 2026 --week 4 --no-wandb
```

(A live rebuild overwrites that week's `graph_results.json`; the digest's own `payload.json` keeps what the digest used.)

The [knowledge-graph guide](../guides/knowledge-graph.md) explains the graph itself (what each node and relationship means, how the query library works).

## Files

| Week | File |
|---|---|
| 2026 week 4 (first live digest with graph sections) | [2026-week04-queries.md](2026-week04-queries.md) |
