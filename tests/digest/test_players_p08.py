"""P08 targets in the digest: the table-only "TD chance" / "Sack chance" columns, and the
guarantee that a week with only P06 projections reads exactly as it did before them."""

from __future__ import annotations

import polars as pl
from conftest import make_payload
from test_players import (
    SPECS,
    TUESDAY,
    frame,
    model_payload,
    proj,
    run_placeholder,
    sb_rows,
    week_preds,
)

from nflengine.digest import format as F
from nflengine.digest import render as R
from nflengine.digest.players import (
    _name,
    _shared_labels,
    build_model_watch,
    model_watch,
    pick_chances,
    scoreboard_highlights,
)
from nflengine.models.player_schema import TARGET_BY_KEY


def extra_row(key: str, base: dict, **kw) -> dict:
    """A P08 projection row for the same player and game as `base` (a main-target row)."""
    t = TARGET_BY_KEY[key]
    r = {
        **base,
        "target": t.name,
        "group": t.group,
        "target_label": t.label,
        "kind": t.kind,
        "unit": t.unit,
        "is_main": False,
        "p10": None,
        "p50": None,
        "p90": None,
        "drivers": [],
    }
    r.update(kw)
    return r


def with_p08() -> pl.DataFrame:
    rb = proj("rb1", "Bijan Robinson", "ATL", "TB", 0.9, group="RB")
    rb.update(position="RB", pgroup="RB")
    qb = proj("qb1", "Josh Allen", "BUF", "MIA", 0.7, group="QB")
    qb.update(position="QB", pgroup="QB")
    edge = proj("e1", "Micah Parsons", "GB", "DET", 0.8, group="EDGE/DL")
    edge.update(position="LB", pgroup="LB")
    rows = [
        proj("p1", "Puka Nacua", "LA", "SF", 1.0),
        proj("p3", "Fred Warner", "SF", "LA", 0.8, group="LB/S"),
        rb,
        qb,
        edge,
    ]
    p08 = [
        extra_row("td-wrte", rows[0], mean=0.41, p_ge1=0.41, baseline=0.3, baseline_p_ge1=0.3),
        extra_row("td-rb", rb, mean=0.34, p_ge1=0.34, baseline=0.25, baseline_p_ge1=0.25),
        extra_row("pass_tds-qb", qb, mean=1.64, p_ge1=0.8, p_ge2=0.5, p50=2.0),
        extra_row("sacks-edge", edge, mean=0.31, p_ge1=0.22, p_ge2=0.03, p50=0.0),
    ]
    return frame(rows + p08)


def test_only_p06_projections_give_the_p06_tables() -> None:
    preds = week_preds()
    assert pick_chances(preds) == {} and pick_chances(None) == {}
    pw = model_watch(preds, TUESDAY)
    assert all(
        w.td_chance is None and w.sack_chance is None and w.pass_tds is None for w in pw.items
    )
    table = R.watch_table(make_payload(players_to_watch=pw.items, tough_spots=pw.tough))
    for chunk in table.split("\n\n")[1::2]:  # the two tables (titles sit between them)
        assert chunk.splitlines()[:2] == R.MODEL_HEADER  # exactly the P06 header
    assert "TD chance" not in table and "Sack chance" not in table
    assert table.count("| Baseline | Confidence |") == 2
    # and nothing extra reaches the saved payload or the LLM input
    dump = make_payload(players_to_watch=pw.items).model_dump(mode="json")
    assert {"td_chance", "sack_chance", "pass_tds"}.isdisjoint(dump["players_to_watch"][0])


