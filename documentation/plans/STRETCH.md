# Stretch Goals (Unscheduled)

Ideas that aren't on the critical path. Pick them up only after the relevant phase is done, and only with Rishi's go-ahead. Promote one to a proper phase file when it's started.

| Idea | After | Notes |
|---|---|---|
| **Q&A agent over the graph:** ask questions in plain English, get Cypher run in read-only mode | P05 | Text-to-Cypher in a read-only session with write clauses rejected ([05](../05-knowledge-graph.md#stretch-qa-agent-over-the-graph)). Covers the agentic / RAG learning goals |
| Hand-curated coordinator + coaching-tree seed | P05 | Feeds a richer Q5; small CSV (32 teams × OC/DC × seasons) |
| Play-level nodes in the graph | P08 | ~45k plays/season; enables play-pattern queries (e.g. play-action success vs specific defenses) |
| HTML email rendering of the digest | P07 | Simple Markdown → HTML template |
| Ratings and graph history before 2018 / 2010 | P08 | More history for coaching trees and player careers |
| **Pipeline control room (web app):** run the weekly pipeline from a button and watch every stage live, plus the digest archive and scoreboards | All P phases (at least P07/P08); before T00 | Local only; Rishi's idea for later, not to be started without him. Notes below |
| Kicker / punter targets | P08 | Low priority |
| Uncertainty-aware game simulation (simulating the season to project playoff odds) | P08 | Uses the game model's distributions; fun season-long output |
| Big Data Bowl 2027 track | T04 | If announced and relevant |

## Pipeline control room (web app): notes for later

Rishi's idea, captured on 2026-10-03 at the end of P04. Pick it up only when he says so.

- **Goal:** a clean local web app.
  - One button runs `nfl weekly run`; others resume from a step or generate a backtest digest.
  - Every stage is shown live as it happens: ingest, readiness, curate, ratings / Elo, game model, then (once built) graph and player model, payload, the GLM call, checks, render.
  - The results follow: the digest, report card, season scorecard, and the W&B runs and artifacts for that week.
- **W&B's role:** W&B stays the record for charts and versioned artifacts (`game-model`, `digest`, ...). The app adds what W&B can't do: run control, live step status, a readable digest and its archive. It links to or embeds W&B charts rather than redrawing them.
- **Stack** (decide then; aim for something polished, beyond Streamlit):
  - a Python API backend (e.g. FastAPI) that wraps the `nfl` CLI and streams progress to the browser (WebSocket or server-sent events);
  - a modern frontend (e.g. React / Next.js or SvelteKit with Tailwind and a component library) with a pipeline-graph view of the stages;
  - Streamlit stays the quick fallback.
- **What it can read today** (already written by the pipeline):
  - `runs/<season>/week<NN>/weekly_run.json` (each step's status, start / end, detail);
  - the run folder: `payload.json`, `checks.json`, `raw_llm_output.json` (GLM provider, latency, tokens, cost), `digest.md`, `watchlist.parquet`, `predictions_games.parquet`;
  - `runs/<season>/season_scorecard.parquet` and `reports/<season>/`;
  - the output of `nfl data-status` and `nfl doctor`;
  - the W&B API (runs, summaries, artifact versions).
- **Likely pipeline additions:**
  - structured progress events per step (start / end, row counts, durations; e.g. JSON lines a backend can stream);
  - progress for the GLM call, which can take about 20 minutes at `reasoning_effort: max`;
  - a lock so only one run happens at a time.
- **Guardrails:**
  - localhost only, never exposed to the internet;
  - secrets stay server-side (the W&B and OpenRouter keys never reach the browser);
  - buttons run only an allowlist of `nfl` commands;
  - follow the Security section of `CLAUDE.md`.
- **Timing:** after the P phases, once the pipeline's steps are stable (P07 / P08), and before Track 2 (T00). A Track 2 tab can come later.
