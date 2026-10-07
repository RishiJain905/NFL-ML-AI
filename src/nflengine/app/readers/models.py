"""The Models page (CR03): one card per production model, its headline backtest numbers, its
settings and its model card.

- **Which version is in production:** the W&B `production` alias (the artifact list the
  Artifacts section reads), named by the local `models/<name>/<alias>/meta.json`; without W&B,
  the newest run summary's `models` (labelled "from the run summary").
- **Headline numbers:** the canonical backtests' `summary.json` on D: (`runs/backtests/game/
  <variant>`, `runs/backtests/player/<target>-<group>`, `runs/backtests/team/<target>-team`);
  the ratings' evaluation lives only in W&B (`ratings-eval`, read once and cached for a week).
- **Settings:** `config/settings.yaml` (never a secret: settings hold no keys).
- **Model cards:** a fixed list of files under `documentation/` (`CARDS`): the browser asks
  for a card by id, never by path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nflengine.app.readers.common import clean, num, read_json, read_text
from nflengine.paths import DataPaths

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS = REPO_ROOT / "documentation"

# id -> (title, path under documentation/)
CARDS: dict[str, tuple[str, str]] = {
    "game-model-v0": ("Game model v0", "model_cards/game-model-v0.md"),
    "game-model-v1": ("Game model v1 (evaluated, not promoted)", "model_cards/game-model-v1.md"),
    "team-ratings": ("Team ratings and Elo", "model_cards/team_ratings.md"),
    "player-model-v1": ("Player model v1: overview", "model_cards/player-model-v1.md"),
    "player-qb": ("Player model: QB", "model_cards/player-qb.md"),
    "player-rb": ("Player model: RB", "model_cards/player-rb.md"),
    "player-wrte": ("Player model: WR/TE", "model_cards/player-wrte.md"),
    "player-defense": ("Player model: defense", "model_cards/player-defense.md"),
    "player-p08": ("Player model: the P08 targets", "model_cards/player-p08.md"),
    "team-stats-v1": ("Team stat totals v1", "model_cards/team-stats-v1.md"),
    "llm-digest-writer": ("The digest writer (guide)", "guides/llm-digest-writer.md"),
}
MAX_CARD_CHARS = 400_000

GROUP_LABEL = {
    "qb": "QB",
    "rb": "RB",
    "wrte": "WR/TE",
    "edge": "EDGE/DL",
    "lbs": "LB/S",
    "cbs": "CB/S",
}


def card(card_id: str) -> dict[str, Any] | None:
    if card_id not in CARDS:
        return None
    title, rel = CARDS[card_id]
    text = read_text(DOCS / rel)
    if text is None:
        return None
    return {"id": card_id, "title": title, "markdown": clean(text, None, MAX_CARD_CHARS) or ""}


def _cards(*ids: str) -> list[dict[str, str]]:
    return [
        {
            "id": i,
            "title": CARDS[i][0],
            "kind": "guide" if CARDS[i][1].startswith("guides/") else "card",
        }
        for i in ids
        if (DOCS / CARDS[i][1]).exists()
    ]


def _summary(paths: DataPaths, *parts: str) -> dict[str, Any]:
    return read_json(paths.runs.joinpath("backtests", *parts, "summary.json")) or {}


def _production(
    paths: DataPaths, name: str, artifacts: dict[str, Any] | None, local_label: str | None
) -> dict[str, Any]:
    for col in (artifacts or {}).get("collections") or []:
        if col.get("name") != name:
            continue
        for v in col.get("versions") or []:
            if "production" in (v.get("aliases") or []):
                alias = next((a for a in v["aliases"] if a[:4].isdigit() and "-w" in a), None)
                meta = read_json(paths.models / name / alias / "meta.json") if alias else None
                return {
                    "version": v.get("version"),
                    "label": clean((meta or {}).get("model_version"), None, 80),
                    "week_alias": alias,
                    "run_id": v.get("logged_by"),
                    "source": "wandb",
                }
    if local_label:
        alias = local_label.rsplit(":", 1)[-1] if ":" in local_label else None
        return {
            "version": None,
            "label": clean(local_label, None, 80),
            "week_alias": alias,
            "run_id": None,
            "source": "local",
        }
    return {"version": None, "label": None, "week_alias": None, "run_id": None, "source": None}


def _latest_models(paths: DataPaths, season: int) -> dict[str, str]:
    """The newest run summary's `models` (game / player / team -> label)."""
    for w in range(22, 0, -1):
        s = read_json(paths.run_dir(season, w) / "run_summary.json")
        if s and isinstance(s.get("models"), dict):
            return {k: (v or [None])[0] for k, v in s["models"].items() if v}
    return {}


