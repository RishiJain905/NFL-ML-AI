"""Play calling (PC00+): team tendencies, forecasts and play diagrams.

A track of its own (documentation/play-calling/). It adds new tables and models only and never
changes the production models, their features, settings or artifacts (the no-touch rule, D107).
Nothing here imports from `nflengine.models` or `nflengine.features`.

- `labels`: the definitions (neutral situation, deep shot, blitz, run direction ...), the
  situation buckets and the metric registry: one place the tables, pages and forecast share.
- `participation`: nflverse participation (research only, 2016-2025) parsed into play labels.
- `build`: plays + FTN (+ participation for history) -> enriched plays and tendency tables, as
  of each week; `nfl playcalling build`.
"""
