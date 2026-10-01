# T00: Big Data Bowl Data and Baselines

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P00 (technically). **Recommended start: after P04**, so the weekly digest isn't delayed.
- **Unlocks:** T01
- **Read first:** [07 Track 2](../07-track2-big-data-bowl.md) (all of it), [08 → Track 2 logging](../08-experiment-tracking.md)

## Goal

Big Data Bowl 2026 Prediction data on D: as clean Parquet, with the task confirmed from the actual files, direction-standardized coordinates, week-based splits, an evaluation harness, and the **constant-velocity** and **physics** baselines scored. These are the bars every later model must beat.

## Tasks

### Data
- [ ] 🤖 Check whether **Big Data Bowl 2027** has been announced. Record the result in PROGRESS and in decision D11.
- [ ] 🧑 **Rishi** accepts the competition rules on Kaggle (needs Rishi's account) and adds the Kaggle token to `.env`.
- [ ] 🤖 Download with the Kaggle CLI to `{NFL_DATA_ROOT}/bdb/raw/`. Record file list, sizes and checksums.
- [ ] 🤖 **Confirm the task from the files and the competition's Data/Evaluation tabs:** column names, which players are predicted, the meaning of the output frames, ball landing fields, seasons and weeks included, the exact metric. **Update [07](../07-track2-big-data-bowl.md)** with the confirmed facts (replace "confirm on download").
- [ ] 🤖 CSV → Parquet partitioned by week (`bdb/parquet/week=NN/`); dataset tag `bdb2026-v1`; W&B artifact reference.
- [ ] 🤖 Direction standardization (all plays left to right; adjust `dir` and `o`) + unit tests on a hand-made example.

### Splits and harness
- [ ] 🤖 Week-based train / val / test splits ([07 → Splits](../07-track2-big-data-bowl.md#splits)); assert no game appears in two splits.
- [ ] 🤖 `track2/evaluate.py`: the competition metric + breakdowns by role, horizon (frame index) and pass depth; trajectory plot helper (predicted vs actual on a field), for a **fixed set of 20 sample plays** reused by every model.

### Baselines
- [ ] 🤖 Rung 0: constant velocity from the last pre-release frame.
- [ ] 🤖 Rung 0b: physics heuristic (the targeted receiver accelerates toward the landing spot up to a max speed; defenders move toward the landing spot or the receiver). Its few parameters are tuned on the training split only.
- [ ] 🧑 **Rishi runs** `uv run nfl track2 baseline --rung 0` and `--rung 0b`, and looks at the W&B charts and trajectory images.
- [ ] ✋ **Checkpoint:** close T00.

## Rishi-in-the-loop moments: what to look for

- **RMSE by horizon:** error should grow with time in the air. The physics baseline should mainly help at longer horizons.
- **Trajectory images:** where does constant velocity fail? Usually receivers breaking toward the ball, and defenders reacting. That's exactly what the learned models must capture.
- Compare with the public reference (~1.70 yd for constant velocity on a held-out week; see [07](../07-track2-big-data-bowl.md)). A big mismatch may mean a bug in the metric or the standardization.

## Exit criteria

| Criterion | Verify |
|---|---|
| Data on D: as Parquet, task confirmed | [07](../07-track2-big-data-bowl.md) updated; dataset artifact in W&B |
| Splits leak-free | Test passes |
| Both baselines scored on validation | W&B `track2-bdb` runs with RMSE overall and by horizon and role |

## Handoff to T01

- The evaluation harness and the 20-play visual set, plus the baseline numbers to beat.
