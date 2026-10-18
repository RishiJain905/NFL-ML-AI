# Ask the Engine mockup (the visual spec for AE01's page)

**Live mockup:** <https://claude.ai/artifact/9vCLeQ3eZ6czTeU1dki2TU> ("Ask the Engine Mockup", published 2026-10-18; v4: v2 kept short table text and list chips on one line, v3 wraps long query lines, v4 the parity fixes (the box's margin, the sort hint only for 2+ rows, the session list's screen-reader words); private to Rishi's account, agents read it with the Artifact tool's `read` action; Rishi waived the ✋ approval step). To update it: rebuild, check it, copy the HTML to a folder on C: (the session scratchpad: the Artifact tool doesn't publish from D:) and publish that copy to the same URL. The spec that goes with it is [`../AE01-ask-tab.md`](../AE01-ask-tab.md) and [`../README.md`](../README.md) §3 and §5; what the answers mean is in the [guide](../../guides/ask-the-engine.md).

The page is drawn inside the control room's shell: the same sidebar (the **Explore** group gets **Ask the Engine** after Play calling), header, tokens and both themes × modes under the gear. This folder holds the source, so the design is versioned with the docs:

| File | What it is |
|---|---|
| `template.html` | The page skeleton with four placeholders (`{{CSS}}`, `{{ASKCSS}}`, `{{JS}}`, `{{DATA}}`) and the striped "Mockup controls" strip |
| `ask.css` | The page's own styles (the question box, the examples, the status line, banners, the running card, the answer card, the rows table, the query block, the refusal / which-one / no-rows / error cards, the session list). Tokens only; every class starts with `ask-`. The control room's [`styles.css`](../../control-room/mockup/styles.css) is inlined unchanged before it. The React page's `web/src/pages/explore/ask.css` is a copy: keep the two in step |
| `ask.js` | The whole mockup in plain JavaScript: the shell, the page, every card state, the session list, tooltips on hover and focus |
| `dump_payloads.py` | Writes the payloads: the real app API's answers, in-process, with the real model (see its docstring: about ten real questions, ~$0.01 of OpenRouter credit, so don't re-run it casually) |
| `build_mockup.py` | Reads the payloads, embeds them, and writes one HTML file. It stops and names any missing key. No model call |

## Rebuild it

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run python documentation/ask-the-engine/mockup/build_mockup.py                     # payloads.json -> ask.html
uv run python documentation/ask-the-engine/mockup/build_mockup.py --payloads <file>   # any other payloads file
```

- **What the build reads:** `{NFL_DATA_ROOT}/cache/ask-mockup/payloads.json` (or `--payloads`) and the control room's `styles.css`.
- **What it writes:** `{NFL_DATA_ROOT}/cache/ask-mockup/ask.html`. Nothing else, and no data goes into git.
- **Viewing it locally:** Python's `http.server` can't serve files from D: on this machine. Copy the HTML to a folder on C: (a scratchpad), serve that (`python -m http.server <port> --bind 127.0.0.1`) and open `http://127.0.0.1:<port>/ask.html`.

## The payloads file

The API's answers in the shapes of `web/src/api/types.ts` (AE01 block), so the React page reuses the mockup's structure:

