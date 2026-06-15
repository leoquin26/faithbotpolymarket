"""Best-effort daily P&L reconciliation from the REAL on-chain account
(data-api.polymarket.com/activity — same source as the Polymarket CSV export).

Used on bot startup to ground daily_wins/daily_losses (and thus the daily
stop-loss) in real account numbers instead of the drift-prone internal counter
(which can mis-count across restarts or a phantom resolution). Fully guarded
with a short timeout so it never blocks/breaks startup.
"""
import os
import time
import datetime
from typing import Optional, Tuple

from loguru import logger


def _funder_addr() -> Optional[str]:
    a = os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("FUNDER_ADDRESS")
    if not a:
        try:
            import config
            a = getattr(config, "FUNDER_ADDRESS", None)
        except Exception:
            a = None
    return a


def realized_pnl_today(day: Optional[str] = None,
                       timeout: float = 12.0) -> Optional[Tuple[float, float, float]]:
    """Return (win_profit, loss_cost, net) realized for `day` (local), or None
    if the account couldn't be reached. Realized = settled windows only:
      win  -> a REDEEM exists for the window: profit = redeem_usdc - buy_usdc
      loss -> window closed (ws+900 < now) and no redeem: cost = buy_usdc
    Open/unsettled windows are excluded.
    """
    if os.getenv("DAILY_RECONCILE_ON", "on").lower() in ("off", "0", "false"):
        return None
    addr = _funder_addr()
    if not addr:
        return None
    day = day or datetime.date.today().isoformat()
    d0 = datetime.datetime.strptime(day, "%Y-%m-%d")
    start = int(time.mktime(d0.timetuple()))
    end = start + 86400
    now = int(time.time())
    try:
        import httpx
        rows = []
        with httpx.Client(timeout=timeout) as cli:
            for page in range(20):
                r = cli.get("https://data-api.polymarket.com/activity",
                            params={"user": addr, "limit": 500, "offset": page * 500})
                r.raise_for_status()
                batch = r.json()
                if not batch:
                    break
                rows += batch
                if min(int(t.get("timestamp", 0)) for t in batch) < start:
                    break
        # aggregate per window-slug
        buys = {}     # slug -> [usdc, outcome, ws]
        redeems = {}  # slug -> usdc
        for a in rows:
            ts = int(a.get("timestamp", 0))
            if not (start <= ts < end):
                continue
            slug = a.get("slug") or ""
            typ = (a.get("type") or "").upper()
            side = (a.get("side") or "").upper()
            usdc = float(a.get("usdcSize") or 0) or float(a.get("size") or 0) * float(a.get("price") or 0)
            if typ == "TRADE" and side == "BUY":
                ws = 0
                parts = slug.rsplit("-", 1)
                if parts and parts[-1].isdigit():
                    ws = int(parts[-1])
                b = buys.setdefault(slug, [0.0, a.get("outcome", ""), ws])
                b[0] += usdc
            elif typ in ("REDEEM", "CLAIM") or side == "REDEEM":
                redeems[slug] = redeems.get(slug, 0.0) + usdc
        win_profit = loss_cost = 0.0
        for slug, (buy_usdc, _oc, ws) in buys.items():
            if slug in redeems:
                win_profit += max(0.0, redeems[slug] - buy_usdc)
            elif ws and (ws + 900) < now:   # window closed, no redeem => lost
                loss_cost += buy_usdc
            # else: still open -> skip
        return (round(win_profit, 4), round(loss_cost, 4), round(win_profit - loss_cost, 4))
    except Exception as e:
        logger.debug(f"[DAILY RECONCILE] skipped: {e}")
        return None
