#!/usr/bin/env python3
"""Walk-forward backtest of a calibrated model for the early-drift edge.
Target: drift_correct (does betting sign(drift) win the 15m close?).
Honest OOS: train on the past, test on the future. CPU-only (no GPU needed)."""
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import accuracy_score, roc_auc_score, brier_score_loss

df = pd.read_csv("dataset.csv").sort_values("ws").reset_index(drop=True)
print(f"rows={len(df)}  base drift-correct={df.drift_correct.mean()*100:.1f}%")

# --- feature engineering (all known at minute 5, no look-ahead) ---
sign = np.sign(df.drift_bps).replace(0, 1)
df["roc_aligned"] = df.roc_last_bps * sign          # momentum confirming the drift
df["coin_idx"] = df.coin.map({"BTC": 0, "ETH": 1, "SOL": 2, "XRP": 3})
FEATS = ["abs_drift_bps", "abs_z", "sigma_bps", "roc_aligned",
         "agree_btc", "hour", "coin_idx", "btc_drift_bps"]
X = df[FEATS].values; y = df.drift_correct.values

# --- walk-forward: expanding train, rolling OOS test (time-ordered) ---
N = len(df); folds = 6; start = N // 3
bounds = np.linspace(start, N, folds + 1).astype(int)
oos_p = np.full(N, np.nan); model_name = "HistGradientBoosting"
for i in range(folds):
    tr_end, te_end = bounds[i], bounds[i + 1]
    Xtr, ytr = X[:tr_end], y[:tr_end]
    clf = CalibratedClassifierCV(
        HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06,
                                       max_iter=300, l2_regularization=1.0),
        method="isotonic", cv=3)
    clf.fit(Xtr, ytr)
    oos_p[te_end - (te_end - tr_end):te_end] = clf.predict_proba(X[tr_end:te_end])[:, 1]

m = ~np.isnan(oos_p); yt = y[m]; pt = oos_p[m]
base = yt.mean()
print(f"\n=== WALK-FORWARD OOS ({m.sum()} test windows, {model_name}, isotonic) ===")
print(f"  base rate (always bet drift): {base*100:.1f}%")
print(f"  model accuracy @0.5:          {accuracy_score(yt, pt>0.5)*100:.1f}%")
print(f"  AUC: {roc_auc_score(yt, pt):.3f}  |  Brier: {brier_score_loss(yt, pt):.4f} "
      f"(base Brier {brier_score_loss(yt, np.full_like(pt, base)):.4f})")

# --- the money question: does model confidence pick the winners? ---
print("\n=== accuracy by model-confidence decile (can it tell WHEN to trade?) ===")
order = np.argsort(pt); dec = np.array_split(order, 10)
for i, idx in enumerate(dec):
    print(f"  decile {i+1} (prob {pt[idx].min():.2f}-{pt[idx].max():.2f}): "
          f"actual {yt[idx].mean()*100:4.1f}%  (n={len(idx)})")

# --- tradeable subsets: only bet when model_prob >= thresh ---
print("\n=== tradeable subset (bet only when model says >= T) ===")
for T in (0.70, 0.75, 0.80, 0.85):
    sel = pt >= T
    if sel.sum():
        print(f"  prob>={T}:  WR {yt[sel].mean()*100:4.1f}%  | trades {sel.sum()} "
              f"({sel.mean()*100:.0f}% of windows)")

# --- calibration check ---
print("\n=== calibration (predicted vs actual) ===")
for lo in (0.5, 0.6, 0.7, 0.8, 0.9):
    sel = (pt >= lo) & (pt < lo + 0.1)
    if sel.sum() > 30:
        print(f"  predicted {lo:.1f}-{lo+0.1:.1f}: actual {yt[sel].mean()*100:4.1f}% (n={sel.sum()})")

# --- logistic baseline + feature importance (permutation on last fold) ---
lr = LogisticRegression(max_iter=1000).fit(
    (X[:bounds[-2]] - X[:bounds[-2]].mean(0)) / (X[:bounds[-2]].std(0) + 1e-9), y[:bounds[-2]])
print("\n=== logistic coefficients (standardized; sign = direction of effect) ===")
for f, c in sorted(zip(FEATS, lr.coef_[0]), key=lambda x: -abs(x[1])):
    print(f"  {f:<16} {c:+.3f}")
