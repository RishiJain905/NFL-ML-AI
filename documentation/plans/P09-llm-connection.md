# P09: Connect a Real LLM

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P04 (can happen **any time after P04**, whenever Rishi is ready)
- **Unlocks:** nothing (quality upgrade to the digest prose)
- **Read first:** [06 → LLM provider interface, Prompt structure, Automated checks](../06-weekly-digest.md#llm-provider-interface)

> **Partly done in P04 (2026-10-03, D56).** At Rishi's request the `openrouter` provider is already connected and the default: `z-ai/glm-5.3-flash`, reasoning `max`, routed to the cheapest of `baseten/fp8` / `relace` / `novita/fp8` / `deepinfra/fp4`. It has retries, sanitized errors, partial-reply rejection, usage/cost/latency logging, `nfl doctor` reachability, and the regenerate-once flow with feedback. On the final round of 2025 backtests (2025 weeks 4/8/9/14) it passed every check on the first try in 4 of 4, and the first live digest passed too. What's left here: an `anthropic` / `openai_compatible` adapter if ever wanted, Rishi's side-by-side review, prompt iteration, and latency (19–21 minutes per live digest at max effort).
>
> **Closed 2026-10-05 (D87).** No adapters (Rishi's choice); GLM re-checked on today's full digest over three fact-checked backtest rounds; the prompt, the regeneration message, two checks, several payload strings, the routing and `nfl doctor` tuned. See "As built" at the end.

## Goal

Replace the placeholder template writer with a real LLM of Rishi's choosing (a Claude model, or an open-source model behind an OpenAI-compatible endpoint), **changing only config**, with every check still enforced.

## Scope

- **In:** provider adapters, prompt tuning on backtest weeks, check calibration for real prose, the regenerate-once flow with real feedback, cost and latency logging.
- **Out:** the Q&A agent over the graph (STRETCH).

## Tasks

- [x] ✋ **Checkpoint: Rishi picks the provider and model**, and adds `LLM_API_KEY` / `LLM_BASE_URL` to `.env`. *(P04, D56: OpenRouter + `z-ai/glm-5.3-flash`, key `OPENROUTER_API_KEY`; the `LLM_*` variables stay reserved, D87.)*
- [x] 🤖 `digest/llm/anthropic.py` and/or `digest/llm/openai_compatible.py`, implementing `LLMClient`. Structured output: JSON `{section_id: markdown}`. Timeouts, retries, token / latency logging to W&B. *(Done as `digest/llm/openrouter.py` in P04. Rishi chose **neither** native adapter at the P09 kickoff (D87): OpenRouter serves Claude and open models by config; the registry now says so and `nfl doctor` FAILs on them.)*
- [x] 🤖 Regenerate-once: pass the failed checks back as a structured "fix these" message. *(P04: first draft + offending tokens + every section's word count and the whole-digest cap.)*
- [x] 🤖 `nfl doctor` checks that the configured provider is reachable. *(P04: key accepted + model listed; **P09 adds**: at least one `llm.openrouter.only` endpoint serves the model.)*
- [x] 🤖 Generate digests for the same 3–4 2025 backtest weeks used in P04, so placeholder and real-LLM versions can be compared side by side. *(2025 weeks 4 / 8 / 9 / 14 with today's full digest, placeholder and GLM side by side, three rounds; see "As built".)*
- [x] 🧑 **Rishi reviews** the real-LLM backtest digests (same 1–5 rubric as P04) and suggests changes to tone and prompt. *(Rishi reviewed GLM's backtests in P04 ("really really good outside of the players to watch section"); at the P09 kickoff he chose "P04 counts; agents re-verify": an opus-high fact-check scored every P09 backtest on the same rubric.)*
- [x] 🤖 Prompt iterations (each version hashed and logged to W&B `digest-dev`). Loosen or tighten check normalization if real prose triggers false positives (without weakening what the checks guarantee). *(`325b85a78f4e` → `8e36d33c11d3` → `78d676ccaceb` → `1c27f929b4d0` → `0eb81af95a6c` → `37d179c4b93a` → `0ee03e0cd26e`; check fixes to the sentence splitter, the name heuristic and the hedge phrases; no check loosened.)*
- [x] 🧑 **Rishi runs** the first live weekly digest with the real LLM. *(2026 week 4, delegated by Rishi in P04; the tuned prompt goes live with Tuesday's week-5 run.)*
- [x] ✋ **Checkpoint:** switch `llm.provider` in `settings.yaml` for production and close P09. *(`openrouter` has been production since P04; the close was waived by the kickoff ("mark the phase complete").)*

## Rishi-in-the-loop moments: what to look for

- **Check failure rate across backtests:** a good model should pass first time most of the time. Repeated number-provenance failures mean the prompt must stress "use display strings exactly".
- **Tone:** does it read like "a smart friend who watches more film than you"? Is it too hype-y or too dry?
- **Hedging:** do low-confidence items actually sound uncertain?

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| Swapping provider = config only | `git diff` of the switch touches only `settings.yaml` (+ `.env`) |
| Checks pass first time on ≥ 80% of backtest digests | W&B `digest-dev` runs |
| Rishi prefers it to the placeholder | Review notes in PROGRESS |
| A live digest published with the real LLM | Report on D: + `weekly-pipeline` run |

## Pitfalls / notes

- Smaller open-source models may struggle with strict JSON output. Use the server's JSON / grammar mode if it has one, or a lenient parser plus a fallback.
- Never let the LLM see raw numeric values without display strings. Keep the payload as it is.

## As built: deviations from the task list (closed 2026-10-05)

P09 found the real LLM already connected (P04, D56) and turned into a **re-check of GLM on today's much larger digest** (the P05 graph sections, the P06 player model with 10 + 10 picks, the P08 columns and GDS items; the prompt grew from ~8k to ~22k tokens). Decisions: D87.

**Kickoff choices (AskUserQuestion):** P04's review and live run count for the 🧑 / ✋ steps and agents re-verify; **no native adapters**; **no lower-effort trial** (`reasoning_effort` stays `max`).

**What was built or changed**

| Piece | Change |
|---|---|
| `digest/llm/__init__.py`, `base.py` | `anthropic` / `openai_compatible` are `NOT_BUILT`: "isn't built (D87). Use llm.provider: openrouter with that model's OpenRouter name, or placeholder." |
| `doctor.py` | The `llm` row FAILs for a provider that isn't built or is unknown, and checks that at least one `llm.openrouter.only` entry serves the model ("K of M allowed ones listed") |
| `config/settings.yaml` | `deepinfra/fp4` dropped from `only` (0 reasoning tokens on 3 of its 5 calls); `max_tokens` 96,000 |
| `digest/prompt/system.md`, `sections.yaml` | Seven versions, ending at **`0ee03e0cd26e`** (rules 2, 12, 22 and the format rules; the players, report card, game outlook, trends and under-the-hood instructions; placeholder-only examples) |
| `digest/llm/openrouter.py` | The regeneration message keeps passing sentences, adds a missing owner instead of deleting, removes a number that isn't the named owner's, trims whole sentences only |
| `digest/checks.py` | Sentence split after a closing quote / bracket (`_split_closed`: runs of marks, abbreviation / initial guards, a sentence-start lookahead, the Jr. / Sr. opener rule); the name heuristic reads initials and inner capitals and compares names without periods; "confidence is low" is a hedge |
| `digest/format.py`, `build.py`, `players.py`, `llm/placeholder.py` | No "-0.00"; `pct_points_change` ("up 7.0 points"); pressure evidence says what it means; count items with their unit (`count_stat`, "1.5 pressures per game"); drivers as plain clauses, " (not his own baseline)" on one that pushes against the pick's gap; notes that carry his numbers name the player (`own_note`) |
| Tests | `tests/test_doctor_llm.py` (new) and new cases in the digest tests |

**The backtest rounds** (2025, GLM via OpenRouter, W&B `digest-dev`; files in `runs/digest-backtests/2025/`, reports in `reports/backtests/2025/`, the P04-era reports kept in `archive-pre-p09/`)

| Round | Prompt (code) | 2025 w4 | w8 | w9 | w14 | First time |
|---|---|---|---|---|---|---|
| 1 | `325b85a78f4e` (P08) | regenerated, passed (`9zgdsr8u`, 18 min) | regenerated, passed (`2pcrkemo`, 9 min) | regenerated, passed (`in9l3civ`, 35 min) | regenerated, passed (`rvoz28rs`, 19 min) | **0 of 4** |
| 2 | `8e36d33c11d3` (w4, w8), `78d676ccaceb` (w9), `1c27f929b4d0` (w14) | first time (`rt845e9p`, 13 min) | **banner** after the regeneration: GLM's own "in 1 game" (`0m8xytqj`, 26 min) | length, then passed (`nyroja5e`, 20 min) | report-card length, then passed (`ibyeovtp`, 14 min) | 1 of 4 |
| 3 | `0eb81af95a6c` (w4, w8), `37d179c4b93a` (w9) | first time (`tyaammhp`, 14 min) | first time (`zoh82b5v`, 12 min) | one "his" orphan, then passed (`ffql6n5p`, 37 min) | stopped (superseded) | 2 of 3 |
| **5 (final code)** | `37d179c4b93a` (w4, w8), **`0ee03e0cd26e`** (w9, w14); notes that name their player | **first time** (`vl9a23xc`, 16 min, $0.012) | **first time** (`lt0ucwnb`, 19 min, $0.015) | **first time** (`navpqgvu`, 3 min on BaseTen, $0.021) | **first time** (`eta3qyx8`, 21 min, $0.012) | **4 of 4** |

The fact-check scores (would I read this / did I learn something / nothing wrong or invented) went from **3 / 3 / 4** in round 1 to **4–5 / 4 / 5** for the prose in round 5; the only page-level deduction left is the backtest-only injury wording (D66). Every round had no fallback; no regeneration ran on an endpoint that skipped the reasoning after DeepInfra left the list. Round 4 (weeks 4 and 8 on `37d179c4b93a`) was folded into round 5.

**Exit criteria**

| Criterion | Result |
|---|---|
| Swapping provider = config only | Shown without touching the file: `llm.model` changed in memory to `anthropic/claude-sonnet-5.5` → the client sends that model with no code change; `nfl doctor` FAILs while GLM's `only` list is kept and passes once it's removed ("8 endpoints"); `llm.provider: anthropic` → doctor FAIL + `ProviderNotConnected` |
| Checks pass first time on ≥ 80% of backtest digests | **Met: 4 of 4 (100%) on the final code** (round 5: no regeneration, no fallback; the one warning, a hedge false positive in week 4, is fixed). Round 1 on the P08 prompt was 0 of 4. Over every P09 GLM digest the first-time rate was 7 of 15, rising with each version |
| Rishi prefers it to the placeholder | GLM has been production since P04 by his choice; the P09 fact-check rates the final GLM digests above the placeholder (see the rounds) |
| A live digest published with the real LLM | 2026 week 4 (P04 run `vvit9fdd`, P05 `o0skjazq`, P06 `0eh6h6ll`); the tuned prompt goes live with week 5 |

**Not done / for later:** a native adapter (not wanted); a lower-effort trial (skipped by Rishi; latency stays 13–35 min per digest); backtest digests word the P06 injury note as "this week's injury report" although a backtest uses the week's final Friday report (D66), which contradicts the Tuesday "Starters out" note on the same page (backtests only); graph items that repeat team-trend numbers or rank a "better without him" ripple as a risk (insight ranking, not the writer).
