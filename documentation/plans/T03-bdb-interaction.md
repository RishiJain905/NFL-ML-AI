# T03: BDB Interaction Model (Attention Across Players / GNN)

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** T02
- **Unlocks:** T04
- **Read first:** [07 → Model ladder (rung 3)](../07-track2-big-data-bowl.md#model-ladder-each-rung-must-beat-the-one-before)

## Goal

Model player **interactions**: receivers react to defenders and the ball, defenders react to receivers. Use a Transformer across all players in a play (attention over players at each time step), or a graph neural network with players as nodes and distance- or role-based edges. This is where strong public solutions ended up.

## Tasks

- [ ] 🤖 Architecture: a factorized encoder (over time per player → attention across players, with role and side embeddings), then a decoder for the residual future trajectories. Optional GNN variant (PyTorch Geometric) with k-nearest-neighbor edges, for comparison.
- [ ] 🤖 Reuse the T02 data pipeline (play-level batching with all players).
- [ ] 🧑 **Rishi runs** training and a sweep on the GPU machine, live in W&B.
- [ ] 🤖 Final **test-split** evaluation, run **once** for each of rungs 0 / 0b / 1 / 2 / 3, giving one comparison table and one chart.
- [ ] 🧑 **Rishi reviews** the final ladder comparison and trajectory images.
- [ ] ✋ **Checkpoint:** close T03 (beats T02, or a documented reason it didn't).

## Rishi-in-the-loop moments: what to look for

- **Attention maps** (optional visualization): which players does the targeted receiver's prediction "look at"? It's often the nearest defender and the ball. A nice interpretability check, and a good learning exercise.
- **Gains by role:** interaction models usually help the **defenders'** predictions most, since defenders react to others.

## Exit criteria

| Criterion | Verify |
|---|---|
| Ladder table on the test split for all rungs | W&B report "Track 2 ladder" |
| T03 beats T02, or the reason is documented | Report + PROGRESS note |
