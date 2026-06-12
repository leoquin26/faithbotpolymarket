#!/usr/bin/env python3
"""Append new resolved Polymarket 15m fills to data/poly_reconciled.csv.

Pulls the wallet's TRADE activity from the public data-api, aggregates BUY
fills per (slug, outcome), resolves winners via the Gamma API (same path the
bot uses), and appends rows in the existing CSV format:
    day,coin,dir,shares,avg,cost,winner,result,pnl,ws
Idempotent: rows already present (coin, ws, dir) are skipped; unresolved
windows are picked up on the next run. Intended for cron (every few hours).
"""
import csv, os, re, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

import poly_resolution  # noqa: E402  (reuses the bot's winner resolution)

CSV_PATH = "data/poly_reconciled.csv"
NY = ZoneInfo("America/New_York")
SLUG_RE = re.compile(r"^(btc|eth|sol|xrp)-updown-15m-(\d{10})$")
MAX_PAGES = int(os.getenv("RECONCILE_MAX_PAGES", "6"))   # 6 x 500 trades
LOOKBACK_SEC = int(os.getenv("RECONCILE_LOOKBACK_SEC", str(14 * 86400)))


def existing_keys():
    keys = set()
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH) as f:
            for r in csv.DictReader(f):
                try:
                    keys.add((r["coin"], int(r["ws"]), r["dir"]))
                except (KeyError, ValueError):
                    pass
    return keys


def fetch_trades(addr):
    rows, cutoff = [], int(time.time()) - LOOKBACK_SEC
    with httpx.Client(timeout=20) as cli:
        for page in range(MAX_PAGES):
            r = cli.get("https://data-api.polymarket.com/activity",
                        params={"user": addr, "type": "TRADE",
                                "limit": 500, "offset": page * 500})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows += batch
            if min(int(t.get("timestamp", 0)) for t in batch) < cutoff:
                break
    return [t for t in rows if int(t.get("timestamp", 0)) >= cutoff]


def main():
    addr = os.getenv("POLYMARKET_FUNDER_ADDRESS")
    if not addr:
        print("no POLYMARKET_FUNDER_ADDRESS in env"); return 1

    have = existing_keys()
    agg = {}  # (coin, ws, dir) -> [shares, cost]
    for t in fetch_trades(addr):
        if t.get("side") != "BUY":
            continue
        m = SLUG_RE.match(t.get("slug") or "")
        if not m:
            continue
        coin, ws = m.group(1).upper(), int(m.group(2))
        direction = (t.get("outcome") or "").upper()
        if direction not in ("UP", "DOWN"):
            continue
        key = (coin, ws, direction)
        if key in have:
            continue
        size, price = float(t.get("size") or 0), float(t.get("price") or 0)
        a = agg.setdefault(key, [0.0, 0.0])
        a[0] += size
        a[1] += size * price

    now = int(time.time())
    new_rows = []
    for (coin, ws, direction), (shares, cost) in sorted(agg.items(), key=lambda kv: kv[0][1]):
        if shares <= 0 or now < ws + 900 + 600:
            continue  # window not comfortably over yet
        slug = f"{coin.lower()}-updown-15m-{ws}"
        market = poly_resolution.fetch_market_by_slug(slug)
        winner = poly_resolution.resolved_winner(market) if market else None
        if winner not in ("UP", "DOWN"):
            continue  # not resolved yet; retry next run
        result = "WIN" if direction == winner else "LOSS"
        pnl = (shares - cost) if result == "WIN" else -cost
        new_rows.append(dict(
            day=datetime.fromtimestamp(ws, NY).strftime("%Y-%m-%d"),
            coin=coin, dir=direction,
            shares=round(shares, 2), avg=round(cost / shares, 2),
            cost=round(cost, 2), winner=winner, result=result,
            pnl=round(pnl, 2), ws=ws,
        ))

    if not new_rows:
        print(f"{datetime.now().isoformat(timespec='seconds')} nothing new"); return 0

    write_header = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["day", "coin", "dir", "shares", "avg",
                                          "cost", "winner", "result", "pnl", "ws"])
        if write_header:
            w.writeheader()
        for row in new_rows:
            w.writerow(row)
    print(f"{datetime.now().isoformat(timespec='seconds')} appended {len(new_rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
