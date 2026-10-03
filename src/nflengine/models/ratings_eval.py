"""Walk-forward checks for team ratings (documentation/04 -> A; 08 -> track1-ratings).

The tuning objective: how well ratings **as of week w** predict **week w's** per-play EPA
margin (home offense EPA/play minus away offense EPA/play, garbage time down-weighted
like the model). Prediction = net[home] - net[away] + home field (0 at neutral sites).
Scored as mean squared error on regular-season games.

Two fixed baselines (identical for every configuration, so a sweep has one reference):
- `base_last_season`: last season's raw net EPA/play (no opponent adjustment, no update).
- `base_to_date`: this season's raw net EPA/play so far (last season's before week 2).
Both add last season's league home-field edge.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import polars as pl

from nflengine.models.ratings import play_weights

EVAL_SEASONS = tuple(range(2015, 2026))
WEEK_BUCKETS = ((1, 3), (4, 8), (9, 99))


# ---- targets ----------------------------------------------------------------------------


def team_game_epa(rp: pl.DataFrame, garbage_weight: float, garbage_wp: float) -> pl.DataFrame:
    """Offense EPA per team-game with the model's garbage-time weights."""
    w = play_weights(rp, garbage_weight, garbage_wp)
    return (
        rp.with_columns(w)
        .group_by("season", "week", "game_id", "off", "def")
        .agg(
            (pl.col("epa") * pl.col("w")).sum().alias("epa_w"),
            pl.col("w").sum().alias("w"),
            pl.len().alias("plays"),
        )
        .with_columns((pl.col("epa_w") / pl.col("w")).alias("epa_pp"))
    )


def game_targets(games: pl.DataFrame, tg: pl.DataFrame) -> pl.DataFrame:
    """One row per completed game: each side's EPA/play and the home EPA margin."""
    side = tg.select("game_id", pl.col("off").alias("team"), "epa_pp", "plays")
    g = games.filter(pl.col("completed")).select(
        "game_id",
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "game_type",
        "home_team",
        "away_team",
        pl.col("neutral_site").fill_null(False),
        pl.col("result").cast(pl.Float64),
    )
    return (
        g.join(
            side.rename({"team": "home_team", "epa_pp": "home_epa", "plays": "home_plays"}),
            on=["game_id", "home_team"],
        )
        .join(
            side.rename({"team": "away_team", "epa_pp": "away_epa", "plays": "away_plays"}),
            on=["game_id", "away_team"],
        )
        .with_columns((pl.col("home_epa") - pl.col("away_epa")).alias("epa_margin"))
    )


# ---- predictions ------------------------------------------------------------------------


def add_prediction(
    targets: pl.DataFrame, net: pl.DataFrame, name: str = "pred", hfa_col: str = "hfa"
) -> pl.DataFrame:
    """net (season, week, team, net, hfa as of week) -> prediction for that week's games."""
    lk = net.select("season", "week", "team", "net", hfa_col)
    out = targets.join(
        lk.rename({"team": "home_team", "net": "_nh", hfa_col: "_hfa"}),
        on=["season", "week", "home_team"],
        how="left",
    ).join(
        lk.select("season", "week", "team", "net").rename({"team": "away_team", "net": "_na"}),
        on=["season", "week", "away_team"],
        how="left",
    )
    pred = (
        pl.col("_nh")
        - pl.col("_na")
        + pl.when(pl.col("neutral_site")).then(0.0).otherwise(pl.col("_hfa"))
    )
    return out.with_columns(pred.alias(name)).drop("_nh", "_na", "_hfa")


def _team_side_sums(tg: pl.DataFrame) -> pl.DataFrame:
    """Per team-game: offense and defense weighted EPA sums (for raw baselines)."""
    off = tg.select("season", "week", "game_id", pl.col("off").alias("team"), "epa_w", "w")
    dfn = tg.select(
        "season",
        "week",
        "game_id",
        pl.col("def").alias("team"),
        pl.col("epa_w").alias("depa_w"),
        pl.col("w").alias("dw"),
    )
    return off.join(dfn, on=["season", "week", "game_id", "team"], how="full", coalesce=True)