def _latest_prompt(paths: DataPaths, season: int) -> str | None:
    from nflengine.app.readers.digest import PROMPT_ID

    for w in range(22, 0, -1):
        text = read_text(paths.report_path(season, w))
        if text and (m := PROMPT_ID.search(text)):
            return m.group(1)
    return None


def _target_bars(paths: DataPaths, folder: str, targets: list[str]) -> list[dict[str, Any]]:
    bars = []
    for key in targets:
        s = _summary(paths, folder, key)
        target, _, group = key.rpartition("-")
        label = (
            f"{target} · {GROUP_LABEL.get(group, group.upper())}" if folder == "player" else target
        )
        if num(s.get("improvement_pct")) is not None:
            bars.append(
                {"label": label, "value": round(float(s["improvement_pct"]), 2), "metric": "mae"}
            )
        elif num(s.get("brier_improvement_pct")) is not None:
            bars.append(
                {
                    "label": label,
                    "value": round(float(s["brier_improvement_pct"]), 2),
                    "metric": "brier",
                }
            )
    bars.sort(key=lambda b: -b["value"])
    return bars


def get_models(
    paths: DataPaths,
    season: int,
    artifacts: dict[str, Any] | None,
    ratings_eval: dict[str, Any] | None,
    state: dict[str, Any],
) -> dict[str, Any]:
    from nflengine.settings import get_config

    cfg = get_config()
    local = _latest_models(paths, season)
    mo, mk = _summary(paths, "game", "model_only"), _summary(paths, "game", "market")
    gm = cfg.game_model or {}
    game = {
        "id": "game",
        "title": "Game model",
        "production": _production(paths, "game-model", artifacts, local.get("game")),
        "headline": [
            {
                "label": "Model only",
                "value": num(mo.get("brier_model"), 4),
                "unit": "brier",
                "detail": f"vs Elo {num(mo.get('brier_elo'), 4)} · "
                f"{int(mo.get('games') or 0):,} games, walk-forward",
            },
            {
                "label": "Market-informed",
                "value": num(mk.get("brier_model"), 4),
                "unit": "brier",
                "detail": f"vs closing market {num(mk.get('brier_market'), 4)}",
            },
            {
                "label": "Pick accuracy",
                "value": num(mk.get("accuracy_model"), 4),
                "unit": "pct",
                "detail": "market-informed, every backtest game",
            },
        ],
        "bars": None,
        "settings": [
            {"label": "Version", "value": str(gm.get("version", "v0")), "mono": True},
            {
                "label": "Win probability",
                "value": f"from the margin ({gm.get('win_method', 'margin')})",
            },
            {"label": "Calibration", "value": str(gm.get("calibration", "none"))},
            {"label": "Refit", "value": "every Tuesday, walk-forward"},
            {"label": "v1 (trees on top)", "value": "evaluated, not promoted (D79)"},
        ],
        "cards": _cards("game-model-v0", "game-model-v1"),
        "note": None,
    }
    rs = (ratings_eval or {}).get("summary") or {}
    rcfg, ecfg = cfg.ratings or {}, cfg.elo or {}
    ratings = {
        "id": "ratings",
        "title": "Team ratings & Elo",
        "production": {
            "version": None,
            "label": "rebuilt every Tuesday (features/team_ratings, team_elo)",
            "week_alias": None,
            "run_id": (ratings_eval or {}).get("id"),
            "source": "local",
        },
        "headline": [
            {
                "label": "Rating MSE",
                "value": num(rs.get("objective_mse"), 4),
                "unit": "mse",
                "detail": (
                    f"vs {num(rs.get('mse_base_last_season'), 4)} / "
                    f"{num(rs.get('mse_base_to_date'), 4)} baselines"
                    if rs
                    else "W&B ratings-eval (not reachable now)"
                ),
            },
            {
                "label": "Elo Brier",
                "value": num(rs.get("elo_brier"), 4),
                "unit": "brier",
                "detail": f"walk-forward, {int(rs.get('games') or 0):,} games" if rs else "",
            },
        ],
        "bars": None,
        "settings": [
            {
                "label": "Ratings",
                "value": f"half-life {rcfg.get('half_life_weeks')} · "
                f"prior {rcfg.get('prior_regression')} · alpha {rcfg.get('ridge_alpha')} (D46)",
            },
            {
                "label": "Elo",
                "value": f"K {ecfg.get('k')} · home field {ecfg.get('hfa')} · "
                f"revert {ecfg.get('revert')}",
            },
            {"label": "Trends", "value": "descriptive only (D47)"},
        ],
        "cards": _cards("team-ratings"),
        "note": None,
    }
    live = list((cfg.player_model or {}).get("live_targets") or [])
    pbars = _target_bars(paths, "player", live)
    player = {
        "id": "player",
        "title": "Player models",
        "production": _production(paths, "player-model", artifacts, local.get("player")),
        "headline": [
            {
                "label": "Live stats",
                "value": float(len(live)),
                "unit": "count",
                "detail": "one LightGBM model per stat and group",
            },
            {
                "label": "Beat the baseline",
                "value": float(sum(1 for b in pbars if b["value"] > 0)),
                "unit": "count",
                "detail": f"of {len(pbars)} with a backtest, 2019–2025 walk-forward",
            },
        ],
        "bars": pbars or None,
        "settings": [
            {"label": "Ranges", "value": "P10–P90 (yards: conformal; counts: negative binomial)"},
            {"label": "Counts compared with", "value": "the baseline's median (D65)"},
            {"label": "Chances", "value": "Brier vs the player's rolling rate (P08)"},
        ],
        "cards": _cards(
            "player-model-v1",
            "player-qb",
            "player-rb",
            "player-wrte",
            "player-defense",
            "player-p08",
        ),
        "note": (
            "% better than the rolling baseline: MAE for amounts and counts, Brier for chances."
        ),
    }
    tlive = list((cfg.team_model or {}).get("live_targets") or [])
    tbars = _target_bars(paths, "team", tlive)
    team = {
        "id": "team",
        "title": "Team stat totals",
        "production": _production(paths, "team-model", artifacts, local.get("team")),
        "headline": [
            {
                "label": "Shipped",
                "value": float(len(tlive)),
                "unit": "count",
                "detail": "of 5 evaluated; takeaways not shipped (D80)",
            },
        ],
        "bars": tbars or None,
        "settings": [
            {
                "label": "Consistency layer",
                "value": "receptions clamped to targets; yards logged only (D81)",
            }
        ],
        "cards": _cards("team-stats-v1"),
        "note": None,
    }
    llm = cfg.llm
    route = (llm.openrouter or {}).get("order") if hasattr(llm, "openrouter") else None
    writer = {
        "id": "writer",
        "title": "Digest writer",
        "production": {
            "version": None,
            "label": clean(llm.model, None, 80),
            "week_alias": None,
            "run_id": None,
            "source": "local",
        },
        "headline": [],
        "bars": None,
        "settings": [
            {"label": "Provider", "value": str(llm.provider)},
            {"label": "Model", "value": str(llm.model), "mono": True},
            {"label": "Route", "value": " → ".join(route) if route else "default", "mono": True},
            {
                "label": "Reasoning effort",
                "value": str(getattr(llm, "reasoning_effort", "") or "default"),
            },
            {"label": "Prompt", "value": _latest_prompt(paths, season) or "—", "mono": True},
            {"label": "Checks", "value": "11 (provenance, binding, meaning, length …)"},
        ],
        "cards": _cards("llm-digest-writer"),
        "note": None,
    }
    return {"wandb": state, "models": [game, ratings, player, team, writer]}
