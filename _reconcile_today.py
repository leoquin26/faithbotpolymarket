#!/usr/bin/env python3
"""Reconcile the bot's daily P&L against the REAL on-chain account activity
(data-api.polymarket.com/activity — the same source as the CSV export).

Realized P&L for a day = sum(REDEEM usdc in) - sum(BUY usdc out).
Prints the real number and compares to the bot's daily_pnl.json.

Usage: python3 _reconcile_today.py [YYYY-MM-DD]
"""
import os, sys, json, datetime, time
try:
    import force_tor  # route Polymarket through Tor like the bot
except Exception:
    pass
import httpx

DAY = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
# day bounds in the server's local tz (the CSV/bot use local day)
d0 = datetime.datetime.strptime(DAY, "%Y-%m-%d")
start = int(time.mktime(d0.timetuple()))
end = start + 86400


def fetch_activity(addr):
    rows = []
    with httpx.Client(timeout=30) as cli:
        for page in range(40):
            r = cli.get("https://data-api.polymarket.com/activity",
                        params={"user": addr, "limit": 500, "offset": page * 500})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows += batch
            if min(int(t.get("timestamp", 0)) for t in batch) < start - 86400:
                break
    return rows


def main():
    addr = os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("FUNDER_ADDRESS")
    if not addr:
        try:
            import config
            addr = getattr(config, "FUNDER_ADDRESS", None)
        except Exception:
            pass
    if not addr:
        print("no funder address"); return
    print(f"wallet …{addr[-6:]}  day {DAY}")
    acts = [a for a in fetch_activity(addr) if start <= int(a.get("timestamp", 0)) < end]
    buys = redeems = 0.0
    nb = nr = 0
    detail = []
    for a in acts:
        typ = (a.get("type") or "").upper()
        side = (a.get("side") or "").upper()
        usdc = float(a.get("usdcSize") or a.get("usdc") or 0)
        if usdc <= 0:  # derive from size*price for trades
            usdc = float(a.get("size") or 0) * float(a.get("price") or 0)
        if typ == "TRADE" and side == "BUY":
            buys += usdc; nb += 1; detail.append(("BUY ", a.get("slug", ""), usdc, a.get("outcome", "")))
        elif typ in ("REDEEM", "CLAIM") or side == "REDEEM":
            redeems += usdc; nr += 1; detail.append(("REDM", a.get("slug", ""), usdc, ""))
    real = redeems - buys
    print(f"BUYS  : {nb} totaling ${buys:.2f}")
    print(f"REDEEMS: {nr} totaling ${redeems:.2f}")
    print(f"REAL realized P&L = ${real:+.2f}")
    try:
        dp = json.load(open("data/daily_pnl.json"))
        botnet = dp.get("wins", 0) - dp.get("losses", 0)
        print(f"\nbot daily_pnl.json: wins={dp.get('wins')} losses={dp.get('losses')} -> net ${botnet:+.2f}")
        print(f"DRIFT (bot - real) = ${botnet - real:+.2f}")
    except Exception as e:
        print("could not read daily_pnl.json:", e)
    print("\n--- activity detail ---")
    for tag, slug, usdc, oc in sorted(detail, key=lambda x: x[1]):
        print(f"  {tag} {slug:<40} ${usdc:6.2f} {oc}")


if __name__ == "__main__":
    main()