def add_baselines(targets: pl.DataFrame, tg: pl.DataFrame) -> pl.DataFrame:
    """Add `base_last_season` and `base_to_date` predictions (see module docstring)."""
    sums = ["epa_w", "w", "depa_w", "dw"]
    sides = _team_side_sums(tg).with_columns(pl.col(sums).fill_null(0.0))
    season_raw = (
        sides.group_by("season", "team")
        .agg(
            (
                pl.col("epa_w").sum() / pl.col("w").sum()
                - pl.col("depa_w").sum() / pl.col("dw").sum()
            ).alias("net_last")
        )
        .with_columns((pl.col("season") + 1).cast(pl.Int32).alias("season"))
    )
    hfa = (
        targets.filter(~pl.col("neutral_site"))
        .group_by("season")
        .agg(pl.col("epa_margin").mean().alias("hfa_last"))
        .with_columns((pl.col("season") + 1).cast(pl.Int32).alias("season"))
    )
    # Season-to-date (exclusive of the current week) raw net per team.
    cum = (
        sides.sort("season", "team", "week")
        .with_columns(
            [
                pl.col(c).cum_sum().shift(1, fill_value=0.0).over("season", "team").alias(f"c_{c}")
                for c in ("epa_w", "w", "depa_w", "dw")
            ]
        )
        .with_columns(
            pl.when(pl.col("c_w") > 0)
            .then(pl.col("c_epa_w") / pl.col("c_w") - pl.col("c_depa_w") / pl.col("c_dw"))
            .alias("net_td")
        )
        .select("season", "week", "team", "net_td")
    )
    out = targets.join(hfa, on="season", how="left")
    for side in ("home", "away"):
        out = out.join(
            season_raw.rename({"team": f"{side}_team", "net_last": f"_{side}_last"}),
            on=["season", f"{side}_team"],
            how="left",
        ).join(
            cum.rename({"team": f"{side}_team", "net_td": f"_{side}_td"}),
            on=["season", "week", f"{side}_team"],
            how="left",
        )
    h = pl.when(pl.col("neutral_site")).then(0.0).otherwise(pl.col("hfa_last").fill_null(0.0))
    last = pl.col("_home_last") - pl.col("_away_last")
    td = pl.col("_home_td").fill_null(pl.col("_home_last")) - pl.col("_away_td").fill_null(
        pl.col("_away_last")
    )
    return out.with_columns(
        (last + h).alias("base_last_season"), (td + h).alias("base_to_date")
    ).drop("_home_last", "_away_last", "_home_td", "_away_td", "hfa_last")


# ---- scoring ------------------------------------------------------------------------------


def eval_frame(df: pl.DataFrame, seasons: Sequence[int] = EVAL_SEASONS) -> pl.DataFrame:
    """Regular-season games of `seasons` that every prediction column can score."""
    return df.filter((pl.col("game_type") == "REG") & pl.col("season").is_in(list(seasons)))


def mse(df: pl.DataFrame, col: str, target: str = "epa_margin") -> float:
    return float(((df[col] - df[target]) ** 2).mean())


def score(df: pl.DataFrame, cols: Sequence[str], target: str = "epa_margin") -> dict[str, float]:
    """Pooled MSE / MAE / correlation (with EPA margin and points) per prediction column."""
    out: dict[str, float] = {"games": float(df.height)}
    for c in cols:
        err = df[c] - df[target]
        out[f"mse_{c}"] = float((err**2).mean())
        out[f"mae_{c}"] = float(err.abs().mean())
        out[f"corr_{c}"] = float(np.corrcoef(df[c].to_numpy(), df[target].to_numpy())[0, 1])
        out[f"corr_points_{c}"] = float(
            np.corrcoef(df[c].to_numpy(), df["result"].to_numpy())[0, 1]
        )
        for lo, hi in WEEK_BUCKETS:
            part = df.filter(pl.col("week").is_between(lo, hi))
            label = f"w{lo}_{hi}" if hi < 99 else f"w{lo}plus"
            out[f"mse_{c}_{label}"] = mse(part, c, target) if part.height else float("nan")
    return out


