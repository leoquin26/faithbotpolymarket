#!/usr/bin/env python3
"""Score the bot's LIVE-logged research features with the trained model and check
whether the probability still separates winners from losers. Validates feature
parity before integrating into the live bot."""
import csv, math, numpy as np, joblib

M = joblib.load("drift_model.joblib"); model = M["model"]; FEATS = M["feats"]
COIN_IDX = {"BTC": 0, "ETH": 1, "SOL": 2, "XRP": 3}


def feats_from_research(r):
    drift_pct = float(r["drift_pct"]); sign = 1 if drift_pct >= 0 else -1
    abs_drift_bps = abs(drift_pct) * 100          # %->bps
    sigma_bps = float(r["sigma"]) * 1e4
    abs_z = abs_drift_bps / (sigma_bps * math.sqrt(5) + 1e-9)
    roc_aligned = float(r["roc60_bps"]) * sign     # roc60 ~ last-minute roc
    btc = float(r["btc_drift_pct"] or 0)
    agree_btc = int((drift_pct > 0) == (btc > 0))
    hour = int(r["ts"][11:13])
    return [abs_drift_bps, abs_z, sigma_bps, roc_aligned, agree_btc, hour,
            COIN_IDX.get(r["coin"], 1), btc * 100]


rows = [r for r in csv.DictReader(open("clean_bot_research.csv")) if r.get("winner")]
X = np.array([feats_from_research(r) for r in rows])
y = np.array([int(r["drift_correct"]) for r in rows])
p = model.predict_proba(X)[:, 1]
print(f"scored {len(rows)} live research windows | overall drift-correct {y.mean()*100:.0f}%")
print("\nmodel prob vs actual (does the live-fed model still rank?):")
order = np.argsort(p)
for half, idx in [("LOW half", order[:len(order)//2]), ("HIGH half", order[len(order)//2:])]:
    print(f"  {half}: model {p[idx].mean():.2f} -> actual {y[idx].mean()*100:.0f}% (n={len(idx)})")
for T in (0.6, 0.7, 0.8):
    sel = p >= T
    if sel.sum():
        print(f"  prob>={T}: actual {y[sel].mean()*100:.0f}% (n={sel.sum()})")
