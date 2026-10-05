"""The player model's targets and its prediction file contract (plans P06, P08;
documentation/11).

`TARGETS` lists every target (one model per target x position group): the P06 ones and
the P08 additions (`phase="p08"`). Only the **live** targets (`live_targets()`, settings
`player_model.live_targets`; default: every P06 target) are refit and projected by the
weekly run; a P08 target joins them only after it beats its baseline in the walk-forward
backtest (the P08 ✋ ship decision). Each weekly run writes one row per (player, game,
target) to `runs/<season>/week<NN>/predictions_players.parquet` with `PRED_SCHEMA`;
walk-forward backtests write the same rows (with `actual` / `played` filled) to
`runs/backtests/player/<target>-<group>/predictions_players.parquet`. Everything
downstream (watch list, payload, report card, graph, scoreboard) reads only these columns.

Kinds:
- `amount` (yards, EPA): quantile models; P50 is the projection, P10-P90 the 80% range;
- `count` (carries, targets, pressures, TDs, sacks ...): a Poisson mean + a negative
  binomial; `event_probs=True` also fills `p_ge1` / `p_ge2` (P(>= 1), P(>= 2)) from that
  distribution (pass TDs, interceptions, sacks; documentation/11). `p_ge1` is the negative
  binomial's P(>= 1) recalibrated on its own earlier walk-forward values (a distribution
  fitted for the mean, median and range isn't exactly right about zero); `p_ge2` is the plain
  P(>= 2), capped at `p_ge1`;
- `prob` (yes/no events: a touchdown, an interception, a pass defended): a LightGBM
  classifier plus a calibration layer fitted on its own earlier walk-forward predictions;
  `column` is the 0/1 label, `mean` = `p_ge1` = P(event), `p10` / `p50` / `p90` stay null,
  `baseline` is the player's rolling rate of the event.

Pools (who a target is projected and scored for; documentation/11: only games he played):
- QB: the team's main QB in the game (most dropbacks);
- RB: running backs with offensive snaps; WR/TE: wide receivers and tight ends with
  offensive snaps;
- EDGE/DL: defensive linemen **and linebackers** with defensive snaps (nflverse labels
  many edge rushers LB, so off-ball LBs are in the pool too; their history says they
  rarely pressure);
- LB/S: linebackers and safeties with defensive snaps;
- CB/S (P08, coverage): cornerbacks and safeties with defensive snaps.

`TEAM` (P08 team stat totals, `models/team_runs.py`) is a scoreboard group only: team
rows never enter `PRED_SCHEMA` files, but their scoreboard rows use `SCOREBOARD_SCHEMA`
with `position_group = "TEAM"`.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

# the P06 groups (dashboard panels, drift signals); CB/S and TEAM rows reach both through
# the scoreboard's own `position_group` values once a P08 target ships
GROUPS = ("QB", "RB", "WR/TE", "EDGE/DL", "LB/S")
GROUP_SLUG = {
    "QB": "qb",
    "RB": "rb",
    "WR/TE": "wrte",
    "EDGE/DL": "edge",
    "LB/S": "lbs",
    "CB/S": "cbs",
    "TEAM": "team",  # P08 team stat totals: scoreboard rows only (module docstring)
}
GROUP_PGROUPS = {
    "QB": ("QB",),
    "RB": ("RB",),
    "WR/TE": ("WR", "TE"),
    "EDGE/DL": ("DL", "LB"),
    "LB/S": ("LB", "S"),
    "CB/S": ("CB", "S"),
}
DEFENSE = ("EDGE/DL", "LB/S", "CB/S")
KINDS = ("amount", "count", "prob")


@dataclass(frozen=True)
class Target:
    name: str  # "rec_yds"
    group: str  # "WR/TE"
    column: str  # history / actual column ("receiving_yards"); prob targets: a 0/1 column
    kind: str  # "amount" (quantile) | "count" (Poisson + negative binomial) | "prob" (yes/no)
    label: str  # "receiving yards" (reader-facing)
    unit: str  # digest/format.py metric unit: "yards" | "count" | "epa" | "prob"
    main: bool = False  # the group's main stat (players to watch)
    start_season: int = 2013  # first season with this target's data
    # weeks its label publishes late (PFR pressures: 1). A Tuesday run doesn't have last
    # week's label yet, so neither its features nor the training rows use it
    label_lag: int = 0
    event_probs: bool = False  # counts: also P(>= 1) / P(>= 2) (`p_ge1`, `p_ge2`)
    # event_probs: the label value from which the "P(>= 1)" event counts. Sacks credit halves
    # (`def_sacks` 0.5 = a shared sack): the NB probability of "at least one" matches "credited
    # with any sack" (0.166 of EDGE/DL player-games) and not "a full sack or more" (0.139), so
    # sacks use 0.5; the other counts are whole numbers (1.0)
    event_min: float = 1.0
    phase: str = "p06"  # the plan phase that added it (documentation/11 priority order)

    @property
    def key(self) -> str:
        """File / run label: `rec_yds-wrte`."""
        return f"{self.name}-{GROUP_SLUG[self.group]}"


TARGETS: tuple[Target, ...] = (
    Target("pass_yds", "QB", "passing_yards", "amount", "passing yards", "yards", main=True),
    Target("pass_epa", "QB", "epa_per_db", "amount", "EPA per dropback", "epa"),
    Target("rush_yds", "RB", "rushing_yards", "amount", "rushing yards", "yards", main=True),
    Target("carries", "RB", "carries", "count", "carries", "count"),
    Target("receptions", "RB", "receptions", "count", "receptions", "count"),
    Target("scrim_yds", "RB", "scrimmage_yards", "amount", "scrimmage yards", "yards"),
    Target("rec_yds", "WR/TE", "receiving_yards", "amount", "receiving yards", "yards", main=True),
    Target("targets", "WR/TE", "targets", "count", "targets", "count"),
    Target("receptions", "WR/TE", "receptions", "count", "receptions", "count"),
    Target("pressures", "EDGE/DL", "pressures", "count", "pressures", "count", True, 2018, 1),
    Target("tackles", "LB/S", "tackles", "count", "tackles", "count", main=True),
    # ---- P08 (documentation/11): live only once shipped (`live_targets`)
    Target(
        "pass_tds",
        "QB",
        "passing_tds",
        "count",
        "passing TDs",
        "count",
        event_probs=True,
        phase="p08",
    ),  # fmt: skip
    Target(
        "ints",
        "QB",
        "passing_interceptions",
        "count",
        "interceptions thrown",
        "count",
        event_probs=True,
        phase="p08",
    ),  # fmt: skip
    Target("rush_yds", "QB", "rushing_yards", "amount", "rushing yards", "yards", phase="p08"),
    Target("td", "RB", "any_td", "prob", "chance of a touchdown", "prob", phase="p08"),
    Target("td", "WR/TE", "any_td", "prob", "chance of a touchdown", "prob", phase="p08"),
    Target(
        "sacks",
        "EDGE/DL",
        "def_sacks",
        "count",
        "sacks",
        "count",
        event_probs=True,
        event_min=0.5,
        phase="p08",
    ),  # fmt: skip
    Target("qb_hits", "EDGE/DL", "def_qb_hits", "count", "QB hits", "count", phase="p08"),
    Target(
        "cov_tgt",
        "CB/S",
        "pfr_targets_allowed",
        "count",
        "targets allowed in coverage",
        "count",
        start_season=2018,
        label_lag=1,
        phase="p08",
    ),  # fmt: skip
    Target(
        "cov_cmp",
        "CB/S",
        "pfr_completions_allowed",
        "count",
        "completions allowed in coverage",
        "count",
        start_season=2018,
        label_lag=1,
        phase="p08",
    ),  # fmt: skip
    Target(
        "cov_yds",
        "CB/S",
        "pfr_yards_allowed",
        "amount",
        "yards allowed in coverage",
        "yards",
        start_season=2018,
        label_lag=1,
        phase="p08",
    ),  # fmt: skip
    Target("int", "CB/S", "def_int_any", "prob", "chance of an interception", "prob", phase="p08"),
    Target("pd", "CB/S", "pd_any", "prob", "chance of a pass defended", "prob", phase="p08"),
)
TARGET_BY_KEY = {t.key: t for t in TARGETS}
MAIN_TARGETS = {t.group: t for t in TARGETS if t.main}
P06_KEYS = tuple(t.key for t in TARGETS if t.phase == "p06")


def live_targets(keys: list[str] | tuple[str, ...] | None = None) -> tuple[Target, ...]:
    """The targets the weekly run refits and projects: settings
    `player_model.live_targets` (a list of keys), or every P06 target when it isn't set.
    A P08 target is added there only after the ship decision (it beat its baseline)."""
    if keys is None:
        from nflengine.settings import get_config

        keys = (get_config().player_model or {}).get("live_targets") or P06_KEYS
    unknown = [k for k in keys if k not in TARGET_BY_KEY]
    if unknown:
        raise ValueError(f"unknown live target keys {unknown}; see TARGETS")
    return tuple(t for t in TARGETS if t.key in set(keys))


def get_target(name: str, group: str | None = None) -> list[Target]:
    """Targets matching a name (`receptions` -> RB and WR/TE), a key (`receptions-rb`) or
    `all`; optionally only one group."""
    if name in TARGET_BY_KEY:
        found = [TARGET_BY_KEY[name]]
    elif name == "all":
        found = list(TARGETS)
    else:
        found = [t for t in TARGETS if t.name == name]
    if group is not None:
        found = [t for t in found if t.group == group]
    if not found:
        raise ValueError(f"unknown target {name!r} (group {group}); see TARGETS")
    return found


def event_threshold(name_col: str = "target", group_col: str = "group") -> pl.Expr:
    """Per row, the label value from which its target's "P(>= 1)" event counts
    (`Target.event_min`; 0.5 for a probability target's 0/1 label). A Polars expression over
    the `target` / `group` columns of `PRED_SCHEMA` rows."""
    key = pl.concat_str(name_col, group_col, separator="|")
    table = {f"{t.name}|{t.group}": (0.5 if t.kind == "prob" else t.event_min) for t in TARGETS}
    return key.replace_strict(table, default=1.0, return_dtype=pl.Float64)


def event_expr() -> pl.Expr:
    """1.0 / 0.0 per scored row: did the target's "P(>= 1)" event happen (`actual` reached
    `event_threshold`); null where `actual` is null."""
    return pl.when(pl.col("actual").is_not_null()).then(
        (pl.col("actual") >= event_threshold()).cast(pl.Float64)
    )


def pool_expr(group: str) -> pl.Expr:
    """True for history rows in `group`'s pool (needs `pgroup`, `played_off`, `played_def`,
    `main_qb` from `features.player_data.player_history`)."""
    pg = pl.col("pgroup").is_in(list(GROUP_PGROUPS[group]))
    if group == "QB":
        return pg & pl.col("main_qb")
    if group in DEFENSE:
        return pg & pl.col("played_def")
    return pg & pl.col("played_off")


DRIVER = pl.Struct(
    {
        "feature": pl.String,
        "phrase": pl.String,  # readable, from features/descriptions.yaml (no numbers)
        "value": pl.Float64,  # the feature's value for this player
        "contribution": pl.Float64,  # SHAP contribution to the projection (target units)
    }
)

PRED_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "kickoff_utc": pl.Datetime("us", "UTC"),
    "created_at": pl.Datetime("us", "UTC"),  # when the projection was made (live: run time)
    "player_id": pl.String(),
    "player": pl.String(),
    "team": pl.String(),
    "opponent": pl.String(),
    "home": pl.Boolean(),
    "position": pl.String(),  # roster label ("WR", "OLB" ...)
    "pgroup": pl.String(),  # QB RB WR TE DL LB S
    "group": pl.String(),  # model group (GROUPS)
    "target": pl.String(),  # Target.name
    "target_label": pl.String(),
    "kind": pl.String(),
    "unit": pl.String(),
    "is_main": pl.Boolean(),
    "p10": pl.Float64(),  # prob targets: null
    "p50": pl.Float64(),  # the projection (prob targets: null)
    "p90": pl.Float64(),
    "mean": pl.Float64(),  # expected value (counts: the Poisson mean; amounts: = p50; prob: P)
    # P08: P(>= 1) / P(>= 2) of a count with `event_probs` (from its negative binomial);
    # prob targets: p_ge1 = P(event), p_ge2 null; null for every other target
    "p_ge1": pl.Float64(),
    "p_ge2": pl.Float64(),
    "baseline": pl.Float64(),  # player rolling baseline (documentation/11)
    "baseline_source": pl.String(),  # rolling | last_season | role (rookies / no history)
    "baseline_season_mean": pl.Float64(),  # season-to-date mean (null before his 1st game)
    "baseline_role": pl.Float64(),  # position average for his snap-share role
    # the baseline as the same kind of projection, the one MAE is scored on (D65): amounts =
    # baseline; counts = the negative-binomial median at the baseline mean (same dispersion)
    "baseline_p50": pl.Float64(),
    # P08: the baseline's P(>= 1) (counts with event_probs: the same function as `p_ge1` at the
    # baseline mean; prob targets: = baseline, the rolling rate). Brier is scored on it vs p_ge1
    "baseline_p_ge1": pl.Float64(),
    # amounts: p50 - baseline; counts and prob: mean - baseline (a count's median is coarse)
    "outperformance": pl.Float64(),
    "outperf_z": pl.Float64(),  # outperformance / the target's typical baseline miss
    "games_history": pl.Int32(),  # games in this pool before the projection (all seasons)
    "games_season": pl.Int32(),  # ... this season
    "snap_share_l2": pl.Float64(),  # mean snap share (his side) over his last 2 games
    "role_ok": pl.Boolean(),  # snap share >= 50% over the last 2 games
    "role_change": pl.Boolean(),  # a regular teammate in his group out (vacated usage)
    "vacated_share": pl.Float64(),  # usage share of teammates who missed the last game
    "injury_status": pl.String(),  # his report status for the week (null = not listed)
    "confidence": pl.String(),  # high | medium | low (low: thin history or a wide range)
    "drivers": pl.List(DRIVER),  # top 3 SHAP drivers, largest |contribution| first
    "model_version": pl.String(),  # "player-model-v1:2026-w04" (backtests: ":backtest")
    "feature_hash": pl.String(),
    "trained_through": pl.String(),  # last as-of week in the training rows ("2026-w03")
    "actual": pl.Float64(),  # filled once the game is played (null live)
    "played": pl.Boolean(),  # in the target's pool in that game (null until played)
}


# The accuracy scoreboard (documentation/11): one row per (season, week, target, group).
# Live rows: `runs/<season>/accuracy_scoreboard.parquet` (the week's saved pre-kickoff
# projections, scored the next week); backtest rows: `runs/backtests/player/scoreboard.parquet`.
SCOREBOARD_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "target": pl.String(),
    "position_group": pl.String(),
    "target_label": pl.String(),
    "n_scored": pl.Int32(),  # projected players who played
    "n_not_played": pl.Int32(),  # projected players who didn't (logged, not scored)
    "mae_model": pl.Float64(),
    "mae_baseline": pl.Float64(),  # player rolling baseline (`baseline_p50`, D65)
    "improvement_pct": pl.Float64(),  # 100 * (mae_baseline - mae_model) / mae_baseline
    "coverage_80": pl.Float64(),  # share of actuals inside P10-P90
    # P08: Brier of `p_ge1` vs the 0/1 event (prob targets; counts with event_probs: >= 1)
    # and of `baseline_p_ge1`; ECE of `p_ge1`. Null for the other targets. Prob targets
    # have null MAE / coverage columns
    "brier_model": pl.Float64(),
    "brier_baseline": pl.Float64(),
    "calibration_ece": pl.Float64(),
    "mode": pl.String(),  # live | backtest
}


STATUS_FILE = "player_status.json"  # the weekly player step's verdict for the run folder


def write_status(run_dir, status: str, detail: str = "") -> None:
    """Record the player step's result next to the projections (`ok` / `degraded`): the
    digest falls back to the heuristic when the latest refit failed, even if an older
    projection file from an earlier run of the week is still there (Sol review)."""
    import datetime as dt
    import json
    from pathlib import Path

    folder = Path(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STATUS_FILE).write_text(
        json.dumps(
            {
                "status": status,
                "detail": detail,
                "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )


def read_status(run_dir) -> str | None:
    """`ok`, `degraded`, or None when no player step has run for that folder."""
    import json
    from pathlib import Path

    path = Path(run_dir) / STATUS_FILE
    if not path.exists():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("status"))
    except (ValueError, OSError):
        return None


def empty_predictions() -> pl.DataFrame:
    return pl.DataFrame(schema=PRED_SCHEMA)


def conform(df: pl.DataFrame) -> pl.DataFrame:
    """Columns of `PRED_SCHEMA`, in order, cast (missing columns become null)."""
    if df.height == 0:
        return empty_predictions()
    cols = []
    for c, t in PRED_SCHEMA.items():
        if c in df.columns:
            cols.append(pl.col(c).cast(t))
        else:
            cols.append(pl.lit(None, dtype=t).alias(c))
    return df.select(cols)


def score_predictions(preds: pl.DataFrame, hist: pl.DataFrame) -> pl.DataFrame:
    """Fill `actual` / `played` from the player history table (`player_history`).

    `played` = the player is in the target's pool for that game (a main QB, offensive /
    defensive snaps); `actual` = the target column (null when he didn't play, and for
    pressures when PFR hasn't published that game yet). Games not in `hist` (not played
    yet) leave both null.
    """
    if preds.is_empty():
        return preds
    out = []
    played_games = set(hist["game_id"].unique().to_list())
    for (tname, group), part in preds.group_by("target", "group", maintain_order=True):
        tgt = get_target(str(tname), str(group))[0]
        h = hist.select(
            "player_id",
            "game_id",
            pool_expr(tgt.group).alias("_played"),
            pl.col(tgt.column).cast(pl.Float64).alias("_actual"),
        )
        j = part.drop("actual", "played", strict=False).join(
            h, on=["player_id", "game_id"], how="left"
        )
        game_done = pl.col("game_id").is_in(list(played_games))
        j = j.with_columns(
            pl.when(game_done).then(pl.col("_played").fill_null(False)).alias("played"),
        ).with_columns(
            pl.when(pl.col("played")).then(pl.col("_actual")).alias("actual"),
        )
        out.append(j.drop("_played", "_actual"))
    cols = list(preds.columns) + [c for c in ("actual", "played") if c not in preds.columns]
    return pl.concat(out, how="diagonal_relaxed").select(cols)
