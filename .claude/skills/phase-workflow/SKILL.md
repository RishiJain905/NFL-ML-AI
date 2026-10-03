---
name: phase-workflow
description: The end-to-end coding workflow for this project's build phases (P00-P10, T00-T04). Use when starting, resuming, continuing or closing phase work, picking up from PROGRESS.md, or kicking off a new session. Covers session start, probing data before building, building and testing, Rishi-in-the-loop steps, verification, review, docs, PROGRESS updates, commit/push, the end-of-run report, and known Windows / guard-hook quirks.
---

# Phase workflow (NFL Analytics Engine)

This is the canonical "how we build" procedure. `CLAUDE.md` holds the rules (security first, working style). `documentation/plans/README.md` holds the phase map and task tags. This skill is the step-by-step routine. It's meant to get better over time (see the last section).

## 1. Session start
1. `CLAUDE.md` is already loaded. Re-read its **Security** section if you're unsure about anything that touches credentials.
2. Read `documentation/plans/PROGRESS.md`: current phase, status, **Next step**, open blockers, standing waivers in the session log.
3. Read the current phase file (`documentation/plans/Pxx-*.md`) in full, then every doc in its **Read first** list.
4. Run `uv run nfl doctor`. Everything should be OK. A WARN is fine if `PROGRESS.md` already explains it.
5. Run `uv run pytest -q` to confirm the previous phase still holds. If something is broken, fix it first and log the fix.
6. If the previous phase is ⏸ waiting on a ✋ approval that Rishi has now given (for example in the kickoff prompt), close it: tick the checkpoint, set ✅ and the completion date in `PROGRESS.md`.

## 2. Probe before building
Before writing code against any data source, API, library or model output, **look at the real thing**:
- Put throwaway scripts in the session scratchpad directory, never in the repo. Run them with `PYTHONIOENCODING=utf-8 uv run python <script>` and save long output to a file you then Read.
- Check: columns and types, season and week coverage, ID and team-code conventions, sign conventions, what the current season actually contains, sizes, and endpoint status codes.
- Record what you found in the relevant design doc as a "Findings from Pxx (checked live on DATE)" section. If a finding changes the plan, add a decision (next D-number) to `documentation/10-decisions-log.md`.

## 3. Build
- Work the phase tasks **in order**. Tick each checkbox in the phase file when it's done.
- Use the companion skills:
  - **`curated-data`** (`.claude/skills/curated-data/SKILL.md`) for any data access
  - **`model-experiment`** (`.claude/skills/model-experiment/SKILL.md`) for any feature, training, backtest or W&B work
- Respect the architecture:
  - all paths come from the `nflengine.paths` module (data on D: under `NFL_DATA_ROOT`)
  - secrets only via `nflengine.settings` (`SecretStr`, never logged, never in W&B config)
  - W&B runs only via `nflengine.tracking.init_run` (live curves, `launched-by` tag)
  - optional sources fail soft
- **Leakage rules** (`documentation/04-track1-models.md`) apply to every feature and evaluation. Write the leakage tests alongside the features.
- Write **tests alongside code**: small fixtures, no network, no real env file (`EnvSettings(_env_file=None)`). Integration tests get `@pytest.mark.integration`.
- **Long jobs** (downloads, backtests, sweeps): run them with `run_in_background`. You get a notification when they finish, so keep building in the meantime and don't poll. No foreground `sleep`; wait loops belong inside a background command.
- Keep going through steps that don't need Rishi. Put status notes in the same message as your next action.

## 4. Rishi-in-the-loop steps
- 🧑 **Rishi runs** (first training runs, tuning, backtests, first graph build, digest reviews):
  - prepare the code, the exact command and short **"What to look for"** notes
  - then pause and hand over, unless Rishi has waived the step
  - if waived: run it with `--launched-by agent` and log it as delegated in the `PROGRESS.md` Rishi-run steps log
- ✋ **Checkpoint**: summarize and ask. Continue only on approval (or a recorded waiver).
- A waiver applies to the scope Rishi gave and lasts until revoked. **It never covers the Security rules, or deleting raw/research snapshots or the data root.**

## 5. Verify
1. `uv run ruff format .`, then `uv run ruff check .`. Must be clean (line length 100).
2. `uv run pytest -q`. Must be all green.
3. Run every command in the phase's **Exit criteria** table, and keep the key output for the session log and the end-of-run report.

