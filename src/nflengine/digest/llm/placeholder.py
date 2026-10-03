"""PlaceholderLLM: a deterministic template writer (documentation/06; default provider).

It fills fixed sentence templates straight from the payload's display strings, so the full
pipeline, the checks and the report card run end to end with no API key, and the checks
get tested on predictable text. It ignores the system prompt and any regeneration
feedback. Every sentence that carries a number also names the entity that owns it.
"""

from __future__ import annotations

from typing import Any

from nflengine.digest.names import last_name

TRIM = 1.0  # stop adding optional sentences past the section's budget (the total must fit)


def _words(text: str) -> int:
    return len(text.split())


def _fit_items(main: list[str], notes: list[str], budget: int | None, minimum: int) -> str:
    """Items in rank order while they fit the budget (at least `minimum`), then each kept
    item's optional note while room remains."""
    keep: list[int] = []
    used = 0
    for i, s in enumerate(main):
        if i < minimum or budget is None or used + _words(s) <= budget * TRIM:
            keep.append(i)
            used += _words(s)
    out = []
    for i in keep:
        out.append(main[i])
        if notes[i] and (budget is None or used + _words(notes[i]) <= budget * TRIM):
            out.append(notes[i])
            used += _words(notes[i])
    return " ".join(out)


def _fit(required: list[str], optional: list[str], budget: int | None) -> str:
    """Required sentences always; optional ones while the section stays within budget."""
    out = list(required)
    for s in optional:
        if budget is None or _words(" ".join([*out, s])) <= budget * TRIM:
            out.append(s)
    return " ".join(out)


def _short_names(payload: dict[str, Any]) -> dict[str, str]:
    """Player id -> last name when it's unique among the payload's players, else full name."""
    people = {
        p["player_id"]: p["player"]
        for key in ("under_the_hood", "players_to_watch")
        for p in payload.get(key, [])
    }
    counts: dict[str, int] = {}
    for name in set(people.values()):
        counts[last_name(name)] = counts.get(last_name(name), 0) + 1
    return {pid: (last_name(n) if counts[last_name(n)] == 1 else n) for pid, n in people.items()}


