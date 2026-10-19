"""The history graph (AE02; documentation/ask-the-engine/AE02-history-graph.md, D112, D145).

A second, permanent Neo4j database in its own container (`nfl-neo4j-history`, compose profile
`history`): every play since 2018 with the players on the field (participation, research
only), the drives, games, teams, rosters and coaches. Built from scratch with a bulk import
(`build.py`), appended to weekly for the current season (`append.py`), read by Ask the Engine
through its guard (the `history` source). The weekly digest graph (`nfl-neo4j`) is never
touched: no code here names its service, its folders or its Bolt port.

Modules: `schema.py` (labels, keys, properties) and `schema.cypher` (constraints, indexes),
`export.py` (curated + research data -> frames -> CSVs), `docker.py` (the container and the
import, by name only), `client.py` (the driver on Bolt 7688), `build.py`, `append.py`,
`status.py`.
"""
