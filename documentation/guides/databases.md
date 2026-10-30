# Our databases: why DuckDB, and why DuckDB + Neo4j

A short guide to the two kinds of database this project uses and why. The deeper guides are linked at the end.

## The short version

| | DuckDB | Neo4j: the weekly graph | Neo4j: the history graph |
|---|---|---|---|
| Kind | Relational (tables + SQL), runs **inside** Python | Graph (nodes + relationships + Cypher), a **server** in Docker | The same, a second server |
| Holds | Every curated table and feature table, as views over Parquet files | This week's teams, players, coaches, games and projections | Every play since 2018 and who was on the field |
| Lives at | `D:\nfl-ml-data\curated\nfl.duckdb` (+ the Parquet files beside it) | `D:\nfl-ml-data\neo4j\` (container `nfl-neo4j`) | `D:\nfl-ml-data\neo4j-history\` (container `nfl-neo4j-history`) |
| Look at it | Python (below) | http://localhost:7474 (Bolt 7687) | http://localhost:7475 (Bolt 7688) |
| Written by | `curate` (the tables' views), `ratings` (the feature views) | the weekly run's `graph` and `player` steps | `nfl history-graph append` / `build` |
| Good at | Totals, averages, filters, leaderboards, joins of a few tables | "Who's connected to whom" questions, a few hops deep | The same, over nine seasons of plays |

## 1. Why DuckDB

The decision is D22 ("Polars + DuckDB ... modern, fast, fits the data size"), and doc 02's tech stack sums it up as "zero setup, fast, works well with Polars".

- **Zero setup.** DuckDB is a library inside the Python process, not a server. Nothing to install, start or keep running, and nothing in Docker. (Neo4j, by contrast, needs Docker and about 50 seconds to start.)
- **It reads Parquet where it sits.** The real data is plain Parquet files on D:. `nfl.duckdb` holds only **views**, saved queries such as `SELECT * FROM read_parquet('D:/nfl-ml-data/curated/games.parquet')`, which is why the file is only ~270 KB. There's no load or import step: `curate` rewrites the Parquet files and recreates the views, and that's it.
- **Fast at analytics.** It stores and reads data by column, so a query that scans 18,738 plays of 2026 (or ~49,000 a season before) and sums two columns reads only those two columns.
- **Fits Polars.** Both are built on Apache Arrow, so a query's result becomes a Polars frame almost without copying. Most of the pipeline is Polars code reading the same Parquet files directly; DuckDB is the SQL door to them.
- **Fits the size.** A few GB, one user, one machine. A database server (Postgres, say) would add work and gain nothing here.

## 2. Why DuckDB and not SQLite (both need no setup)

SQLite is embedded too: no server, just a file. So setup isn't the difference. These are:

| | DuckDB | SQLite |
|---|---|---|
| Built for | Analytics: scan many rows, group, sum | Apps: many small reads and writes of single records |
| Stores data | By column | By row |
| Parquet | Queries the files where they sit | Can't: every table would be copied into the `.sqlite` file after every curate (an import step and a second copy that can drift) |
| A "sum one column over 400,000 plays" query | Reads that one column | Reads every column of every row |
| Polars | Arrow in, Arrow out | Rows converted one by one |

So DuckDB has the same zero setup and is the better fit for what we do with it.

## 3. Why DuckDB **and** Neo4j

They answer different kinds of questions. DuckDB is the **relational** database (tables and SQL); Neo4j is a **graph** database (things and the links between them).

- **Table questions go to DuckDB:** "most 100-yard receiving games since 2020", "Kansas City's points per game this season", "every quarterback's completion rate on 3rd down". These are filters, groups and sums over rows.
- **Relationship questions go to Neo4j:** "who has played with both of these players", "how are Tom Brady and Patrick Mahomes connected through teammates and coaches", "which coaches came from the same staff". These follow links from one thing to the next, several steps deep.

SQL *can* follow links: each hop is a join, and an open-ended chain needs a recursive query. One or two hops are fine. "The shortest chain of teammates and coaches between two players" gets awkward to write and slow to run as the chain grows. Neo4j stores each link directly, so following a link is a step, not a search; that's what it's built for. It also runs graph algorithms (PageRank, similarity, communities) through its Graph Data Science plugin, which the weekly graph and the Graph lab use. Learning Neo4j was also one of the project's goals.

The data flows one way: **Parquet files → DuckDB views → the Neo4j graphs** (both graphs are built from the curated Parquet files). So the graphs can always be wiped and rebuilt, and the Parquet files are what everything agrees on.

## 4. Why two Neo4j databases

The free (Community) edition of Neo4j allows one database per server, so the second graph needs a second server (container).

- **The weekly graph** (`nfl-neo4j`) is rebuilt from scratch every week as of that week (1.5–2.5 minutes); the digest's graph findings and the player projections live there.
- **The history graph** (`nfl-neo4j-history`) holds every play since 2018: 378,696 plays and about 10 million relationships after 2026 week 7, a 1.3 GB store. A full build takes 6–10 minutes, so it's built once a season and **appended** each week instead. It only starts when asked (`uv run nfl history-graph start`).

Keeping them apart means the weekly rebuild never touches the big graph, and the big graph never slows the weekly run.

## 5. Who writes what, and when

| Step | DuckDB / Parquet | Weekly graph | History graph |
|---|---|---|---|
| Weekly run: `curate` | Rewrites every curated Parquet file, then recreates `nfl.duckdb`'s views | | |
| Weekly run: `ratings` | Writes the feature tables (`team_ratings`, `team_elo`, `team_trends`, ...) and adds their views | | |
| Weekly run: `graph` | | Wiped and rebuilt for the week | |
| Weekly run: `player` | | This week's projections written in | |
| `nfl history-graph append` (Tuesdays) | | | The season's new games and plays synced in |
| Ask the Engine, the control room | Read only | Read only | Read only |

## 6. Looking at the data

**DuckDB**, from the repo folder (read-only):

```powershell
uv run python -c "import duckdb; c = duckdb.connect('D:/nfl-ml-data/curated/nfl.duckdb', read_only=True); print(c.sql('SELECT season, count(*) AS plays FROM plays WHERE season >= 2024 GROUP BY 1 ORDER BY 1'))"
```

On 2026-10-30 that printed 49,492 plays for 2024, 48,771 for 2025 and 18,738 for 2026 so far. `SELECT view_name FROM duckdb_views() WHERE NOT internal` lists the 38 views. The `curated-data` skill is the data dictionary.

**Close DuckDB connections when you're done, and don't hold one open during a weekly run.** `curate` and `ratings` replace `nfl.duckdb` with a new file, and Windows refuses to replace a file another program has open (D100).

**Neo4j:** the Browser at http://localhost:7474 (weekly) or http://localhost:7475 (history), user `neo4j` and the password from the env file. The [knowledge graph](knowledge-graph.md) and [history graph](history-graph.md) guides have walkthrough queries with their real results.

**Without writing any query:** the control room's Explore → **Ask the Engine** page picks the source for you (tables, the weekly graph or the history graph) and shows the query it ran.

## 7. Good to know

- **Nothing here is the source of truth on its own.** The raw snapshots under `D:\nfl-ml-data\raw\` are. Every curated table, view and graph is rebuilt from them, so none of them is backed up separately and none is edited by hand.
- **No database is written from the control room** except by the commands its buttons launch (Run, Resume, the Saturday update, Update play calling).
- **More detail:** the [knowledge graph guide](knowledge-graph.md) (the weekly graph), the [history graph guide](history-graph.md) (the second Neo4j), [Ask the Engine](ask-the-engine.md) (how questions reach DuckDB or Neo4j safely), the `curated-data` skill (every table and column), doc 02's tech stack.