class PlaceholderLLM:
    name = "placeholder"

    def __init__(self, model: str | None = None):
        self.model = model or "templates-v1"

    def generate(
        self, system_prompt: str, payload: dict[str, Any], output_spec: dict[str, Any]
    ) -> dict[str, str]:
        budgets = {s["id"]: s.get("word_budget") for s in output_spec.get("sections", [])}
        writers = {
            "report_card": self._report_card,
            "game_outlook": self._game_outlook,
            "team_trends": self._team_trends,
            "under_the_hood": self._under_the_hood,
            "players_to_watch": self._players_to_watch,
        }
        out: dict[str, str] = {}
        for sec in budgets:
            writer = writers.get(sec)
            out[sec] = writer(payload, budgets[sec]) if writer else ""
        return out

    # ---- sections -----------------------------------------------------------------------------

    def _report_card(self, p: dict[str, Any], budget: int | None) -> str:
        rc = p["report_card"]
        if rc["status"] != "scored":
            note = rc["note"] or "nothing to grade this week."
            return f"Report card: {note}"
        wk = rc["scored_week_display"]["display"]
        req = [
            f"The model went {rc['picks_correct']['display']} of {rc['picks_total']['display']} "
            f"on its week {wk} picks, with a Brier score of {rc['brier']['display']} against "
            f"Elo's {rc['brier_elo']['display']} (lower is better)."
        ]
        opt: list[str] = []
        bm = rc.get("biggest_miss")
        if bm:
            req.append(
                f"Biggest miss: the model had the {bm['favored_name']} at {bm['prob']['display']}"
                f" and the {bm['winner_name']} won {bm['final_score']}."
            )
        if rc.get("points_mae"):
            opt.append(
                f"Score predictions missed by {rc['points_mae']['display']} per team on "
                "average for the model."
            )
        s = rc.get("season_to_date")
        if s and s["weeks"]["value"] > 1:
            opt.append(
                f"Season to date the model is {s['picks_correct']['display']} of "
                f"{s['picks_total']['display']}, Brier {s['brier']['display']} vs Elo's "
                f"{s['brier_elo']['display']}."
            )
        if rc.get("watchlist_total") and rc["watchlist_total"]["value"]:
            opt.append(
                f"The watch list went {rc['watchlist_hits']['display']} of "
                f"{rc['watchlist_total']['display']} above baseline."
            )
        cal = sorted(rc.get("calibration", []), key=lambda b: -b["games"]["value"])
        if cal:
            b = cal[0]
            opt.append(
                f"Calibration so far: in games the model called {b['bucket']}, the favorite "
                f"won {b['favorite_wins']['display']} of {b['games']['display']}."
            )
        return _fit(req, opt, budget)

    def _game_outlook(self, p: dict[str, Any], budget: int | None) -> str:
        games = [g for g in p["games"] if g["status"] == "upcoming"]
        if not games:
            return "Every game this week kicked off before this run, so there is no outlook."
        req: list[str] = []
        opt: list[str] = []
        disagree = sorted(
            (g for g in games if g.get("model_vs_consensus")),
            key=lambda g: (g["model_vs_consensus"]["size"] != "large", g["kickoff"]),
        )
        for g in disagree[:2]:
            c = g["model_vs_consensus"]
            req.append(f"In {g['matchup']}, the model is {c['text']} (without market data).")
        if not disagree and p["meta"]["market_data_used"]:
            req.append("The model and consensus agree on every game this week.")
        if not p["meta"]["market_data_used"]:
            req.append("No market data was available, so these are model-only numbers.")
        hl = p.get("game_highlights") or {}
        lop = hl.get("most_lopsided") or []
        close = hl.get("closest") or []
        if lop:
            h = lop[0]
            opt.append(
                f"{h['rank_note'].capitalize()} is the {h['team_name']} at "
                f"{h['prob']['display']} in {h['matchup']}."
            )
        if close and (not lop or close[0]["game_id"] != lop[0]["game_id"]):
            h = close[0]
            opt.append(
                f"{h['rank_note'].capitalize()} is {h['matchup']}, with the {h['team_name']} "
                f"at {h['prob']['display']}."
            )
        if p["meta"].get("early_season"):
            opt.append("Early-season ratings still lean on last season, so treat these as rough.")
        if p.get("started_games"):
            opt.append("Games that kicked off before this run are listed without a prediction.")
        return _fit(req, opt, budget)

    def _team_trends(self, p: dict[str, Any], budget: int | None) -> str:
        trends = p["team_trends"]
        if not trends:
            return (
                "No team has moved outside its normal week-to-week range yet, so there are no "
                "trend shifts to report."
            )
        caveat = "Trends describe what changed; they don't predict the next game on their own."
        main: list[str] = []
        extras: list[list[str]] = []
        for t in trends:
            name = t["team_name"]
            word = "up" if t["direction"] == "up" else "down"
            s = (
                f"The {name} are trending {word}: net rating {t['trend_delta']['display']} "
                f"(now {t['net_rating']['display']})"
            )
            drivers = t.get("drivers", [])
            if drivers:
                d = drivers[0]
                s += f", led by the {d['unit']} ({d['change']['display']})"
            main.append(s + ".")
            more = [
                f"The {name} {d['unit']} also moved ({d['change']['display']})."
                for d in drivers[1:2]
            ]
            more += [f"Context for the {name}: {ev}." for ev in t.get("evidence", [])[:1]]
            extras.append(more)
        # every team's headline first, then each team's extras while the budget allows
        used = _words(" ".join([*main, caveat]))
        keep: list[list[str]] = [[] for _ in main]
        for i, more in enumerate(extras):
            for s in more:
                if budget is None or used + _words(s) <= budget * TRIM:
                    keep[i].append(s)
                    used += _words(s)
        body = [" ".join([m, *k]) for m, k in zip(main, keep, strict=True)]
        return " ".join([*body, caveat])

    def _under_the_hood(self, p: dict[str, Any], budget: int | None) -> str:
        items = p["under_the_hood"]
        if not items:
            return "No tracking or charting notes this week: there is no previous week to read."
        short = _short_names(p)
        main: list[str] = []
        notes: list[str] = []
        for u in items:
            who = f"{u['player']} ({u['team_name']} {u['position']})"
            last, label = u["last_week"]["display"], u["label"]
            count = u.get("unit") == "count"
            phrase = u.get("unit") in ("count", "yards_per_carry", "yards_per_catch")
            stat = (
                f"{last} {label} last week"
                if count
                else (f"{last} last week" if phrase else f"{label} of {last} last week")
            )
            if not count:
                stat += f" on {u['volume']['display']} {u['volume_label']}"
            if u["kind"] == "standout" or not u.get("season_avg"):
                s = f"{who}: {stat}, the {u['rank_note']}."
                note = ""
            else:
                verb = "up" if u["kind"] == "riser" else "down"
                avg = u["season_avg"]["display"] + (" per game" if count else "")
                s = f"{who}: {stat}, {verb} from {avg} ({u['norm_note']})."
                note = f"For {short[u['player_id']]}, that was the {u['rank_note']}."
            if u.get("confidence") == "low":
                s = s[:-1] + ", a small sample."
            main.append(s)
            notes.append(note)
        return _fit_items(main, notes, budget, minimum=3)

    def _players_to_watch(self, p: dict[str, Any], budget: int | None) -> str:
        items = p["players_to_watch"]
        if not items:
            return "No watch list this week: there isn't enough recent usage data yet."
        short = _short_names(p)
        main: list[str] = []
        for w in items:
            rank_note = f"the {w['opp_def_rank']['display']}"
            main.append(
                f"{w['player']} ({w['team_name']} {w['position']}) draws the "
                f"{w['opponent_name']} {w['opp_def_unit']} ({rank_note} by EPA allowed): "
                f"{short[w['player_id']]}'s {w['usage_metric']} is "
                f"{w['usage_recent']['display']} lately, up from {w['usage_before']['display']} "
                f"{w['usage_before_note']}; baseline {w['baseline']['display']} "
                "(low confidence)."
            )
        return _fit_items(main, [""] * len(main), budget, minimum=4)
