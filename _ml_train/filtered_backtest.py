#!/usr/bin/env python3
"""HONESTY CHECK: does the model add value INSIDE the bot's tradeable band (the
bot already gates drift>=10bps), or was its power just from excluding the easy
tiny-drift coin-flips? Re-run walk-forward at several drift floors."""
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score

full = pd.read_csv("dataset.csv").sort_values("ws").reset_index(drop=True)
sign = np.sign(full.drift_bps).replace(0, 1)
full["roc_aligned"] = full.roc_last_bps * sign
full["coin_idx"] = full.coin.map({"BTC": 0, "ETH": 1, "SOL": 2, "XRP": 3})
FEATS = ["abs_drift_bps", "abs_z", "sigma_bps", "roc_aligned", "agree_btc", "hour",
         "coin_idx", "btc_drift_bps"]

for floor in (0, 10, 15, 20):
    df = full[full.abs_drift_bps >= floor].reset_index(drop=True)
    X = df[FEATS].values; y = df.drift_correct.values
    N = len(df); start = N // 2
    oos_p = np.full(N, np.nan)
    bounds = np.linspace(start, N, 5).astype(int)
    for i in range(len(bounds) - 1):
        te = bounds[i + 1]; tr = bounds[i]
        clf = CalibratedClassifierCV(
            HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06, max_iter=250,
                                           l2_regularization=1.0), method="isotonic", cv=3)
        clf.fit(X[:tr], y[:tr])
        oos_p[tr:te] = clf.predict_proba(X[tr:te])[:, 1]
    m = ~np.isnan(oos_p); yt = y[m]; pt = oos_p[m]
    base = yt.mean()
    # discrimination INSIDE the band: top-decile vs bottom-decile actual WR
    order = np.argsort(pt); bot = order[:len(order)//5]; top = order[-len(order)//5:]
    spread = yt[top].mean() - yt[bot].mean()
    try: auc = roc_auc_score(yt, pt)
    except Exception: auc = float('nan')
    print(f"drift>={floor:>2}bps | n={N:6d} | base {base*100:4.1f}% | "
          f"AUC {auc:.3f} | top20% {yt[top].mean()*100:4.1f}% vs bot20% {yt[bot].mean()*100:4.1f}% "
          f"(spread {spread*100:+.0f}pts)")