| Key | What |
|---|---|
| `meta`, `weeks`, `team_info` | The shell's answers: the sidebar's week rows are the real ones |
| `examples`, `examples_graph_down` | `GET /api/ask/examples` (`AskExamplesResponse`): the chips, the "More examples" groups, the sources' freshness, Neo4j's state, the model and the limits; the second with Neo4j reported down |
| `answers.<key>` | `{request, http_status, body: AskResponse}` from a real `POST /api/ask`: `template_graph` (qb_receivers), `template_sql` (head_to_head), `freeform` (a generated SQL query), `lists` (coaching_tree: list cells), `refused_code` (a write request), `refused_router` (not NFL), `clarify` (Michael Thomas: which one?), `clarify_picked` (the follow-up with `picks`), `empty` (no rows), `graph_down` (Neo4j down mid-question), `llm_down` (the model's endpoint answered 503 from a mock transport) |
| `errors.<key>` | `{http_status, body: {error}}`: `busy` (409), `rate_limited` (429), `too_long` (422) |

**Wording updated by hand:** the backend's text changed after the 2026-10-18 dump (`refusal.by` "source" for a stop mid-question, the model-error, 429 and 422 messages). `ask.js` patches those few strings at load time (the `today()` block) rather than spending credit on a re-dump; drop the block after the next dump.

## What the mockup shows

The striped **Mockup controls** strip (not part of the app) picks the **Answer card** (every state below), a **Request refused** notice (409 / 429 / 422), the **Status** (Normal, Graph down, Weekly refresh running) and the **Session** (this session's questions, or empty). The sidebar's other items show a stand-in notice.

- **The header:** "Explore · Ask the Engine" and a status line: each source's freshness label ("weekly graph as of 2026 week 6 · Neo4j up", "curated data through 2026 week 5") with a status dot, and the model (label · effort). Each item is focusable with a tooltip (what the source is, when it's rebuilt, the model's provider, timeout and the limits).
- **Banners** above everything: the graph down ("Graph questions can't be answered now; questions about the tables still work."), the weekly refresh running (`busy`: questions are refused until it ends).
- **The question box:** a textarea (16 px, so phones don't zoom), **Enter** asks, **Shift+Enter** is a new line; a character counter "41 / 500" (amber past 90%, red and "too long" past 500, which also disables Ask); the source picker **Auto · Weekly graph · Tables** ("(down)" beside Weekly graph while Neo4j is down; still pickable); the **Ask** button. **Try one:** the featured chips (`featured`, "this week" on this-week graph templates); **More examples (49)** opens the `groups` (The weekly graph · The curated tables) as lists. A chip or an example **fills the box and focuses it** (it doesn't ask): most examples are a name or a team away from the question you want.
- **Running:** the question in quotes, three pulsing dots, the elapsed seconds ticking, **Cancel** (also **Esc**) and the note that Cancel only stops waiting: the server finishes the question and logs its cost. In the mockup, asking one of the dumped questions plays its real answer back after its own time (capped at 4 s).
- **The answer card:** "Answer" and the question as the card's heading (focus moves there when an answer arrives), the time it was asked; the answer sentences big; **✓ Every number checked against the rows** when `answer_kind` is "model" (tooltip: the number check); for "fallback" a notice that the model's sentences didn't pass the number check, so only the server's fixed line and the table are shown. Chips: **Source** (`source.label`), **Route** (`route_label`; the template's title and what a template means on hover / focus; free-form says the guard checked the model's query, its limits and the retries, and is tinted), **Time** (`timing.total_s`; hover / focus: route, write the query, run the query, write the answer), **Cost** ($ to 4 decimals; "no model call" for 0, "cost unknown" for null; hover / focus: each model call's purpose, provider, seconds and cost). **The rows:** "Showing N of M rows" (and "more rows existed than the 500-row cap" when `truncated`), a table that scrolls inside its card (sticky headers, 440 px tall at most), headers are sort buttons (ascending → descending → original; `aria-sort`), numeric columns right-aligned, list cells as small chips (8, then "+N"). **The query** (collapsed): the language tag (Cypher / SQL), **Copy**, the query in monospace with ligatures off (JetBrains Mono would draw `->` as an arrow), and the **bound values** (`$player = 00-0033873`).
- **Refused:** "Not answered", a stamp (READ ONLY for `write`, NFL ONLY for `not_nfl`, GRAPH DOWN, REFRESHING, otherwise REFUSED), `refusal.message` as the heading and who refused it in plain words: `code` → "Refused before any model call"; `source` → "Stopped: the data source isn't available (the graph is down)" / "the weekly refresh started"; `router` → "Refused by the router (the model's first step)"; `generator` → "Refused by the model while writing the query"; `guard` → "Stopped by the guard: <rule>"; then the rule, the time and cost chips.
- **Which one?** "Needs a choice", "Which Michael Thomas?", the server's sentence and one button per option label ("Michael Thomas (WR, NO, 2016-2023)"). A button re-asks the same question with `picks: [{text, kind, value}]` (the mockup plays the real follow-up for the first option).
- **No rows:** "No rows matched.", a hint (a wider season range, another spelling, another source), the columns the query would have returned, the source / route / time / cost chips and the query.
- **Error:** "The model didn't answer" (`llm`), "Out of credits" (`out_of_credits`), "The query kept failing" (`query_failed`, with the last query tried) or "Something went wrong"; the message and a short list: stage (in words), provider, model, code.
- **Request refused** (inline, above the answer, with Dismiss): 409 "Still finishing the last question.", 429 "Too many questions in a minute." with the server's message (how long to wait), 422 with the server's message. The page keeps the last answer under it.
- **The session list** (right rail; under the page below 1100 px): "This session · N questions", newest first, each a button with a status dot (answered green, no rows grey, refused amber, needs a choice accent, error red; the status in words for screen readers), the question (two lines at most), the time and the route; the shown one is highlighted. A click shows that answer again. **Clear session**; "Kept in this browser only" (the app: `localStorage` `cr.askSession`, capped at 50 questions).
- **Nothing asked yet:** a short card saying what an answer comes with.

## What's real and what's mockup-only

- **Real (the API's answers):** every answer, number, query, label, message and status, and the examples.
- **Mockup-only (each says so on the page, marked * in the controls):** "Free-form, 1 retry" (the real free-form answer with `retries: 1`), "Number check failed" (a real answer drawn as `fallback` with the server's own fixed sentence), "Refresh running" (a refusal with `source.busy`), "Out of credits" and "Query kept failing" (the real error answer with the server's fixed texts), the Weekly refresh running status (the real status with `busy: true`), and the running card's playback.

## What the real data changed

- **A refusal by `code` isn't always "before any model call":** the first dump's graph-down answer had `by: "code"` after a route call. The backend now says `by: "source"` for a stop mid-question, and the card words the two apart.
- **List cells are common** (a coaching tree's roles, teams and seasons): small chips, so a five-season run stays one line.
- **Wide tables:** the player-seasons template returns 19 columns: the table scrolls inside its card, never the page.
- **Model sentences sometimes described the plumbing** ("The query returned one row."): the answer prompt now tells the model not to, so real answers read more directly than the dumped ones (no page change).
- **Times range from 0 s (a refusal in code) to 18 s (free-form):** "under 0.1 s", two decimals under 1 s, one under 10 s.
- **JetBrains Mono's ligatures** turned Cypher's `->` into an arrow: off in the query block.

## Changing the design

Design changes start here:
1. Edit the source and rebuild.
2. Republish to the same URL.
3. Update `../AE01-ask-tab.md` (and `../README.md` §3 if a screen changed).
4. Change the code (`web/src/pages/explore/AskPage.tsx`, `ask/`, `ask.css`).
