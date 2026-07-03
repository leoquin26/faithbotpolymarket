#!/usr/bin/env python3
"""Hidden Markov regime detector (SHADOW — research logging only, no trading impact).

3-state GaussianHMM (TREND / CHOP / PANIC) on 15m log-returns + volatility proxy,
fitted on a rolling ~9-day window per coin, refit every 6h, cached. Output = the
posterior probability of each regime at the latest bar.

Purpose: log the live regime posterior per window into clean_bot_research.csv so the
verifier can test — out of sample — whether HMM state predicts drift follow-through
better than (or additively to) ER + the signal-health gate. Deploys into trading ONLY
if it passes the gate (n>=80, z>=1.64, EV>0). See CHANGELOG v1.34.0.
"""
import time
import numpy as np

try:
    import httpx
except Exception:
    httpx = None

_SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
_FIT_TTL = 6 * 3600        # refit every 6h (rolling window; stale fits go bad — see post's own warning)
_POST_TTL = 120            # posterior re-read cadence
_BARS = 880                # ~9.2 days of 15m bars

_cache = {}                # coin -> {"ts_fit","model","labels","ts_post","post"}


def _klines(coin, limit=_BARS):
    if not httpx:
        return None
    for base in ("https://api.binance.us/api/v3/klines",
                 "https://data-api.binance.vision/api/v3/klines"):
        try:
            r = httpx.get(base, params={"symbol": _SYMS.get(coin, ""), "interval": "15m",
                                        "limit": limit}, timeout=8, trust_env=False)
            if r.status_code == 200:
                closes = np.array([float(k[4]) for k in r.json()], dtype=float)
                if len(closes) > 100:
                    return closes
        except Exception:
            continue
    return None


def _features(closes):
    lr = np.diff(np.log(closes))
    vol = np.abs(lr)                        # per-bar vol proxy
    return np.column_stack([lr, vol])


def _label_states(model):
    """Map raw state indices -> semantic labels. PANIC = highest vol; of the rest,
    TREND = strongest drift per unit vol; CHOP = the remainder."""
    means = model.means_                    # (n_states, 2): [mean_ret, mean_vol]
    vols = means[:, 1]
    panic = int(np.argmax(vols))
    rest = [i for i in range(len(vols)) if i != panic]
    drift_ratio = [abs(means[i, 0]) / (vols[i] + 1e-12) for i in rest]
    trend = rest[int(np.argmax(drift_ratio))]
    chop = [i for i in rest if i != trend][0]
    return {trend: "T", chop: "C", panic: "P"}


def get_regime(coin):
    """Returns {"T": p, "C": p, "P": p, "label": "T"} for the LATEST bar, or None.
    Cached: fit every 6h, posterior every 2min. Never raises."""
    try:
        from hmmlearn.hmm import GaussianHMM
        now = time.time()
        c = _cache.get(coin, {})
        if c.get("post") is not None and now - c.get("ts_post", 0) < _POST_TTL:
            return c["post"]
        closes = _klines(coin)
        if closes is None:
            return None
        X = _features(closes)
        model, labels = c.get("model"), c.get("labels")
        if model is None or now - c.get("ts_fit", 0) > _FIT_TTL:
            model = GaussianHMM(n_components=3, covariance_type="diag",
                                n_iter=60, random_state=7)
            model.fit(X)
            labels = _label_states(model)
            c["model"], c["labels"], c["ts_fit"] = model, labels, now
        post = model.predict_proba(X)[-1]   # posterior at the latest bar
        out = {labels[i]: round(float(post[i]), 3) for i in range(3)}
        out["label"] = max(("T", "C", "P"), key=lambda k: out[k])
        c["post"], c["ts_post"] = out, now
        _cache[coin] = c
        return out
    except Exception:
        return None


def fmt(coin):
    """Compact string for the research CSV, e.g. 'T0.62/C0.31/P0.07' ('' if unavailable)."""
    r = get_regime(coin)
    if not r:
        return ""
    return f"T{r['T']:.2f}/C{r['C']:.2f}/P{r['P']:.2f}"


if __name__ == "__main__":
    for c in ("BTC", "ETH", "SOL"):
        t0 = time.time()
        print(c, get_regime(c), f"({time.time()-t0:.1f}s)")
