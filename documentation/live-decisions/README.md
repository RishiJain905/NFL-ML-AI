# Live decisions: the 3rd- and 4th-down bot

Status → see the **Live decisions** rows in [plans/PROGRESS.md](../plans/PROGRESS.md). Decisions: D107–D109 in the [decisions log](../10-decisions-log.md). Planned 2026-10-08.

While Rishi watches a game, he opens the control room, picks a game in progress and, on a 3rd or 4th down he cares about, presses **Check this play**. Within a second he sees what the numbers say:
- **4th down:** go for it, kick or punt, the win probability after each choice, and how sure the bot is;
- **3rd down:** the chance they convert, the chance they pass, and "if they're stopped short", the 4th-down call for each possible distance.

Next to the call he sees whether this team tends to pull it off: its conversions, its kicker, its coach's habits. It's its own track, **LD00–LD03**, with its own new models. The **existing models stay exactly as they are** (D107).

## 1. What was agreed (Rishi, 2026-10-08)

| Topic | Decision |
|---|---|
| Live data | **ESPN's public (unofficial) site API**, polled from the app's server. Rishi accepted the terms caveat (Disney's terms bar automated access; personal, low-volume use) and the risk that it changes without notice (D109) |
| When it computes | **Only when Rishi presses "Check this play"** on the game he picked. No automatic alerts. Several games can be live at once; he chooses which one and which plays matter (tied game, 4th & 5, 30 seconds left) |
| Downs | 3rd and 4th downs. A check on 3rd down also shows the 4th-down call for every distance they could be left with, so the answer is ready even when ESPN's feed is late |
| Where it lives | A new **Game day** week tab, added after **Graph**. Existing tabs keep their order and content (D108) |
| Models | All new: win probability, 3rd/4th-down yards gained, field goal, punt, and a situational pass model. Trained, tracked and versioned on their own. They read curated data and may read published outputs (market lines, team ratings), and never change them (D107) |
| Speed | Inference is well under a millisecond per decision (a probe: 0.14 ms for a full go / kick / punt decision with LightGBM in the project's venv). ESPN's feed is the slow part |

## 2. What the research found (2026-10-08)

**ESPN's live feed** (field names checked against this season's archived scoreboards):
- `events[i].competitions[0].situation`: `down`, `distance`, `yardLine`, `downDistanceText` ("3rd & Goal at LAR 2"), `possession` (team id as a string), `possessionText`, `isRedZone`, `homeTimeouts`, `awayTimeouts`, `lastPlay.{text, type.text, probability.homeWinPercentage, start / end.yardLine}`. It describes the **next snap**, and it's absent before and after a game.
- Clock and score: `competitions[0].status.{clock, displayClock, period, type.state}` (pre / in / post), `competitors[k].{score, homeAway, team.id}`.
- Endpoints:
  - the full scoreboard: 22 KB gzipped, cached by ESPN for 5 s;
  - one game, `.../scoreboard/{eventId}`: 3.4 KB, cached 1 s. **Use this for a check**;
  - `summary?event=ID`: 52 KB, with every play (`drives.previous[].plays[]`: start / end state, clock, text, `wallclock` = snap time), ESPN's own win probability per play and DraftKings lines.

  Responses took 60–170 ms; no rate limiting was seen (10 quick calls).
- **Quirks:**
  - `yardLine` counts from the **home** team's goal line, not the offense's.
  - `yardsToEndzone` was wrong in about 1 snap in 10 (0 on timeouts, mirrored on home punts): derive yards to goal from `yardLine` and possession.
  - Timeouts in goal-to-go read `down = -1, distance = 0`.
  - The summary's plays have no timeouts remaining (nfl4th parses "Timeout #N by TEAM").
  - Team codes differ from nflverse (WSH / WAS, LAR / LA).
  - Fake punts are typed "Rush" with "(Punt formation)" in the text.
  - No wind in the feed (temperature and conditions only); `venue.indoor` exists.
- `api.nfl.com` needs a token (HTTP 401). The licensed option is Sportradar (2 s updates, paid).

**Timing:**
- In week 4 a 3rd-down snap came a median 42 s before the next 4th-down snap (p10 36 s, p90 51 s, 219 pairs).
- ESPN's feed was seen trailing the snap by **8, 12, 30 and 84 s** in archived snapshots, plus up to 5 s of cache.
- TV delay at Super Bowl LX: antenna ~19 s, cable 38 s, Peacock 48 s, YouTube TV / Hulu 53 s, NFL+ 62 s ([The Desk](https://thedesk.net/2026/02/stats-perform-phenix-latency-super-bowl-lx/)). On a stream the feed usually beats the picture; on cable or an antenna it sometimes won't. **Hence the 3rd-down "if stopped short" table, and a clear "as of" stamp on every answer.**

**How others do it:**
- **`nfl4th`** (Ben Baldwin, [github.com/nflverse/nfl4th](https://github.com/nflverse/nfl4th)):
  - a yards-gained model with 76 classes, trained on 3rd and 4th downs 2014–2019 (down, distance, yard line, era, roof, spread, total);
  - a field goal GAM on distance;
  - an empirical punt distribution;
  - win probability averaged from nflfastR's `vegas_wp` and a second model.

  Its live bot polls ESPN's summary every minute and posts **after** the play. It still assumes the opponent starts at its own 25 after a score; since 2025 a touchback comes out to the **35**.
- **NGS Decision Guide** (AWS): XGBoost with 17 yardage classes, team EPA features, Brier 0.21.
- **"Analytics, have some humility"** (Brill, Yurko & Wyner, [arXiv 2311.03490](https://arxiv.org/abs/2311.03490)): with bootstrapped uncertainty only **48%** of 4th-down calls in 2018–22 were clear-cut, and 44% differ by under 1 win-probability point. So the bot labels **toss-ups**.
- **nflfastR's WP model** ([write-up](https://opensourcefootball.com/posts/2020-09-28-nflfastr-ep-wp-and-cp-models/)): 12 features plus the spread; calibration error 0.0055–0.0066. Our curated `plays` already carry `wp`, `vegas_wp` and `xpass`: ready-made baselines.

## 3. Screens

**Game day** (week tab, after Graph):

| State | Shows | Reads | Phase |
|---|---|---|---|
| Current week, games on | The week's games (upcoming / live / final) with score, clock and down & distance, refreshed slowly while the tab is open. Click one → its panel: score, clock, possession, timeouts, ESPN's situation and when ESPN last updated, our pre-game win % and the market spread. **Check this play** → the call card | ESPN scoreboard (server side, cached), the week's `predictions_games.parquet` (read-only), curated `lines` | LD02 |
| The call card: 4th down | Go / Field goal / Punt with the win probability after each, the gain over the next best, a **Confident / Lean / Toss-up** label, the odds behind it (convert %, make %, the punt's expected landing spot), and "**Is this team good at this?**" (season and last-season 4th-down and short-yardage conversions, the kicker's makes by distance, the coach's go rate in spots like this) | The LD00 models (in memory), curated plays and player stats as of last week | LD02 |
| The call card: 3rd down | Chance they convert, chance they pass, and the **if-stopped table** (4th & 1 … 4th & 10 from where they'd be: the call and the gain for each) | Same | LD02 |
| A finished week | **Decision review:** every 4th down of the week, the bot's call against the coach's, the win probability it cost or gained; the most aggressive coaches; the bot's own calibration on real plays | Curated plays (read-only), the LD00 models | LD03 |
| A week before its games | The slate with kickoff times, "Game day opens at the first kickoff" | The calendar | LD02 |

## 4. Architecture

```mermaid
flowchart LR
    B[Browser<br/>Game day tab] -- "GET /api/live/..." --> A[FastAPI<br/>127.0.0.1:8765]
    A -- "on a click only<br/>(min 2 s apart per game)" --> E[ESPN site API<br/>allowlisted host]
    A --> M[LD00 models<br/>in memory]
    A --> R[Readers<br/>read-only]
    R --> D[(D:\nfl-ml-data<br/>curated, runs, models/live-decisions)]
    T[nfl live ... CLI] --> E
    T --> M
```

- **`src/nflengine/live/`** (new package): `espn.py` (the client: host allowlist, timeouts, back-off), `state.py` (ESPN JSON → our game state, with every quirk above), `models.py` (load / score), `decide.py` (go / kick / punt; the 3rd-down table), `context.py` ("is this team good at this?"), `replay.py`.
- **Models** under `{NFL_DATA_ROOT}/models/live-decisions/<version>/`, logged to W&B as the artifact `live-decision-models` (with a `production` alias of its own).
- **CLI:** `nfl live train | backtest | games | call --event ID | replay --event ID | latency --event ID`.
- **App:** `GET /api/live/{season}/{week}/games`, `GET /api/live/games/{event}/situation`, `GET /api/live/games/{event}/call`, `GET /api/live/{season}/{week}/review`. The event id is validated (digits, and it must be in that week's scoreboard).
- **What the app writes:** nothing outside `cache/control-room/live/`. "Check" is a GET: it changes nothing.

## 5. Safety rules

On top of the control room's §5 and CLAUDE.md's Security section:
1. **One outbound host:** `site.api.espn.com` (HTTPS). The client refuses any other host. No credentials are sent; there are none.
2. **Polite polling:** a fresh ESPN call only on a click, or a slow list refresh (about 30 s) while the Game day tab is visible; at most one call per game every 2 s; exponential back-off on errors; a fixed User-Agent naming the project.
3. **ESPN text is data:** play descriptions are shown as text (never HTML) and pass through `clean()`.
4. **A broken feed never breaks the app:** the card says "ESPN didn't answer (…), last good state 14 s ago" and still shows the last answer.

## 6. The no-touch rule (D107)

This track adds; it never changes what exists:
- no change to the game, ratings / Elo, player or team-totals models, their features, settings, training, artifacts or `production` aliases, the digest or its writer, the weekly graph, or the weekly run's steps and the Run button;
- new code in `src/nflengine/live/`; new W&B job types and artifacts; new data folders; a new `live:` block in `settings.yaml`;
- shared files change only by adding (a CLI group, a path helper, the app's router and tab list);
- every phase closes with the **production-unchanged check**: the protected-paths test (`tests/test_production_untouched.py`, LD00 creates it) and a rehearsal of the newest published week that reproduces its predictions (`weekly-ops` skill §5c).

## 7. Phases

| Phase | Title | Depends on | Delivers |
|---|---|---|---|
| [LD00](LD00-decision-models.md) | Decision models | P10 | Win probability, yards gained (3rd / 4th down), field goal, punt, pass models; the decision engine; leave-one-season-out backtests vs `vegas_wp` and simple baselines; W&B runs and the `live-decision-models` artifact; model card; the production-unchanged test |
| [LD01](LD01-live-feed.md) | The live feed and replay | LD00 | The ESPN client and state parser; `nfl live games / call / replay / latency`; state parity vs nflverse play-by-play for 2026 weeks 1–5; a measured feed lag on a live game |
| [LD02](LD02-game-day-tab.md) | The Game day tab | LD01, CR03 | Mockup first; the tab, the click-to-check call card with team context; the first live use on a real game |
| [LD03](LD03-decision-review.md) | Decision review and season tracking | LD02 | Finished weeks' decision review, coach aggressiveness, the bot's live calibration and a `decision-review` W&B run per week |

```mermaid
flowchart LR
    P10 --> LD00 --> LD01 --> LD02 --> LD03
    CR03 --> LD02
```

Same 🤖 / 🧑 / ✋ tags and protocol as every phase ([plans/README.md](../plans/README.md)). Commit prefixes `[LD00]` … `[LD03]`. **Mockup first** for LD02 and LD03, as in the control room: the approved mockup is the visual spec.

**Docs each phase leaves:**
- the guide `guides/live-decisions.md` (started in LD00);
- the model card `model_cards/live-decision-models.md` (LD00);
- the skill `.claude/skills/live-decisions/SKILL.md` (LD00);
- the W&B guide's new section (LD00, LD03).

## 8. As built

Each phase adds what it built and any differences from this spec here, with decision numbers.

### LD00: decision models (2026-10-10; D113, D114, D115)

- **Built:** `src/nflengine/live/` = `schema.py` (the `GameState` contract, eras, rules by season), `data.py` (training frames), `models.py` (the six models), `decide.py` (the engine), `train.py`, `backtest.py`; `nfl live train | backtest | call`; `tests/live/` (294 unit tests + the bundle's 7 integration tests); `tests/test_production_untouched.py` (the D107 guard for all three tracks). Guide [`guides/live-decisions.md`](../guides/live-decisions.md), model card [`model_cards/live-decision-models.md`](../model_cards/live-decision-models.md), skill `live-decisions`.
- **Shipped:** `live-decision-models:production` = `2010-2025_20261010T054343Z` (W&B `hxmgioka`). Backtest `p3y90nh0` (the ship decision; re-run as `m70hg3pa` after the Sol fixes, D115, with identical model metrics): win probability within 0.0011 Brier of `vegas_wp` (calibration 0.0048), yards gained 12 / 12 seasons, field goal 10 / 12, pass 6 / 12 (it beats `xpass` in every season `xpass` never trained on; shipped by Rishi, D114).
- **Findings from the LD00 probes (checked live on 2026-10-10):**
  - 31,985 4th downs in 2018–2025 (5,750 attempts); lines, `wp`, `vegas_wp`, `xpass` complete on runs and passes.
  - **Rules eras:** touchback after a kickoff at the 20 (to 2015), 25 (2016–23), 30 (2024), 35 (2025+); the receiver's mean start after a score 23.0 → 25.4 → 29.8 → 30.6 yards from its own goal (2026 weeks 1–4: 30.5); extra point 99.3% → 93–96% from 2015; OT 15 → 10 minutes in 2017; in 2025 no OT game ended on the first possession (both teams possess).
  - **Field goals:** distance = yards to goal + 18 (90%); a miss → the spot of the kick 8 yards back; indoors +3.6 points at 40–55 yards; a kicker's own record adds ~0.1% (not used).
  - **State semantics:** the second-half kickoff goes to the team that didn't receive the opening one (2,227 / 2,227 games); timeouts are never missing on a snap; `games.neutral_site` (not the raw `location`) marks neutral sites.
- **Differences from this spec:** the bootstrap uses 20 refits and only for calls closer than 5 points (the 20 ms budget; D113); the win-probability model is boosted from a logistic base (D113, model card → Tuning); goal-to-go defensive-penalty first downs are left out of the yards-gained model; the bot's live use reads `models/live-decisions/production.json` (the app reads files, not the alias).
- **For LD01:** build a `GameState` (offense's side: `yardline_100` from `yardLine` + possession, both timeouts, the pre-game spread and total from the offense's side, `season`, `home` +1 / 0 / −1, `receive_2h_ko` from the opening kickoff); `GameState` rejects bad input with a readable message; `Engine(load_production()).fourth_down(state)` / `.third_down(state)`.
