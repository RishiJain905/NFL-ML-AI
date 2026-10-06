# Control room mockup (the visual spec)

**Live mockup:** <https://claude.ai/artifact/1z7AapJzJwKusNtWzXYfQw> (private to Rishi's account; agents read it with the Artifact tool's `read` action). Approved on 2026-10-05; the spec that goes with it is [`../README.md`](../README.md).

This folder holds its source, so the design is versioned with the docs:

| File | What it is |
|---|---|
| `template.html` | The page skeleton with three placeholders: `{{CSS}}`, `{{JS}}`, `{{DATA}}` |
| `styles.css` | Every colour token, for **Turf & Pylon** and **Playbook** × dark / light, plus the component styles. Port these tokens to `web/` as they are |
| `app.js` | The whole mockup in plain JavaScript: shell, week tabs, the three pipeline views (drive chart, pipeline map, timeline), MLOps sections, season pages, a simulated run, small SVG charts |
| `build_mockup.py` | Reads the data from the data root, embeds it, and writes one HTML file |

## Rebuild it

```powershell
uv run python documentation/control-room/mockup/build_mockup.py          # with the W&B artifact list
uv run python documentation/control-room/mockup/build_mockup.py --no-wandb
```

- **What it reads:**
  - the **2026 week 4** run folder and digest;
  - the week-5 slate;
  - Elo and ratings;
  - the 2026 accuracy scoreboard;
  - the 2025 game and player backtests;
  - the week-4 ingest manifest and the quality file;
  - optionally, the live W&B artifact list (through the project's settings helper; the key is never printed).
- **What it writes:** `{NFL_DATA_ROOT}/cache/control-room-mockup/control-room.html`. Nothing else is written, and no data goes into git.
- **To update the published mockup:** open that file, check it, then publish it to the same artifact URL. From a new conversation, publish with the URL as `url` so the link stays the same.

## What's real and what's sample

- **Real:** everything on week 4 (digest, games, players and watch list, graph insights, step times, checks, LLM calls and cost, W&B run links, the ingest and quality tables, the artifact versions), the week-5 slate, Elo and ratings, and the 2025 season charts.
- **Sample (labelled on the page):**
  - the week-4 **Results** tab (made-up finals and box-score numbers; week 4 wasn't graded yet);
  - the simulated week-5 run (its step times are realistic, but "10:14 AM", "8m 41s", "$0.012", "≈17.4k nodes" and "4 insights" are made up);
  - the **Health** page statuses;
  - the "Tough spots" and the Models page numbers are copied from the week-4 digest and the model cards.
- **Mockup only:**
  - the striped **"Mockup controls"** strip (the week-5 state picker);
  - the run's 1× / 4× speed and "Skip to end".

  In the app, a run takes as long as it takes.

## Changing the design

Design changes start here. Edit the source, rebuild, republish to the same URL, then update `../README.md` (and the phase files if a screen changed), then the code.
