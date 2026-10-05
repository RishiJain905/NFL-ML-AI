# Guide: the LLM that writes the digest prose (P04, D56; re-checked and closed in P09, D87)

**Writer:** `openrouter` · `z-ai/glm-5.3-flash` · reasoning effort `max` · **Chosen:** 2026-10-03 (D56, at Rishi's request in P04) · **Fallback writer:** `placeholder` (template text) · **Key:** `OPENROUTER_API_KEY` · **Code:** `digest/llm/` (`base.py`, `openrouter.py`, `placeholder.py`, registry in `__init__.py`), `digest/prompt/` (`system.md`, `sections.yaml`, `__init__.py`), `digest/synthesize.py`, `digest/checks.py`, `digest/facts.py`, `digest/render.py`, `digest/run.py`, `digest/graph_sections.py` · **Config:** `config/settings.yaml` → `llm`, `digest.word_budgets` · **Decisions:** D53, D56, D60, D61, D87 · **Plan:** [P09](../plans/P09-llm-connection.md) (closed 2026-10-05) · **Spec:** [06](../06-weekly-digest.md)

Paths below that start with `runs/` or `reports/` are under the data root (`D:\nfl-ml-data`).

## The short version

- The LLM is a **copy editor, not an analyst.** Code makes every number, every ranking and every "who is home" fact. The LLM turns that into readable sentences.
- It writes **up to seven short sections** (570 words, or 740 with the knowledge-graph sections). Code writes the header, tables, report card numbers and footer.
- Every sentence it writes is **checked by code** (11 checks). A fail-level problem triggers **one** rewrite with the exact offending tokens. If it still fails, the digest ships with a **warning banner**.
- If the provider is down, rejects the request or returns garbage, the **placeholder writes the prose instead** and the footer says so. The weekly run doesn't stop because the provider failed. (A config error that stops the client being built, such as `llm.provider: anthropic`, does stop it.)
- A call costs about half a cent to 1.7 cents (a digest 1 to 2.7 cents in the P09 backtests). It takes from 2.5 minutes to over 20 (9 to 35 minutes per digest in P09, with a ~22,000-token prompt), because the model "thinks" for most of that time.
- Switching models is a **config-only change** in `config/settings.yaml`, plus a smoke test on backtest weeks. Section 6 is the step-by-step.

## 1. What the LLM does, and what it doesn't

### Who does what

| Job | Who |
|---|---|
| Win probabilities, predicted scores, margins, ratings, trends, watch list, graph insights | Code and the models (P02 ratings, P03 game model, P04 rule-based sections, P05 graph) |
| Every number's display string ("64%", "Colts by 5", "0.09 more EPA per dropback on offense") | Code (`digest/format.py`) |
| Who is home, which game is "closest" or "most lopsided", how big a consensus gap is, which way a defense rank points, what changed vs what the level is | Code. It writes the words, the LLM copies them (see "Meaning stays in code" in section 4) |
| Header, report card numbers, game outlook table, "Heuristic picks..." line, banners, footer | Code (`digest/render.py`) |
| Turning the payload's facts into short, readable paragraphs in one voice | **The LLM** |
| Checking every number, name, word and length in those paragraphs | Code (`digest/checks.py`) |

**What it must not do** (system prompt rules, enforced by the checks):

- **No predictions.** It never forecasts a game. It describes what the payload says.
- **No arithmetic.** It never computes, rounds, combines or compares numbers ("twice as many", "doubled", "half" are rejected).
- **No lookups.** It has no outside knowledge to add. A team or player that is not in the payload fails `unknown_entities`.
- **No betting or fantasy talk.** Win probabilities are percentages. Lines, odds, locks and start/sit are banned words.
- **No invented motives.** A player facing his former team is a fact, not "revenge".

### The seven prose sections

Code puts them in this order (`render.SECTION_TITLES`). The budgets are in `config/settings.yaml` → `digest.word_budgets`. A section more than 25% over its budget fails a check. More than 25% under only warns.

| # | Section id | Title in the digest | What the LLM writes | Budget | Payload part it reads | On when |
|---|---|---|---|---|---|---|
| 1 | `report_card` | Report card | 1 to 2 sentences on last week's picks: pick record, Brier next to Elo's, score error, the biggest miss (said plainly as a miss), season to date, one calibration bucket | 60 | `report_card` | Always (code-written when there is nothing to grade) |
| 2 | `game_outlook` | Game outlook | A blurb under the code table: 1 to 3 games where the model (without market data) differs most from consensus, plus the most lopsided and closest games with their `rank_note` | 80 | `games`, `game_highlights` | Always |
| 3 | `team_trends` | Team trend shifts | 2 to 4 teams trending up or down: the first driver, the net rating now, supporting evidence. Descriptive, not predictive | 120 | `team_trends` | Always |
| 4 | `under_the_hood` | Last week under the hood | 3 to 5 tracking / charting notes: who rose or fell against his own norm, with small samples flagged | 110 | `under_the_hood` | Always |
| 5 | `players_to_watch` | Players to watch | The 2 to 3 most notable picks per side, offense first, of the 10 offense + 10 defense model picks that code lists in two tables above the prose (D70): projection, vs-baseline, likely range, one or two drivers copied whole. Without projections (a failed refit), the 4 to 6 most notable of 8 low-confidence heuristic picks | 200 | `players_to_watch` | Always |
| 6 | `matchup_risk` | Matchup / risk to watch | The knowledge-graph item for this section (usually one): an injury ripple, a QB change or a trend mismatch | 80 | `graph_insights` | Phase P05: only when the graph is available |
| 7 | `non_obvious` | Non-obvious insights | One or two graph items: a player facing his former team, common opponents, a trend mismatch | 90 | `graph_insights` | Phase P05: only when the graph is available |

- **Totals:** 570 words for sections 1 to 5, **740** with the graph sections (the players budget went from 160 to 200 with D70).
- **Whole-digest cap:** 105% of the active LLM budgets, so 777 words with the graph sections (598 without them). Code-written sections are left out of the cap.
- **A real digest** (2026 week 4, live, P05, before D70): 23 + 66 + 147 + 127 + 145 + 99 + 109 = **716 words**. Team trends came to 147 against a limit of 150.
- **`payload` keys in `sections.yaml`** only tell the model where to look. The model is sent the **whole** payload (7,700 to 9,000 prompt tokens in the runs so far), not just those parts.

### Which sections code writes instead

| Situation | What code writes | Where |
|---|---|---|
| The report card has nothing to grade (no saved predictions for last week, or week 1) | One fixed sentence: "Report card: <note>". Week 4 of 2026 said "no predictions were saved for week 3, so there is nothing to grade; next week's report card grades this week's picks." | `run.fixed_sections` |
| The graph worked but no item cleared the bar for a graph section | "No graph angle cleared the bar this week." | `graph_sections.fixed_graph_sections` |
| The graph is unavailable (Neo4j down, build failed) or `--graph off` | Nothing: sections 6 and 7 are not in the digest at all. With `unavailable` there is an info banner and the footer says why | `PromptBundle.active` (phase gating) |
| The LLM provider failed | The placeholder writes every LLM section for that attempt | `synthesize._generate` |

**Why code writes the empty cases:** the LLM adds nothing there but risk. In the first live digest GLM padded the "nothing to grade" report card with an invented flourish. Code-written sections are left out of the LLM's output spec, merged back before the checks, and checked like the rest.

**What code always renders around the prose:**

- the title and run line (run time, data freshness per source);
- the report card's bullet lines (week, season, calibration);
- the game outlook table with its notes (no market line, neutral site, games already kicked off), its **QBs (away / home)** column and the **⚠ QB changes** notes (D63);
- **Starters out this week** under the table (regular starters who won't play, up to 3 per team);
- the **Offense** and **Defense** watch tables above the players prose (all 20 picks; the "TD chance" / "Sack chance" columns since P08, which never reach the LLM), and the **Tough spots** (P06, D70). With heuristic picks instead: one table of 8 and a "low confidence" line;
- **More from the graph** after the Non-obvious prose (up to 6 strong graph stories that didn't fit, one line each);
- **Latest news** (up to 4 ESPN headlines, live runs) before the footer;
- any banner, and the footer.

The LLM sees all of these in the payload (`qb_changes`, `starters_out`, `graph_more`, `news`) but prompt rule 21 tells it not to restate them: they're already on the page. Everyone they name is registered in the fact index, so a passing mention doesn't trip `unknown_entities`.

## 2. The current model and how it is called

### The settings (`config/settings.yaml` → `llm`)

```yaml
llm:
  provider: openrouter
  model: z-ai/glm-5.3-flash
  reasoning_effort: max
  max_tokens: 64000
  timeout_seconds: 600
  retries: 2
  openrouter:
    only: [baseten/fp8, relace, novita/fp8, deepinfra/fp4]
    sort: price
    allow_fallbacks: true
```

| Setting | Now | What it does |
|---|---|---|
| `provider` | `openrouter` | Which client the registry builds: `placeholder` or `openrouter`. `anthropic` and `openai_compatible` were left unbuilt when P09 closed (D87): OpenRouter serves Claude and open models by changing `model` |
| `model` | `z-ai/glm-5.3-flash` | The OpenRouter model id |
| `reasoning_effort` | `max` | Sent as `reasoning: {effort: max, exclude: true}`. The model thinks first. The thinking is **billed but not returned** (`exclude`). Leave it blank or `null` and no `reasoning` field is sent (for models that don't think) |
| `max_tokens` | `64000` | A cap on the **whole completion, thinking included**. Every allowed endpoint accepts up to 131,072 |
| `timeout_seconds` | `600` | Per request. The client gives up after 10 minutes without a reply |
| `retries` | `2` | Extra attempts, so 3 in all (see below) |
| `temperature` | not set | `llm.temperature` exists in the code but the file doesn't set it, so the provider default applies |
| `openrouter.only` | 4 endpoint tags | Only these endpoints may serve the request |
| `openrouter.sort` | `price` | OpenRouter tries the cheapest allowed endpoint first |
| `openrouter.allow_fallbacks` | `true` | If that endpoint fails, OpenRouter tries the next allowed one. `false` would fail instead |

The whole `openrouter:` block is passed to OpenRouter as its `provider` routing object. Any other routing option OpenRouter supports can go there with no code change. `require_parameters` is **not** set, so an endpoint that lacks a feature (JSON mode) ignores it.

### The four allowed endpoints (live listing, 2026-10-03)

Prices are dollars per million tokens. The thinking tokens count as output, so output price matters most.

| Endpoint tag | Input | Output | JSON mode | Max output tokens |
|---|---|---|---|---|
| `relace` | 0.032 | 0.50 | **no** | 131,072 |
| `novita/fp8` | 0.084 | 0.28 | yes | 131,072 |
| `deepinfra/fp4` | 0.075 | 0.25 | yes | 131,072 |
| `baseten/fp8` | 0.15 | 0.50 | yes | 131,072 |

OpenRouter picks the endpoint at request time. Every call in the saved run files was served by **Novita** (the first backtests used Novita or DeepInfra). `calls[].provider` shows the provider's name, such as `Novita`, not the tag. The cost of the 2026 week 4 live call checks out: 8,975 input tokens × $0.084/M + 17,010 output tokens × $0.28/M = **$0.0055**.

### What one request contains

| Part | Value |
|---|---|
| URL | `https://openrouter.ai/api/v1/chat/completions` |
| Header | `Authorization: Bearer <key>` (key from `OPENROUTER_API_KEY`), `Content-Type`, `X-Title: NFL Analytics Engine digest` |
| `model`, `max_tokens` | from `llm.model`, `llm.max_tokens` |
| `messages` | a **system** message (all of `system.md`) and a **user** message: the output spec as JSON ("OUTPUT SPEC"), then the whole payload between `<payload>` tags, then "Return only the JSON object." |
| `response_format` | `{"type": "json_object"}`, always requested |
| `usage` | `{"include": true}`, which makes OpenRouter report the exact cost |
| `reasoning` | `{"effort": <llm.reasoning_effort>, "exclude": true}` (only when an effort is set) |
| `provider` | the `llm.openrouter` block |

### JSON mode and lenient parsing

- JSON mode is **requested, not required.** Relace doesn't support it, and not every future model will. So the reply is parsed leniently (`openrouter.parse_sections`):
  - code fences (` ```json `) are stripped;
  - everything from the first `{` to the last `}` is taken, so text around the object is ignored;
  - it must be a JSON object, and **every expected section id must be a non-blank string** (extra keys are dropped);
  - otherwise the reply is rejected with a short fixed message ("empty reply", "no JSON object in the reply", "reply is not valid JSON (...)", "reply is missing or has blank sections: ...").
- A partial reply must never pass as "all checks passed" with empty sections. That came out of the Sol review (also why the `complete` check exists).
- **A reply that arrives but can't be used gets one more request** with the same prompt. A reply cut off by `max_tokens` lands here too (`finish_reason: length` is recorded in `calls`). A second failure means the placeholder writes that attempt.

### Timeouts and retries

| Event | What the client does |
|---|---|
| Timeout, connection error | Retry |
| HTTP 408, 409, 425, 429, 500, 502, 503, 504 | Retry |
| HTTP 200 with an `error` and no `choices` | Retry |
| Any other HTTP status (400, 401, 402, 403, 404 ...) | **Stop at once.** Message: "HTTP <status>: error code <code>" |
| Waits between attempts | 5 s, then 10 s (`min(30, 5 × 2^(attempt-1))`) |
| All attempts used | "gave up after 3 attempts (timeout)" and the placeholder takes over |

**Worst case:** 3 attempts × 600 s + 15 s of waiting = about **30 minutes** for one call before it gives up. A digest makes up to two calls (the draft and the regeneration), so up to about an hour. P07's schedule has to allow for this, or use a lower effort.

### Measured latency and cost

| Run | Calls | Latency | Prompt tokens | Completion tokens (thinking) | Cost | Served by |
|---|---|---|---|---|---|---|
| 2026 week 4 live, final (`runs/2026/week04/`) | 1 | **147 s** (2.4 min) | 8,975 | 17,010 (15,963) | **$0.0055** | Novita |
| 2025 week 4 backtest | 1 | 321 s (5.4 min) | 7,659 | 16,608 (15,702) | $0.0053 | Novita |
| 2025 week 9 backtest | 1 | **1,260 s (21 min)** | 8,803 | **43,864** (42,771) | $0.0130 | Novita |
| First live attempts (PROGRESS, D56) | 1 to 2 | 19 to 21 min | | | $0.013 to $0.016 | |
| First backtests (D56) | 1 | 4.5 to 15 min | | 10k to 26k | $0.003 to $0.008 | Novita, DeepInfra |
| P09 final code, 2025 weeks 4 / 8 / 14 (round 5) | 1 each | 16, 19, 21 min | 22,098 to 23,829 | 37,481 to 46,538 (36k to 45k) | $0.012 to $0.015 | Novita |
| P09 final code, 2025 week 9 (round 5) | 1 | **3 min** | 22,558 | 35,729 (34,618) | $0.021 | **BaseTen** |
| P09 regenerations (rounds 1–3) | 2 | 9 to 37 min in all | ~24k on the second call | | $0.011 to $0.027 | Novita, BaseTen, DeepInfra (0 reasoning: dropped) |

- **BaseTen reasoned as much as Novita in a fifth of the time** (week 9: 3 minutes vs 12–23) for about a cent more per digest. `sort: price` usually picks Novita. `sort: throughput` in `llm.openrouter` (or BaseTen first in an `order` list) would make the weekly digest step about 3 minutes; it is Rishi's call (P09 kept the routing on price).

- **Thinking is almost the whole bill:** 94% of the week 4 live completion tokens (15,963 of 17,010) were thinking. The visible JSON is about 1,000 tokens.
- **Latency swings 10x** (2.5 to 21 minutes, and up to 30 if a call retries) for the same prompt size. It tracks how long the model thinks and how busy the endpoint is.
- **`latency_s` is the whole time inside the HTTP layer, retries and waits included.** It does not say how many attempts it took. The week 9 call (21 minutes) is longer than one 600-second timeout, so it very likely included a timed-out attempt (unless the provider kept the connection alive with bytes). The files don't record which.
- **Token headroom:** the week 9 call used 43,864 of the 64,000 allowed tokens (69%). A bigger week could hit the cap, the reply would be cut off, and the placeholder would take over. Raising `max_tokens` is a config change (up to 131,072 for these endpoints).
- **Money is not a concern:** two calls a week for a whole season costs under a dollar.

## 3. One digest, step by step

```text
payload.json (validated by Pydantic) ---> fact index (who owns which number)
        |
prompt = system.md + sections.yaml instructions + word budgets (the "output spec")
        |
provider call (OpenRouter) ---fails---> placeholder writes this attempt
        |
parse reply into {section_id: markdown}; merge in the code-written sections
        |
11 checks ---------------- no fail-level problem ---------------> render
        |
a fail-level check failed
        |
regenerate ONCE: first draft + offending tokens + every section's word count
        |
11 checks again ----------- passed ------------------------------> render
        |
still failing -----> render with a warning banner (the digest still ships)
        |
render -> digest.md, report copy, payload.json, raw_llm_output.json, checks.json, W&B
```

1. **Payload.** `run.build_payload` assembles the report card, games, trends, under-the-hood items, watch list and graph items. Pydantic validates it (`extra="forbid"`: no stray fields). Every number is a pair, `{value, display}`.
2. **Fact index** (`facts.build_fact_index`). For every team and player, the set of display strings and numbers it owns. The checks use it.
3. **Active sections.** The prompt bundle lists the sections. Sections tagged `phase: P05` (the two graph sections) are on only when the graph state is `ok`. Code-written sections are removed from the LLM's spec.
4. **Prompt.** `system.md` (the rules), plus the output spec: each active section's id, title, payload keys, word budget and instructions, and the total budget. See section 5.
5. **Provider call.** One request. The calls and their cost, tokens and latency are kept in `llm.calls`.
6. **Parse and merge.** The reply must contain every expected section. Code-written sections are merged in.
7. **Checks.** All 11 run on every attempt, for every provider (section 4). A **fail-level** problem sends the digest to step 8. Warnings never trigger a rewrite.
8. **Regenerate once.** The request becomes a short conversation: the original messages, then the model's own first draft (as an assistant message), then a "fix these" message listing:
   - every fail-level issue as `[check] section: 'token' (reason)`;
   - **every LLM section's word count** and its limit, `[length] team_trends: 147 words now; aim for 120, never more than 150`;
   - **the whole-digest cap**, `[length] whole digest: 716 words now; aim for 700, never more than 735 in all`.

   The length lines are always sent, because fixing one problem must not break another. Two real cases: a fix pushed a section from 149 to 151 words on a 150-word limit, and a P05 regeneration ended at 740 words on a 735 cap with every section inside its own limit.
9. **Final checks.** If a fail-level check still fails, the digest is published with a warning banner under the header: "Automated checks failed after one regeneration: <check names>. Treat the prose below with care; details in `checks.json`." The CLI also exits with code 2. A flagged digest beats no digest.
10. **Render, save, log** (section 7).

### When something goes wrong

| What happens | Result | Where you see it |
|---|---|---|
| `OPENROUTER_API_KEY` isn't set | "OPENROUTER_API_KEY is not set (check with `nfl doctor`)", then the placeholder writes | footer, `fallback_notes`, a yellow line in the terminal |
| Key rejected (401), out of credit (402), a wrong model id or no allowed endpoint for the model (an HTTP 4xx, usually 400 or 404) | Not retried. "HTTP 404: error code 404", then the placeholder writes | same |
| Timeouts, connection errors, 429, 5xx | Retried twice, then "gave up after 3 attempts (...)", then the placeholder | same |
| Reply is unusable, or a section is missing or blank | One more request. A second failure means the placeholder | `calls` has two rows, the first with `parsed: false` |
| First draft fails a fail-level check | Regenerate once | `regenerated: true` in `checks.json` |
| The **regeneration call** fails | The placeholder's text **replaces** the first draft (it passes the checks) | `writers: ["openrouter", "placeholder"]` |
| Second draft still fails | Published with the warning banner | banner, `passed: false`, exit code 2 |
| `llm.provider` is `anthropic`, `openai_compatible` or a typo | The run **stops** while building the client: "LLM provider 'anthropic' isn't built (D87). Use llm.provider: openrouter with that model's OpenRouter name, or placeholder." (a typo: "unknown LLM provider"). The fallback only covers failures after the client exists. `nfl doctor` shows the same as a FAIL | terminal, `nfl doctor` |

**The placeholder** (`llm/placeholder.py`) fills fixed sentence templates straight from the payload's display strings. It ignores the system prompt and any feedback. Every sentence with a number also names the number's owner, and it fits each section to its budget, so it passes all the checks. That is why it is a safe fallback and a good baseline for side-by-side backtests.

**How a fallback shows up:**

- The footer line reads `Writer: placeholder (templates (fallback)), prompt <hash> (the configured LLM failed; see raw_llm_output.json)`.
- `raw_llm_output.json` has the reason in `fallback_notes` (status code and short error code only).
- W&B has `llm/final_writer = placeholder` and `llm/fallback = true`.
- A fallback is easy to miss, so **check the `Writer:` line after any config change.** A digest that passed every check can still have been written by templates.

### The knowledge-graph sections

- `nfl digest --graph auto|build|read|off` (default `auto`). A backtest builds the graph as of its Tuesday (about 1.5 minutes, needs the `nfl-neo4j` container). A live run reads the weekly `graph` step's `graph_results.json` and builds only if it's missing.
- The digest **re-picks** the items from the stored candidates with its own run time, so a game that has kicked off since the build is never featured. A live digest also skips games kicking off within **90 minutes**, because writing can take 20 or more minutes.
- Graph state `ok` switches on the `P05` sections. A section with no pick is the fixed "No graph angle cleared the bar this week." sentence.
- Graph state `unavailable` means no graph sections, an info banner ("Graph sections skipped this week: ...") and a footer line "Knowledge graph: unavailable (...)".
- When comparing models on backtests, **make sure the footer says "Knowledge graph: rebuilt for this run" for every week.** If Neo4j is down, the prompt the model sees is different.
- After the digest is written, the picks that the final prose actually names are appended to the published-insight log (novelty: the same story isn't picked again for 3 weeks).

## 4. The guardrails

### The checks (`digest/checks.py`)

All 11 run on every attempt. **Fail** means a regeneration (then a banner if it persists). **Warn** never triggers anything: it is listed in the footer, `checks.json` and W&B.

| Check | Level | What it catches | Real example from this project |
|---|---|---|---|
| `complete` | fail | A requested section is empty or missing | Sol review (the Codex code review of P04): a partial reply could pass as "checks passed" with blank sections. The parser now rejects it too |
| `number_provenance` | fail | A number in the prose that isn't in **any** payload display string. Digits, `%`, ranges, ordinals ("28th"), decimals like `.999` | Both fact-checks found every number GLM wrote was in the payload. The one hole was the checker's: the Sol review found `.999`-style decimals slipping past the number parser (fixed, with a test) |
| `spelled_out_numbers` | fail | Number words that aren't in the payload: "two-thirds", "a dozen", "doubled", "half", "twice", "ten". Not flagged: "one", "first / second / third", and football phrases ("first half", "two-minute drill", "double coverage") | Sol review: "59 percent" was wrongly treated as a number word. Fixed ("percent" is a number) |
| `entity_binding` | fail | A number in a sentence where **no entity named in that sentence owns it** (a number moved to the wrong player or team) | **A real slip:** GLM attached the 50 to 60% bucket's "14 of 20" to the 70 to 80% bucket (2025 week 4 backtest). Sol: each team owned its opponent's predicted score. False alarms found with GLM and fixed: "Chase Brown" counted as naming Ja'Marr Chase ("Chase"), "Vikings-Steelers" didn't name both teams, and in the first live run a person in a team's evidence ("without guard Aaron Banks") didn't count as naming the Packers |
| `unknown_entities` | fail | A team or rostered player (this and last season's nflverse rosters) in the prose who isn't in the payload | No GLM slip recorded. It guards against an invented or off-payload name |
| `meaning` | fail | "A at B" that isn't a real game in that home / road order. "Closest", "most lopsided", "right behind", "toss-up" on a game that isn't. "Much / notably higher ... consensus" with the wrong tier | A published "Colts at Titans" for the Titans at Colts game. "Tightest call" and "right behind" computed by GLM itself (P04 fact-check) |
| `banned_language` | fail | spread, line, odds, moneyline, over/under, total points, bet(s), betting, wager, lock, cover, fade, value play, fantasy, start/sit, PPR, parlay, vig, ATS, waiver wire ... Allowed football phrases: "offensive line", "line of scrimmage", "goal line", "sideline", "Cover 2", "fade route", "spread offense" | Sol review: hyphenated "must-bet" and "betting-lock" slipped through (now matched on letter boundaries). The placeholder itself once wrote "lines" |
| `length` | fail | A section more than **125%** of its budget, or the digest more than **105%** of the active total | 149 to 151 words on a 150 limit while fixing another issue (first live run); 740 on a 735 cap (P05 backtest) |
| `length_short` | warn | A section under **75%** of its budget. Warn only: padding a thin section invites invention (D53) | Used to fire on every code-written section (a 23-word "nothing to grade" report card against 60); since P05 code-written sections are left out of the length checks |
| `hedging` | warn | A low-confidence item named without a hedge phrase ("low confidence", "small sample", "heuristic", "could", "might", "descriptive" ...). A graph section holding a low-confidence item with no hedge anywhere | 2026 week 4 live: Brock Bowers named in a trends sentence without a hedge. GLM also once hedged the wrong claim ("low confidence" on a confirmed QB start): fixed with the item's `note` |
| `name_heuristic` | warn | Two or more capitalized words that match nothing known (a possibly made-up player) | One possessive false positive in P04 (fixed) |

- **Where "meaning" can check, it does:** home / road order, the superlatives and the consensus tier can all be verified against the payload. Most other meaning problems can't be, which is why the next two parts exist.
- **The checks are the same for every provider.** They matter most with a smaller or cheaper model.
- **False-positive revisit trigger (D53):** if the false-positive rate passes 20% of runs over a month, loosen or fix the check (never weaken what it guarantees).

### Whose number is it? (the owner rules, in plain words)

The binding check asks one question per sentence: "for each number here, does someone named **in this sentence** own it?" Owners come from the payload.

| Part of the digest | Who owns its numbers |
|---|---|
| Report card | "the model" (also "model", "picks", "we", "our", "report card", "Brier", "calibration", "watch list"). Elo's Brier also belongs to "Elo". The biggest miss also belongs to both teams in that game |
| Game table facts | The home win % and home score belong to the home team, the away ones to the away team. The margin belongs to both |
| Game highlights | The favorite's probability to the favorite only. The `rank_note` to both teams in that game |
| Team trends | The team: the change, the window, the net rating, each driver change and each evidence string |
| Under the hood, players to watch | The player (a unique last name works as a short alias) |
| Graph items | Each fact's numbers go only to the owners listed on that fact. The headline's and sample's numbers go to the game's two teams and the item's subject people |
| Season and week numbers | **Nobody and everybody**: global, any sentence may use them |

Rules of the road:

1. **Report card sentences have an implicit owner.** "Week 3 went 12 of 16" counts as the model's number without saying "the model". **Exception:** a sentence naming a calibration bucket ("70-80%") must name it, and only that bucket owns its counts.
2. **A short name only counts outside a full name.** A unique last name is an alias ("Chase"), but it doesn't count when it sits inside another known full name ("Chase Brown"). Team names follow the same rule.
3. **Naming a person names his team.** A person from a team's evidence line or QB slot, or a graph item's person, binds to that team too.
4. **A hyphen is a word boundary for names.** "Vikings-Steelers" names both teams. (Banned and hedge phrases stay hyphen-aware, so "two-point" stays whole.)
5. **Graph facts with several clauses** ("the Bucs lost to the Vikings by 7; the Packers lost to the Vikings by 17") bind per clause. An owner named only in another clause doesn't own this clause's numbers, so moving the 7 to the Packers fails.
6. **Every person and team in a graph item is a known entity,** common opponents included, so `unknown_entities` doesn't fire on them.
7. **A low-confidence item's subject people need a hedge,** and a section with such an item needs a hedge phrase somewhere (warn level).

### Meaning stays in code

The P04 fact-check read GLM's four backtest digests against their payloads. **Every number was right, and every digest still had a meaning error:** a made-up superlative, a home / road swap, a flattened consensus tier, a trend change read as a level, a QB's start read as "taking over". The rule that came out of it (D53): **the LLM never derives an order, a direction, a venue or a tier.** Code writes the words, the prompt says "copy them", and a `meaning` check verifies what it can.

| The LLM could get wrong | What code writes instead | Payload field |
|---|---|---|
| Who is home | "Titans at Colts" (the second team is at home) | `games[].matchup` |
| Which game is closest or most lopsided | The top 3 lopsided and top 2 closest games, each with a ready-made note | `game_highlights[].rank_note` |
| How big a consensus gap is | "much higher on the Commanders than consensus", "notably higher ..." (two tiers, so "much" and "notably" differ) | `model_vs_consensus.text` |
| Whether a number is a change or a level | "up 0.12 EPA per play over the last 3 weeks" (the change); the level is separate | `trend_delta`, `net_rating` |
| Which way a defense rank points | "league's weakest", "3rd-weakest", "4th-strongest", "about league-average (16th-strongest)" (never a bare "1st") | `opp_def_rank` |
| How far back QB evidence goes | "started their latest game in place of ...", "last season's main starter was ..." | `evidence` strings |
| Which unit a number describes | Complete phrases: "0.09 more EPA per dropback on offense", "+5.8 rush yards over expected per carry", "offensive tackle Joe Alt" | `drivers[].change`, `evidence` |
| What a graph fact means | "worse without him", "lost to the Vikings by 7", "listed out for this week (knee)" | `graph_insights[].facts[].text`, `headline`, `note` |
| What is uncertain in a graph item | A `note` ("a small sample: ...", the new QB's thin history with these receivers) | `graph_insights[].note` |
| The "nothing to grade" report card | One fixed sentence | `run.fixed_sections` |

**When you add a new fact to the payload:** put the sign, unit, direction and time scope into the display string, never leave them for the LLM to infer ("Never put an ordinal, a sign or a unit in the payload without the word that says which way it points").

## 5. The prompt files

Two files, both in `src/nflengine/digest/prompt/`. A change to either changes the **prompt hash**.

### `system.md`: the rules

The file opens with the voice ("a smart friend who watches more film than he does, texting him what actually matters. Direct, specific, a bit of personality, no hype") and the job ("You do no prediction, no arithmetic and no lookups"). Then 22 numbered hard rules and a few format rules.

| Group | Rules | What they are for |
|---|---|---|
| **Numbers** | 1, 2, 10, 11 | Use only the payload's display strings, exactly as written ("64%", not "64 percent"). Never compute, round, combine or compare. Digits only, never "four" or "half". A calibration count belongs to its bucket: name it in the same sentence |
| **No betting or fantasy** | 3, 4, 5, 10 | No spreads, totals, odds, "locks", "value", "fades", covering. Describe the consensus gap in words only. No fantasy points, rankings, start/sit. Rule 10 lists the exact words the checks reject (even in everyday use: "cover", "line", "spread", "value") so the model avoids them |
| **Honesty** | 6, 7, 8, 9, 16 | Say "low confidence" or "small sample" in the same sentence. Attribute news ("per ESPN"). Don't add teams, players or facts, and name the player or team that owns each number in the same sentence (8). Trends are descriptive, not predictive. Evidence is time-scoped ("started their latest game" is not "has taken over") |
| **Meaning stays in code** | 12, 13, 14, 15, 17, 18, 19, 20 | Never rank or compare yourself: superlatives only from `game_highlights` / `rank_note` (12). Use the `matchup` string exactly (13). Use `model_vs_consensus.text` word for word (14). A trend delta is a change, the level is `net_rating` (15). Copy evidence and driver strings whole, with their unit words (17). How to write a team trend: first driver by its unit, then "net rating now ..." (18). Describe a matchup only with `opp_def_rank` (19). Copy graph fact texts whole, and never turn a with / without comparison into a forecast (20) |
| **Code-written neighbours** | 21 | The game table's QB column and QB-change notes, "Starters out", "More from the graph", "Tough spots", the watch-list look-back, the projection highlights and "Latest news" are already on the page: don't restate them |
| **Player projections** | 22 | A projection is the middle outcome and the interval its likely range; copy projection, range, baseline and vs-baseline with their units; a driver compares him with a typical player in his group, never his own form; never "will reach" a number |
| **Format** | (list after the rules) | Markdown prose only, no headers (code adds them), no tables. Return JSON `{section_id: markdown}` for exactly the ids in the output spec. Stay within each section's budget (plus or minus 25%) and the whole digest within the total |

Where the later rules came from: 12 to 16 match the P04 fact-check (superlatives, home / road swap, consensus tiers, trend as level, QB evidence), 11 matches the calibration-bucket slip, 17 to 19 came from the second fact-check (dropped qualifiers, "a tough matchup"), 20 came with the graph sections (P05), 21 with the code-written graph extras (D63) and 22 with the player model (P06, its wording fixed after the week-4 fact-check, D69).

The prompt rules and the checks say the same things twice on purpose. The prompt tells the model what to do. The checks prove it did.

### `sections.yaml`: the sections

One entry per prose section, in output order:

| Field | Meaning |
|---|---|
| `id` | The key the model must return, and the key the checks and `render.py` use |
| `title` | The heading code prints (`render.SECTION_TITLES` holds the live titles) |
| `payload` | Which payload keys the section reads. A hint only: the whole payload is sent |
| `instructions` | What to write: how many items, which fields, what to flag. For example players: "Copy opp_def_rank's display exactly ('3rd-weakest' = one of the worst defenses, a good matchup; '4th-strongest' = a tough one)" |
| `phase` | Optional. `P05` marks the graph sections, which are off unless the run enables the phase (graph state `ok`) |

The **word budgets** are not in this file. They are in `config/settings.yaml` → `digest.word_budgets`, and each section's budget is added to the output spec the model sees.

### The prompt hash

- **What it is:** the first 12 hex characters of `sha256(system.md + "\n---\n" + sections.yaml)`. The 2026 week 4 live digest used `8e0cd7b1a080`; P08 ended at `325b85a78f4e`; **P09 ended at `0ee03e0cd26e`** (the version that goes live with week 5). The regeneration message (`openrouter.fix_message`) is code, not prompt, so it doesn't change the hash.
- **Where it shows:** the digest footer ("prompt `8e0cd7b1a080`"), W&B config `prompt_hash`, and the W&B artifact metadata.
- **What it does not cover:** the **word budgets** (logged separately as W&B config `word_budgets`), and the **model and provider** (W&B config `llm_model`, `llm_provider`; the footer's `Writer:` line). A change to any of those leaves the hash alone. When comparing runs, compare hash, model and budgets together.

### How to change a prompt safely

**Step 1. Write down the failure first.** An issue from `checks.json`, or a finding from a read-through. Without it you can't tell whether the change helped.

**Step 2. Fix it in code or the payload if you can.** Every durable fix so far was a code-made string, not a prompt sentence. Edit the prompt only if the payload can't carry the meaning.

**Step 3. Change one thing, then run the offline tests.**

```bash
uv run pytest tests/digest
```

**Step 4. Run backtests with both writers on the same weeks.** Run the placeholder first, so the run folders end up holding the model's files (the reports are kept apart either way; see the overwrite warning in section 6).

```bash
uv run nfl digest --season 2025 --weeks 5-9 --backtest --llm placeholder
uv run nfl digest --season 2025 --weeks 5-9 --backtest
```

Reports land side by side at `reports/backtests/2025/week<NN>-digest-placeholder.md` and `week<NN>-digest-openrouter.md`. W&B runs go to group `digest-dev`, job `backtest`. Each week also rebuilds the graph as of its Tuesday, so the `nfl-neo4j` container must be running.

**Step 5. Compare**, in W&B (`digest-dev`) or from the files:

- the **first-pass rate** (share of weeks with `regenerated = false` and no fallback), against P09's 80% bar;
- `checks_failed` and `checks_warnings` per week;
- `words/<section>` against the budgets;
- `llm/cost_usd` and `llm/latency_s`.

**Step 6. Read the prose against `payload.json`** with the meaning checklist in section 6. P04 and P05 used an independent read-through of each digest like this, and it found problems no check could see.

**Step 7. Record it.** The new hash goes into the PROGRESS session log. A change that alters behavior also gets a decision row in `10-decisions-log.md` and an update to doc 06 in the same commit (CLAUDE.md rule).

## 6. Switching the model or provider, step by step

**A model switch on OpenRouter is a config-only change.** `git diff` should touch only `config/settings.yaml`. (P09's exit criterion: swapping provider means config only, plus the env file.)

### Which setting needs which environment variable

Variable names only. Rishi edits the env file himself; agents never open it.

| `llm.provider` | Needs | Status |
|---|---|---|
| `placeholder` | Nothing | Works today |
| `openrouter` | `OPENROUTER_API_KEY` | Works today |
| `anthropic` | `LLM_API_KEY` (reserved) | **Not built (D87).** Raises "LLM provider 'anthropic' isn't built (D87) ..." and `nfl doctor` shows FAIL. For a Claude model, keep `openrouter` and set `model` to its OpenRouter name |
| `openai_compatible` | `LLM_API_KEY`, `LLM_BASE_URL` (reserved): any OpenAI-compatible server, such as Ollama, vLLM or LM Studio | **Not built (D87).** Same message |

`.env.example` lists the names. The settings module keeps the `LLM_API_KEY` and `LLM_BASE_URL` fields (as `SecretStr` and a plain string) for a later adapter, but no code reads them. Rishi decided in P09 that OpenRouter is enough (D87). Adding a provider later means one new module in `digest/llm/` plus a line in the registry (`PROVIDERS` in `llm/__init__.py`); see "Adding another provider type" below. A test enforces that nothing outside `digest/llm/` imports a provider.

### The steps (switching to another OpenRouter model)

**Step 1. Look at the model's endpoints (no key needed).**

```bash
curl -s https://openrouter.ai/api/v1/models/<model>/endpoints
```

Or print just the fields that matter:

```bash
curl -s https://openrouter.ai/api/v1/models/<model>/endpoints | uv run python -c "
import json, sys
for e in json.load(sys.stdin)['data']['endpoints']:
    p = e['pricing']
    print(e['tag'], e.get('max_completion_tokens'), p['prompt'], p['completion'],
          'json' if 'response_format' in e['supported_parameters'] else 'no-json',
          'reasoning' if 'reasoning' in e['supported_parameters'] else 'no-reasoning')
"
```

What to look for:

- **`tag`:** the exact string `llm.openrouter.only` takes. GLM's four tags won't exist for another model.
- **`max_completion_tokens`:** at least `llm.max_tokens` (64,000).
- **`reasoning` in the supported parameters,** if you want an effort setting.
- **`response_format`:** nice to have (lenient parsing covers its absence).
- **Prices** per token (multiply by 1,000,000 for dollars per million) and `context_length` (the prompt is 7,700 to 9,000 tokens).

**Step 2. Save the baseline before you change anything.** Backtest runs **overwrite** earlier ones:

- the report `reports/backtests/<season>/week<NN>-digest-openrouter.md` is named by provider only, **not by model**, so a new model's run replaces GLM's;
- the run folder (`runs/digest-backtests/<season>/week<NN>/`) keeps one `checks.json` and one `raw_llm_output.json` per week, for **whichever writer ran last** (in the 2025 backtest folder today, weeks 4 and 9 hold GLM's files and weeks 8 and 14 hold the placeholder's);
- W&B keeps every run, but the run name `digest-<season>-w<NN>-backtest-openrouter` doesn't name the model either (use the config `llm_model` column).

Copy the GLM reports you want to compare against to names that include the model.

**Step 3. Edit the `llm` block in `config/settings.yaml`.**

```yaml
llm:
  provider: openrouter
  model: <vendor>/<model>
  reasoning_effort: max      # or high / medium / low; blank or null for a model that doesn't think
  max_tokens: 64000          # at most the endpoints' max_completion_tokens
  timeout_seconds: 600
  retries: 2
  openrouter:
    only: [<tag-1>, <tag-2>]  # tags from step 1, or delete this line to allow every endpoint
    sort: price
    allow_fallbacks: true
```

- **Change `only` together with `model`.** The four tags are GLM's. For another model OpenRouter finds no endpoint it is allowed to use and answers with an HTTP error (not retried), and the placeholder writes the digest. `nfl doctor` flags this (step 4). Delete the line rather than leave it as an empty list.
- **`only` is also who gets to see the payload.** Each endpoint provider has its own data policy.
- A thinking model needs a large `max_tokens` (the thinking counts). A model that doesn't think can use a much smaller one.

**Step 4. Confirm the key and the model.**

```bash
uv run nfl doctor
```

The `llm` row should read `openrouter key accepted; <model> has N endpoints, K of M allowed ones listed` (2026-10-05: `z-ai/glm-5.3-flash has 34 endpoints, 4 of 4 allowed ones listed`). Doctor reports set / not set and never shows the value. It checks that the key is accepted (a status-only call), that the model exists, and (since P09) that **at least one entry of `llm.openrouter.only` serves that model** (an entry matches an endpoint tag such as `novita/fp8` or a provider slug such as `novita`). A model switch that keeps GLM's four tags now shows `FAIL: none of the 4 llm.openrouter.only endpoints serves <model>` instead of quietly falling back to the placeholder on Tuesday. **It does not check the effort value or whether the model follows the output format.** The smoke test does.

**Step 5. Smoke test one backtest week, without W&B.**

```bash
uv run nfl digest --season 2025 --week 9 --backtest --no-wandb
```

Then check:

- the footer's `Writer:` line names the **new model**, not `placeholder (templates (fallback))`;
- `raw_llm_output.json` → `fallback_notes` is empty and `calls[0].provider` is one of your allowed endpoints;
- `checks.json` → `first_attempt.passed`;
- the footer says `Knowledge graph: rebuilt for this run`.

**Step 6. Run the comparison weeks,** the new model and the placeholder (section 5, step 4). Keep Neo4j up so the graph sections are the same in both.

**Step 7. Compare.** The first-pass rate is the headline number. Five weeks (`--weeks 5-9`) is a start; more weeks give a more reliable rate.

| What to compare | How | Good looks like |
|---|---|---|
| **First-pass rate** | Weeks with `first_attempt.passed = true` **and no fallback** (a fallback to the placeholder also "passes first time") | **At least 80%** (P09's bar). GLM's final round was 4 of 4 |
| **Final result** | `checks.json` → `final.passed`, no banner | Every week passes |
| **Warnings** | `checks_warnings`: `hedging`, `name_heuristic`, `length_short` | Few. A new model that stops hedging low-confidence items needs a prompt look |
| **Length** | `words/<section>` against the budgets | Inside 75% to 125% (code-written sections excepted) |
| **Cost** | `llm/cost_usd` | The same order as GLM (half a cent to 1.6 cents a call) |
| **Latency** | `llm/latency_s` | Under the 600-second timeout, so no retries |
| **Meaning** | A read-through against `payload.json` | No errors from the checklist below |

A quick way to list the model's own backtest weeks (it skips weeks whose files were overwritten by another writer or that fell back):

```bash
uv run python - <<'EOF'
import json, pathlib
root = pathlib.Path("D:/nfl-ml-data/runs/digest-backtests/2025")
rows = []
for d in sorted(root.glob("week*")):
    chk, raw = d / "checks.json", d / "raw_llm_output.json"
    if not (chk.exists() and raw.exists()):
        continue
    c = json.loads(chk.read_text(encoding="utf-8"))
    r = json.loads(raw.read_text(encoding="utf-8"))
    if r["provider"] == "openrouter" and not r["fallback_notes"]:
        secs = sum(x["latency_s"] for x in r["calls"])
        cost = sum(x["cost"] or 0 for x in r["calls"])
        rows.append((d.name, r["model"], c["first_attempt"]["passed"], round(secs), round(cost, 4)))
for row in rows:
    print(*row)
print("first pass:", sum(r[2] for r in rows), "of", len(rows))
EOF
```

Today it prints `week04 z-ai/glm-5.3-flash True 321 0.0053` and `week09 z-ai/glm-5.3-flash True 1260 0.013`, then `first pass: 2 of 2`.

**The meaning checklist** (what the checks can't see; the P04 and P05 fact-checks found each of these):

- the home / road order of every "A at B";
- every "closest", "most lopsided", "tightest", "right behind" matches `game_highlights`;
- "much" vs "notably" matches the consensus tier;
- "up" or "down" is a change over the window, not a level;
- the defense rank reads the right way ("3rd-weakest" is a good matchup for the player);
- QB statements stay time-scoped ("started their latest game" is not "has taken over");
- a number sits next to the unit it describes;
- no invented motive, forecast or fact;
- low-confidence hedges sit on the uncertain claim, not on the confirmed one.

**Step 8. If it passes, go live carefully.** Commit the `settings.yaml` change. In the same commit, log a decision in `10-decisions-log.md` and update doc 06's provider table (it names `z-ai/glm-5.3-flash`). The first live run with the new model deserves a line-by-line read against its `payload.json`, as the first GLM digest got.

### Rolling back

- **Back to GLM:** restore the old `llm` block (`provider: openrouter`, `model: z-ai/glm-5.3-flash`, `reasoning_effort: max`, `max_tokens: 64000`, the four-tag `only` list). `git diff config/settings.yaml` shows what changed. `git restore config/settings.yaml` discards an uncommitted edit.
- **Out of the LLM entirely:** set `llm.provider: placeholder`, or for one run use `uv run nfl digest --season <S> --week <W> --llm placeholder` or `uv run nfl weekly run --season <S> --week <W> --llm placeholder`.
- **The placeholder always stays** as the fallback writer, whatever the configured provider is.

### Adding another provider type (not built, D87)

**Usually you don't need one.** OpenRouter serves Claude (`anthropic/...`), GPT, Gemini and the open models, so a model switch stays a config change (the steps above). A native adapter only makes sense to use a key from another vendor directly, or a model running on this PC (Ollama, LM Studio, vLLM).

`anthropic` and `openai_compatible` need a new client class implementing `generate(system_prompt, payload, output_spec) -> {section_id: markdown}`, a line in `PROVIDERS`, and the env variables above. The checks, the regenerate-once flow, the fallback and the files all work unchanged. The regeneration contract matters: when `output_spec["previous"]` is set, send the first draft and `output_spec["feedback"]` back to the model (OpenRouter's `fix_message` is the template). Smaller open-source models may struggle with strict JSON, so use the server's JSON or grammar mode where it has one. The lenient parser stays as the safety net.

### P09: the re-check on today's digest (2026-10-05, D87)

P09 was written before OpenRouter was connected. By the time it ran, GLM had been writing the digest since P04, but the digest had grown a lot: the graph sections (P05), the player model with 10 offense + 10 defense picks (P06, D70), the P08 columns and GDS items. The prompt went from ~8,000 to ~22,000 tokens. So P09 became a **re-check of GLM on the full digest**, with fixes.

**Rishi's choices at the kickoff:** P04's review and live run count for the 🧑 / ✋ steps and agents re-verify; **no native `anthropic` / `openai_compatible` adapters** (OpenRouter serves those models by config); **no lower-effort trial** (`reasoning_effort` stays `max`).

**How it was checked.** The 2025 weeks 4, 8, 9 and 14 as backtests, each with the placeholder first (it rebuilds the week's graph) and then GLM (`--graph read`, the same graph). Every GLM digest was read against its `payload.json` by an independent opus-high fact-checker: every number's field, owner, unit and time scope, the meaning checklist above, and a 1–5 score on P04's rubric (would I read this / did I learn something / nothing wrong or invented).

**What round 1 found** (prompt `325b85a78f4e`, the P08 code):

- **0 of 4 passed first time**; all 4 passed after one regeneration, no fallback, and the fact-check found **no material meaning error**.
- Every first-attempt failure was a real slip, not a false positive: **orphan sentences** that name nobody, so `entity_binding` can't find an owner ("Driver: 'his recent form ... 1.4 tackles above ...'", "Context: pressure rate allowed by the offense +7.0 points ...", "It's a bigger role this week ... 58% ...", "Low confidence, small sample (5 targets).", "Small sample - 5 games without him."), plus "Three moves worth tracking" (a number word).
- Meaning slips the checks can't see: a self-made verdict ("Good week"), "the model's biggest disagreements" inside one consensus tier, "season average" for a norm that blends two seasons, the prompt's own words in the prose ("a fact, not a motive"), a passing-offense stat tied to a pass-defense trend ("That tracks with ...").
- The players section read like a table row turned back into text; reader scores 3 / 3 / 4, and GLM added little over the placeholder.
- **Two endpoint and check problems:** DeepInfra served 2 of the 4 regenerations with **0 reasoning tokens** (3 of its 5 saved calls ever), and the sentence splitter didn't split after `.'`, so a quoted driver sentence merged with the next player's and the binding reason named the wrong player (a merge could also hide a slip). "J.J. McCarthy" tripped the name heuristic as "J Mc".

**What changed** (D87; each fix aimed at the slip, never at loosening a check):

| Area | Change |
|---|---|
| Prompt rules (`system.md`) | Rule 2: no counts of its own ("in 1 game"). Rule 12: no grading ("good week") and no "biggest" outside an item's `rank_note`. Rule 22: a driver pointing the other way from `vs_baseline` is a comparison with a typical player; low confidence, a baseline note and a driver are separate facts. Format rules: natural sentences with no label-and-colon openers, the owner named in every sentence with a number (no "he / his / it" carrying a number), a hedge never tied with "so" / "since", trim whole sentences, never copy the instructions, aim for each budget because the whole digest only gets 5% |
| Section instructions (`sections.yaml`) | Players: one or two natural sentences each, projection + vs-baseline + baseline, the range only for low confidence, one driver framed against a typical player, a placeholder-only example sentence. Report card: lean (pick record, Brier vs Elo "(lower is better)", the biggest miss), because the code bullets already list the rest. Game outlook: "much" gaps first, never "biggest", every game named with its favorite's win %. Trends: evidence in its own sentence naming the team. Under the hood: "his norm", not "season average". Every example uses placeholders, never real names |
| Payload strings | Drivers are plain clauses ("his share of the team's carries lately puts the projection 9 rushing yards above a typical player in his group"), and one pushing against the gap adds "(not his own baseline)". Rate changes say which way and what it means ("pressure rate allowed by the offense up 7.0 points vs earlier this season (its QB under more pressure)"). Count items carry their unit ("9 pressures", "1.5 pressures per game"). A rounded zero prints "0.00", never "-0.00" |
| Regeneration (`openrouter.fix_message`) | Change only what the issues need and keep the rest; "no entity named" means add the name, a named owner who doesn't own the number means remove it; trim whole sentences, never words inside a copied string |
| Checks | The splitter also splits after a closing quote or bracket; the name heuristic reads initials and inner capitals. Nothing was loosened |
| Routing and doctor | `deepinfra/fp4` left `llm.openrouter.only`; `max_tokens` 96,000 (a first call used 51k of 64k); `nfl doctor` FAILs when no `only` entry serves the model |

**The rounds** (GLM; W&B group `digest-dev`; each run's config holds its `prompt_hash`):

| Round | Prompt (code) | 2025 w4 | w8 | w9 | w14 | First time |
|---|---|---|---|---|---|---|
| 1 | `325b85a78f4e` (P08) | regenerated, passed (`9zgdsr8u`, 18 min) | regenerated, passed (`2pcrkemo`, 9 min) | regenerated, passed (`in9l3civ`, 35 min) | regenerated, passed (`rvoz28rs`, 19 min) | **0 of 4** |
| 2 | `8e36d33c11d3` (w4, w8), `78d676ccaceb` (w9), `1c27f929b4d0` (w14) | first time (`rt845e9p`, 13 min) | **banner** after the regeneration: GLM's own "in 1 game" (`0m8xytqj`, 26 min) | length, then passed (`nyroja5e`, 20 min) | report-card length, then passed (`ibyeovtp`, 14 min) | 1 of 4 |
| 3 | `0eb81af95a6c` (w4, w8), `37d179c4b93a` (w9) | first time (`tyaammhp`, 14 min) | first time (`zoh82b5v`, 12 min) | one "his" orphan, then passed (`ffql6n5p`, 37 min) | stopped (superseded) | 2 of 3 |
| **5 (final code)** | `37d179c4b93a` (w4, w8), **`0ee03e0cd26e`** (w9, w14); notes that name their player | **first time** (`vl9a23xc`, 16 min, $0.012) | **first time** (`lt0ucwnb`, 19 min, $0.015) | **first time** (`navpqgvu`, 3 min on BaseTen, $0.021) | **first time** (`eta3qyx8`, 21 min, $0.012) | **4 of 4** |

The fact-check scores (would I read this / did I learn something / nothing wrong or invented) went from **3 / 3 / 4** in round 1 to **4–5 / 4 / 5** for the prose in round 5; the only page-level deduction left is the backtest-only injury wording (D66). Every round had no fallback; no regeneration ran on an endpoint that skipped the reasoning after DeepInfra left the list. Round 4 (weeks 4 and 8 on `37d179c4b93a`) was folded into round 5.

**What is still open:** the backtest-only injury wording (a backtest uses the week's final Friday report, D66, but the note says "this week's injury report" on a Tuesday-dated page), two graph-ranking habits the writer can't fix (a "better without him" ripple chosen as the week's risk; a trend mismatch that repeats the team-trend numbers), the outlook sometimes quoting the underdog's win % (correct but inconsistent), `check_hedging` letting one unnamed hedge sentence cover a whole section, and the routing choice above (price vs throughput).

**The Sol review** (Codex `gpt-6.1-sol`) found 6 issues in the P09 code, all fixed and verified: the closing-quote split breaking quoted abbreviations, runs of closing marks, a quoted "Jr." before a new sentence, invented initialed names slipping past the name heuristic, `nfl doctor` matching service-tier endpoints by provider slug (OpenRouter never does) and accepting null endpoint entries; a missing model is now a FAIL.

## 7. What to look at after a run

### The run folder (`runs/<season>/week<NN>/`, backtests under `runs/digest-backtests/`)

| File | What's in it | Look here when |
|---|---|---|
| `payload.json` | Exactly what the LLM was shown, validated | Reading the prose against its source |
| `raw_llm_output.json` | `provider`, `model` (the **configured** writer), `writers` (who actually wrote each attempt), `fallback_notes`, `calls`, `attempts` (the raw sections of the first draft and the regeneration) | A fallback, cost, latency, or diffing the first draft against the rewrite |
| `checks.json` | `passed`, `regenerated`, `banner`, `writers`, `fallback_notes`, `first_attempt` and `final`, each with `failed`, `warnings`, `word_counts` and every check's `issues` (section, token, reason, sentence) | A failed check or a banner |
| `digest.md` | The final digest (a copy goes to `reports/<season>/week<NN>-digest.md`, backtests to `reports/backtests/<season>/week<NN>-digest-<writer>.md`) | Reading it |

- **`checks.json`**: `first_attempt` says what triggered the regeneration, `final` what shipped. With no regeneration, they are identical. `first_attempt.passed` is the "first-pass" number.
- **`raw_llm_output.json` → `calls`**, one row per request (a regeneration or an unusable-reply retry adds a row): `model`, `provider` (the serving endpoint), `latency_s` (retries and waits included), `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `cost` (dollars), `finish_reason` (`stop` is normal; **`length` means the cap cut it off**), `regeneration`, `parsed`. Failed requests (timeouts, errors) leave no row; the reason is in `fallback_notes` if it ended in a fallback.
- Nothing in these files holds the key or provider-written error text.

### The footer

```text
- Checks: all passed; warnings: length_short, hedging
- Writer: openrouter (z-ai/glm-5.3-flash), prompt `8e0cd7b1a080`
```

| Line | Meaning |
|---|---|
| `Checks:` | `all passed` or `FAILED: <names>`, then `(after one regeneration)` if it rewrote, then `; warnings: <names>` |
| `Writer:` | The final writer and model, the prompt hash, and `(the configured LLM failed; see raw_llm_output.json)` if the placeholder wrote it |
| `Knowledge graph:` | `rebuilt for this run (Neo4j); N insights used`, `unavailable: <why>` or `off` |

### Weights & Biases (live: group `weekly-pipeline`, job `main`; backtest: group `digest-dev`, job `backtest`)

| Where | Keys | What they tell you |
|---|---|---|
| Config | `prompt_hash`, `llm_provider`, `llm_model`, `word_budgets` | What produced the run (the four things to compare) |
| Tags | `llm:<writer>`, `graph:<status>`, `prod` or `backtest` | Filtering |
| Summary: result | `checks_passed`, `checks_failed`, `checks_warnings`, `regenerated`, `banner` | Did it pass, did it need a rewrite |
| Summary: writer | `llm/final_writer`, `llm/fallback` | Who wrote the final text (`placeholder` + `true` = a fallback) |
| Summary: usage | `llm/calls`, `llm/latency_s`, `llm/prompt_tokens`, `llm/completion_tokens`, `llm/reasoning_tokens`, `llm/cost_usd`, `llm/providers` | Totals over **all** calls in the run, regeneration included. Absent when no call returned |
| Summary: checks | `check/<name>` (1 pass, 0 fail), `check_issues/<name>` (count) | Which check failed, how often (final attempt only) |
| Summary: length | `words/<section>`, `words_total` | Against the budgets |
| Charts | `words_per_section` (bar: words per section), `check_issue_counts` (bar: issues per check) | At a glance, on mobile too |
| Tables | `check_issues` (check, level, section, token, reason, sentence), `season_scorecard`, `game_outlook` | The exact sentences that tripped a check |
| Artifact | `digest` / `digest-backtest`, alias `<season>-w<NN>` (backtests: `...-<writer>`), holding the four files above | Reproducing a run |

The `check_issues` table and `check/*` keys describe the **final** attempt. The attempt that triggered a regeneration is only in `checks.json` → `first_attempt`.

## 8. Security

- **The key** is read only through the settings module: `EnvSettings.openrouter_api_key`, a pydantic `SecretStr`. It is unwrapped in exactly two places: the request header (`openrouter._headers`) and `nfl doctor`'s status-only call to OpenRouter's `/key` endpoint.
- **Sent only in the header.** `Authorization: Bearer ...`, over HTTPS to `openrouter.ai`. It isn't in the request body, the payload, W&B config, or any saved file.
- **Never logged.** If the variable is unset, the only message is "OPENROUTER_API_KEY is not set (check with `nfl doctor`)".
- **Provider error text is never stored.** A provider's message could echo the request, including the key. So `LLMError` messages are built only from fixed text, the HTTP status and a short machine code ("HTTP 404: error code 404", or "provider error"). Other exceptions are reported by type name only. `raw_llm_output.json`, `checks.json` and W&B get only these safe strings. A test (`tests/digest/test_openrouter.py`) checks a fake key never appears in the notes or `checks.json`.
- **Check the key with `uv run nfl doctor`,** which prints `set`, `not set` or `accepted`, never a value. Don't open the env file or print variables to debug a key. If a value is ever exposed, stop, tell Rishi and rotate it.
- **What leaves your machine:** the whole digest payload (7,700 to 9,000 tokens: model outputs, public football statistics, ESPN headlines on live runs, your followed teams) goes to OpenRouter and to whichever allowed endpoint serves the call. There are no credentials in it. `llm.openrouter.only` is how you limit who sees it.

## 9. Known limitations and lessons

1. **The checks prove origin, not meaning.** Every number must come from the payload and sit next to its owner. Nothing proves the sentence says the right thing. In P04, all four GLM digests had every number correct and **all four still had a meaning error** (made-up superlatives, a Colts / Titans home swap, flattened consensus tiers, trends read as levels, "has taken over from" last season's QB, a bare "1st" defense rank read as "toughest"). The fixes were in code, not in the checks (section 4, "Meaning stays in code"). After a prompt, payload or model change, **read the prose against `payload.json`.**
2. **A defense rank needs its direction in the string.** The bare "1st" (meaning weakest) was written as "the 1st-ranked pass defense", and GLM called a 17th-of-32 defense "a tougher matchup". The display is now "league's weakest", "3rd-weakest", "4th-strongest", and "about league-average (...)" for the middle of the league (`build.defense_rank_note`). The same goes for any sign or unit.
3. **Small numbers are owned by coincidence.** A number counts as "owned" if any display string of a named entity contains it, with no check of *which* stat it came from. Tested on the 2026 week 4 payload: the Raiders own a "3" (from "over the last 3 weeks"), so "The Raiders had 3 sacks last week" passes both provenance and binding, while "7 sacks" fails. Two stats of the same owner can also be swapped (the driver's 0.09 placed where the trend's 0.12 belongs) and pass. The defenses are code-made whole phrases and prompt rules 17 and 18 ("copy evidence strings and driver changes whole, with their unit"). They reduce the risk but don't remove it. The only full fix would be per-field binding, which no check does today.
4. **Latency at `reasoning_effort: max` is large and unpredictable.** 2.5 to 21 minutes in P04, and 9 to 35 minutes per digest in the P09 backtests (one call of 13–23 minutes, a regeneration adds 1–12 more), almost all of it hidden thinking. The graph picks skip games kicking off within 90 minutes for this reason. A lower effort (`high`, `medium`) is untested (Rishi kept `max` in P09): if you try it, run the comparison in section 6 before going live.
5. **Token headroom.** The P09 week 9 backtest's first call used 51,293 completion tokens, so `max_tokens` went from 64,000 to **96,000** (every allowed endpoint takes 131,072). A truncated reply is retried once, and a second truncation falls back to the placeholder. Watch `finish_reason` in `calls`. **Also watch `reasoning_tokens`:** a 0 with `reasoning_effort: max` means the endpoint skipped the thinking (DeepInfra did on 3 of 5 calls, so it left the list in P09).
6. **The sentence splitter mostly, not always, splits after "Jr." and "Sr.".** (Since P09 it also splits after a closing quote or bracket, `.'` or `.)`, where GLM ended quoted driver sentences.) The checks work sentence by sentence. A name ending in "Jr." or "Sr." does not end a sentence unless the next word is a common opener (The, A, In, His, Their, It, But, With, Last, Week ...). Followed by a team or player name, the two sentences stay merged ("Michael Penix Jr. Packers had 14"). That only loosens binding for those two sentences and can trip the warn-only name heuristic.
7. **Fixed in P05: warnings that were always on.** `length_short` used to fire whenever a section was code-written (a "nothing to grade" report card is 23 words against a budget of 60). Code-written sections are now left out of the length checks and the whole-digest cap; their words are still counted in `words/*`. The live week-4 digest (run before the fix) still shows that warning.
8. **Backtest files are shared between writers.** `payload.json`, `raw_llm_output.json` and `checks.json` in a run folder belong to whichever writer ran last for that week. Reports are split by provider name, not model. Save what you want to compare (section 6, step 2).
9. **News isn't wired in.** The live payload carries ESPN `news`, and prompt rule 7 says to attribute it, but no section asks for news and the fact index doesn't register it. Any number in a news sentence would fail provenance.
10. **The placeholder is stiff but safe.** If it writes the digest, the prose reads like templates, but it passes every check. That is by design: the guarantees (every number from the payload, owned, no betting language) do not depend on the model.

## Related

- Spec and layout: [06 → Role of the LLM, LLM provider interface, Prompt structure, Automated checks, As built in P04 / P05](../06-weekly-digest.md)
- Decisions: [D53 check rules, D56 OpenRouter and GLM, D60 graph text and picks, D61 the weekly `graph` step, D87 the P09 re-check](../10-decisions-log.md)
- P09 (closed 2026-10-05, D87): [P09](../plans/P09-llm-connection.md), the re-check in section 6
- How-to for the digest code (checks, owners, debugging): the `digest-checks` skill at `.claude/skills/digest-checks/SKILL.md`
- Run history and the fact-check findings: [PROGRESS](../plans/PROGRESS.md)
