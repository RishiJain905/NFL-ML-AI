# AE00: The safe query layer and the model bake-off

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Ask the Engine rows)

- **Depends on:** P10 (the weekly graph, curated data, the OpenRouter key)
- **Unlocks:** AE01 (the page), AE02 (Ask's third source), AE04 (the golden set)
- **Read first:**
  - [Ask the Engine README](README.md), all of it, especially §2 (the safety tests), §4 and §5;
  - the `neo4j-graph` skill (schema, the query library, the golden tests);
  - the `curated-data` skill (tables, keys, quirks: the SQL templates' raw material);
  - the [LLM digest writer guide](../guides/llm-digest-writer.md) (how the OpenRouter provider is wired; Ask gets its own, and the digest's stays as is);
  - [05 Knowledge graph](../05-knowledge-graph.md) → the Q&A stretch notes.

## Goal

Everything Ask needs, testable from the terminal before any UI:
- a guard stack that only lets safe, read-only, bounded queries reach Neo4j and DuckDB;
- a template library;
- an Ask LLM provider separate from the digest's;
- a golden set of questions with reference answers and an evaluation harness;
- a bake-off that picks a fast, cheap model on our own questions.

## Scope

- **In:**
  - `ask/guard.py`, `ask/templates.py`, `ask/names.py`, `ask/llm.py`, `ask/route.py`, `ask/answer.py`, `ask/evaluate.py`;
  - `nfl ask "<question>"` and `nfl ask eval`;
  - the golden set;
  - the bake-off;
  - the guide, skill and W&B section.
- **Out:** the page (AE01); the history graph (AE02).

## Tasks

### Probe first
- [ ] 🤖 **Re-run the safety probes** from README §2 as tests, against the live Neo4j and an in-memory DuckDB:
  - writes in READ mode rejected;
  - `LOAD CSV` / `apoc.load` / `apoc.export` / `gds.graph.project` caught by the guard (they plan as read);
  - the timeout;
  - the DuckDB lock-down (research folder, `COPY`, `ATTACH`, `INSTALL`, system files refused).
- [ ] 🤖 **The Windows file-handle question:** does an open DuckDB view over `curated/*.parquet` block the curate step's `replace()`? Test it: a long query while `fsutil.replace_file` replaces a curated file. If it blocks past the 2 s retry, open a fresh connection per question and close it at once, and test that.
- [ ] 🤖 **The model list and prices:** re-read OpenRouter's model list (`/api/v1/models`, public) and pick the bake-off candidates. As of 2026-10-08: Claude Haiku 5.5, GPT-6 Luna, Qwen3.8 Flash, DeepSeek V4.1 Flash, Mercury 2.5, and GLM 5.3 Flash at low effort as the control.

### Build (`src/nflengine/ask/`)
- [ ] 🤖 **`guard.py`:**
  - **Cypher:**
    - one statement;
    - a token / parser allowlist;
    - `EXPLAIN` checked for type `r` and the absence of `ProcedureCall` / `LoadCSV`;
    - `execute_read` with `timeout = 10 s`;
    - a `LIMIT` cap.
  - **SQL:**
    - `duckdb.extract_statements` → `SELECT` only;
    - the sandboxed connection (views over the curated Parquet files; `allowed_directories`, `enable_external_access = false`, `lock_configuration = true`, autoload off);
    - a 10 s interrupt;
    - a row cap.
  - Every refusal names the rule it broke, in fixed text.
- [ ] 🤖 **`templates.py`:** a YAML library (`config/ask_templates.yaml`) of:
  - graph templates (the 17 `graph/queries/*.cypher` files, plus Browser examples from the `neo4j-graph` skill §8 and the week-4 queries file);
  - SQL recipes from the `curated-data` skill;
  - each with a description, typed parameters (team, player, season range, week, stat) and an example question.
- [ ] 🤖 **`names.py`:** fuzzy resolution of players, coaches and teams (names, nicknames, team aliases) against curated tables, with `rapidfuzz` or plain trigram scoring. Ambiguous names return choices, not a guess.
- [ ] 🤖 **`llm.py`:** the Ask provider.
  - **Providers:** `openrouter` (any model; the existing key) or `openai_compatible` (`LLM_BASE_URL` + `LLM_API_KEY`, for a local server behind HTTPS).
  - **Settings:** low or no reasoning, short timeouts, `max_tokens` small.
  - **Each call records** model, provider, latency, tokens and cost, never the key.
  - It reuses nothing mutable from `digest/llm/` (it may import its helpers read-only).
- [ ] 🤖 **`route.py`:**
  1. Resolve names.
  2. Ask the LLM to pick a template and fill its parameters (JSON), or say "none".
  3. On "none", generate a query (Cypher or SQL, schema + 3–5 nearest examples in the prompt).
  4. Guard it, run it.
  5. On an error or an empty result, up to 2 correction rounds with the error message.
- [ ] 🤖 **`answer.py`:** 2–3 sentences from the rows. Every number in the text must appear in the rows (a provenance check modelled on `digest/checks.py`'s); if it fails, show the rows with a fixed sentence.
- [ ] 🤖 **CLI:** `nfl ask "<question>" [--source graph|sql|auto] [--show-query]`.

### The golden set and the bake-off
- [ ] 🤖 **Golden set** (`config/ask_golden.yaml`), 60–100 questions:
  - about a third answerable by templates, a third needing free-form SQL, a third graph relationships;
  - some trick ones: ambiguous names, impossible requests ("delete…"), out-of-range seasons, questions about data we don't have.

  Each has a **reference query** (hand-written, checked) whose result is the expected answer **on the data of the day**, so the set doesn't go stale.
- [ ] 🤖 **`nfl ask eval --model <id> [--n all]`**: runs the set and logs a W&B run (`job_type: ask-eval`):
  - execution accuracy (the result set equals the reference's, order-insensitive);
  - template hit rate;
  - retries;
  - refusals (the impossible ones must be refused);
  - latency p50 / p95;
  - cost per question;
  - a table of every question.
- [ ] 🧑 **Bake-off:** Rishi runs (or delegates) `nfl ask eval` for each candidate and compares in W&B.
- [ ] ✋ **Pick the model**, by a rule fixed before the runs: the highest execution accuracy among models with p95 latency ≤ 8 s and cost ≤ $0.002 a question; ties go to the faster. Write it in `settings.yaml` → `ask.llm.model` and log the decision.

### Docs and close
- [ ] 🤖 Guide `documentation/guides/ask-the-engine.md`:
  - what text-to-query is and why it goes wrong;
  - the template-first design;
  - the guard stack in plain words;
  - the golden set and how to add a question;
  - the bake-off results;
  - how to switch the model, local models included.
- [ ] 🤖 Skill `.claude/skills/ask-the-engine/SKILL.md`; CLAUDE.md's skills table; the W&B guide (`ask-eval`).
- [ ] 🤖 Codex / Sol review (the guard especially: try to break it). Production-unchanged check.
- [ ] ✋ **Close AE00.**

## Rishi-in-the-loop moments (what to look for)

- **The bake-off** (W&B `ask-eval` runs): execution accuracy first, then p95 latency and cost. Check the per-question table for the trick questions: a model that "answers" an impossible question is worse than one that refuses.

## Exit criteria (+ how to verify)

- The guard's tests pass (every probe from README §2 refused or bounded).
- `nfl ask` answers the golden set at the chosen model's measured accuracy (the session log has the number).
- The chosen model is in `settings.yaml`, and the digest's `llm:` block is unchanged.
- Tests and ruff pass; the production-unchanged check passes.

## Pitfalls / notes

- **Don't add indexes or nodes to the weekly graph** for Ask (no full-text index, no build-info node): names are resolved in Python, and "as of" comes from the week's `graph_results.json`.
- **Prompt size:** the full schema of both sources is long. Filter it to the labels and tables the resolved names and words point to (schema filtering helps small models most).
- **Costs are tiny but real:** log every call's cost to the eval runs, and show it in AE01.
