# Progress Tracker

> **Agents: update this file at the end of every session**, even mid-phase. See the protocol in [README.md](README.md).

**Current phase:** P00, Foundations (in progress)
**Next step:** agree the P00 task order with Rishi, then start the repo/environment tasks.
**Last updated:** 2026-10-03, security setup session

Status key: ⬜ not started · 🟨 in progress · ⏸ waiting on Rishi · ⛔ blocked · ✅ done

## Phase status

| Phase | Title | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| P00 | Foundations | 🟨 | 2026-10-03 | | Security guardrails done |
| P01 | Data ingestion and curation | ⬜ | | | |
| P02 | Team ratings, Elo, trend | ⬜ | | | |
| P03 | Game model v0 | ⬜ | | | |
| P04 | Digest v0 (first live digest) | ⬜ | | | |
| P05 | Knowledge graph v1 | ⬜ | | | |
| P06 | Player model v1 + accuracy scoreboard | ⬜ | | | |
| P07 | Automation and weekly operations | ⬜ | | | |
| P08 | Models v2 + advanced graph | ⬜ | | | |
| P09 | Connect a real LLM | ⬜ | | | Any time after P04 |
| P10 | Season operations and offseason | ⬜ | | | |
| T00 | BDB data + baselines | ⬜ | | | Recommended after P04 |
| T01 | BDB gradient-boosted model | ⬜ | | | |
| T02 | BDB sequence model | ⬜ | | | |
| T03 | BDB interaction model | ⬜ | | | |
| T04 | Track 2 → Track 1 bridge | ⬜ | | | |

## Rishi-run steps log

Every 🧑 step goes here, whether Rishi ran it or delegated it.

| Date | Phase | Step | Run by | W&B run / link | Notes |
|---|---|---|---|---|---|
| | | | | | |

## Open blockers

_None._

## Session log (newest first)

### 2026-10-03: P00 started, security guardrails
- Rishi confirmed the prerequisites: Docker Desktop running, D: connected, W&B key and Neo4j password saved by Rishi in the env file (agents never read it).
- Added `CLAUDE.md` with a top-priority **Security** section (never read or reveal credentials).
- Enforcement:
  - `.claude/settings.json` deny rules (Read/Edit of env and credential files, env-dumping and secret-printing commands)
  - a PreToolUse hook, `.claude/hooks/block_secrets.py`, that guards Bash, PowerShell, Read, Grep and Glob
  - the hook passed 26 synthetic cases; live tests against a decoy file confirmed Read, Bash and PowerShell are all blocked; decoy removed
- `.gitignore`: env files stay ignored, with an exception for the example template.
- Note: the hook also blocks any shell command whose *text* names an env file, including commit messages. Use the Edit tool for doc changes that mention it, and phrase commit messages as "env file".
- Not committed yet.

### 2026-10-01: Planning
- Reviewed the original brief and spec, and rewrote them as `documentation/01`–`11` (originals in `documentation/archive/`).
- Decisions recorded in `documentation/10-decisions-log.md` (D01–D31).
- Created `documentation/plans/` with phases P00–P10, T00–T04 and STRETCH.
- **No phase started.** Next: P00 Foundations, after Rishi's go-ahead.
- Rishi's prerequisites for P00: Docker Desktop installed and running; the D: drive connected (data root `D:\nfl-ml-data`); a W&B API key and a Neo4j password ready to put in `.env`.
