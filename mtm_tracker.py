#!/usr/bin/env python3
"""READ-ONLY mark-to-market tracker. Logs the live sell-value of CleanBot's open
positions every ~10s, with the running PEAK. Reveals whether positions go into
profit mid-window then reverse at settlement (the 'we only catch reversals' pattern)
— i.e. whether a take-profit exit would have booked the gain. No orders."""
import force_tor, time, json, sys
from pathlib import Path
import poly_resolution as pr
from order_manager import OrderManager
from loguru import logger

logger.remove()
logger.add(sys.stdout, format="{time:HH:mm:ss} | {message}")
logger.add("mtm.log", rotation="10 MB", format="{time:YYYY-MM-DD HH:mm:ss} | {message}")
om = OrderManager()
STATE = Path("clean_bot_state.json")
peak = {}
logger.info("=== MTM TRACKER start (sell-now value + peak for open positions) ===")


def token_for(coin, ws, direction):
    m = pr.fetch_market_by_slug(pr.market_slug(coin, ws, "15m"))
    if not m:
        return None
    toks = m.get("clobTokenIds")
    outs = m.get("outcomes")
    if isinstance(toks, str):
        toks = json.loads(toks)
    if isinstance(outs, str):
        outs = json.loads(outs)
    for o, t in zip(outs or [], toks or []):
        if str(o).lower() == direction.lower():
            return t
    return None


while True:
    try:
        d = json.loads(STATE.read_text())
        pos = {k: v for k, v in d.get("positions", {}).items() if v.get("status") == "filled"}
        now = time.time()
        for key, p in pos.items():
            coin, ws, dr, entry, sh = p["coin"], p["ws"], p["dir"], p["entry"], p["shares"]
            tok = token_for(coin, ws, dr)
            if not tok:
                continue
            b = om.get_clob_book(tok) or {}
            bid = b.get("bid")
            if bid is None:
                continue
            bid = float(bid)
            pk = max(peak.get(key, -9.0), bid)
            peak[key] = pk
            sell_pnl = (bid - entry) * sh
            peak_pnl = (pk - entry) * sh
            t_left = int((ws + 900) - now)
            logger.info(f"[MTM] {coin} {dr} entry={entry:.2f} now_bid={bid:.2f} "
                        f"peak={pk:.2f} | sell-now=${sell_pnl:+.2f} peak=${peak_pnl:+.2f} "
                        f"t_left={t_left}s")
        # forget resolved positions
        for k in list(peak):
            if k not in pos:
                del peak[k]
    except Exception as e:
        logger.warning(f"mtm err {e}")
    time.sleep(10)
