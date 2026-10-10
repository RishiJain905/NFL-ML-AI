---
name: sol-qa
description: User-invoked only. How to call Sol (Codex, gpt-6.1-sol at xhigh effort, Fast speed) through /codex:rescue for a task Rishi gives in his prompt. Covers the flags, the Fast service tier (set in Codex's user config, how to check and verify it), the background flow, fetching results, guardrails, and what to do when Sol is out of weekly or 5-hour usage.
disable-model-invocation: true
---

# sol-qa: how to call Sol (Codex, `gpt-6.1-sol`, `xhigh`, Fast)

**User-invoked only.** You are reading this because Rishi invoked it (`/sol-qa`) or `@`-mentioned this file in his prompt. Call Sol only in a session where he did that, and only for the task he gives in that prompt. Never call Sol on your own initiative, and don't treat this file as a standing instruction in later sessions.

**When his prompt names `/sol-qa`, follow this file yourself.** The `Skill` tool refuses to load it (`disable-model-invocation: true`); that only blocks loading it through the tool. Read this file and make the call in §2 directly. Don't stop and ask Rishi to type `/sol-qa` himself (LD00, 2026-10-10: he corrected exactly that).

"Sol" is Codex running `gpt-6.1-sol`, reached through the Codex plugin's `/codex:rescue` path. This file only explains **how to call it**. The task itself is whatever Rishi writes in his prompt (a review, a QA pass, a second opinion, a diagnosis), so keep this procedure generic.

Verified 2026-10-03 with plugin `codex@openai-codex` 1.0.6 and Codex CLI 0.160.0; Fast speed verified 2026-10-10 (§1). If a step stops working, re-run a read-only smoke call (step 2) before assuming the task is at fault.

## 1. Fixed settings

- **Model:** `gpt-6.1-sol`. **Effort:** `xhigh`. **Speed:** Fast.
- Pass the model and effort flags on every call, even though they are Rishi's current Codex defaults, so a changed default can't silently change Sol. Don't change the model, effort or speed unless Rishi says so.

### Speed: Fast (the `priority` service tier)

- **The options for `gpt-6.1-sol`:** Standard (the default) and **Fast** (service tier id `priority`, "2x speed, increased usage" in Codex's model list). There is no "Ultrafast" tier for this model; the label exists in Codex's interface code, but no model offers it (checked in the model cache and the CLI, 2026-10-10).
- **It's a Codex config setting, not a flag.** The plugin's `task` command takes only `--model`, `--effort`, `--write`, `--background` and `--fresh` / `--resume`, and sends Codex only the model and effort; the speed comes from Codex's config. Rishi set it **user-wide** on 2026-10-10: `service_tier = "priority"` in `~/.codex/config.toml` (it was `"default"` = Standard). Every Codex run on this machine is Fast.
- **Pre-flight check** (read only that key; never print or copy the whole file, which may hold other tools' settings): `grep -nE '^\s*service_tier\s*=' ~/.codex/config.toml` must show `service_tier = "priority"`. If it shows anything else, tell Rishi; don't edit his Codex config without his OK.
- **A wrong value fails silently.** A tier the model doesn't advertise is dropped from the request ("Configured service tier `…` is not advertised as supported for model `…` and will be omitted from requests") and the run goes at Standard speed.
- **Verify a run went Fast:** the session files (`~/.codex/sessions/`) don't record the tier, but Codex's log does. After a run, look for its thread id in `~/.codex/logs_2.sqlite` (read-only):

```bash
f="$(cygpath -w ~/.codex/logs_2.sqlite)"; python - "$f" <<'PY'
import re, sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for tid, body in con.execute("select thread_id, feedback_log_body from logs where feedback_log_body like '%service_tier%' order by ts desc limit 200"):
    m = re.search(r'service_tier":"(\w+)"', body or "")
    if m: print(tid, m.group(1))
PY
```

  The newest threads should show `priority` (before 2026-10-10 every run showed `default`). The thread id is the Codex session id that `node "$CC" status` / `result` print.
- **Measured** (one smoke call, 2026-10-10): about 43 output tokens/s on Fast vs about 28 on Standard for a first step of the same context size (~22k input tokens), so roughly 1.5x on that sample; Codex advertises 2x. A config change took effect without restarting Codex (the shared app-server picked it up).
- **Cost:** Fast uses more of the weekly and 5-hour Codex limits per call. When a limit runs out, §5 applies unchanged.

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

Ignore the `DEP0190` deprecation warning that node prints. It is harmless. Poll sparingly, because an `xhigh` run can take many minutes (a full phase review took ~10 min at Standard speed on 2026-10-10; expect less on Fast). Rather than polling by hand, start one background wait loop that checks `status <job-id>` every minute and saves `result <job-id>` to the scratchpad when the job is done. When a background job finishes, the subagent may send a second report, and either source is fine.

**Presenting results.** Give Rishi Sol's output unaltered (quote it or point to it), labeled as Sol's. Put your own commentary separately.

## 4. Guardrails

- **Security.** Codex is not bound by Claude's deny rules, but it loads `AGENTS.md` on every thread, which carries the full Security section, and `.codex/config.toml` filters secret-looking env vars from its commands. Don't weaken either. Never ask Sol to read `.env` or other credential files or to print env vars. Before keeping anything Sol produced, check its output and any diff for secret values and for env or credential files. If Sol's output shows a secret, stop, tell Rishi right away, and recommend rotating that credential.
- **Never write the env file's literal file name in the task text.** Write "the env file" instead. The forwarder passes the task text through a Bash call, and the guard hook blocks any Bash text that names an env file, so the call fails before Sol starts. Seen 2026-10-03.
- **Sandbox limits on this machine** (verified 2026-10-03): inside Sol's sandbox, `python`, `uv` and `pytest` can't launch, `D:` is readable but not writable, and `git` and `ruff` work. So don't ask Sol to run the test suite or write data. Run tests yourself and give Sol the results if it needs them.
- **Setup problems.** If the call says Codex is missing or not authenticated, stop and tell Rishi to run `/codex:setup` (or `!codex login`).

## 5. If Sol is out of usage (weekly or 5-hour limit)

Recognize it by the call failing or the job output containing a Codex error about a usage limit, rate limit or quota (the protocol names these `usageLimitExceeded` and `rateLimitExceeded`).

Then **ignore the Sol task entirely**:
- Don't retry, wait for the limit to reset, or loop.
- Don't re-run it on a different Codex model (such as `spark` or a mini model).
- Don't hand it to a Claude subagent or any other model, and don't do the work yourself in its place.
- Tell Rishi in one line that Sol hit its limit and the task was skipped, then carry on with anything else he asked for.
