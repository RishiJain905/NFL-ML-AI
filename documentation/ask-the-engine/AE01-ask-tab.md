# AE01: The Ask the Engine page

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Ask the Engine rows)

- **Depends on:** AE00 (the query layer and the chosen model), CR03 (the finished control room)
- **Unlocks:** AE03's Connections and Graph lab sit beside it
- **Read first:**
  - [Ask the Engine README](README.md) §1, §3, §5 (all the safety rules: this is the app's first free-text endpoint);
  - the `control-room` skill and the [control room README](../control-room/README.md) §5 and §7.

## Goal

**Explore → Ask the Engine** in the sidebar. Rishi types a question and, in a few seconds, gets:
- a short answer;
- the rows behind it;
- the query that produced it;
- where the data came from and how fresh it is;
- what it cost.

Questions and answers from the session stay in the browser. It covers the weekly graph and the curated data; the history graph joins in AE02.

## Scope

- **In:**
  - a mockup (✋ first);
  - `POST /api/ask` and `GET /api/ask/examples`;
  - the page;
  - the safety review of the free-text endpoint;
  - the guide section.
- **Out:** Connections and Graph lab (AE03); saving questions on the server (not planned: browser only).

## Tasks

### Mockup first
- [ ] 🤖 **Mockup**, both themes:
  - the question box with example chips;
  - an answer card (answer, rows table, a collapsible query block with copy, the source chip "weekly graph as of 2026 week 6", route "template: q6_former_teammates" or "free-form, 1 retry", time and cost);
  - a refusal card ("I can only read data; that question asks to change it");
  - an ambiguous-name card ("Which Josh Allen?");
  - the session list.
- [ ] ✋ **Rishi approves the mockup.**

### Backend
- [ ] 🤖 **`POST /api/ask`** `{question, source?: auto|graph|sql}`:
  - the launch token and `Origin` (like every POST);
  - 500-character cap;
  - one question at a time per app (409 while one runs);
  - about 10 a minute.

  Runs AE00's route → guard → answer. Returns answer, rows (scalar columns only), columns, query, language, source and as-of, route, retries, latency, cost. Errors are fixed text (never the provider's raw message); every text goes through `clean()`.
- [ ] 🤖 **`GET /api/ask/examples`:** example questions from the template library.
- [ ] 🤖 **Neo4j down:** graph questions say so and SQL questions still work. LLM down: "the model didn't answer", with the route and provider.
- [ ] 🤖 **Tests:**
  - the endpoint with a fake LLM (template route, free-form, a refusal, an error retry, an impossible question);
  - token and Origin required;
  - caps and rate limit;
  - nothing written outside `cache/control-room/`;
  - **the response scanned for key-shaped strings** (the existing test extended);
  - the existing endpoints unchanged.

### Web
- [ ] 🤖 **Sidebar → Explore → Ask the Engine** (the Explore group exists after PC01, or this phase creates it); route `/explore/ask`.
- [ ] 🤖 **The page** as in the mockup; the session's questions in `localStorage` (try / catch, works without it); keyboard-friendly; the rows table with TanStack Table; the query block monospace with copy.
- [ ] 🤖 **Web tests** for each card state.

### Verify and close
- [ ] 🤖 **Run the golden set through the API** (not just the CLI); same accuracy.
- [ ] 🧑 **Rishi asks it 10–20 of his own questions** and notes the misses. Misses become golden-set questions or new templates.
- [ ] 🤖 **Mockup parity;** a Codex / Sol review focused on the free-text endpoint (prompt injection through question text or row text, guard bypasses, leaks); fixes with tests; a decision-log entry.
- [ ] 🤖 **Docs:** the guide `ask-the-engine.md` (the page walkthrough, good questions to ask, what it can't do); `guides/control-room.md`; the `control-room` skill (the free-text rule).
- [ ] 🤖 Production-unchanged check; the CR test suite passes unchanged.
- [ ] ✋ **Close AE01.**

## Rishi-in-the-loop moments (what to look for)

- **Your own questions:** right answer? Readable query? Fast enough to feel like chat (aim: under 5 s typical)? Did it ever make up a number (it shouldn't: the provenance check)?

## Exit criteria (+ how to verify)

- The page answers the golden set at AE00's accuracy through the API.
- Rishi's own-question round is done, with misses turned into tests or templates.
- Safety tests pass; the existing app is unchanged; tests, ruff, web checks and the production-unchanged check pass.

## Pitfalls / notes

- **Prompt injection through data:** a player's name or a play description can't instruct anything, because the answer step has no tools and only formats rows. Keep it that way: never let the answer step trigger a second query.
- **Long-running questions:** the 10 s query timeout and the LLM timeout bound a question at about 30 s. The page shows elapsed time and a Cancel button (which only stops waiting).
