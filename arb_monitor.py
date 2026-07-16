#!/usr/bin/env python3
"""READ-ONLY fee-aware Polymarket arbitrage monitor (15m crypto UP/DOWN).
Polymarket crypto fee: 7% TAKER only (fee/share = 0.07*p*(1-p)); makers pay 0 + rebate.
Flags NET-positive arbs only (gross edge minus taker fees). Also logs the maker-side
gross (fee-free if you provide liquidity). No orders. 3s scan."""
import force_tor, time, sys
from market_data import get_market_info
from order_manager import OrderManager
from loguru import logger

logger.remove()
logger.add(sys.stdout, format="{time:HH:mm:ss} | {message}")
logger.add("arb_monitor.log", rotation="20 MB", format="{time:YYYY-MM-DD HH:mm:ss} | {message}")

COINS = ["BTC", "ETH", "SOL"]
FEE = lambda p: 0.07 * p * (1 - p)        # taker fee per share
MIN_NET = 0.003                            # flag >= 0.3c net edge after fees
om = OrderManager()
best = {c: {"taker_net": -9.0, "maker_gross": -9.0} for c in COINS}
taker_arbs = maker_arbs = 0
n = 0
logger.info(f"=== FEE-AWARE ARB MONITOR | {','.join(COINS)} | 7% taker fee | min_net={MIN_NET} ===")

while True:
    n += 1
    cur = []
    for c in COINS:
        try:
            info = get_market_info(c)
            if not info or not info.up_token_id or not info.down_token_id:
                continue
            ub = om.get_clob_book(info.up_token_id) or {}
            db = om.get_clob_book(info.down_token_id) or {}
            ua, ubid = ub.get("ask"), ub.get("bid")
            da, dbid = db.get("ask"), db.get("bid")
            # TAKER buy-both: pay asks + fees, redeem $1
            if ua and da:
                ua, da = float(ua), float(da)
                net = 1.0 - ua - da - FEE(ua) - FEE(da)
                best[c]["taker_net"] = max(best[c]["taker_net"], net)
                if net > MIN_NET:
                    taker_arbs += 1
                    # fillable size: shares available at (or better than) the flagged asks on BOTH legs
                    pairs = 0.0
                    try:
                        ud = om.get_full_depth(info.up_token_id)
                        dd = om.get_full_depth(info.down_token_id)
                        u_sh = sum(s for p, s in ud.get("asks", []) if p <= ua + 1e-9)
                        d_sh = sum(s for p, s in dd.get("asks", []) if p <= da + 1e-9)
                        pairs = min(u_sh, d_sh)
                    except Exception:
                        pass
                    logger.info(f"[TAKER-BUY] {c} UP@{ua:.2f}+DOWN@{da:.2f} gross={1-ua-da:+.3f} "
                                f"fee={FEE(ua)+FEE(da):.3f} NET=${net:+.3f}/pair "
                                f"FILLABLE={pairs:.0f}pairs (${net*pairs:+.2f} total)")
            # TAKER sell-both (mint set, sell to bids): collect bids - fees, owe $1
            if ubid and dbid:
                ubid, dbid = float(ubid), float(dbid)
                net = ubid + dbid - 1.0 - FEE(ubid) - FEE(dbid)
                best[c]["taker_net"] = max(best[c]["taker_net"], net)
                if net > MIN_NET:
                    taker_arbs += 1
                    logger.info(f"[TAKER-SELL] {c} UP@{ubid:.2f}+DOWN@{dbid:.2f} gross={ubid+dbid-1:+.3f} "
                                f"fee={FEE(ubid)+FEE(dbid):.3f} NET=${net:+.3f}/pair")
                # MAKER-side: if you PROVIDE the liquidity (fee-free) the gross spread is yours
                mg = ubid + dbid - 1.0
                best[c]["maker_gross"] = max(best[c]["maker_gross"], mg)
                cur.append(f"{c}:sell{ubid+dbid:.3f}")
        except Exception:
            pass
    if n % 20 == 1:
        b = " ".join(f"{c}[takerNet={best[c]['taker_net']:+.3f},makerGross={best[c]['maker_gross']:+.3f}]"
                     for c in COINS if best[c]['taker_net'] > -9)
        logger.info(f"scan#{n} | {' '.join(cur)} | BEST: {b} | taker_arbs={taker_arbs}")
    time.sleep(3)
