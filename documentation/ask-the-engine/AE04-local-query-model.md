# AE04 (optional): A local text-to-query model

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Ask the Engine rows). **Optional:** start only if Rishi wants the ML experiment.

- **Depends on:** AE00 (the golden set, the eval harness, the `openai_compatible` provider)
- **Unlocks:** a free, private, fast Ask model, if it's good enough
- **Read first:**
  - [Ask the Engine README](README.md) §1 (the JEPA note);
  - LLM-JEPA, Huang, LeCun & Balestriero ([arXiv 2509.14252](https://arxiv.org/abs/2509.14252), ICLR 2026; code at github.com/galilai-group/llm-jepa): a training objective that adds a JEPA term (predict the embedding of one view, the query, from another, the question) to the normal next-token loss. It was tested on text-to-SQL (Spider) and regex generation (NL-RX), with gains over plain fine-tuning and less overfitting, at the cost of extra forward passes and two hyperparameters;
  - the `model-experiment` skill.

## Goal

Fine-tune a small open model (about 1–4B parameters) to turn our questions into our Cypher and SQL, once with plain fine-tuning and once with the LLM-JEPA objective. Serve the better one locally behind an OpenAI-compatible endpoint and score it in AE00's bake-off against the OpenRouter models. It's a real ML experiment with a clear yes / no.

## Scope

- **In:**
  - a training set;
  - two fine-tunes (LoRA) with W&B tracking;
  - local serving;
  - the bake-off entry;
  - a model card.
- **Out:** replacing the chosen OpenRouter model unless the bake-off says so (✋).

## Tasks

- [ ] 🤖 **Hardware check:** the GPU, VRAM and drivers; whether LoRA on a 1–4B model fits. If not, stop and report: this phase is optional.
- [ ] 🤖 **Training data:**
  - questions generated from the template library (many phrasings per template, filled with real names and seasons), paired with their filled queries;
  - plus a held-out split of hand-written questions;
  - **the golden set is never trained on** (it's the test).

  Every training query is run through the guard and against the data, so it's valid.
- [ ] 🧑 **Fine-tunes** (Rishi runs, or delegates), live in W&B (`job_type: ask-finetune`):
  - (a) LoRA with next-token loss;
  - (b) the same with the LLM-JEPA term (a small λ / k sweep, as the paper describes).

  Charts: train / validation loss, execution accuracy on the validation split per epoch.
- [ ] 🤖 **Serve** the best checkpoint locally with an OpenAI-compatible server (llama.cpp, vLLM or Ollama: whichever runs on this machine), over HTTPS on localhost if the server supports it. Point `ask.llm` at it with `provider: openai_compatible` (`LLM_BASE_URL`, `LLM_API_KEY`: existing names).
- [ ] 🧑 **Bake-off entry:** `nfl ask eval --model local:<name>` on the golden set: execution accuracy, latency, cost (0).
- [ ] ✋ **Decide:** keep the OpenRouter model, switch to the local one, or use local for templates and OpenRouter for free-form. Log the decision.
- [ ] 🤖 **Docs:**
  - model card `model_cards/ask-local-model.md` (base model, data, both objectives, results vs the bake-off, limits);
  - the Ask guide (running the local server, switching providers);
  - the W&B guide (`ask-finetune`).
- [ ] ✋ **Close AE04.**

## What to look for

- **JEPA vs plain** at the same budget: the paper reports gains on Spider. On our small, narrow set, check that it isn't just noise across 2–3 seeds.
- **Local vs OpenRouter:** a local model that's 10 points less accurate but free and private may still be the right call for template questions.

## Pitfalls / notes

- **Overfitting to templates:** a model trained on template phrasings can look perfect on them and fail on real questions. The hand-written held-out split and the golden set guard against that.
- **Never train on the golden set**, or the bake-off means nothing.
