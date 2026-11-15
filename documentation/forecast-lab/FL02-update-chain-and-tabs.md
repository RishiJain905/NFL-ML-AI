# FL02: the update, the Run-week chain and the tabs

**Track:** [Forecast lab](README.md) · **Status:** see [PROGRESS](../plans/PROGRESS.md) · **Decision:** D158

**Read first:** the track [README](README.md) §3–§6, the [control room guide](../guides/control-room.md) §2b (running the week), §2f (the Update play calling button, the closest precedent) and §5 (the safety rules), the `control-room` and `weekly-ops` skills.

## What it delivers

One press of **Run week N** refreshes everything: the weekly run goes exactly as today, and when it ends well the app starts the Forecast lab update. The same after the Saturday injury update. Two new week tabs show the results.

## Tasks

- [x] 🤖 `forecastlab/update.py`: `plan_update` (what's due and why, read-only) and `run_update` (availability view(s), curves, last week's grades; each step stands alone).
- [x] 🤖 `nfl forecastlab update --auto [--expect-week N] [--dry-run]` (holds the weekly-run lock; exit codes as `nfl playcalling update`), plus `backtest`, `predict`, `grade`.
- [x] 🤖 The runner's fifth kind `forecastlab_update`, the pre-flight gate `forecastlab`, and the follow-up the app starts after a successful `weekly`, `resume` or `injury_update` run (`AppSettings.chain`, on in `nfl app`).
- [x] 🤖 Readers and endpoints: `GET /api/weeks/{S}/{W}/availability`, `/outcomes`, `/outcomes/curve`.
- [x] 🤖 The mockup on real data (✋ approval waived, D158), then the two tabs, the Update control, and the line in the Run dialogs that says what follows.
- [x] 🤖 Tests: the plan case by case, the run, the CLI, the gate, the kind, the follow-up (starts once, never loops, never from a GET, never in rehearsal mode, refused over Tailscale).
- [x] 🤖 The production-unchanged check (the guard test, a zero-line protected diff and an import check; **no old-vs-new rehearsal was run**, see As built).
- [x] 🤖 The guide and the model cards (sonnet-xhigh, at Rishi's request), the control-room guide and skill, the runbook's Tuesday and Saturday lines.

## Rules the follow-up keeps (D158)

- It starts only from the press that started the run: a thread begun by that `POST /api/run`. No page load and no GET starts anything.
- It waits for the run the app launched to exit with code 0. A failed run, a "not ready" stop, a held lock or a lost child starts nothing.
- It runs only what's due. With nothing due (or no promoted model) it does nothing.
- It never starts after a play-calling update or after itself.
- Never in rehearsal mode; over Tailscale every write is still refused (D157).
- A failure in it never touches the published week: the tabs show the Update button instead.
- A run started from a terminal has no follow-up: use the tabs' Update button or `uv run nfl forecastlab update --auto`.

## As built: deviations from the task list

Decision: D161. Guide: [forecast-lab.md](../guides/forecast-lab.md) §6-§8; the control-room guide §2h.

- **A view is re-made only for a reason.** The Tuesday view is made once; it is made again only when the week's digest is newer than it (the week was run again) and no injury update has run. The Saturday view is made once the injury update has run, and again if it runs again. The first plan compared the view with the curated snap counts' file time: every curate rewrites that file, so Wednesday's play-calling refresh would have reopened the step for nothing (the test worker's find).
- **Each part stands alone.** Inside a step each view and each family has its own try: a failed Saturday view keeps the Tuesday one, a failed availability grade doesn't block the curves' grade. The step is failed and its detail says what worked.
- **`follows` and `chain`.** A chained run records the run it came after (`follows` on the app's record and on every run info), so the page can say "started by the app after the week's run"; the pre-flight answer has `chain` (does this app start the follow-up?), and the Run dialogs show their extra line only when it is true. Both asked for by the UI worker.
- **The follow-up belongs to the run's week.** If the calendar's week changed while the run was going, it stops.
- **One team, one stat per answer.** The readers send one team's players (`?team=`) and one stat's curves (`?target=`): the full answers were about 1 MB each.
- **The might-sit list** is not the file's `at_risk` flag (377 rows in week 10): it is every player with a game status, plus regulars (30% of the snaps over their last 2 games) with a low chance, without practice-squad players; grouped report / uncertain / reserve and ordered by the playing time at stake. 124 rows in week 10's Tuesday view.
- **"Who inherits"** drops teammates who are themselves unavailable (reserve, Out, Doubtful), not teammates with a low chance of playing: a backup quarterback's chance is low only while the starter plays.
- **Mockups** (approval waived; the lead stood in): built from the real week-10 answers by `mockup/dump_payloads.py`; the Saturday view and the after-the-games states use synthetic rows, marked as such.
- **The production-unchanged check** was the guard test, a zero-line diff under the protected folders, and an import check (none of the 86 production modules loads `nflengine.forecastlab`). No rehearsal of the weekly run was run: the weekly path's code and settings blocks are byte-identical by the guard's own hashes.
- **Not done here, on their dates** (PROGRESS → Season calendar): the first follow-up after a real Run week N and the first live grades (Tue 2026-11-17); the first Saturday view on a complete report.
- **Review fixes.** The test worker's finds: a 500 on a hand-edited run record whose id wasn't text; a crash on a wrong-shape meta file; one family's failed grade blocking the other; the re-make rule above; `KeyError: x` masked by the scrubber; a field named `target_key` failing the leak scan (any field name containing "key" is masked: the Outcomes answers call it `stat`). The read-only review's (opus-high; it also rebuilt 2022's availability baseline and 15 week-10 curves by brute force, identical): every string of the tabs' answers now goes through `clean()` and the five GETs are in the leak scan; a damaged or foreign file is an empty state, never a 500; the shipped gate also covers "who inherits" and the curves; `run_predict` never writes results and a row made after kickoff is filled but left out of the live score; a week is graded in pieces (re-graded while rows of final games wait for results; the step looks 3 weeks back, so a missed week is picked up); the JSON writes retry on a held file and the curves' week file is written under a file lock; the injury-update dialog no longer promises a curves refresh.
- **Known limits.** The outcome curves don't follow Saturday's news: the injury update doesn't rewrite the week's player projections, so a player ruled Out on Saturday keeps his Tuesday curve (the Availability tab shows him Out). Live, the availability model's calibration layer is the one saved at promotion, while the backtest refit it weekly. A game whose snap counts never arrive keeps its week due for grading for three weeks (each update re-grades it; harmless).
