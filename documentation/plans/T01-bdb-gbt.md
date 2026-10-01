# T01: BDB Gradient-Boosted Model

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** T00
- **Unlocks:** T02
- **Read first:** [07 → Features, Model ladder](../07-track2-big-data-bowl.md#features)

## Goal

A LightGBM model predicting each player's future displacement (dx, dy) from engineered features. It should beat the physics baseline, and its feature importance should teach us which movement signals matter.

## Tasks

- [ ] 🤖 Feature pipeline (`track2/features.py`), per [07 → Features](../07-track2-big-data-bowl.md#features):
  - relative geometry to the landing spot, the targeted receiver and the nearest players
  - separation and leverage at release
  - motion history over the last 5–10 frames
  - kinematics: time to reach the landing spot, heading change
  - play context
- [ ] 🤖 Target framing: predict the **residual over the physics baseline** for each future frame, or for horizon buckets plus interpolation. Pick one and record why.
- [ ] 🤖 Training script with LightGBM's W&B callback (live curves), early stopping on the validation split.
- [ ] 🧑 **Rishi runs** the first training run, then a small tuning sweep (W&B sweep or Optuna).
- [ ] 🤖 Feature importance + SHAP summary logged; short notes on which signals matter (these feed T04).
- [ ] ✋ **Checkpoint:** close T01.

## Rishi-in-the-loop moments: what to look for

- Validation RMSE vs the physics baseline **by horizon**. The gain should appear across horizons, not just the first few frames.
- SHAP: do separation, heading toward the landing spot and closing speed show up as top features? That's an early sign for the T04 findings.

## Exit criteria

| Criterion | Verify |
|---|---|
| Beats the physics baseline on validation RMSE | W&B comparison chart |
| Feature-importance notes written | `documentation/findings/bdb-notes-t01.md` (short) |

## Handoff to T02

- The feature pipeline and the residual-target framing (the sequence model can reuse the baseline residual idea).