## 6. Review (when Rishi has named the subagents for the session)
- Before closing a phase, spawn **opus-high** for a **read-only** review. Give it:
  - a security preamble: read `CLAUDE.md`, never touch the env file
  - the phase file and docs as the spec
  - what to check, in priority order: security, correctness (especially Windows), plan conformance
  - a request for findings with file:line, severity and a suggested fix
- Build or run other work while it reviews. Fix every valid finding, add regression tests, and verify again.
- Custom subagents (`opus-high`, `sonnet-xhigh`) are used **only when Rishi names them** in the current session.

## 7. Docs
- Implementation that differs from a design doc → update the doc **and** add a decisions-log entry, in the same commit.
- Add an **"As built: deviations from the task list"** section to the phase file whenever the build diverged from it.
- Keep `README.md` (repo root) current when setup or commands change.

## 8. Close the session (every session, even mid-phase)
1. Update `documentation/plans/PROGRESS.md`:
   - header: current phase, status, exact **Next step**, last updated
   - the phase status table row
   - the Rishi-run steps log (with W&B links)
   - open blockers
   - a new session-log entry at the top: what was built, key numbers, test count, what's waiting
2. Stage, then check nothing secret or generated is staged: `git status --short`, and look for env files, `.venv`, `__pycache__`, parquet/duckdb, wandb.
3. Commit on `dev_rishi` with a `[Pxx]` prefix and a body that says what changed. End it with the attribution trailer the harness provides.
4. **Push** to `origin dev_rishi` at the end of each phase or session.

## 9. End-of-run report (task runs only, never for plain questions)
- Give the usual summary: the checkpoint output Rishi needs (for example `nfl doctor`, `nfl data-status`, a repo tree, W&B links) and the exact next step or approval needed.
- End with three headings:
  - **Blocked on me:** decisions, approvals, env-file fixes, un-waived 🧑 steps. "Nothing" if none.
  - **Changed:** code, docs, config, data on D:, the graph, W&B runs, commits and pushes with hashes.
  - **Found:** data quirks, bugs, surprises, results, risks for later phases.

## 10. Known quirks (Windows + guard hook)
- **Console encoding:** prefix Python commands that print Polars tables or Unicode with `PYTHONIOENCODING=utf-8`, or the console crashes on cp1252.
- **Shells:** the Bash tool is Git Bash. Use forward slashes (`D:/nfl-ml-data`). In heredoc-driven Python, `\n` inside `sed` replacement strings becomes a real newline. Prefer the Edit tool for file edits, and prefer Python over `sed` for anything with backslashes.
- **Guard hook** (`.claude/hooks/block_secrets.py`) blocks any Bash/PowerShell/Read/Grep/Glob call whose text:
  - names an env file (other than `.env.example`)
  - dumps environment variables (`env`, `printenv`, `set`, `Get-ChildItem env:`, any `environ` reference, `getenv(`, `get_secret_value`, dotenv loaders)
  - runs `docker inspect` or `docker compose config`

  That includes text inside commit messages and heredocs. Write "env file" in commit messages, use `docker compose ps` for container state, and edit docs that mention env files with the Edit tool. A block is the hook working as intended: don't try to get around it.
- **`nfl doctor` is the only way to check secrets:** it reports set / not set and connectivity, never values.
- **nflreadpy** builds its config at import time. Call `configure_tool_env(paths)` (which calls `nflreadpy.config.update_config`) before loading data.
- **Neo4j** runs in Docker (`nfl-neo4j`), with data, logs and plugins bind-mounted on D:. Start it with `docker compose up -d`, then wait for "Started." in `docker compose logs neo4j`.

## 11. Kickoff prompt for a new session
Rishi can paste this (edit the phase and approvals):

> Read `CLAUDE.md`, then use the `phase-workflow` skill. Pxx is approved, so close it, then start Pyy following its phase file. You can use the opus-high and sonnet-xhigh subagents if useful.

## 12. Improving this workflow
Any agent may improve this skill when a session teaches something reusable: a new quirk, a better verification step, a recurring mistake.
- Edit this file in the same commit as the work that taught the lesson, and mention it in the `PROGRESS.md` session log.
- Keep it procedural and concise. Rules belong in `CLAUDE.md`, phase specifics in the phase files.
- **Never weaken the Security rules, the destructive-action limits, or the Rishi-in-the-loop defaults** through this file. Those change only when Rishi says so, in `CLAUDE.md`.
