"""
Feed-consistency fixes (Jun 10 2026):

1. _multi_price() in run_bot.py: add on-chain Chainlink fallback so spot stays
   Chainlink-family when RTDS is down (was RTDS -> Binance -> Bybit).

2. Momentum on Chainlink: in run_bot.py, build the tick series from Chainlink
   (RTDS or on-chain) when available, so ROC/trend measure the SAME feed the
   level (dist_pct) uses. Falls back to Binance ticks only if no Chainlink.
"""

rb = "run_bot.py"
s = open(rb).read()

# --- Fix 1: on-chain fallback inside _multi_price ---
old_mp = (
    "    if _CHAINLINK_OK and _chainlink_ws is not None:\n"
    "        try:\n"
    "            cl = _chainlink_ws.get_price(coin)\n"
    "            if cl and cl > 0:\n"
    "                return cl\n"
    "        except Exception:\n"
    "            pass\n"
    "    p = binance_ws.get_price(coin)\n"
)
new_mp = (
    "    if _CHAINLINK_OK and _chainlink_ws is not None:\n"
    "        try:\n"
    "            cl = _chainlink_ws.get_price(coin)\n"
    "            if cl and cl > 0:\n"
    "                return cl\n"
    "        except Exception:\n"
    "            pass\n"
    "    # On-chain Chainlink fallback (same oracle family as settlement) before\n"
    "    # dropping to Binance/Bybit, so spot stays Chainlink when RTDS is down.\n"
    "    if _chainlink_onchain is not None:\n"
    "        try:\n"
    "            co = _chainlink_onchain.get_price(coin)\n"
    "            if co and co > 0:\n"
    "                return co\n"
    "        except Exception:\n"
    "            pass\n"
    "    p = binance_ws.get_price(coin)\n"
)
if "On-chain Chainlink fallback (same oracle family as settlement) before" in s:
    print("_multi_price already has on-chain fallback")
elif old_mp in s:
    s = s.replace(old_mp, new_mp)
    print("_multi_price on-chain fallback added")
else:
    print("WARN: _multi_price anchor not found")

# --- Define _chainlink_tick_history helper right after _multi_price returns ---
helper_anchor = "    return None\n\nfrom market_data import get_market_info, MarketInfo\n"
helper_def = (
    "    return None\n\n\n"
    "def _chainlink_tick_history(coin: str, seconds: int = 300):\n"
    "    \"\"\"(ts, price) ticks on the Chainlink feed for ROC. On-chain history\n"
    "    (RTDS keeps only latest), so ROC matches the level's feed.\"\"\"\n"
    "    if _chainlink_onchain is not None:\n"
    "        try:\n"
    "            return _chainlink_onchain.tick_history(coin, seconds)\n"
    "        except Exception:\n"
    "            return None\n"
    "    return None\n\n"
    "from market_data import get_market_info, MarketInfo\n"
)
if "_chainlink_tick_history(coin: str" in s:
    print("helper already defined")
elif helper_anchor in s:
    s = s.replace(helper_anchor, helper_def)
    print("_chainlink_tick_history helper defined")
else:
    print("WARN: helper anchor not found")

# --- Fix 2: build Chainlink tick series for momentum ---
old_ticks = "                ticks = binance_ws.get_tick_history(coin, 300)\n"
new_ticks = (
    "                ticks = binance_ws.get_tick_history(coin, 300)\n"
    "                # Momentum must measure the SAME feed as the level (dist_pct).\n"
    "                # Use a Chainlink tick history when available; the level uses\n"
    "                # Chainlink, so ROC/trend should too. Falls back to Binance.\n"
    "                try:\n"
    "                    _cl_ticks = _chainlink_tick_history(coin, 300)\n"
    "                    if _cl_ticks and len(_cl_ticks) >= 5:\n"
    "                        ticks = _cl_ticks\n"
    "                except Exception:\n"
    "                    pass\n"
)
if "_chainlink_tick_history(coin, 300)" in s:
    print("chainlink tick wiring already present")
elif old_ticks in s:
    s = s.replace(old_ticks, new_ticks)
    print("chainlink tick wiring added")
else:
    print("WARN: ticks anchor not found")

open(rb, "w").write(s)
print("run_bot.py written")
