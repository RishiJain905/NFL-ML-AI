# T02: BDB Sequence Model

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** T01
- **Unlocks:** T03
- **Read first:** [07 → Model ladder, training notes](../07-track2-big-data-bowl.md#model-ladder-each-rung-must-beat-the-one-before)

## Goal

A neural sequence model (GRU or a small Transformer encoder over the pre-release frames → a decoder for the future frames) that beats T01. Trained on Rishi's GPU machine (or Colab).

## Tasks

- [ ] 🤖 Add `torch` (CUDA build matching Rishi's GPU) as an optional dependency group, `track2-gpu`.
- [ ] ✋ **Checkpoint:** confirm the GPU machine setup (local vs Colab), and where the data is read from (D: locally, or an uploaded Parquet subset for Colab).
- [ ] 🤖 PyTorch `Dataset` streaming from Parquet; per-player sequences with play-context features; tensors in standardized coordinates.
- [ ] 🤖 Model: an encoder (GRU or Transformer over time) + a decoder predicting the **residual over the physics baseline** for each output frame. Huber loss; masking for variable output lengths.
- [ ] 🤖 Augmentation: flip the field top to bottom (y, dir, o).
- [ ] 🤖 Training loop with W&B: loss per step and per epoch, validation RMSE by horizon each epoch, trajectory images for the 20 fixed plays every N epochs, checkpoint artifacts.
- [ ] 🧑 **Rishi runs** the first training run on the GPU and watches the curves live.
- [ ] 🧑 **Rishi runs** a short sweep (learning rate, hidden size, layers, dropout).
- [ ] ✋ **Checkpoint:** close T02.

## Rishi-in-the-loop moments: what to look for

- **Train vs validation loss:** if validation flattens or rises while training keeps falling, that's overfitting. Add dropout, augment more, or stop earlier.
- **Learning-rate sensitivity:** if loss spikes or goes to NaN, lower the learning rate or add gradient clipping.
- **Trajectory images across epochs:** predictions should go from straight lines (baseline-like) to curving toward the ball.

## Exit criteria

| Criterion | Verify |
|---|---|
| Beats T01 on validation RMSE | W&B comparison |
| Reproducible | Config + checkpoint artifact; rerun within noise |
