#!/usr/bin/env python3
"""SHARED feature computation — imported by BOTH the training builder and the live
bot so features are identical (this is what fixes the parity break). Pure math, no
deps. Given the window-open strike + the 1-minute closes since open + BTC's drift,
returns the model feature vector in FEATS order."""
import math

FEATS = ["abs_drift_bps", "abs_z", "sigma_bps", "roc_aligned",
         "agree_btc", "hour", "coin_idx", "btc_drift_bps"]
COIN_IDX = {"BTC": 0, "ETH": 1, "SOL": 2, "XRP": 3}


def compute(strike, early_closes, btc_drift, hour, coin):
    """strike: window-open price. early_closes: list of 1-min CLOSES at +1..+k min
    since open (k>=2). btc_drift: BTC's same-window drift (fraction). Returns the
    8-feature list, or None if inputs insufficient."""
    if not strike or strike <= 0 or len(early_closes) < 2:
        return None
    drift = (early_closes[-1] - strike) / strike
    rets = [(early_closes[0] - strike) / strike]
    for i in range(1, len(early_closes)):
        a, b = early_closes[i - 1], early_closes[i]
        if a:
            rets.append((b - a) / a)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    sigma = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5
    roc_last = rets[-1]
    z = drift / (sigma * math.sqrt(len(rets)) + 1e-9)
    sgn = 1.0 if drift >= 0 else -1.0
    btc_drift = btc_drift if btc_drift is not None else 0.0
    return [abs(drift) * 1e4, abs(z), sigma * 1e4, roc_last * 1e4 * sgn,
            int((drift > 0) == (btc_drift > 0)), int(hour),
            COIN_IDX.get(coin, 1), btc_drift * 1e4]
