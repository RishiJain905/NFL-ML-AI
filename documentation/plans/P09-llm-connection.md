# P09: Connect a Real LLM

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P04 (can happen **any time after P04**, whenever Rishi is ready)
- **Unlocks:** nothing (quality upgrade to the digest prose)
- **Read first:** [06 → LLM provider interface, Prompt structure, Automated checks](../06-weekly-digest.md#llm-provider-interface)

> **Partly done in P04 (2026-10-03, D56).** At Rishi's request the `openrouter` provider is already connected and the default: `z-ai/glm-5.3-flash`, reasoning `max`, routed to the cheapest of `baseten/fp8` / `relace` / `novita/fp8` / `deepinfra/fp4`. It has retries, sanitized errors, partial-reply rejection, usage/cost/latency logging, `nfl doctor` reachability, and the regenerate-once flow with feedback. On the final round of 2025 backtests (2025 weeks 4/8/9/14) it passed every check on the first try in 4 of 4, and the first live digest passed too. What's left here: an `anthropic` / `openai_compatible` adapter if ever wanted, Rishi's side-by-side review, prompt iteration, and latency (19–21 minutes per live digest at max effort).

## Goal

Replace the placeholder template writer with a real LLM of Rishi's choosing (a Claude model, or an open-source model behind an OpenAI-compatible endpoint), **changing only config**, with every check still enforced.

## Scope

- **In:** provider adapters, prompt tuning on backtest weeks, check calibration for real prose, the regenerate-once flow with real feedback, cost and latency logging.
- **Out:** the Q&A agent over the graph (STRETCH).

## Tasks

- [ ] ✋ **Checkpoint: Rishi picks the provider and model**, and adds `LLM_API_KEY` / `LLM_BASE_URL` to `.env`.
- [ ] 🤖 `digest/llm/anthropic.py` and/or `digest/llm/openai_compatible.py`, implementing `LLMClient`. Structured output: JSON `{section_id: markdown}`. Timeouts, retries, token / latency logging to W&B.
- [ ] 🤖 Regenerate-once: pass the failed checks back as a structured "fix these" message.
- [ ] 🤖 `nfl doctor` checks that the configured provider is reachable.
- [ ] 🤖 Generate digests for the same 3–4 2025 backtest weeks used in P04, so placeholder and real-LLM versions can be compared side by side.
- [ ] 🧑 **Rishi reviews** the real-LLM backtest digests (same 1–5 rubric as P04) and suggests changes to tone and prompt.
- [ ] 🤖 Prompt iterations (each version hashed and logged to W&B `digest-dev`). Loosen or tighten check normalization if real prose triggers false positives (without weakening what the checks guarantee).
- [ ] 🧑 **Rishi runs** the first live weekly digest with the real LLM.
- [ ] ✋ **Checkpoint:** switch `llm.provider` in `settings.yaml` for production and close P09.

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
