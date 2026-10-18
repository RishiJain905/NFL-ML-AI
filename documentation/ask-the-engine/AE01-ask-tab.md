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
- [x] 🤖 **Mockup**, both themes ([Ask the Engine Mockup](https://claude.ai/artifact/9vCLeQ3eZ6czTeU1dki2TU), v4; source `mockup/`, from the real API's answers):
  - the question box with example chips;
  - an answer card (answer, rows table, a collapsible query block with copy, the source chip "weekly graph as of 2026 week 6", route "template: q6_former_teammates" or "free-form, 1 retry", time and cost);
  - a refusal card ("I can only read data; that question asks to change it");
  - an ambiguous-name card ("Which Josh Allen?");
  - the session list.
- [x] ✋ **Rishi approves the mockup.** Waived by Rishi at the kickoff (2026-10-18: "Waive it, keep going").

### Backend
- [x] 🤖 **`POST /api/ask`** `{question, source?: auto|graph|sql}` (+ `picks` for a which-one card, D142):
  - the launch token and `Origin` (like every POST);
  - 500-character cap;
  - one question at a time per app (409 while one runs);
  - about 10 a minute.

  Runs AE00's route → guard → answer. Returns answer, rows (scalar columns only), columns, query, language, source and as-of, route, retries, latency, cost. Errors are fixed text (never the provider's raw message); every text goes through `clean()`.
- [x] 🤖 **`GET /api/ask/examples`:** example questions from the template library (+ each source's status and freshness, the model, the limits, the lock).
- [x] 🤖 **Neo4j down:** graph questions say so and SQL questions still work (the guard's `source.down`). LLM down: "the model didn't answer", with the route and provider.
- [x] 🤖 **Tests:** (`tests/app/test_app_ask.py`, `test_app_ask_redteam.py`, `tests/ask/test_ask_picks.py`, `tests/test_safetext.py`)
  - the endpoint with a fake LLM (template route, free-form, a refusal, an error retry, an impossible question);
  - token and Origin required;
  - caps and rate limit;
  - nothing written outside `cache/control-room/`;
  - **the response scanned for key-shaped strings** (the existing test extended);
  - the existing endpoints unchanged.

### Web
- [x] 🤖 **Sidebar → Explore → Ask the Engine** (the Explore group exists after PC01, or this phase creates it); route `/explore/ask`.
- [x] 🤖 **The page** as in the mockup; the session's questions in `localStorage` (try / catch, works without it); keyboard-friendly; the rows table with TanStack Table; the query block monospace with copy.
- [x] 🤖 **Web tests** for each card state (`web/src/pages/explore/AskPage.test.tsx`).

### Verify and close
- [x] 🤖 **Run the golden set through the API** (not just the CLI); same accuracy (`nfl ask eval --via api`: main 94.0%, held-out 91.2%; AE00 94.0-96.4% / 88.2%).
- [x] 🧑 **Rishi asks it 10–20 of his own questions** and notes the misses. Misses become golden-set questions or new templates. **Rishi at the kickoff: "agent stands in, you try later"**: 18 fresh questions + a which-one follow-up through the API, checked by hand: 16 right, 2 misses fixed (ties at a leader template's cut → golden g116; the number check refusing "40" of "the first 40 of 54 rows"). Rishi's own round is on PROGRESS's Next step.
- [x] 🤖 **Mockup parity;** a Codex / Sol review focused on the free-text endpoint (prompt injection through question text or row text, guard bypasses, leaks); fixes with tests; a decision-log entry. (Parity: every card state, Turf light / dark and 390 px, four small fixes, mockup v4. Sol: 2 high + 2 medium, a verification pass, all fixed: D143.)
- [x] 🤖 **Docs:** the guide `ask-the-engine.md` (the page walkthrough, good questions to ask, what it can't do); `guides/control-room.md`; the `control-room` skill (the free-text rule).
- [x] 🤖 Production-unchanged check; the CR test suite passes unchanged.
- [x] ✋ **Close AE01.** Waived by Rishi's kickoff ("marking the phase complete").

## Rishi-in-the-loop moments (what to look for)

- **Your own questions:** right answer? Readable query? Fast enough to feel like chat (aim: under 5 s typical)? Did it ever make up a number (it shouldn't: the provenance check)?

## Exit criteria (+ how to verify)

- The page answers the golden set at AE00's accuracy through the API.
- Rishi's own-question round is done, with misses turned into tests or templates.
- Safety tests pass; the existing app is unchanged; tests, ruff, web checks and the production-unchanged check pass.

## Pitfalls / notes

- **Prompt injection through data:** a player's name or a play description can't instruct anything, because the answer step has no tools and only formats rows. Keep it that way: never let the answer step trigger a second query.
- **Long-running questions:** the 10 s query timeout and the LLM timeout bound a question at about 30 s. The page shows elapsed time and a Cancel button (which only stops waiting).

## As built: deviations from the task list

- **"Which one?" is answered on the page:** `POST /api/ask` takes `picks: [{text, kind, value}]` (≤ 5); the engine accepts a value only if it's one of the choices it offered for that name (or names exactly one entity), and a template value repeating the name gets the picked id (D142).
- **Neo4j down** is the guard's new rule `source.down` (the app's 30 s check, or the driver failing to connect): the question stops at once with a fixed sentence, `refusal.by = "source"`; the weekly lock taken mid-question stops it the same way.
- **The engine is built once and refreshed before each question** (today, the graph's week, the curated dates, the names), since the app runs for days; a 16 KB body cap read from the stream; the 429 carries `Retry-After`.
- **A bounded scrub** (`src/nflengine/safetext.py`): the review found `ops.summary.scrub` quadratic on a long word (a legal query result could hold the app for hours); `ops/` is protected (D107), so the new code uses `scrub_bounded`, and the fix to the original is on Rishi's list.
- **Changes to AE00's layer the round found:** the answer prompt's "answer as to a fan" rule (the prompt hash changed: `nfl ask pick` compares runs after it); the number check allows the count of rows the model was shown; the five SQL leader templates keep ties at the cut; golden g116 (100 questions).
- **`nfl ask eval --via api`** is the repeatable form of "the golden set through the API" (`via: api` runs never enter `nfl ask pick`).
- **The review:** the test worker (4 bugs: lost call costs on an error card, **the quadratic `scrub()`**, pick text adding prompt lines, a contract-test collision) and Sol (2 high, 2 medium: secrets half-shown at the scrub's cut, huge list cells after the query timer, no body-read deadline, the build before the lock check), then Sol's verification (3 not fully closed + an ENUM label read as a type): all fixed with tests (D143); kept: the instant between a run taking the lock and writing its note
