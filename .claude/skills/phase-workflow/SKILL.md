---
name: phase-workflow
description: The end-to-end coding workflow for this project's build phases (P00-P10, T00-T04, the control room CR00-CR03, and the feature tracks LD00-LD03, PC00-PC03, AE00-AE04). Use when starting, resuming, continuing or closing phase work, picking up from PROGRESS.md, or kicking off a new session. Covers session start, probing data before building, building and testing, Rishi-in-the-loop steps, verification, review, docs, PROGRESS updates, commit/push, the end-of-run report, and known Windows / guard-hook quirks.
---

# Phase workflow (NFL Analytics Engine)

This is the canonical "how we build" procedure. `CLAUDE.md` holds the rules (security first, working style). `documentation/plans/README.md` holds the phase map and task tags. This skill is the step-by-step routine. It's meant to get better over time (see the last section).

## 1. Session start
1. `CLAUDE.md` is already loaded. Re-read its **Security** section if you're unsure about anything that touches credentials.
2. Read `documentation/plans/PROGRESS.md`: current phase, status, **Next step**, open blockers, standing waivers in the session log.
3. Read the current phase file (`documentation/plans/Pxx-*.md`; control-room phases are `documentation/control-room/CRxx-*.md`, and their visual spec is the mockup linked in that folder's README; the feature tracks are `documentation/live-decisions/LDxx-*.md`, `documentation/play-calling/PCxx-*.md` and `documentation/ask-the-engine/AExx-*.md`, each folder with a spec README) in full, then every doc in its **Read first** list. **Feature tracks obey the no-touch rule (D107):** never edit production model, feature, digest, weekly-graph or weekly-run code; run the production-unchanged check before closing.
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
- **Confirm the waiver at session start.** If the kickoff prompt doesn't explicitly say the 🧑 runs or ✋ checkpoints are waived, ask once with AskUserQuestion before running any of them. "Complete all tasks of Pxx" is *not* an explicit waiver (P03 lesson). Record the answer and its scope in `PROGRESS.md`.
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
- **Guides** (`documentation/guides/`, CLAUDE.md → Documentation): when the phase introduces a new technology, component, service, pipeline stage, W&B run or artifact, write or update its plain-language guide (primer, wiring, outputs, how to look at it, how to change it, limits, real numbers) and add it to `documentation/README.md`. New W&B charts or artifacts go into `guides/weights-and-biases.md` in the same commit. Rishi asked for this after P05: the design docs alone didn't explain the new pieces to a newcomer.

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
- **ruff also formats Python code blocks inside Markdown** (docs and skills). Run `uv run ruff format .` before `ruff format --check`, or the check fails on a doc snippet.
- **`nfl doctor` is the only way to check secrets:** it reports set / not set and connectivity, never values.
- **nflreadpy** builds its config at import time. Call `configure_tool_env(paths)` (which calls `nflreadpy.config.update_config`) before loading data.
- **Neo4j** runs in Docker (`nfl-neo4j`), with data, logs and plugins bind-mounted on D:. Start it with `docker compose up -d`, then wait for "Started." in `docker compose logs neo4j`.
- **W&B sweeps** (`tracking.run_sweep`) work in-process on Windows and take about 10–15 s per run, so 175 runs is about 45 minutes. Run them with `run_in_background`, and smoke-test them first with a 2-value grid.
- **Shared test fixtures** live in `tests/conftest.py`. Tests import helpers from it directly (`from conftest import make_league`). **Never add a `conftest.py` in a test subfolder** (P07): without `__init__.py` files it shadows the top-level `conftest` module and those imports fail at collection; put autouse fixtures in `tests/conftest.py`.
- **Subagents in parallel (P03).** Give each subagent its own new files only (it must not touch `cli.py`, `settings.yaml` or modules you are editing) and a precise interface: function names, output columns. Integrate yourself. If a subagent goes idle without its final report reaching you, ask it with SendMessage.
- **Late subagent reports (P04).** Both parallel subagents showed as idle long before their final reports arrived (about 20 minutes later, after a SendMessage). Don't block on them: read and review their files yourself, run their tests, and measure the numbers you need (P04: the watch-list hit rate); fold in the report when it lands.
- **Smoke-testing a 🧑 step's code path.** If a test of the live path writes the outputs Rishi's run should produce (P04: `reports/2026/week04-digest.md`), delete those derived files afterwards and say so, so his run is the first real one.
- **The secret-guard / deny rules can block a shell `grep` whose text names `.env.example` alongside other files.** Read `.env.example` with the Read tool (it's the one env file agents may read) and search the other files with Grep.
- **Heredoc-driven Python edits can corrupt regexes (P04).** Inside a `python - << 'EOF'` script, a regex written as `"\b..."` in a normal (non-raw) string becomes a literal backspace character in the file. It happened twice, and a test caught it both times. Prefer the Edit tool for regex lines; after any scripted edit, scan for control characters (`[\x00-\x08\x0b\x0c\x0e-\x1f]`) in changed files. **It happened again in P09** (four `\b` in `checks.py` became backspaces inside a `'''...'''` block, caught by the scan before a backtest imported it): for regex code, write the script with the Write tool, or use `"\\b"` in raw strings only.
- **Real LLM calls are slow at high reasoning effort** (minutes per digest). Run them with `run_in_background`, keep `--no-wandb` for the first try, and keep the placeholder as the fallback writer.
- **Neo4j integration tests own the graph (P05).** `pytest -m integration tests/graph/...` rebuilds the whole graph for each golden week (~1.5 min each, ~10 min in all) and ends by rebuilding the live week. Don't run a graph build, a backtest digest or a subagent's graph query at the same time. Pipe-buffered output (`... | tail`) shows nothing until the run ends: wait for the notification.
- **Multi-line Python edits with quotes: write the script with the Write tool** to the scratchpad and run it (`uv run python <script>`). A heredoc whose text mixes `'''`, apostrophes and backticks failed to parse in Git Bash (P05).
- **Subagent reviews of each other's contracts pay off (P05).** opus-high, wiring the digest side of a payload model it didn't design, found three real binding holes (a fact with two teams' numbers owned by both, a shared opponent owning both margins, a teammate standing in for his team). Ask the integrating subagent to report where the contract made its job awkward.
- **Write the contract module before spawning subagents (P06).** `models/player_schema.py` (targets, the prediction-file schema, scoring helpers) was written first; then sonnet-xhigh built two feature families against a fixed function signature and opus-high built everything downstream of the prediction file, in parallel with the lead's model work. Both found real holes (an empty-frame bug, a confidence rule that flagged every pass rusher, a stale-file bug in backtest digests). Hand a subagent a read-only review of the lead's code while long jobs run: it found 8 issues, no leakage, and verified the fixes.
- **Ask for the rest of a truncated subagent report** with SendMessage; the final message is often cut mid-table.
- **A CLI test must never reach the real pipeline (P07).** When a command's wiring changes, re-check every test that drives it: an old test stubbed `run_weekly` while the CLI had moved to `run_pipeline`, so full `pytest` runs (the lead's and both subagents') wrote run records to D: and logged four failed W&B runs. Stub the outermost function the CLI calls; `tests/conftest.py` now sets `WANDB_MODE=disabled` for every non-integration test as a safety net. After any full test run, a quick `ls` of the live run folder shows whether something leaked.
- **Subagent final reports can arrive very late (P07).** Both named subagents showed idle about 40 minutes before their reports reached the lead (after a SendMessage). Don't wait: review their files and tests yourself, run their smoke yourself (with hashes of the live files before and after), and fold in their reports when they land. An unnamed background Agent's report arrives with its completion notice. Cross-reviews (each subagent reviewing the other's module and the lead's code, read-only) found more real issues than either build pass.
- **Backtest digests and backtest graph builds replace the live Neo4j graph** (`--as-of` simulations no longer do: `graph="off"`). Restore it afterwards (the `neo4j-graph` skill §6 recipe).
- **A "live" switch makes a parallel build safe for production (P08).** With a live run days away, the contract gave new work its own off-switch before any worker started (`player_model.live_targets`, `team_model.live_targets`, `game_model.version`), and the weekly function's default changed to read it. Workers could add targets, groups and model versions freely; only the ship decision flipped them on. Prove it with a scratch rehearsal of the weekly path whose fresh rows must equal the published files exactly.
- **Write each ✋ rule down before the reported runs** (a timestamped scratch file, then the decisions log). P08's game model v1 gained −0.0005 Brier on the reported window after losing on the tuning window; the rule fixed in advance made "keep v0" a non-event.
- **Every final report from three parallel workers arrived truncated (P08).** Ask for "the rest, in two or three messages" right away with a numbered list of what you still need. Workers also ask questions mid-run by message; answer at once, because a reply can land after they've moved on (one worker went ahead with an edit before my approval reached it).
- **Shared files between workers: one owner per file, and the lead edits a worker's file only with a heads-up message** (P08: the lead's one-line filter in `digest/players.py` while its owner was still editing it).
- **Stopping a background script on Windows (P09): `TaskStop` ends only the outer bash.** The script's loop and its `uv` / `nfl` / `python` children keep going (the round went on to the next week). Find them with `Get-CimInstance Win32_Process` (match the script name or the command line) and end the tree with `taskkill /PID <bash pid> /T /F`; then check that nothing matches. Killed W&B runs show up as crashed in their group.
- **Another Claude session may share Neo4j (P09).** Before a backtest graph build or `pytest -m integration`, tell any other session working in this repo (SendMessage) and ask it to stay off Neo4j until you've restored the live graph; two builds at once leave a graph neither can trust. If they overlapped, discard that week's results and rebuild.
- **A phase that waits for the calendar (P10).** When most tasks depend on dates (a season, an offseason), ask once how "complete" should work. P10's answer was "build + rehearse now, close, move the dated steps to PROGRESS → Season calendar" (D89); tick a task when its tool or procedure is proven, mark the dated part ⏭, and keep the 🧑 / ✋ steps for their dates.
- **Rehearse instead of hand-made scratch harnesses (P10).** `nfl weekly rehearse --season S --week W` runs the live steps on a past week with a pinned clock into `rehearsals/<S>/` (weekly-ops skill §5c). Use it to prove a change on real weeks (the playoffs, week 1) before a live Tuesday.
- **"The live path is unchanged" when a published file can't be reproduced (P10).** A shared input rebuilt after the published run (P08's `nfl features game` rewrote `features/game_features.parquet` 17 h after week 4's player run) makes the published file irreproducible. Then compare **old code vs new code on today's inputs**: `git archive HEAD src config` into the scratchpad, run it with `PYTHONPATH=<scratch>/src`, an explicit data root (the archived code can't find the env file: pass `ensure_data_root("D:/nfl-ml-data")`, never copy the env file), the same pinned moment (fake `dt` in the modules that read the clock; `datetime.now` must return the fake subclass or `isinstance` checks fail) and outputs redirected; P10 got max diff 0.
- **A rehearsal is only as faithful as its view of "now".** Dropping *all* of the replayed week's box-score rows moved a week-level median and flipped one confidence label; dropping only the rows of games after the pinned time matched the old code exactly.
- **Heredoc Python, once more (P10):** a `\r` inside a normal string in a `<< 'EOF'` script wrote a carriage return into a doc (`bdb\<edition>\raw`), and `Path.read_text`'s universal newlines turned it into a line break, so a plain search couldn't find it. For any text with backslashes, write the script with the Write tool, and check with `grep -c $'\r'` as well as the control-character scan.
- **Subagent reports can go to "main", not "team-lead" (P10).** opus-high's sends to `team-lead` reported success three times and never arrived; it then sent to `main` and they did. If a worker says it sent and nothing came, ask it to send to `main`.
- **Even a quoted heredoc (`<< 'EOF'`) can fail in the Bash tool (CR01, twice):** "unexpected EOF while looking for matching `''" on Python edit scripts whose text held apostrophes and backticks. Don't retry variants: write the script to the scratchpad with the Write tool and run it with `uv run python <script>`. One-line `sed` with `\n` escapes in the pattern also misfired; use the Edit tool for those.
- **Contract first, then parallel UI workers (CR01).** Writing the API types (`web/src/api/types.ts`), the hooks and the shared view model before spawning two frontend workers let both build against fixtures while the lead wrote the backend; real JSON dumps in the scratchpad (sent by message, never committed) caught the size and shape surprises. Look at the result in a real browser before the review: the screenshots found two layout faults the component tests couldn't (a label crossed by an edge, a table cut off at 20 rows).
- **Real-data parity tests find what fixtures can't (CR01):** the published digest has CRLF line ends (Windows), so a reader using `read_text` wasn't byte-identical; a fixture written with `write_text` hid it. Write fixture text with `write_bytes` when exact content matters.
- **After a crash (CR02: the PC rebooted mid-session), the subagents are gone but their files survive.** Check `git status` and each worker's gates, then start a new worker per task with the original brief plus a "resuming after a crash" preamble that lists its surviving files and what was still missing; both CR02 workers finished from there. Also expect Neo4j to replay its transaction logs on the next start (20+ minutes on the HDD bind mount: `docker compose logs neo4j` shows "Recovery required"); it isn't serving until "Started.".
- **Scanning for stray control characters on this checkout:** the working copy is CRLF (`core.autocrlf=true`) and Python's `write_text` writes CRLF on Windows, so every line ends in a carriage return, and `grep -c` for it in Git Bash misreports. Scan with Python for `\r(?!\n)|[\x00-\x08\x0b\x0c\x0e-\x1f]` (a CR not followed by LF, or another control character). The P10 trap struck twice more in CR02: a Windows path ending in `\runs` and a regex inside a heredoc's Python string both became carriage returns (even in a quoted heredoc). For any text with backslashes, write the script with the Write tool using raw strings (`r'...'`), never a heredoc.
- **Old code vs new code without copying the env file (CR02):** a scratchpad script that does `sys.path.insert(0, <src dir>)`, asserts `nflengine.__file__` is under it, sets `ops.rehearsal.ensure_data_root = lambda *a, **k: ensure_data_root("D:/nfl-ml-data", create_dirs=False)` and calls `run_rehearsal(..., at=<the published moment>, root=<its own rehearsal folder>)`; run it once with `git archive HEAD src config` unpacked in the scratchpad and once with the repo's `src`, then join the prediction files on their keys (sorting on non-unique columns misaligns rows).
- **Shell quirks (CR02):** `tail -3 a b` fails in Git Bash ("option used in invalid context"): use `tail -n 3`. A foreground `sleep N; cmd` is blocked by the harness: wait for a background job's notification, or use Playwright's `browser_wait_for`. A stopped `nfl app` server: find it by its port (`Get-NetTCPConnection -LocalPort <port>`) and `taskkill /PID <pid> /T /F`.
- **The guard hook reads the whole Bash text, prose included (CR03):** a doc edit through a Bash heredoc whose text merely mentions the environment object (a skill line saying "never into the environment") was blocked. Write such edits as a script with the Write tool and run it, or reword; never try to get around a block on a real secret read.
- **Shared Playwright browser between workers (CR03):** a worker's `page.route` stubs (its fixture server) stayed on the shared browser after it finished, so the lead's first page load failed with `route.fetch ECONNREFUSED`. Close the browser (`browser_close`) before your own browser checks; and screenshot only after a `wait_for` on content that appears **last** (the race in the control-room skill §6 struck on three pages).
- **Real JSON before the UI review (CR03):** dump every endpoint on the real data root into the scratchpad as soon as the backend runs, and have each UI worker render its pages from it (read only, never committed): it changed a unit (a 0–1 share), long tooltips, grouped chips and card links, which fixtures had hidden. Workers also found four backend wrinkles (a missing `project_url` on cached answers, mixed units in a tile, a naive time, a wrong column name) that the backend tests hadn't.
- **Time-bomb tests (LD00 session start):** a test that pins the plan's clock (`now=`) but lets the code stamp results with the real clock passes until the real date crosses a deadline, then fails (`tests/ops/test_pipeline.py`'s `on_time` after 2026-10-09). Pin every clock a test depends on (`ops.records.utc_now`), and suspect the date first when an old test fails on an untouched tree.
- **Test basenames must be unique across `tests/` (LD00):** there are no `__init__.py` files, so `tests/live/test_backtest.py` next to `tests/test_backtest.py` breaks collection ("import file mismatch"). Prefix a new folder's files (`test_live_*.py`); check with `uv run pytest --collect-only -q tests`.
- **Timing checks need an idle machine (LD00):** a subagent's model fit running at the same time turned a 15 ms check into 23 ms. Run timing budgets after background jobs finish, or let the train command record them.
- **Feature tracks can't edit production code (D107):** the session-start fix had to stay in a test; `tests/test_production_untouched.py` hashes the protected files and settings blocks (the tracks' own blocks excluded). Regenerating its manifest needs Rishi's OK and its own commit.
- **Smoke-test W&B logging before the real runs.** Do one short backtest tagged `smoke` (2 seasons, `save=False`). It checks that the tables and plots log correctly before the runs that count (P03: `e9ex7x3q`).

## 12. Improving this workflow
Any agent may improve this skill when a session teaches something reusable: a new quirk, a better verification step, a recurring mistake.
- Edit this file in the same commit as the work that taught the lesson, and mention it in the `PROGRESS.md` session log.
- Keep it procedural and concise. Rules belong in `CLAUDE.md`, phase specifics in the phase files.
- **Never weaken the Security rules, the destructive-action limits, or the Rishi-in-the-loop defaults** through this file. Those change only when Rishi says so, in `CLAUDE.md`.
