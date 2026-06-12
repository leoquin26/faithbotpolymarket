#!/usr/bin/env python3
"""Fit a logistic direction model on recorded SIGNAL features + outcomes.

Reads data/trade_events.jsonl: joins SIGNAL events (with the jun12 feature
snapshot: dist_pct, roc60, roc300, sigma, t_remaining, book_up, regime) to
RESOLVED outcomes on (coin, window_start). Label: did UP win the window.

Trains pure-python logistic regression (no sklearn needed), evaluates
walk-forward (train on first 70%, test on last 30%) and compares Brier vs:
  - the live engine's recorded prob (converted to P(UP)),
  - a constant baseline.

This is a TOOL, not wired into the engine. Run it after 150+ resolved
signals with the full snapshot; if it beats the live engine's Brier
out-of-sample, wire its coefficients into a calibrator (shadow first).
"""
import json
import math
import sys
from collections import OrderedDict

EVENTS = "data/trade_events.jsonl"
MIN_N = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def load():
    signals, resolved = {}, {}
    for ln in open(EVENTS):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        k = (e.get("coin"), e.get("window_start"))
        if e.get("event") == "SIGNAL" and e.get("roc60") is not None:
            signals[k] = e  # keep last full-snapshot signal per window
        elif e.get("event") == "RESOLVED" and e.get("won") is not None:
            resolved[k] = e
    rows = []
    for k, s in signals.items():
        r = resolved.get(k)
        if not r:
            continue
        side_up = (s.get("side") == "UP")
        up_won = bool(r["won"]) == side_up
        rows.append(dict(
            y=1.0 if up_won else 0.0,
            dist=float(s.get("dist_pct") or 0) * 100,          # in %
            roc60=float(s.get("roc60") or 0) * 1e4,            # in bps
            roc300=float(s.get("roc300") or 0) * 1e4,          # in bps
            book=float(s.get("book_up") or 0.5) - 0.5,
            chop=1.0 if s.get("regime") == "CHOPPY" else 0.0,
            zlead=(float(s.get("dist_pct") or 0)
                   / max(1e-9, float(s.get("sigma") or 3e-4)
                         * math.sqrt(max(1.0, float(s.get("t_remaining") or 450))))),
            prob_up=(float(s.get("prob") or 0.5) if s.get("side") == "UP"
                     else 1.0 - float(s.get("prob") or 0.5)),
        ))
    return rows


FEATS = ["dist", "roc60", "roc300", "book", "chop", "zlead"]


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, x))))


def standardize(rows, feats):
    stats = {}
    for f in feats:
        v = [r[f] for r in rows]
        mu = sum(v) / len(v)
        sd = (sum((x - mu) ** 2 for x in v) / max(1, len(v) - 1)) ** 0.5 or 1.0
        stats[f] = (mu, sd)
    return stats


def train(rows, feats, stats, l2=1.0, iters=3000, lr=0.05):
    w = OrderedDict((f, 0.0) for f in feats)
    b = 0.0
    n = len(rows)
    for _ in range(iters):
        gw = dict((f, 0.0) for f in feats)
        gb = 0.0
        for r in rows:
            z = b + sum(w[f] * (r[f] - stats[f][0]) / stats[f][1] for f in feats)
            err = sigmoid(z) - r["y"]
            for f in feats:
                gw[f] += err * (r[f] - stats[f][0]) / stats[f][1]
            gb += err
        for f in feats:
            w[f] -= lr * (gw[f] / n + l2 * w[f] / n)
        b -= lr * gb / n
    return w, b


def brier(rows, pf):
    return sum((pf(r) - r["y"]) ** 2 for r in rows) / len(rows)


def main():
    rows = load()
    print(f"joined full-snapshot signals with outcomes: n={len(rows)}")
    if len(rows) < MIN_N:
        print(f"need >= {MIN_N} (pass a lower number as argv[1] to force) — "
              "let the bot accumulate more data first.")
        return
    cut = int(len(rows) * 0.7)
    tr, te = rows[:cut], rows[cut:]
    stats = standardize(tr, FEATS)
    w, b = train(tr, FEATS, stats)

    def model_p(r):
        return sigmoid(b + sum(w[f] * (r[f] - stats[f][0]) / stats[f][1] for f in FEATS))

    base = sum(r["y"] for r in tr) / len(tr)
    print("\ncoefficients (standardized):")
    for f, v in w.items():
        print(f"   {f:<8} {v:+.3f}")
    print(f"   bias     {b:+.3f}")
    print(f"\nTRAIN (n={len(tr)}): model={brier(tr, model_p):.4f} "
          f"live={brier(tr, lambda r: r['prob_up']):.4f} "
          f"const={brier(tr, lambda r: base):.4f}")
    print(f"TEST  (n={len(te)}): model={brier(te, model_p):.4f} "
          f"live={brier(te, lambda r: r['prob_up']):.4f} "
          f"const={brier(te, lambda r: base):.4f}")
    print("\nDeploy bar: model must beat BOTH live and const on TEST, "
          "with n_test >= 50, before wiring into the engine (shadow first).")


if __name__ == "__main__":
    main()