def by_group(df: pl.DataFrame, cols: Sequence[str], by: str, target: str = "epa_margin"):
    """MSE per `by` value (season or week) for each prediction column."""
    return (
        df.group_by(by)
        .agg(
            pl.len().alias("games"),
            *[((pl.col(c) - pl.col(target)) ** 2).mean().alias(f"mse_{c}") for c in cols],
        )
        .sort(by)
    )


# ---- trend validation -----------------------------------------------------------------------


def _ols_fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    Xc = np.column_stack([np.ones(len(X)), X])
    return np.linalg.lstsq(Xc, y, rcond=None)[0]


def _ols_predict(coef: np.ndarray, X: np.ndarray) -> np.ndarray:
    return coef[0] + X @ coef[1:]


def walk_forward_r2(
    df: pl.DataFrame,
    target: str,
    models: dict[str, Sequence[str]],
    eval_seasons: Sequence[int],
    min_train_seasons: int = 3,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Out-of-sample R² per season for each linear model, refit on earlier seasons only.

    Returns (per-season table, pooled out-of-sample predictions with a `cluster` column
    for bootstrapping). R² is 1 - SSE / SST with SST around the training-set mean.
    """
    rows, preds = [], []
    for season in eval_seasons:
        train = df.filter(pl.col("season") < season)
        test = df.filter(pl.col("season") == season)
        if train["season"].n_unique() < min_train_seasons or test.is_empty():
            continue
        y_tr, y_te = train[target].to_numpy(), test[target].to_numpy()
        row = {"season": season, "rows": test.height, "train_seasons": train["season"].n_unique()}
        pred = test.select("season", "week", "team", target)
        base = float(y_tr.mean())
        sst = float(((y_te - base) ** 2).sum())
        for name, feats in models.items():
            coef = _ols_fit(train.select(feats).to_numpy(), y_tr)
            yhat = _ols_predict(coef, test.select(feats).to_numpy())
            row[f"r2_{name}"] = 1 - float(((y_te - yhat) ** 2).sum()) / sst
            pred = pred.with_columns(pl.Series(f"yhat_{name}", yhat))
        pred = pred.with_columns(pl.lit(base).alias("ybar_train"))
        rows.append(row)
        preds.append(pred)
    per_season = pl.DataFrame(rows)
    pooled = pl.concat(preds) if preds else pl.DataFrame()
    return per_season, pooled


def pooled_r2(pooled: pl.DataFrame, target: str, name: str) -> float:
    y = pooled[target].to_numpy()
    sse = float(((y - pooled[f"yhat_{name}"].to_numpy()) ** 2).sum())
    sst = float(((y - pooled["ybar_train"].to_numpy()) ** 2).sum())
    return 1 - sse / sst


def bootstrap_delta_r2(
    pooled: pl.DataFrame,
    target: str,
    base: str,
    full: str,
    n_boot: int = 1000,
    seed: int = 0,
) -> tuple[float, float]:
    """90% interval for R²(full) - R²(base), resampling team-seasons (rows overlap in time)."""
    rng = np.random.default_rng(seed)
    pooled = pooled.with_columns(
        (pl.col("season").cast(pl.String) + "-" + pl.col("team")).alias("cluster")
    )
    y = pooled[target].to_numpy()
    e_base = (y - pooled[f"yhat_{base}"].to_numpy()) ** 2
    e_full = (y - pooled[f"yhat_{full}"].to_numpy()) ** 2
    e_mean = (y - pooled["ybar_train"].to_numpy()) ** 2
    # Local, contiguous cluster ids (Polars categoricals share a global mapping).
    _, codes = np.unique(pooled["cluster"].to_numpy(), return_inverse=True)
    n_cl = int(codes.max()) + 1
    sums = np.vstack(
        [np.bincount(codes, weights=e, minlength=n_cl) for e in (e_base, e_full, e_mean)]
    )
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        pick = np.bincount(rng.integers(0, n_cl, n_cl), minlength=n_cl)
        sb, sf, sm = sums @ pick
        deltas[i] = (sb - sf) / sm
    lo, hi = np.quantile(deltas, [0.05, 0.95])
    return float(lo), float(hi)
