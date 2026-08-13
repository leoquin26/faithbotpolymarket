#!/usr/bin/env python3
"""BALANCE PROBE — writes the wallet's CHAIN TRUTH to balance.json every run
(cron */10min). The dashboard reads this file so EQUITY is anchored to what
the wallet actually holds, not to the ledger model (which accumulates
partial-fill dust; the model-vs-chain gap was ~$6 by 2026-08-13).
equity = USDC cash + cost basis of open positions. Read-only."""
import json, os, time

V3 = os.path.dirname(os.path.abspath(__file__))
os.chdir(V3)

import force_tor  # noqa
from order_manager import OrderManager
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

c = OrderManager().client
usdc = None
try:
    ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    usdc = int(ba.get("balance", "0")) / 1e6
except Exception as e:
    print("usdc probe failed:", e)

open_cost = 0.0
holdings = []
for st in ("micro_bot_state.json", "hour_bot_state.json"):
    try:
        s = json.load(open(os.path.join(V3, st)))
        opens = s.get("opens") or ({s.get("open", {}).get("coin"): s["open"]}
                                   if s.get("open") else {})
        for p in opens.values():
            open_cost += p["px"] * p["sh"]
            holdings.append(f"{p['coin']} {p['dir']} {p['px']*100:.0f}c x{p['sh']}")
    except Exception:
        pass

out = {"ts": time.time(), "usdc": round(usdc, 2) if usdc is not None else None,
       "open_cost": round(open_cost, 2), "holdings": holdings,
       "equity": round(usdc + open_cost, 2) if usdc is not None else None}
json.dump(out, open(os.path.join(V3, "balance.json"), "w"))
print(out)
