#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import poly_resolution as pr

slugs = [
    ("BTC 12:00-12:15", "BTC", 1780934400),
    ("SOL 12:15-12:30", "SOL", 1780935300),
    ("SOL 12:30-12:45", "SOL", 1780936200),
    ("SOL 12:45-1:00", "SOL", 1780937100),
    ("ETH 1:00-1:15", "ETH", 1780938000),
]
for label, coin, ws in slugs:
    slug = pr.market_slug(coin, ws, "15m")
    m = pr.fetch_market_by_slug(slug)
    if not m:
        print(f"{label}: {slug} NO MARKET")
        continue
    w = pr.resolved_winner(m)
    print(f"{label}: {slug} closed={m.get('closed')} gamma={w}")
