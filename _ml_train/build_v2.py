#!/usr/bin/env python3
"""Rebuild the dataset using the SHARED model_features.compute (parity-guaranteed),
restrict to the bot's tradeable band (drift>=10bps), retrain a calibrated model,
walk-forward validate, and save the deployable band model. Reuses cached klines."""
import json, os, time, math, csv, datetime as dt, numpy as np, pandas as pd, joblib
import model_features as MF
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score, brier_score_loss

COINS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
CACHE = "klines_cache"


def load_series():
    OP, CL = {}, {}
    for c, sym in COINS.items():
        op, cl = {}, {}
        for fn in os.listdir(CACHE):
            if fn.startswith(sym + "_"):
                for k in json.load(open(os.path.join(CACHE, fn))):
                    t = k[0] // 1000; op[t] = float(k[1]); cl[t] = float(k[4])
        OP[c], CL[c] = op, cl
        print(f"  {c}: {len(cl)} bars")
    return OP, CL


print("loading cached klines ...")
OP, CL = load_series()
AGE_MIN = 5   # decide 5 min into the window (the bot will mirror this)


def early_closes(coin, t0, k):
    out = []
    for m in range(1, k + 1):
        v = CL[coin].get(t0 + m * 60)
        if v is None:
            return None
        out.append(v)
    return out


def btc_drift(t0, k):
    s = OP["BTC"].get(t0); c = CL["BTC"].get(t0 + k * 60)
    return (c - s) / s if (s and c) else None


rows = []
allt = sorted(CL["BTC"])
t0 = (allt[0] // 900 + 1) * 900
last = allt[-1] - 900
while t0 <= last:
    bd = btc_drift(t0, AGE_MIN)
    for coin in COINS:
        strike = OP[coin].get(t0)
        ec = early_closes(coin, t0, AGE_MIN)
        close = CL[coin].get(t0 + 840)
        if not strike or ec is None or close is None:
            continue
        feats = MF.compute(strike, ec, bd, dt.datetime.utcfromtimestamp(t0).hour, coin)
        if feats is None:
            continue
        winner = 1 if close > strike else 0
        bet_up = 1 if (ec[-1] - strike) > 0 else 0
        rows.append(feats + [int(bet_up == winner), t0])
    t0 += 900

cols = MF.FEATS + ["drift_correct", "ws"]
df = pd.DataFrame(rows, columns=cols).sort_values("ws").reset_index(drop=True)
df.to_csv("dataset_v2.csv", index=False)
band = df[df.abs_drift_bps >= 10].reset_index(drop=True)
print(f"\ndataset_v2: {len(df)} windows | tradeable band (>=10bps): {len(band)} "
      f"| band base {band.drift_correct.mean()*100:.1f}%")

# walk-forward on the band
X = band[MF.FEATS].values; y = band.drift_correct.values
N = len(band); oos = np.full(N, np.nan); bounds = np.linspace(N//2, N, 5).astype(int)
for i in range(len(bounds)-1):
    tr, te = bounds[i], bounds[i+1]
    clf = CalibratedClassifierCV(HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=250, l2_regularization=1.0),
        method="isotonic", cv=3).fit(X[:tr], y[:tr])
    oos[tr:te] = clf.predict_proba(X[tr:te])[:, 1]
m = ~np.isnan(oos); yt, pt = y[m], oos[m]
order = np.argsort(pt); q = len(order)//5
print(f"WALK-FWD (band, n={m.sum()}): AUC {roc_auc_score(yt,pt):.3f} | "
      f"top20% {yt[order[-q:]].mean()*100:.1f}% vs bot20% {yt[order[:q]].mean()*100:.1f}%")
print("  calibration:", " ".join(
    f"{lo:.1f}->{yt[(pt>=lo)&(pt<lo+0.1)].mean()*100:.0f}%" for lo in (0.6,0.7,0.8,0.9)
    if ((pt>=lo)&(pt<lo+0.1)).sum() > 30))
for T in (0.75, 0.80, 0.85):
    sel = pt >= T
    if sel.sum(): print(f"  prob>={T}: WR {yt[sel].mean()*100:.1f}% ({sel.sum()} = {sel.mean()*100:.0f}% of band)")

final = CalibratedClassifierCV(HistGradientBoostingClassifier(
    max_depth=4, learning_rate=0.06, max_iter=250, l2_regularization=1.0),
    method="isotonic", cv=3).fit(X, y)
joblib.dump({"model": final, "feats": MF.FEATS, "trained_on": "drift>=10bps", "age_min": AGE_MIN},
            "drift_model_band.joblib")
print("\nsaved -> drift_model_band.joblib")
