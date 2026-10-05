"""The consistency layer (plan P08; documentation/11 -> How each kind of target is modeled).

Projections made by separate models should agree where the sport says they must. This module
checks three identities on prediction frames (the player `PRED_SCHEMA`, plus the team model's
`predictions_teams.parquet`) and, where it is safe, lightly adjusts them:

1. **Receptions <= targets** for the same WR/TE game. Compared level by level (`p10`, `p50`,
   `p90`, `mean`): if receptions stochastically can't exceed targets, each quantile of one
   can't exceed the same quantile of the other. `reconcile_receptions` clamps a violating
   reception level to the targets level and counts the adjustments (about 1% of player-games
   on the mean, 0.01% on the median).
2. **Team passing yards ~ the sum of its receivers' yards ~ the main QB's passing yards.**
   Per team-game: WR/TE `rec_yds` + RB receiving (`scrim_yds - rush_yds`, per back, floored at
   0) vs the QB's `pass_yds` vs the team model's `pass_yds`. The identity is exact for results
   (receivers' yards = the QB's passing yards in 99% of team-games; the rest are laterals and
   non-QB throws); for projections it is not, for a statistical reason, so **compare means,
   not medians**: yardage is right-skewed, each player's median sits below his mean, and the
   sum of the WR/TE medians came out 22% below their actual total (160 vs 204 yards a team-game,
   2019-2025). Amount models have no mean (`mean` = P50), so `mean_estimate` uses Swanson's
   rule `0.3 * P10 + 0.4 * P50 + 0.3 * P90`, which lands within 1-4% of the actual mean of every
   P06 yardage target.
3. **An inconsistency summary** (`inconsistency_summary`), a flat dict of shares and median
   relative gaps, one W&B summary key each.

`reconcile_rec_yds` is the optional team-level adjustment (scale a team-game's WR/TE
projections so the receivers' mean total meets the team model's or the QB's, shrunk by
`strength`). Whether to switch it on is decided by `adjustment_effect` on the 2019-2025
backtests: it must not make the player yardage MAE worse. `apply_consistency` is the one call
for the weekly run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl

SWANSON = (0.3, 0.4, 0.3)  # weights of P10, P50, P90 in the mean estimate
LEVELS = ("p10", "p50", "p90", "mean")
K_CLIP = (0.8, 1.25)  # the most a team-level scale factor may move a projection
P06_KEYS = (
    "pass_yds-qb",
    "rush_yds-rb",
    "scrim_yds-rb",
    "rec_yds-wrte",
    "targets-wrte",
    "receptions-wrte",
)
SEASONS = tuple(range(2019, 2026))


def mean_estimate() -> pl.Expr:
    """An expected value for every target kind: Swanson's rule on the quantiles of an amount
    (its `mean` column is the P50), the stored mean for counts and probabilities."""
    w10, w50, w90 = SWANSON
    return (
        pl.when(pl.col("kind") == "amount")
        .then(w10 * pl.col("p10") + w50 * pl.col("p50") + w90 * pl.col("p90"))
        .otherwise(pl.col("mean"))
    )


def _sel(preds: pl.DataFrame, target: str, group: str) -> pl.DataFrame:
    return preds.filter((pl.col("target") == target) & (pl.col("group") == group))


def _played_actual() -> pl.Expr:
    return pl.when(pl.col("played").fill_null(False)).then(pl.col("actual"))


def _gap_scale() -> pl.Expr:
    """The target's typical baseline miss, recovered from the frame itself: the player model
    stores `outperf_z = outperformance / scale` without the scale, and it is one number per
    (season, week, target, group) fit."""
    ratio = pl.when(pl.col("outperf_z").abs() > 1e-9).then(
        pl.col("outperformance") / pl.col("outperf_z")
    )
    return ratio.median().over("season", "week", "target", "group")


def _refresh_gap(out: pl.DataFrame, use: pl.Expr, projection: pl.Expr) -> pl.DataFrame:
    """After an adjustment, recompute the gap to baseline of the changed rows (`use`):
    `outperformance = projection - baseline` and `outperf_z = outperformance / scale`, the
    columns the players-to-watch ranking reads. Frames without those columns are returned as
    they are."""
    need = {"outperformance", "outperf_z", "baseline", "season", "week", "target", "group"}
    if not need <= set(out.columns):
        return out
    out = out.with_columns(_gap_scale().alias("_scale"))
    new_out = projection - pl.col("baseline")
    z = pl.when(pl.col("_scale").is_not_null()).then(new_out / pl.col("_scale"))
    return out.with_columns(
        pl.when(use).then(new_out).otherwise(pl.col("outperformance")).alias("outperformance"),
        pl.when(use)
        .then(z.fill_null(pl.col("outperf_z")))
        .otherwise(pl.col("outperf_z"))
        .alias("outperf_z"),
    ).drop("_scale")


# ---- 1. receptions <= targets -------------------------------------------------------------------


def reconcile_receptions(
    preds: pl.DataFrame, adjust: bool = True
) -> tuple[pl.DataFrame, dict[str, float]]:
    """Check WR/TE `receptions` against `targets` (same player, same game) at every level and
    clamp the violating reception levels to the targets level when `adjust`.

    Returns the frame (same rows and columns) and the counts: `pairs`, `viol_<level>` (rows
    where receptions exceeded targets before the adjustment), `share_<level>`,
    `median_excess_<level>`, `adjusted_rows` (rows changed). Rows are paired on
    (game_id, player_id); a reception row without a targets row is left alone."""
    rec = _sel(preds, "receptions", "WR/TE")
    tgt = _sel(preds, "targets", "WR/TE")
    stats: dict[str, float] = {"pairs": 0.0, "adjusted_rows": 0.0}
    if rec.is_empty() or tgt.is_empty():
        return preds, stats
    t = tgt.select("game_id", "player_id", *[pl.col(c).alias(f"_t_{c}") for c in LEVELS])
    j = rec.select("game_id", "player_id", *LEVELS).join(t, on=["game_id", "player_id"])
    stats["pairs"] = float(j.height)
    changed = pl.lit(False)
    for c in LEVELS:
        over = pl.col(c) > pl.col(f"_t_{c}") + 1e-12
        n = float(j.select(over.sum()).item())
        stats[f"viol_{c}"] = n
        stats[f"share_{c}"] = n / j.height if j.height else 0.0
        ex = j.filter(over).select((pl.col(c) - pl.col(f"_t_{c}")).median()).item()
        stats[f"median_excess_{c}"] = float(ex) if ex is not None else 0.0
        changed = changed | over
    if (
        not adjust
        or stats["viol_mean"] + stats["viol_p10"] + stats["viol_p50"] + stats["viol_p90"] == 0
    ):
        return preds, stats
    clamp = [pl.min_horizontal(pl.col(c), pl.col(f"_t_{c}")).alias(c) for c in LEVELS]
    fixed = j.with_columns(changed.alias("_changed")).with_columns(clamp)
    stats["adjusted_rows"] = float(fixed["_changed"].sum())
    key = ["game_id", "player_id"]
    is_rec = (pl.col("target") == "receptions") & (pl.col("group") == "WR/TE")
    upd = fixed.select(*key, *[pl.col(c).alias(f"_n_{c}") for c in LEVELS], "_changed")
    out = preds.join(upd, on=key, how="left")
    use = is_rec & pl.col("_changed").fill_null(False)
    out = out.with_columns(
        [pl.when(use).then(pl.col(f"_n_{c}")).otherwise(pl.col(c)).alias(c) for c in LEVELS]
    )
    out = _refresh_gap(out, use, pl.col("mean"))
    return out.drop([c for c in out.columns if c.startswith("_n_")] + ["_changed"]), stats


# ---- 2. the passing-yards identity --------------------------------------------------------------


def team_receiving_table(players: pl.DataFrame, teams: pl.DataFrame | None = None) -> pl.DataFrame:
    """One row per (game_id, team): the receivers' projected yards against the QB's and the
    team model's, with the relative gaps `(a - b) / b`.

    Columns, each as a median (`_p50`) and an expected-value (`_mean`, `mean_estimate`) sum, and
    the actual total (`_act`, played rows only; null for games not played):
    `wrte` (WR/TE `rec_yds`), `rb` (RB receiving = `scrim_yds - rush_yds`, per back, floored at
    0), `recv` (their sum), `qb` (the main QB's `pass_yds`), `team` (the team model's
    `pass_yds`; absent without `teams`). Gaps: `gap_recv_qb`, `gap_qb_team`, `gap_recv_team`
    on means, `*_p50` on medians, `gap_act_*` on actual results (the identity's noise floor).
    """
    est = mean_estimate()
    wr = (
        _sel(players, "rec_yds", "WR/TE")
        .group_by("game_id", "team")
        .agg(
            pl.col("p50").sum().alias("wrte_p50"),
            est.sum().alias("wrte_mean"),
            _played_actual().sum().alias("wrte_act"),
            pl.len().alias("n_wrte"),
        )
    )
    sc = _sel(players, "scrim_yds", "RB").select(
        "game_id",
        "team",
        "player_id",
        pl.col("p50").alias("_sc_p50"),
        est.alias("_sc_mean"),
        _played_actual().alias("_sc_act"),
    )
    ru = _sel(players, "rush_yds", "RB").select(
        "game_id",
        "player_id",
        pl.col("p50").alias("_ru_p50"),
        est.alias("_ru_mean"),
        _played_actual().alias("_ru_act"),
    )
    rb = (
        sc.join(ru, on=["game_id", "player_id"])
        .group_by("game_id", "team")
        .agg(
            (pl.col("_sc_p50") - pl.col("_ru_p50")).clip(lower_bound=0).sum().alias("rb_p50"),
            (pl.col("_sc_mean") - pl.col("_ru_mean")).clip(lower_bound=0).sum().alias("rb_mean"),
            (pl.col("_sc_act") - pl.col("_ru_act")).sum().alias("rb_act"),
        )
    )
    qb = (
        _sel(players, "pass_yds", "QB")
        .group_by("game_id", "team")
        .agg(
            pl.col("p50").first().alias("qb_p50"),
            est.first().alias("qb_mean"),
            _played_actual().first().alias("qb_act"),
        )
    )
    j = wr.join(rb, on=["game_id", "team"], how="left").join(qb, on=["game_id", "team"], how="left")
    meta = players.select("season", "week", "game_id", "team", "opponent").unique(
        ["game_id", "team"]
    )
    j = j.join(meta, on=["game_id", "team"], how="left")
    j = j.with_columns(
        pl.col("rb_p50", "rb_mean").fill_null(0.0), pl.col("rb_act").fill_null(0.0)
    ).with_columns(
        (pl.col("wrte_p50") + pl.col("rb_p50")).alias("recv_p50"),
        (pl.col("wrte_mean") + pl.col("rb_mean")).alias("recv_mean"),
        (pl.col("wrte_act") + pl.col("rb_act")).alias("recv_act"),
    )
    if teams is not None and not teams.is_empty():
        tm = (
            teams.filter(pl.col("target") == "pass_yds")
            .select(
                "game_id",
                "team",
                pl.col("p50").alias("team_p50"),
                est.alias("team_mean"),
                _played_actual().alias("team_act"),
            )
            .unique(["game_id", "team"])
        )
        j = j.join(tm, on=["game_id", "team"], how="left")
    else:
        j = j.with_columns(
            pl.lit(None, dtype=pl.Float64).alias(c) for c in ("team_p50", "team_mean", "team_act")
        )

    def gap(a: str, b: str) -> pl.Expr:
        return (pl.col(a) - pl.col(b)) / pl.col(b)

    return j.with_columns(
        gap("recv_mean", "qb_mean").alias("gap_recv_qb"),
        gap("qb_mean", "team_mean").alias("gap_qb_team"),
        gap("recv_mean", "team_mean").alias("gap_recv_team"),
        gap("recv_p50", "qb_p50").alias("gap_recv_qb_p50"),
        gap("qb_p50", "team_p50").alias("gap_qb_team_p50"),
        gap("recv_p50", "team_p50").alias("gap_recv_team_p50"),
        gap("recv_act", "qb_act").alias("gap_act_recv_qb"),
        gap("qb_act", "team_act").alias("gap_act_qb_team"),
        gap("recv_act", "team_act").alias("gap_act_recv_team"),
    ).sort("game_id", "team")


GAP_COLS = (
    "gap_recv_qb",
    "gap_qb_team",
    "gap_recv_team",
    "gap_recv_qb_p50",
    "gap_qb_team_p50",
    "gap_recv_team_p50",
    "gap_act_recv_qb",
    "gap_act_qb_team",
    "gap_act_recv_team",
)
BIG_GAP = 0.10  # a gap beyond +-10% counts as an inconsistency


def gap_stats(table: pl.DataFrame, prefix: str = "inconsistency") -> dict[str, float]:
    """Per gap column: the median absolute gap, the mean (signed) gap = the bias, and the
    share beyond +-`BIG_GAP`, as `<prefix>/<gap>_median_abs`, `_bias`, `_share_gt10`."""
    out: dict[str, float] = {}
    for c in GAP_COLS:
        if c not in table.columns:
            continue
        s = table[c].drop_nulls().drop_nans()
        if s.is_empty():
            continue
        out[f"{prefix}/{c}_median_abs"] = float(s.abs().median())
        out[f"{prefix}/{c}_bias"] = float(s.mean())
        out[f"{prefix}/{c}_share_gt10"] = float((s.abs() > BIG_GAP).mean())
    out[f"{prefix}/team_games"] = float(table.height)
    return out


def inconsistency_summary(
    table: pl.DataFrame,
    receptions: Mapping[str, float] | None = None,
    prefix: str = "inconsistency",
) -> dict[str, float]:
    """The flat summary a run logs (W&B summary keys): the team-game gaps (`gap_stats`) and,
    when given, the receptions-vs-targets counts (`reconcile_receptions` stats) as
    `<prefix>/rec_gt_tgt_share_<level>`, `_pairs`, `_adjusted_rows`."""
    out = gap_stats(table, prefix)
    for k, v in (receptions or {}).items():
        if k.startswith(("share_", "median_excess_")) or k in ("pairs", "adjusted_rows"):
            out[f"{prefix}/rec_gt_tgt_{k}"] = float(v)
    return out


# ---- the optional team-level adjustment ---------------------------------------------------------


def reconcile_rec_yds(
    players: pl.DataFrame,
    table: pl.DataFrame,
    anchor: str = "team",
    strength: float = 0.5,
    clip: tuple[float, float] = K_CLIP,
) -> tuple[pl.DataFrame, dict[str, float]]:
    """Scale each team-game's WR/TE `rec_yds` (P10, P50, P90, mean) so the receivers' expected
    total meets the anchor's: `k = (anchor_mean - rb_mean) / wrte_mean`, clipped to `clip` and
    shrunk by `strength` (`1 + strength * (k - 1)`). `anchor` is `team` (the team model's
    `pass_yds`) or `qb` (the main QB's). Team-games without an anchor are left alone."""
    if anchor not in ("team", "qb"):
        raise ValueError(f"anchor must be 'team' or 'qb', not {anchor!r}")
    a = f"{anchor}_mean"
    k = table.select(
        "game_id",
        "team",
        (
            1.0
            + strength
            * ((pl.col(a) - pl.col("rb_mean")) / pl.col("wrte_mean")).clip(*clip).sub(1.0)
        ).alias("_k"),
    ).filter(pl.col("_k").is_not_null() & pl.col("_k").is_finite())
    is_rec = (pl.col("target") == "rec_yds") & (pl.col("group") == "WR/TE")
    out = players.join(k, on=["game_id", "team"], how="left")
    use = is_rec & pl.col("_k").is_not_null()
    scaled = [
        pl.when(use).then(pl.col(c) * pl.col("_k")).otherwise(pl.col(c)).alias(c) for c in LEVELS
    ]
    n = float(out.filter(use).height)
    moved = out.filter(use).select((pl.col("_k") - 1.0).abs().mean()).item()
    out = out.with_columns(scaled)
    out = _refresh_gap(out, use, pl.col("p50"))
    return out.drop("_k"), {
        "adjusted_rows": n,
        "mean_abs_scale_change": float(moved) if moved is not None else 0.0,
    }


def player_mae(
    preds: pl.DataFrame, target: str, group: str, by: str | None = "season"
) -> pl.DataFrame:
    """MAE of P50 and coverage of the P10-P90 range for one target over scored rows."""
    r = _sel(preds, target, group).filter(
        pl.col("played").fill_null(False) & pl.col("actual").is_not_null()
    )
    err = (pl.col("actual") - pl.col("p50")).abs()
    inside = (pl.col("actual") >= pl.col("p10")) & (pl.col("actual") <= pl.col("p90"))
    aggs = [err.mean().alias("mae"), inside.mean().alias("coverage_80"), pl.len().alias("n")]
    if by is None:
        return r.select(aggs)
    return r.group_by(by).agg(aggs).sort(by)


def adjustment_effect(
    players: pl.DataFrame,
    table: pl.DataFrame,
    anchors: Sequence[str] = ("team", "qb"),
    strengths: Sequence[float] = (0.25, 0.5, 1.0),
) -> pl.DataFrame:
    """What `reconcile_rec_yds` would do to the WR/TE `rec_yds` projections, by anchor and
    strength: pooled MAE before / after, the change in %, seasons improved, coverage before /
    after. Negative `mae_change_pct` = better. `anchor=team` needs the team model's rows in
    `table` (`team_mean`)."""
    base = player_mae(players, "rec_yds", "WR/TE")
    base_all = player_mae(players, "rec_yds", "WR/TE", by=None).row(0, named=True)
    rows = []
    for anchor in anchors:
        if table[f"{anchor}_mean"].null_count() == table.height:
            continue
        for s in strengths:
            adj, st = reconcile_rec_yds(players, table, anchor=anchor, strength=s)
            after = player_mae(adj, "rec_yds", "WR/TE")
            after_all = player_mae(adj, "rec_yds", "WR/TE", by=None).row(0, named=True)
            both = base.join(after, on="season", suffix="_after")
            rows.append(
                {
                    "anchor": anchor,
                    "strength": float(s),
                    "mae_before": base_all["mae"],
                    "mae_after": after_all["mae"],
                    "mae_change_pct": 100 * (after_all["mae"] - base_all["mae"]) / base_all["mae"],
                    "seasons_improved": float((both["mae_after"] < both["mae"]).sum()),
                    "seasons": float(both.height),
                    "coverage_before": base_all["coverage_80"],
                    "coverage_after": after_all["coverage_80"],
                    "rows_adjusted": st["adjusted_rows"],
                }
            )
    return pl.DataFrame(rows)


def apply_consistency(
    players: pl.DataFrame,
    teams: pl.DataFrame | None = None,
    *,
    receptions: bool = True,
    rec_yds_anchor: str | None = None,
    strength: float = 0.5,
) -> tuple[pl.DataFrame, dict[str, float]]:
    """The one call for a weekly run: adjust receptions <= targets (`receptions`), optionally
    scale the receiving yards toward `rec_yds_anchor` ("team" / "qb", None = log only), and
    return the (possibly adjusted) player frame and the `inconsistency/*` summary of the
    projections as they were before any team-level adjustment. Never changes rows' count or
    columns. The team-level adjustment is off by default: see the model card for the
    backtest evidence."""
    out, rstats = reconcile_receptions(players, adjust=receptions)
    table = team_receiving_table(players, teams)
    summary = inconsistency_summary(table, rstats)
    if rec_yds_anchor is not None:
        out, st = reconcile_rec_yds(out, table, anchor=rec_yds_anchor, strength=strength)
        summary["inconsistency/rec_yds_adjusted_rows"] = st["adjusted_rows"]
    return out, summary


# ---- evaluation on the backtests -----------------------------------------------------------------


def load_player_backtests(
    root: Path, keys: Sequence[str] = P06_KEYS, seasons: Sequence[int] = SEASONS
) -> pl.DataFrame:
    """The saved walk-forward player predictions (`runs/backtests/player/<key>/`) of `keys`
    for `seasons`, stacked on the columns they share."""
    parts = []
    for k in keys:
        p = pl.read_parquet(root / k / "predictions_players.parquet")
        parts.append(p.filter(pl.col("season").is_in(list(seasons))))
    common = [c for c in parts[0].columns if all(c in p.columns for p in parts)]
    return pl.concat([p.select(common) for p in parts], how="vertical_relaxed")


def effect_by_season(
    players: pl.DataFrame, table: pl.DataFrame, anchor: str, strength: float
) -> pl.DataFrame:
    """WR/TE `rec_yds` MAE and coverage by season before and after `reconcile_rec_yds`."""
    adj, _ = reconcile_rec_yds(players, table, anchor=anchor, strength=strength)
    before = player_mae(players, "rec_yds", "WR/TE")
    after = player_mae(adj, "rec_yds", "WR/TE")
    return before.join(after, on="season", suffix="_after").with_columns(
        (100 * (pl.col("mae_after") - pl.col("mae")) / pl.col("mae")).alias("mae_change_pct")
    )


def evaluate(
    players: pl.DataFrame,
    teams: pl.DataFrame | None,
    anchor: str = "team",
    strength: float = 0.5,
) -> dict[str, Any]:
    """Everything the evaluation run logs, from backtest frames: the pooled and by-season
    `inconsistency/*` summaries (as projected), the team-game table, the adjustment effects
    (WR/TE `rec_yds` scaled toward the team / QB total, by anchor and strength), the gaps that
    remain after the default adjustment (`anchor`, `strength`; the `inconsistency/after/`
    keys), and `receptions` clamped to `targets` (MAE before / after)."""
    adj, rstats = reconcile_receptions(players, adjust=True)
    table = team_receiving_table(players, teams)
    pooled = inconsistency_summary(table, rstats)
    seasons = sorted(players["season"].unique().to_list())
    by_season = []
    for s in seasons:
        ps = players.filter(pl.col("season") == s)
        _, rs = reconcile_receptions(ps, adjust=False)
        row = {"season": int(s)}
        row.update(
            {
                k.removeprefix("inconsistency/"): v
                for k, v in inconsistency_summary(table.filter(pl.col("season") == s), rs).items()
            }
        )
        by_season.append(row)
    effects = adjustment_effect(players, table)
    scaled, _ = reconcile_rec_yds(players, table, anchor=anchor, strength=strength)
    pooled.update(gap_stats(team_receiving_table(scaled, teams), prefix="inconsistency/after"))
    rec_before = player_mae(players, "receptions", "WR/TE", by=None).row(0, named=True)
    rec_after = player_mae(adj, "receptions", "WR/TE", by=None).row(0, named=True)
    rec_by = player_mae(players, "receptions", "WR/TE").join(
        player_mae(adj, "receptions", "WR/TE"), on="season", suffix="_after"
    )
    pooled["inconsistency/receptions_mae_before"] = rec_before["mae"]
    pooled["inconsistency/receptions_mae_after"] = rec_after["mae"]
    pooled["inconsistency/receptions_mae_change_pct"] = (
        100 * (rec_after["mae"] - rec_before["mae"]) / rec_before["mae"]
    )
    pooled["inconsistency/receptions_seasons_improved"] = float(
        (rec_by["mae_after"] < rec_by["mae"]).sum()
    )
    pooled["inconsistency/receptions_seasons_worse"] = float(
        (rec_by["mae_after"] > rec_by["mae"]).sum()
    )
    return {
        "pooled": pooled,
        "by_season": pl.DataFrame(by_season),
        "table": table,
        "effects": effects,
        "effect_by_season": effect_by_season(players, table, anchor, strength),
        "receptions_by_season": rec_by,
        "default": {"anchor": anchor, "strength": strength},
    }


def decide(
    effects: pl.DataFrame, min_seasons: int = 5, max_cov_shift: float = 0.01
) -> dict[str, Any]:
    """The pre-registered adjust-or-log rule for the team-level adjustment: adjust only if a
    configuration lowers the pooled MAE, improves at least `min_seasons` seasons and moves the
    80% coverage by at most `max_cov_shift`; otherwise log only. Of the configurations that
    pass, the team-model anchor wins over the QB anchor (the QB anchor leans on who turned out
    to be the main QB, which a live run doesn't know); within an anchor, the lightest strength
    that keeps 90% of the best gain (a light adjustment is the point)."""
    if effects.is_empty():
        return {"adjust": False, "reason": "no team-level effects computed"}
    ok = effects.filter(
        (pl.col("mae_change_pct") < 0)
        & (pl.col("seasons_improved") >= min_seasons)
        & ((pl.col("coverage_after") - pl.col("coverage_before")).abs() <= max_cov_shift)
    )
    if ok.height:  # the preferred anchor, then the lightest strength with 90% of its best gain
        anchor = "team" if (ok["anchor"] == "team").any() else ok["anchor"][0]
        pool = ok.filter(pl.col("anchor") == anchor)
        gain = float(pool["mae_change_pct"].min())
        ok = pool.filter(pl.col("mae_change_pct") <= 0.9 * gain).sort("strength")
    if ok.is_empty():
        best = effects.sort("mae_change_pct").row(0, named=True)
        return {
            "adjust": False,
            "reason": "no configuration lowers the pooled MAE in enough seasons",
            "best": best,
        }
    return {"adjust": True, "best": ok.row(0, named=True)}


def run_eval(
    root: Path | None = None,
    team_root: Path | None = None,
    seasons: Sequence[int] = SEASONS,
    keys: Sequence[str] = P06_KEYS,
    launched_by: str | None = None,
    log=print,
    use_wandb: bool = True,
    save_dir: Path | None = None,
    anchor: str = "team",
    strength: float = 0.5,
) -> dict[str, Any]:
    """Measure the consistency layer on the saved 2019-2025 backtests and log one W&B run
    (group `track1-player`, job `eval`, tags `p08` and `consistency`).

    `root` = `runs/backtests/player` (the P06 player predictions), `team_root` =
    `runs/backtests/team` (`pass_yds-team`, the team model's backtest; without it the team
    gaps are null). Writes `summary.json`, `team_games.parquet` and `effects.parquet` into
    `save_dir` (default `<team_root>/consistency`). Changes no projection: it only reports."""
    import json

    from nflengine.paths import ensure_data_root

    paths = ensure_data_root()
    root = root or paths.runs / "backtests" / "player"
    team_root = team_root or paths.runs / "backtests" / "team"
    players = load_player_backtests(root, keys, seasons)
    team_file = team_root / "pass_yds-team" / "predictions_teams.parquet"
    teams = (
        pl.read_parquet(team_file).filter(pl.col("season").is_in(list(seasons)))
        if team_file.exists()
        else None
    )
    n_teams = 0 if teams is None else teams.height
    log(f"consistency: {players.height:,} player rows, {n_teams:,} team rows")
    res = evaluate(players, teams, anchor=anchor, strength=strength)
    res["decision"] = decide(res["effects"])
    save_dir = save_dir or team_root / "consistency"
    save_dir.mkdir(parents=True, exist_ok=True)
    res["table"].write_parquet(save_dir / "team_games.parquet", compression="zstd")
    res["effects"].write_parquet(save_dir / "effects.parquet")
    (save_dir / "summary.json").write_text(
        json.dumps({**res["pooled"], "decision": res["decision"]}, indent=2, default=str),
        encoding="utf-8",
    )
    res["url"] = _log_eval(res, seasons, launched_by) if use_wandb else None
    return res


GAP_TITLES = (
    ("gap_recv_qb", "receivers vs QB, means"),
    ("gap_recv_qb_p50", "receivers vs QB, medians (biased low)"),
    ("gap_recv_team", "receivers vs team model, means"),
    ("gap_qb_team", "QB vs team model, means"),
)
BY_SEASON_CURVES = (
    "gap_recv_qb_median_abs",
    "gap_qb_team_median_abs",
    "gap_recv_team_median_abs",
    "gap_recv_qb_bias",
    "gap_recv_team_bias",
    "gap_recv_qb_p50_bias",
    "gap_recv_qb_share_gt10",
    "gap_act_recv_qb_share_gt10",
    "rec_gt_tgt_share_mean",
)


def _log_eval(res: Mapping[str, Any], seasons: Sequence[int], launched_by: str | None) -> str:
    import wandb

    from nflengine.tracking import dataset_version, git_commit, init_run

    run = init_run(
        "track1-player",
        "eval",
        config={
            "what": "consistency: receptions <= targets; receivers vs QB vs team passing yards",
            "seasons": [min(seasons), max(seasons)],
            "mean_estimate": "0.3 * P10 + 0.4 * P50 + 0.3 * P90",
            "default_adjustment": res["default"],
            "big_gap": BIG_GAP,
            "dataset_version": dataset_version(),
            "git_commit": git_commit(),
        },
        tags=["p08", "consistency"],
        launched_by=launched_by,
        name=f"consistency-{min(seasons)}-{max(seasons)}",
    )
    try:
        run.define_metric("cons/season")
        run.define_metric("cons/*", step_metric="cons/season")
        by = res["by_season"]
        for r in by.iter_rows(named=True):
            point = {"cons/season": r["season"]}
            point.update({f"cons/{c}": r[c] for c in BY_SEASON_CURVES if r.get(c) is not None})
            run.log(point)
        run.summary.update(res["pooled"])
        run.summary.update({f"decision/{k}": v for k, v in res["decision"].items() if k != "best"})
        if "best" in res["decision"]:
            best = res["decision"]["best"]
            run.summary.update({f"decision/best_{k}": v for k, v in best.items()})
        charts: dict[str, Any] = {
            "by_season": wandb.Table(dataframe=by.to_pandas()),
            "adjustment_effects": wandb.Table(dataframe=res["effects"].to_pandas()),
            "adjustment_by_season": wandb.Table(dataframe=res["effect_by_season"].to_pandas()),
            "receptions_by_season": wandb.Table(dataframe=res["receptions_by_season"].to_pandas()),
        }
        tab = res["table"]
        for col, _title in GAP_TITLES:
            vals = tab[col].drop_nulls().drop_nans().clip(-1.0, 1.0).to_list()
            if vals:
                charts[f"hist/{col}"] = wandb.Histogram(vals)
        eff = res["effects"].with_columns(
            (pl.col("anchor") + "@" + pl.col("strength").cast(pl.String)).alias("setting")
        )
        if eff.height:
            charts["adjustment_mae_change"] = wandb.plot.bar(
                wandb.Table(dataframe=eff.select("setting", "mae_change_pct").to_pandas()),
                "setting",
                "mae_change_pct",
                title="WR/TE receiving yards: MAE change (%) when scaled to the team / QB total",
            )
        run.log(charts)
        return run.url
    finally:
        run.finish()
