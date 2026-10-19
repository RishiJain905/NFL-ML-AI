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

---

# Connections and Graph lab (AE03)

**Live mockup:** <https://claude.ai/artifact/JLpvtxrLwAvzxT9DzjzdeD> ("Connections & Graph Lab", published 2026-10-19, v1; private to Rishi's account, agents read it with the Artifact tool's `read` action; to update it: rebuild, copy the HTML to the session scratchpad, publish that copy to the same URL). **The file:** `graphlab.html`, built from this folder's sources and the real app's answers (2026-10-19; Rishi waived the ✋ approval step for AE03, so the React port followed straight away). The spec is [`../AE03-connections-and-graph-ml.md`](../AE03-connections-and-graph-ml.md) and [`../README.md`](../README.md) §3; what the history graph is lives in the [history-graph guide](../../guides/history-graph.md).

Explore → Ask the Engine becomes three parts, a switch under the page title: **Ask** (AE01's page, unchanged, `/explore/ask`) · **Connections** (`/explore/ask/connections`) · **Graph lab** (`/explore/ask/lab/<view>`: `neighbours`, `families`, `chemistry`, `benchmark`). Every view's state is in its URL (`?a=&b=&mode=`, `?player=`, `?family=` / `?person=`, `?min=`), so a link can be shared.

| File | What it is |
|---|---|
| `template_graphlab.html` | The skeleton with five placeholders (`{{CSS}}`, `{{ASKCSS}}`, `{{GLCSS}}`, `{{JS}}`, `{{DATA}}`) and the "Mockup controls" strip |
| `graphlab.css` | The two parts' styles, on top of the control room's `styles.css` and AE01's `ask.css` (both inlined unchanged). Tokens only; every class starts with `gl-`. The React page's `web/src/pages/explore/graphlab/graphlab.css` is a copy: keep the two in step |
| `graphlab.js` | The mockup in plain JavaScript: the shell, the switch, Connections, the four lab views, the map (canvas), the benchmark chart (SVG), tooltips |
| `dump_graphlab.py` | Writes `graphlab.json`: the real app's answers, in-process (no model call, no cost, ~20 s to a minute; it needs the history graph up). Keys: `meta`, `weeks`, `team_info`, `status`, `people.<q>`, `connections.<pair>`, `connection_errors.<code>`, `lab.neighbours.<player>` (`shepley`: Dakoda Shepley, 99 snaps, not in the map), `lab.map`, `lab.families`, `lab.family_of_*`, `lab.chemistry`, `lab.chemistry_300`, `lab.benchmark`; each `{http_status, ms, body}` |
| `build_graphlab.py` | Reads `graphlab.json`, checks every key the page draws (it stops and names what's missing), writes one HTML file |

```powershell
$env:PYTHONIOENCODING = "utf-8"
uv run python documentation/ask-the-engine/mockup/dump_graphlab.py     # the answers -> cache/ask-mockup/graphlab.json
uv run python documentation/ask-the-engine/mockup/build_graphlab.py    # -> cache/ask-mockup/graphlab.html
```

## What it shows

- **The header:** "Explore · Ask the Engine", the three-part switch, and for the two new parts the history graph's label and state (`status.history_state`: up / down / checking / being rebuilt) and the person links' label, each with a tooltip (the links' counts by type, when they were made).
- **Connections:** two person pickers (a combobox over `GET /api/ask/people?q=`: two letters at least, 200 ms after the last key; each match shows the name, position or "coach", team, seasons and how many links; arrows + Enter pick, Escape closes; a picked person becomes a card with ×), a swap button, **Fewest links** / **Strongest links** with one line on what each means, **Connect**. The answer card: "The shortest chain" (or "The strongest chain"), "A to B", the hop count big, chips (mode, the weakest link's snaps, the time, "as of" the person links), then **the chain**: people as pills (players solid, coaches dashed, the two ends ringed in the accent), each link an SVG line coloured and dashed by type (teammates solid `--s2`, coached dashed `--s1`, same staff dotted `--s3`) with its type in words and its evidence text (`links[i].text`). Up to 2 links (3 on a wide screen) the chain runs left to right; longer chains and phones run top to bottom with the evidence beside each link. **Show 3 more chains** (`k=4`) lists the chains compactly (hops, "via …", the link types as small swatches, the weakest link, the cost for strongest); a click draws that one.
- **States:** nothing picked; running (dots, seconds, and for strongest the note that the first search after new links loads them into memory); no chain (`found: false` + the server's `note`); a person with no links (`note`); 503 `history_down` (a banner with the start command); 409 `busy` (Try again); 404 `not_found` / 422 `bad_id` / `same_person` (an inline error).
- **Graph lab:** four tabs. Each view has its "Ran …" chip (tooltip: the derive it ran on, the job's parameters), the W&B run link, the seed, an amber "Made on older person links: re-run `uv run nfl history-graph lab`" when `asof.current` is false, and the not-run-yet card (404 `not_built`, the command in a copyable line).
  - **Neighbours:** a player picker (`kind=player`), the player ("Travis Kelce · TE · KC · 8,761 snaps since 2018"), two ranked lists side by side (**Same position**, **Anyone**): rank, name (a button: picks him), position · team · "N snaps together" or "never on the field together", similarity as a neutral bar 0–1 and its value. Not in the map (`found: false`): a notice saying why. **The map:** 4,272 dots on one canvas (smooth; no SVG needed), coloured by unit (offense `--s2`, defense `--s1`, special teams `--s3`: three colours pass the colour-blind checks for every pair in all four themes, nine position colours wouldn't), a legend whose position-group buttons highlight one group; the picked player in ink with an accent ring and a label, his neighbours ringed; hovering or focusing a list row rings that player on the map; hover a dot for a tooltip, click one to pick him; **Zoom to <player>**; the note that the two PCA axes keep only ~13% of the variation, so the lists are the real answer.
  - **Families:** what a family is (an algorithm grouped people who shared the most snaps: Leiden on the teammate and coaching links; named after the biggest team-era and unit), the counts, a filter box (name, team, top players, coaches), **Find someone's family** (`person=`), the list (name + unit, size, players / coaches, the top three players), and the picked family's card (chips, coaches, then the members table: players by their snaps in this era (the family's team and seasons) with all their snaps since 2018 beside it, then coaches with "—", the searched person highlighted).
  - **Chemistry:** the duos table (QB, receiver, position, targets, catches, yards, TDs, EPA / target, catch rate, seasons, teams; every header sorts), **Min. targets** 100 · 200 · 300 · 500, and the PageRank lists (receivers: "N QBs threw to him"; QBs: "threw to N receivers"; score as a neutral bar).
  - **Benchmark:** what it measures (the sample sizes from the answer) and why the 2026 sub-cubic APSP result saves nothing here; not run yet (404 `not_built`); the results: the run's time, seed, machine and W&B link, a "smoke run" chip and note for a quick check run, a red notice if any algorithm disagreed, a log-log chart of median time by graph size (one line per algorithm, coloured by kind: one pair `--s2`, one source `--s1`, all pairs `--s3`, dash patterns within a kind, a legend that highlights a line, a tooltip per point with median / p90 / fastest / runs) and the table of every run (algorithm slugs in words, "skipped" for a run predicted past the time budget, ✓ agrees with the reference's path lengths).

## What's real and what's mockup-only

- **Real:** every person, chain, evidence line, time, error message, lab row, map point, family, duo and as-of in the page, and the benchmark (the bench worker's smoke run, 2026-10-19; the full run replaces it).
- **Mockup-only (each says so on the page, * in the controls):** "No chain" and "No person links" (real answers with their chains removed and the server's note wording), "Older person links" (`asof.current: false`), a lab view's "Not run yet" (the server's `not_built` text), the 200 / 500 target minimums (filtered from the 100 / 300 answers), and the benchmark's "Not run yet" (the server's text). The search finds only the people in the dump; the dump has neighbours for six players and members for one family.

## What the real data changed

- **People in a chain had no team**, and coaches' `position` was null there but "coach" in the search: the backend now sends `team` and `seasons` for every person, and "coach" everywhere.
- **A stint's team on special-teams snaps** came from the possession team (a kick returner "teammates on ARI 2021" with a Rams player): the derive now takes the team a player was on the field for; 56,685 teammate links changed and the lab was re-run (132 families).
- **The first searches are slow when the graph is cold:** in the dump the first fewest search took ~10 s and the first strongest search ~32 s (the GDS projection on a cold page cache); after that, 30–400 ms. The app now warms the graph up in the background (once per derive, when the page's status call finds it up), and the running card says a cold graph can take up to half a minute.
- **The map's colours:** nine position groups can't be told apart by colour-blind readers in a scatter, so the dots are coloured by unit and a group is picked out by highlighting it; the bars are neutral, because the series colours mean offense / defense on this page.
- **The benchmark's real answer** differed from the planned shape: `sizes` are objects (`{label, n, m, requested}`), algorithms are slugs (the page names them), a skipped run has null timings, `started_at` instead of `ran_at`; the reader now leaves out `gds_procedures` and `times_ms`.
- **Family members' snaps** were each player's snaps since 2018 on every team (Mahomes 9,971 in "2021-2025 KC offense"): the backend now sends `snaps` (in the family's era) and `snaps_total`; a coach's are null ("—").
