#!/usr/bin/env python3
"""Tradeable validation: does the model's probability predict REAL Polymarket PnL?
Train on binance features (early period), apply to real traded windows (later
period, poly_reconciled = real entries/outcomes/pnl). Time-split = honest OOS.
Also saves the deployable model. CPU-only."""
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV

df = pd.read_csv("dataset.csv").sort_values("ws").reset_index(drop=True)
sign = np.sign(df.drift_bps).replace(0, 1)
df["roc_aligned"] = df.roc_last_bps * sign
df["coin_idx"] = df.coin.map({"BTC": 0, "ETH": 1, "SOL": 2, "XRP": 3})
FEATS = ["abs_drift_bps", "abs_z", "sigma_bps", "roc_aligned",
         "agree_btc", "hour", "coin_idx", "btc_drift_bps"]

cutoff = df.ws.quantile(0.55)        # train early, test on later real trades
tr = df[df.ws < cutoff]
clf = CalibratedClassifierCV(
    HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06, max_iter=300,
                                   l2_regularization=1.0), method="isotonic", cv=3)
clf.fit(tr[FEATS].values, tr.drift_correct.values)
print(f"model trained on {len(tr)} early windows (ws<{int(cutoff)})")

# lookup: (coin,ws) -> features + model prob(drift wins) + drift direction
feat = {(r.coin, int(r.ws)): r for r in df.itertuples()}
probs = clf.predict_proba(df[FEATS].values)[:, 1]
pmap = {(r.coin, int(r.ws)): float(p) for r, p in zip(df.itertuples(), probs)}

# join real trades
pr = pd.read_csv("poly_reconciled.csv")
rows = []
for r in pr.itertuples():
    try: ws = int(float(r.ws))
    except Exception: continue
    k = (r.coin.upper(), ws)
    if k not in pmap or ws < cutoff:        # only OOS (later) windows
        continue
    f = feat[k]
    drift_up = f.drift_bps > 0
    bet_up = str(r.dir).upper().startswith("U")
    p_drift = pmap[k]
    p_bet = p_drift if (bet_up == drift_up) else 1 - p_drift   # P(this bet wins)
    win = 1 if str(r.result).upper().startswith("W") else 0
    rows.append({"p_bet": p_bet, "win": win, "pnl": float(r.pnl or 0),
                 "entry": float(r.avg or 0), "aligned": int(bet_up == drift_up)})
b = pd.DataFrame(rows)
print(f"real OOS traded windows joined: {len(b)} | bet-aligned-with-drift: {b.aligned.mean()*100:.0f}%")
print(f"overall real WR {b.win.mean()*100:.1f}% | total PnL ${b.pnl.sum():+.2f} | avg ${b.pnl.mean():+.3f}/trade\n")

print("=== real WR + PnL by MODEL probability (does the model rank real profit?) ===")
b["bin"] = pd.cut(b.p_bet, [0, .4, .5, .6, .7, .8, 1.01])
for name, g in b.groupby("bin", observed=True):
    if len(g):
        print(f"  p_bet {str(name):<12} WR {g.win.mean()*100:5.1f}% | "
              f"PnL/trade ${g.pnl.mean():+.3f} | entry {g.entry.mean()*100:.0f}c | n={len(g)}")

print("\n=== filter: trade only when model p_bet >= T (real PnL) ===")
for T in (0.5, 0.6, 0.65, 0.7, 0.75):
    g = b[b.p_bet >= T]
    if len(g):
        print(f"  p_bet>={T}: WR {g.win.mean()*100:5.1f}% | total PnL ${g.pnl.sum():+.2f} | "
              f"avg ${g.pnl.mean():+.3f} | trades {len(g)} ({len(g)/len(b)*100:.0f}%)")

# save deployable model (trained on ALL data) + metadata
final = CalibratedClassifierCV(
    HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06, max_iter=300,
                                   l2_regularization=1.0), method="isotonic", cv=3)
final.fit(df[FEATS].values, df.drift_correct.values)
joblib.dump({"model": final, "feats": FEATS}, "drift_model.joblib")
print("\nsaved deployable model -> drift_model.joblib (feats:", FEATS, ")")