def test_shipped_targets_add_one_column_per_side() -> None:
    preds = with_p08()
    chances = pick_chances(preds)
    assert chances[("p1", "2025_08_SF_LA")] == {"td": 0.41}
    assert chances[("qb1", "2025_08_MIA_BUF")] == {"pass_tds": 1.64}
    assert chances[("e1", "2025_08_DET_GB")] == {"sacks": 0.22}
    pw = model_watch(preds, TUESDAY)
    by = {w.player: w for w in pw.items}
    assert by["Puka Nacua"].td_chance.display == "41%"
    assert by["Bijan Robinson"].td_chance.display == "34%"
    assert by["Josh Allen"].pass_tds.display == "1.6 passing TDs"
    assert by["Micah Parsons"].sack_chance.display == "22%"
    assert by["Fred Warner"].sack_chance is None  # an off-ball linebacker: no sack projection
    table = R.watch_table(make_payload(players_to_watch=pw.items, tough_spots=pw.tough))
    off, dfn = table.split("\n\n")[1], table.split("\n\n")[3]
    assert "| Baseline | TD chance | Confidence |" in off
    assert "| Baseline | Sack chance | Confidence |" in dfn
    rows = {line.split("|")[1].strip(): line.split("|") for line in off.splitlines()[2:]}
    row = next(v for k, v in rows.items() if k.startswith("Puka Nacua"))
    assert row[6].strip() == "41%"
    qb_row = next(v for k, v in rows.items() if k.startswith("Josh Allen"))
    assert qb_row[6].strip() == "1.6 passing TDs"
    warner = next(line for line in dfn.splitlines() if "Fred Warner" in line).split("|")
    assert warner[6].strip() == "–"  # no chance for him: a dash, never a made-up number
    assert all(line.count("|") == off.splitlines()[0].count("|") for line in off.splitlines())


def test_a_column_only_appears_for_a_side_with_a_chance() -> None:
    preds = with_p08().filter(pl.col("target") != "sacks")  # no sack projections this week
    pw = model_watch(preds, TUESDAY)
    table = R.watch_table(make_payload(players_to_watch=pw.items))
    assert "TD chance" in table and "Sack chance" not in table


def test_p08_rows_never_enter_the_watch_list_or_tough_spots() -> None:
    plain = model_watch(week_preds(), TUESDAY)
    both = model_watch(
        frame(
            [
                *week_preds().to_dicts(),
                extra_row(
                    "td-wrte", proj("p1", "Puka Nacua", "LA", "SF", 1.0), mean=0.9, p_ge1=0.9
                ),
            ]
        ),
        TUESDAY,
    )
    assert [w.player for w in both.items] == [w.player for w in plain.items]
    assert [w.player for w in both.tough] == [w.player for w in plain.tough]
    assert both.frame.select("player_id", "target", "rank").equals(
        plain.frame.select("player_id", "target", "rank")
    )


def test_digest_with_chances_passes_the_checks() -> None:
    pw = model_watch(with_p08(), TUESDAY)
    p = make_payload(players_to_watch=pw.items, tough_spots=pw.tough)
    synth = run_placeholder(p)
    assert synth.final.passed, synth.final.to_dict()
    # the numbers are table-only: the LLM never sees them, so the prose can't quote them
    assert "41%" not in synth.sections["players_to_watch"]


def test_shared_labels_are_found_from_the_rows_not_from_every_target() -> None:
    """P08 adds "rushing yards" (QB) and a touchdown chance for two groups; the report card
    names the group only when the rows being summarised carry the label twice (or it is a P06
    shared one)."""
    p06 = sb_rows(2026, [4], "live", SPECS)
    agg = p06.select("target_label", "position_group")
    assert _shared_labels(agg) == {"receptions"}  # what it always was
    assert _name("rushing yards", "RB", _shared_labels(agg)) == "rushing yards"
    mixed = pl.DataFrame(
        {
            "target_label": ["rushing yards", "rushing yards", "pressures"],
            "position_group": ["RB", "QB", "EDGE/DL"],
        }
    )
    assert "rushing yards" in _shared_labels(mixed)
    assert _name("rushing yards", "QB", _shared_labels(mixed)) == "rushing yards (QB)"
    lines = scoreboard_highlights(
        sb_rows(2026, [4], "live", {"rush_yds": ("RB", "rushing yards", 18.0, 20.0, 0.8)}),
        None,
        2026,
        5,
        "live",
    )
    assert lines[0].startswith("rushing yards projections beat")  # P06 wording unchanged


