# 07: Track 2, Big Data Bowl Movement Model

## Status and dataset choice

- **The 2027 Big Data Bowl had not been announced as of 2026-09-30**, so this track uses the **NFL Big Data Bowl 2026: Prediction** competition data on Kaggle.
- Check again when the 2027 edition is announced (the 2026 edition opened in late September 2025). If it brings newer seasons or a related task, consider adding it. The pipeline should be reusable across editions.
- The competition itself is over. It's unclear whether late submissions still work. **Our evaluation doesn't depend on Kaggle:** we use our own week-based holdout.

## The task (confirm the details on download)

What public sources describe. **Confirm every column name and the metric from the competition's Data and Evaluation tabs before writing model code.**

- **Input:** player tracking frames up to the moment the QB releases the ball (10 Hz: `x`, `y`, speed `s`, acceleration `a`, direction `dir`, orientation `o`), plus play context. That includes the **ball landing location** (`ball_land_x`, `ball_land_y`), the number of frames to predict (`num_frames_output`), player role and side, and a `player_to_predict` flag (the targeted receiver and the relevant defenders).
- **Output:** each flagged player's `(x, y)` for every frame while the ball is in the air.
- **Metric:** RMSE in yards over the predicted x and y positions.
- **Seasons:** training data is from historical seasons (sources say 2023, with some mentioning 2024). The live leaderboard was scored on 2025 Weeks 14–18. Check what's actually in the download.
- **Public reference points (not verified):** one public solution reports a constant-velocity baseline of ~1.70 yd RMSE on a Week 18 holdout, and a strong deep model at ~0.84 yd. These are rough targets only.

Note: the task predicts positions **while the ball is in the air**. It does **not** predict yards after catch. Signals like "expected separation at the catch point" can be derived from it; yards after catch can't.

## Data handling

- Download once with the Kaggle CLI (accept the rules on the site first). Store under `{NFL_DATA_ROOT}/bdb/raw/` on D:, never in the repo.
- Convert the CSVs to Parquet partitioned by week (`{NFL_DATA_ROOT}/bdb/parquet/week=NN/`). Query with DuckDB or Polars lazily. Never re-read the CSVs.
- **Standardize direction:** flip coordinates so every play goes left to right, and adjust `dir` and `o` to match.
- Make a dataset version tag (`bdb2026-v1`) and log it as a W&B artifact reference.

## Splits

- **By week, never by play:** for example, train Weeks 1–14, validate Weeks 15–16, test Weeks 17–18 (adjust to the weeks actually included). No game appears in more than one split.
- The test split is touched **rarely**: once per model family, at the end.
- Also report validation RMSE for each week, to see how much it varies.

## Features

- **Relative geometry:** each player's position and velocity relative to the ball landing spot, the targeted receiver, and the nearest 1–3 opponents and teammates.
- **Separation and leverage:** receiver–nearest-defender distance and angle at release; whether the defender is inside or outside, ahead or behind relative to the landing spot.
- **Motion history:** the last 5–10 pre-release frames of velocity and acceleration (changes in speed and direction), not just a single snapshot.
- **Kinematics:** time needed to reach the landing spot at the current speed, the angle between current heading and the landing spot, how fast heading is changing.
- **Play context:** pass depth, time the ball is in the air (from `num_frames_output`), player role, coverage (if provided), down and distance.

## Model ladder (each rung must beat the one before)

| Rung | Model | Notes | Hardware |
|---|---|---|---|
| 0 | **Constant velocity** from the last pre-release frame | The baseline every model must beat | Laptop |
| 0b | **Physics heuristic:** targeted receiver accelerates toward the landing spot up to a max speed; defenders move toward the landing spot or the receiver | Cheap and often surprisingly strong; sets a better bar than rung 0 | Laptop |
| 1 | **LightGBM** predicting displacement (dx, dy) for each future frame (or for horizon buckets plus interpolation) from engineered features | Fast to iterate; feature importance teaches us things | Laptop |
| 2 | **Sequence model:** GRU or a small Transformer encoder over pre-release frames → decoder for future frames | Learns motion patterns directly | GPU machine / Colab |
| 3 | **Interaction model:** Transformer across players (attention over all 22 players per frame), or a graph neural network with players as nodes and distance-based edges | Movement is driven by interaction; this is where the best solutions ended up | GPU machine |

Training notes for rungs 2–3: predict **residuals on top of the constant-velocity or physics baseline**, not raw positions. Use Huber loss. Add augmentation by flipping the field top to bottom (adjusting y, dir and o).

## Evaluation

- Main metric: **RMSE (yards)**, as in the competition.
- Breakdowns: by role (targeted receiver vs defenders), **by horizon** (frame index; error grows with time in the air), by pass depth, and by coverage type if available.
- Always shown as the improvement over rungs 0 and 0b on the same chart.
- W&B: validation RMSE per epoch, RMSE by horizon, and **trajectory plots** (predicted vs actual paths drawn on a field) for a fixed set of 20 sample plays so models can be compared visually.

## How Track 2 connects to Track 1

Current-season frame-level tracking isn't public, so **the Track 2 model can't be run on this week's games.** The connection is through **research findings** and **historical profiles**, not live inference.

### 1. Research findings → choosing Track 1 features

Use the tracking data to answer questions that tell us which **weekly Next Gen Stats measures** to trust:

- How much does receiver separation at release (and at the catch point) predict completion and EPA on the play?
- Does defender closing speed toward the landing spot matter beyond separation?
- How stable is a player's separation-creating ability from one play to the next and one week to the next (is it a skill or noise)?

Output: a short `documentation/findings/bdb-findings.md` with charts. It recommends which NGS measures to include in the player model and the "Last week under the hood" section, and which to treat as noise. The player model's walk-forward results then confirm or reject each recommendation.

### 2. Historical movement profiles → static graph attributes

For players in the BDB data who are still active, compute profiles such as:

- **Receivers:** average separation gained while the ball is in the air vs the model's expectation ("separation over expected")
- **Defenders:** closing speed toward the landing spot vs expected

Written to `Player` nodes as properties **clearly labeled with the source season** (e.g. `bdb_sep_over_expected_2023`). They can show up in "Non-obvious insights" as historical context ("in the 2023 tracking data, X closed on throws faster than almost any safety"). They're never presented as current-season measurement.

### 3. Explicitly out of scope

- Running Track 2 models on live games.
- Feeding Track 2 predictions into this week's win probabilities.

## Rishi in the loop

Track 2 is mostly hands-on learning, so most training runs are **🧑 Rishi runs** steps (see [plans/README.md](plans/README.md)). The agent writes the code, configs and "what to look for" notes. Rishi launches the runs and watches the curves in W&B.
