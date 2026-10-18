"""Ask the Engine (AE00+, documentation/ask-the-engine/): questions in plain English.

A question becomes a query (a template from `config/ask_templates.yaml` when one fits, a
generated Cypher or SQL query otherwise), the guard checks and runs it read-only against the
weekly graph or the curated tables, and a short answer is written from the rows. Modules:

- `config.py`: the `ask:` block of settings.yaml, typed.
- `guard.py`: the Cypher and SQL checks, the DuckDB sandbox, timeouts and row caps.
- `templates.py`: the template library and its typed parameters.
- `names.py`: players, coaches and teams named in a question, resolved against curated data.
- `llm.py`: the Ask LLM provider (OpenRouter or a local OpenAI-compatible server), separate
  from the digest's writer.
- `route.py`: question -> template or generated query -> guarded run, with corrections.
- `answer.py`: the 2-3 sentence answer and its number-provenance check.
- `evaluate.py`: the golden set and `nfl ask eval` (W&B `ask-eval`).

The no-touch rule (D107) applies: Ask only reads the weekly graph and the curated data.
"""