def test_scoreboard_highlights_skip_probability_rows() -> None:
    live = sb_rows(2026, [4, 5], "live", SPECS)
    prob = pl.DataFrame(
        {
            "season": [2026, 2026],
            "week": [4, 5],
            "target": ["td", "td"],
            "position_group": ["RB", "RB"],
            "target_label": ["chance of a touchdown"] * 2,
            "n_scored": [30, 30],
            "n_not_played": [1, 1],
            "mae_model": [None, None],
            "mae_baseline": [None, None],
            "improvement_pct": [None, None],
            "coverage_80": [None, None],
            "mode": ["live", "live"],
        },
        schema_overrides={c: pl.Float64 for c in ("mae_model", "mae_baseline", "improvement_pct")}
        | {"coverage_80": pl.Float64},
    )
    both = scoreboard_highlights(pl.concat([live, prob], how="diagonal_relaxed"), None, 2026, 6)
    assert both == scoreboard_highlights(live, None, 2026, 6, "live")


def test_placeholder_prose_is_unchanged_by_the_new_fields() -> None:
    plain = run_placeholder(model_payload()).sections["players_to_watch"]
    pw = model_watch(week_preds(), TUESDAY)
    again = run_placeholder(
        make_payload(
            players_to_watch=build_model_watch(
                pw.frame, pick_chances(week_preds())
            ),  # the same picks, now through the chances path
            tough_spots=pw.tough,
        )
    ).sections["players_to_watch"]
    assert plain == again


def _p06_reference_table(items) -> str:
    """The Offense / Defense tables exactly as they were rendered before P08 (a literal copy of
    the old header and row format), to compare byte for byte."""
    header = [
        "| Player | Game | Projection | Range (80%) | Baseline | Confidence | Main driver / note |",
        "|---|---|---|---|---|---|---|",
    ]
    blocks = []
    for side, title in (("offense", "Offense"), ("defense", "Defense")):
        part = [w for w in items if w.side == side]
        if not part:
            continue
        rows = []
        for w in part:
            lead = w.drivers[:1] or ([w.driver_note] if w.driver_note else [])
            why = "; ".join([*lead, *([w.baseline_note] if w.baseline_note else [])]) or "–"
            rows.append(
                f"| {w.player} ({w.team_name} {w.position}) | vs {w.opponent_name} | "
                f"{w.projection.display} | {w.interval.display} | {w.baseline.display} | "
                f"{w.confidence} | {why} |"
            )
        n = len(part)
        blocks.append(
            f"**{title}** ({n} pick{'s' if n != 1 else ''})\n\n" + "\n".join([*header, *rows])
        )
    return "\n\n".join(blocks)


def test_p06_only_week_renders_and_serialises_exactly_as_before() -> None:
    """The same watch list, built without any P08 numbers, with an empty chances map and with a
    chances map that covers none of the picks: one table, byte for byte the pre-P08 one, and
    one payload JSON with none of the new fields in it."""
    preds = week_preds()
    pw = model_watch(preds, TUESDAY)
    picks = pl.DataFrame(pw.frame).drop("projected_at", "source")
    plain = build_model_watch(picks)
    empty = build_model_watch(picks, {})
    elsewhere = build_model_watch(picks, {("nobody", "no_game"): {"td": 0.4}})
    reference = _p06_reference_table(pw.items)
    for items in (pw.items, plain, empty, elsewhere):
        payload = make_payload(players_to_watch=items, tough_spots=pw.tough)
        assert R.watch_table(payload) == reference
        raw = payload.model_dump_json(indent=2)
        assert "td_chance" not in raw and "sack_chance" not in raw and "pass_tds" not in raw
    jsons = {make_payload(players_to_watch=i).model_dump_json() for i in (pw.items, plain, empty)}
    assert len(jsons) == 1
    # fields set on every pick change the table, never the JSON
    with_chances = [w.model_copy(update={"td_chance": F.pct(0.3)}) for w in pw.items]
    assert R.watch_table(make_payload(players_to_watch=with_chances)) != reference
    assert (
        make_payload(players_to_watch=with_chances).model_dump_json()
        == make_payload(players_to_watch=pw.items).model_dump_json()
    )


def test_unsided_picks_keep_the_single_p06_table() -> None:
    """A list without sides (older runs) never gets the extra column, whatever the fields say."""
    pw = model_watch(week_preds(), TUESDAY)
    flat = [w.model_copy(update={"side": None, "td_chance": F.pct(0.3)}) for w in pw.items]
    table = R.watch_table(make_payload(players_to_watch=flat))
    assert table.splitlines()[:2] == R.MODEL_HEADER and "TD chance" not in table
