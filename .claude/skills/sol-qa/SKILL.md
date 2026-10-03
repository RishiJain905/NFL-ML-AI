---
name: sol-qa
description: User-invoked only. How to call Sol (Codex, gpt-6.1-sol at xhigh effort) through /codex:rescue for a task Rishi gives in his prompt. Covers the flags, the background flow, fetching results, guardrails, and what to do when Sol is out of weekly or 5-hour usage.
disable-model-invocation: true
---

# sol-qa: how to call Sol (Codex, `gpt-6.1-sol`, `xhigh`)

**User-invoked only.** You are reading this because Rishi invoked it (`/sol-qa`) or `@`-mentioned this file in his prompt. Call Sol only in a session where he did that, and only for the task he gives in that prompt. Never call Sol on your own initiative, and don't treat this file as a standing instruction in later sessions.

"Sol" is Codex running `gpt-6.1-sol`, reached through the Codex plugin's `/codex:rescue` path. This file only explains **how to call it**. The task itself is whatever Rishi writes in his prompt (a review, a QA pass, a second opinion, a diagnosis), so keep this procedure generic.

Verified 2026-10-03 with plugin `codex@openai-codex` 1.0.6 and Codex CLI 0.160.0. If a step stops working, re-run a read-only smoke call (step 2) before assuming the task is at fault.

## 1. Fixed settings

- **Model:** `gpt-6.1-sol`. **Effort:** `xhigh`.
- Pass both flags on every call, even though they are Rishi's current Codex defaults, so a changed default can't silently change Sol. Don't change the model or effort unless Rishi says so.

## 2. How to call

Use the **Agent tool** with `subagent_type: "codex:codex-rescue"`. This is exactly what `/codex:rescue` does. The `codex:rescue` skill reaches the same subagent, but calling the subagent directly skips the command's "continue the previous thread?" question. Never call the `Skill` tool from inside that subagent. It re-enters the command and hangs the session.

The `prompt` is **flags first, then the task text**:

```
--model gpt-6.1-sol --effort xhigh --fresh --background <task text>
```

The subagent is a thin forwarder. It makes one Bash call to the plugin's `codex-companion.mjs task ...` and hands back the output. It does not inspect the repo, poll or follow up, so everything Sol needs must be in the task text.

| Flag | Meaning |
|---|---|
| `--fresh` | Start a new Sol thread. Use it for every new task. Without it, words like "continue" or "keep going" in the task text make the forwarder resume the last thread. |
| `--resume` | Continue the previous Sol thread for this repo, for follow-ups on Sol's own work. (From the plugin docs, not smoke-tested.) |
| `--background` | Return immediately and fetch the answer later. **Default for any non-trivial task.** |
| `--wait` | Wait for Sol and get its answer in the hand-back. Only for short tasks (the smoke test took ~6 s). |

**Read-only or write.** Unless the request says otherwise, the forwarder gives Sol a write-capable run (`--write`). For QA, review or diagnosis, put "Read-only: do not modify any files" in the task text. That is verified: the job records `write: false` and no files touched. Allow edits only if Rishi's task asks for them.

**Writing the task text.** Sol sees the repo and `AGENTS.md` (auto-loaded) but **not this conversation**. Write a self-contained brief:
- the goal, and the scope (paths, branch or diff to look at)
- what a good answer contains, e.g. findings most severe first, each with `file:line` and a concrete failure scenario
- Reference files by path; don't paste large content. **Never include secret values.**

## 3. Getting the answer

The Agent tool always returns asynchronously, and the subagent's report arrives as a message when it finishes.

- **`--wait`:** the report is Sol's output, verbatim. The forwarder's Bash call has a timeout (2 min by default, 10 min at most), so don't use `--wait` for anything that might run longer than a minute or two.
- **`--background`:** the report is only "Command running in background with ID …", **not** Sol's answer. Fetch the answer from the companion script. Run these from the repo root, because jobs are scoped to the repo:

```bash
CC="$(cygpath -m "$(find ~/.claude/plugins/cache/openai-codex/codex -path '*/scripts/codex-companion.mjs' | sort -V | tail -1)")"
node "$CC" status                  # table of running and recent jobs, with ids
node "$CC" status <job-id>         # one job
node "$CC" result <job-id>         # Sol's final output + `codex resume <session-id>`
node "$CC" cancel <job-id>         # stop a running job
```

Ignore the `DEP0190` deprecation warning that node prints. It is harmless. Poll sparingly, because an `xhigh` run can take many minutes. When a background job finishes, the subagent may send a second report, and either source is fine.

**Presenting results.** Give Rishi Sol's output unaltered (quote it or point to it), labeled as Sol's. Put your own commentary separately.

## 4. Guardrails

- **Security.** Codex is not bound by Claude's deny rules, but it loads `AGENTS.md` on every thread, which carries the full Security section, and `.codex/config.toml` filters secret-looking env vars from its commands. Don't weaken either. Never ask Sol to read `.env` or other credential files or to print env vars. Before keeping anything Sol produced, check its output and any diff for secret values and for env or credential files. If Sol's output shows a secret, stop, tell Rishi right away, and recommend rotating that credential.
- **Sandbox limits on this machine** (verified 2026-10-03): inside Sol's sandbox, `python`, `uv` and `pytest` can't launch, `D:` is readable but not writable, and `git` and `ruff` work. So don't ask Sol to run the test suite or write data. Run tests yourself and give Sol the results if it needs them.
- **Setup problems.** If the call says Codex is missing or not authenticated, stop and tell Rishi to run `/codex:setup` (or `!codex login`).

## 5. If Sol is out of usage (weekly or 5-hour limit)

Recognize it by the call failing or the job output containing a Codex error about a usage limit, rate limit or quota (the protocol names these `usageLimitExceeded` and `rateLimitExceeded`).

Then **ignore the Sol task entirely**:
- Don't retry, wait for the limit to reset, or loop.
- Don't re-run it on a different Codex model (such as `spark` or a mini model).
- Don't hand it to a Claude subagent or any other model, and don't do the work yourself in its place.
- Tell Rishi in one line that Sol hit its limit and the task was skipped, then carry on with anything else he asked for.
