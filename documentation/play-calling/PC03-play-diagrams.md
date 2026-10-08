# PC03: Play diagrams and the play browser

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Play calling rows)

- **Depends on:** PC01 (the team page the browser hangs off)
- **Unlocks:** the "click play and watch it" part of the idea
- **Read first:**
  - [Play calling README](README.md) §1, §2 (no public tracking for 2024–2026) and §3;
  - [07 Track 2: Big Data Bowl](../07-track2-big-data-bowl.md) and [T00](../plans/T00-bdb-data-and-baselines.md) (how BDB data is meant to be stored; T00 is on hold, so this phase loads only what it needs, in T00's layout);
  - the [season operations guide](../guides/season-operations.md) §9 (Big Data Bowl 2027).

## Goal

On a team page, a **play browser** lists that team's plays for a chosen week. Clicking one plays a short animation that reconstructs it from the charting data:
- **before the snap:** hash, QB under center / shotgun / pistol, backfield count, box count, motion;
- **after it:** rushers coming, then the ball's path to the target spot or the run lane, with the result.

It's clearly labelled "**Reconstructed from charting data, not player tracking**". Where the data's terms allow, a "**See a real one**" button plays a real tracked play of the same concept from the Big Data Bowl (2022–2023 seasons).

## Scope

- **In:**
  - `playcalling/diagrams.py` (geometry as JSON);
  - the play browser and the animated SVG player in the web app;
  - a mockup (✋ first);
  - the Big Data Bowl terms check (✋) and, if allowed, a small concept library;
  - a re-check of Big Data Bowl 2027.
- **Out:** inventing details the data doesn't hold (receivers' routes other than the target's, defenders' assignments). The diagram shows only what's known and says so.

## Tasks

### Probe first
- [ ] 🤖 **What a reconstruction can show** for 2026 plays (pbp + FTN) and for 2016–2025 plays (+ participation: formation, personnel, coverage shell, target's route). List each element as known / approximated / unknown:
  - **known:** the hash, QB alignment, backfield count, box count, motion flag, rushers, ball direction and depth, run gap, gain, result;
  - **approximated:** where receivers line up (from personnel and formation, generic spots);
  - **unknown:** routes other than the target's, coverage assignments.
- [ ] 🤖 **Big Data Bowl terms:** read the Kaggle rules for the BDB 2024–2026 competition data (personal, non-commercial research use? keeping it after the competition? showing clips in a private local app?).
- [ ] 🤖 **Big Data Bowl 2027:** check Kaggle and NFL Football Operations; the 2026-10-08 lead was a university event listing for 2026-10-09. Record what it offers (seasons, tracking or not) and update the season calendar's ✋ step.
- [ ] ✋ **Rishi decides:** build "See a real one" (if the terms allow), and whether that pulls part of T00 forward.

### Mockup first
- [ ] 🤖 **Mockup:**
  - the play browser (filters: week, down, play type, result);
  - the diagram player (field, pre-snap picture, play / pause / step, the label);
  - "See a real one" (if going ahead);
  - both themes.
- [ ] ✋ **Rishi approves the mockup.**

### Build
- [ ] 🤖 **`diagrams.py`:** for a play, the geometry the browser animates, in field coordinates:
  - line of scrimmage, hash, first-down line;
  - offense: OL spots, QB depth by alignment, backfield players, receivers in generic spots by personnel and formation (marked approximate), the motion arrow;
  - defense: box defenders by count, rushers marked;
  - the path: a pass arc to the target spot (direction × air yards) then the run after the catch to the end yard line; or the ball carrier through the gap to the gain;
  - the text: pbp `desc` (cleaned), down and distance, the result.

  Unit-tested on a pass, a run, a screen, a play-action deep shot, a sack and a scramble.
- [ ] 🤖 **Endpoints:**
  - `GET /api/playcalling/teams/{team}/plays?season=&week=&filters`: the list;
  - `GET /api/playcalling/plays/{game_id}/{play_id}/diagram`: the geometry; `game_id` is validated by pattern and existence.
- [ ] 🤖 **Web:**
  - the play browser on the team page;
  - an SVG play player (pre-snap → snap → path, about 3–4 s, play / pause / step, reduced-motion respected);
  - the "Reconstructed" label always on.
- [ ] 🤖 **If approved, the concept library:**
  - load the needed BDB files in T00's raw layout (`raw/bdb/<edition>/…`);
  - pick about 10 real examples per concept (play-action deep shot, screen, RPO, inside zone / outside zone runs, Cover 1 / 3 / 2-man looks);
  - write their frames compactly to `playcalling/bdb_library/` (one JSON per play);
  - play them in the same player (real dots, real paths, labelled "Real play: 2022 week 6, Big Data Bowl data").
- [ ] 🤖 **Tests** for the geometry, the endpoints, the player states.

### Verify and close
- [ ] 🤖 **Mockup parity**; Codex / Sol review; fixes with tests.
- [ ] 🤖 **Docs:** the guide (the play browser, what's known vs approximated, the BDB library and its terms); `guides/control-room.md`.
- [ ] 🤖 Production-unchanged check.
- [ ] ✋ **Close PC03**, which closes the Play calling track.

## Rishi-in-the-loop moments (what to look for)

- **The mockup and first plays:** does the reconstruction feel like a playbook card? Is the "approximate" marking clear without being noisy?

## Exit criteria (+ how to verify)

- Any 2026 play of any team can be browsed and played as a reconstruction.
- The BDB decision is recorded (and the library built if approved).
- Tests, ruff and web checks pass; the production-unchanged check passes.

## Pitfalls / notes

- **Never present a reconstruction as real.** The label is part of the component, not optional.
- **BDB data is large** (tens of GB uncompressed for a full edition): load only the plays the library needs and keep the rest on Kaggle until T00.
- **pbp `desc` names players.** That's fine locally, and it passes through `clean()` like every text.
