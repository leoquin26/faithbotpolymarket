# CleanBot ML model — early-drift success predictor

Offline pipeline that trains a **calibrated probability** for "does betting the
early drift win the 15m close?" — used by CleanBot v1.6 as a gate + (future) Kelly
sizer. **CPU-only** (tabular, ~46k rows; no GPU needed). Data/cache are gitignored;
only the scripts + the deployed model (`../drift_model_band.joblib`,
`../model_features.py`) are committed.

## Pipeline (run in order)
1. **`build_dataset.py`** — fetch 120d of 1m binance klines (BTC/ETH/SOL/XRP),
   build per-window features at minute 5 + true 15m outcome → `dataset.csv`
   (~46k windows, base drift-correct 70.6%). Caches klines in `klines_cache/`.
2. **`train_model.py`** — walk-forward OOS backtest + calibration on the full set.
   Headline: top decile 93.5%, calibrated, but inflated by easy tiny-drift windows.
3. **`filtered_backtest.py`** — honesty check: re-run restricted to drift≥10bps (the
   bot's tradeable band). The lift shrinks but stays real (top20% 93.6% vs bot20%
   62.9%).
4. **`model_features.py`** — the SHARED feature math, imported by both the builder
   and the live bot (guarantees parity; fixed a break where the bot computed sigma
   differently). Deployed to the bot.
5. **`build_v2.py`** — rebuild with the shared features, train on the band, validate,
   save **`drift_model_band.joblib`** (the deployed model: HistGradientBoosting +
   isotonic, sklearn 1.8).
6. **`execution_backtest.py`** — tradeable validation vs real Polymarket trades
   (`poly_reconciled.csv`): filtering the old bot's trades by model prob turned
   −$97 into +$136 OOS (the ask sits ~59¢ regardless of confidence → real +EV).
7. **`parity_check.py` / `diag.py`** — feature-parity and join diagnostics.

## Honest scope
- Numbers are **binance directional** (no execution); live WR is lower. The value is
  the **ranking + calibrated probability**, validated OOS on real Polymarket PnL.
- Currently running **DRY shadow** in the bot to confirm live calibration before any
  real money (`CLEAN_MODEL_GATE`, `CLEAN_MODEL_MIN_PROB`).

## Retrain
`python build_dataset.py && python build_v2.py` → redeploy `drift_model_band.joblib`
+ `model_features.py` to the bot, restart. Same sklearn version (1.8) both ends.
