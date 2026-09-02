#!/usr/bin/env python3
"""LAYER-3 WALK-FORWARD on the 15m tape (clean_bot_research.csv): can ANY model
beat the market price after the taker fee? LightGBM, trained on days <= d,
tested on day d+1 (no look-ahead). Signal -> taker seat: buy side X when
P(X) - (ask + fee(ask)) >= margin; hold to settle. Reports test EV/$ by
margin and by t_left bucket. Run: TAPE=<dir> python wf_model.py"""
import os, sys, numpy as np, pandas as pd, lightgbm as lgb
TAPE = os.environ.get("TAPE", "/home/ubuntu/v3-bot")
df = pd.read_csv(os.path.join(TAPE, "clean_bot_research.csv"), low_memory=False)
df = df[df["winner"].isin(["UP", "DOWN"])].copy()
df["y"] = (df["winner"] == "UP").astype(int)
df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
df = df.dropna(subset=["ts"]).sort_values("ts")
df["day"] = df["ts"].dt.floor("D")
num = ["drift_pct", "roc60_bps", "roc300_bps", "sigma", "fav_ask", "up_ask", "down_ask",
       "btc_drift_pct", "sol_drift_pct", "t_left", "er", "flow60", "book_imb", "hmm"]
for c in num: df[c] = pd.to_numeric(df[c], errors="coerce")
df["coin_id"] = df["coin"].astype("category").cat.codes
df["phase_id"] = df["phase"].astype("category").cat.codes
feats = num + ["coin_id", "phase_id"]
df = df.dropna(subset=["up_ask", "down_ask", "t_left"])
for c in ("up_ask", "down_ask", "fav_ask"):
    df[c] = df[c] / 100.0          # the 15m CSV stores prices in cents
df = df[(df["up_ask"] > 0.02) & (df["up_ask"] < 0.98) & (df["down_ask"] > 0.02) & (df["down_ask"] < 0.98)]
days = sorted(df["day"].unique())
print(f"rows {len(df)}  days {len(days)}  span {days[0].date()} -> {days[-1].date()}")
FEE = 0.07
def fee(p): return FEE * p * (1 - p)
preds = []
for i in range(7, len(days)):           # need >= 7 days of history
    tr = df[df["day"] < days[i]]; te = df[df["day"] == days[i]]
    if len(te) == 0: continue
    m = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15,
                           min_child_samples=50, subsample=0.8, colsample_bytree=0.8, verbose=-1)
    m.fit(tr[feats], tr["y"])
    p = m.predict_proba(te[feats])[:, 1]
    out = te[["day", "coin", "t_left", "up_ask", "down_ask", "y"]].copy(); out["p_up"] = p
    preds.append(out)
P = pd.concat(preds)
print(f"test rows {len(P)} over {P['day'].nunique()} days")
# calibration / discrimination
from sklearn.metrics import roc_auc_score, brier_score_loss
mkt = P["up_ask"].clip(0.01, 0.99)
print(f"AUC model {roc_auc_score(P['y'], P['p_up']):.3f} | AUC market(up_ask) {roc_auc_score(P['y'], mkt):.3f} | "
      f"Brier model {brier_score_loss(P['y'], P['p_up']):.4f} market {brier_score_loss(P['y'], mkt):.4f}")
# taker seat at margins
P["tb"] = pd.cut(P["t_left"], [0, 120, 300, 600, 900], labels=["0-2m", "2-5m", "5-10m", "10-15m"])
for margin in (0.04, 0.08, 0.12):
    rows = []
    for _, r in P.iterrows():
        eu = r["p_up"] - (r["up_ask"] + fee(r["up_ask"]))
        ed = (1 - r["p_up"]) - (r["down_ask"] + fee(r["down_ask"]))
        if max(eu, ed) < margin: continue
        if eu >= ed: cost = r["up_ask"] + fee(r["up_ask"]); pnl = (1 - cost) if r["y"] == 1 else -cost
        else: cost = r["down_ask"] + fee(r["down_ask"]); pnl = (1 - cost) if r["y"] == 0 else -cost
        rows.append((r["tb"], cost, pnl))
    if not rows: print(f"margin {margin:.2f}: no trades"); continue
    R = pd.DataFrame(rows, columns=["tb", "cost", "pnl"])
    print(f"margin >= {margin:.2f}: n={len(R)} EV/$ {R['pnl'].sum()/R['cost'].sum():+.4f} | "
          + " ".join(f"{k}:{g['pnl'].sum()/g['cost'].sum():+.3f}(n={len(g)})" for k, g in R.groupby("tb", observed=True)))
