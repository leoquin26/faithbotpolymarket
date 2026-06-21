#!/usr/bin/env python3
"""Retrain matching the bot's REAL entry timing: features are computed at the FIRST
minute (2..5) the drift crosses >=10bps — exactly when the bot enters — instead of a
fixed minute-5 snapshot. Fixes the live miscalibration (model saturated at 0.84
because it scored age~2-3min windows on a minute-5-trained model). CPU-only."""
import json, os, datetime as dt, numpy as np, pandas as pd, joblib
import model_features as MF
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score

COINS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
CACHE = "klines_cache"; FLOOR = 0.0010   # 10 bps entry trigger


def load():
    OP, CL = {}, {}
    for c, sym in COINS.items():
        op, cl = {}, {}
        for fn in os.listdir(CACHE):
            if fn.startswith(sym + "_"):
                for k in json.load(open(os.path.join(CACHE, fn))):
                    t = k[0] // 1000; op[t] = float(k[1]); cl[t] = float(k[4])
        OP[c], CL[c] = op, cl
    return OP, CL


print("loading cached klines ...")
OP, CL = load()


def closes(coin, t0, k):
    out = []
    for m in range(1, k + 1):
        v = CL[coin].get(t0 + m * 60)
        if v is None:
            return None
        out.append(v)
    return out


rows = []
allt = sorted(CL["BTC"]); t0 = (allt[0] // 900 + 1) * 900; last = allt[-1] - 900
while t0 <= last:
    for coin in COINS:
        strike = OP[coin].get(t0)
        close = CL[coin].get(t0 + 840)
        if not strike or close is None:
            continue
        # find FIRST minute k in 2..5 where |drift| >= 10bps (the bot's entry moment)
        entry_k = None
        for k in range(2, 6):
            c = CL[coin].get(t0 + k * 60)
            if c is None:
                break
            if abs((c - strike) / strike) >= FLOOR:
                entry_k = k; break
        if entry_k is None:
            continue
        ec = closes(coin, t0, entry_k)
        if ec is None:
            continue
        bs = OP["BTC"].get(t0); bc = CL["BTC"].get(t0 + entry_k * 60)
        btc_d = (bc - bs) / bs if (bs and bc) else None
        feats = MF.compute(strike, ec, btc_d, dt.datetime.utcfromtimestamp(t0).hour, coin)
        if feats is None:
            continue
        winner = 1 if close > strike else 0
        bet_up = 1 if (ec[-1] - strike) > 0 else 0
        rows.append(feats + [int(bet_up == winner), entry_k, t0])
    t0 += 900

cols = MF.FEATS + ["drift_correct", "entry_k", "ws"]
df = pd.DataFrame(rows, columns=cols).sort_values("ws").reset_index(drop=True)
print(f"entry-timed windows (first cross >=10bps): {len(df)} | base {df.drift_correct.mean()*100:.1f}%")
print("entry-minute distribution:", df.entry_k.value_counts().sort_index().to_dict())

X = df[MF.FEATS].values; y = df.drift_correct.values
N = len(df); oos = np.full(N, np.nan); bounds = np.linspace(N//2, N, 5).astype(int)
for i in range(len(bounds)-1):
    tr, te = bounds[i], bounds[i+1]
    clf = CalibratedClassifierCV(HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=250, l2_regularization=1.0),
        method="isotonic", cv=3).fit(X[:tr], y[:tr])
    oos[tr:te] = clf.predict_proba(X[tr:te])[:, 1]
m = ~np.isnan(oos); yt, pt = y[m], oos[m]
order = np.argsort(pt); q = len(order)//5
print(f"WALK-FWD (n={m.sum()}): AUC {roc_auc_score(yt,pt):.3f} | "
      f"top20% {yt[order[-q:]].mean()*100:.1f}% vs bot20% {yt[order[:q]].mean()*100:.1f}%")
print("  calibration:", " ".join(
    f"{lo:.1f}->{yt[(pt>=lo)&(pt<lo+0.1)].mean()*100:.0f}%(n{((pt>=lo)&(pt<lo+0.1)).sum()})"
    for lo in (0.6,0.7,0.8,0.9) if ((pt>=lo)&(pt<lo+0.1)).sum() > 20))
print("  prob spread: min %.2f max %.2f std %.3f" % (pt.min(), pt.max(), pt.std()))

final = CalibratedClassifierCV(HistGradientBoostingClassifier(
    max_depth=4, learning_rate=0.06, max_iter=250, l2_regularization=1.0),
    method="isotonic", cv=3).fit(X, y)
joblib.dump({"model": final, "feats": MF.FEATS, "trained_on": "first-cross>=10bps entry-timed"},
            "drift_model_band.joblib")
print("saved -> drift_model_band.joblib (entry-timed)")
