"""Forecast lab (FL00+; documentation/forecast-lab/, D158): new models beside the production
ones, never inside them (D107).

- `schema.py`: the contract: targets, file layout, the prediction-file schemas;
- `availability.py` / `availability_runs.py`: will he play, how much, who inherits (FL00);
- `curves.py` / `curves_runs.py`: the full outcome curve behind a player projection (FL01);
- `update.py`: what's due after a weekly run or an injury update, and the update itself (FL02).

Self-contained like `nflengine.live`: it never imports `nflengine.models` or
`nflengine.features`. It reads curated tables and published outputs (the week's
`predictions_players.parquet`, the walk-forward backtest predictions) and writes only under
`{data root}/forecastlab/` and `{data root}/models/forecastlab/`.
"""
