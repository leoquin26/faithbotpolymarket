#!/usr/bin/env python3
"""Window-Reset Sniper — DRY RUN / shadow detector.

Revives the original $360 latency-arbitrage strategy (friend_package
window_sniper) on faithbot's WORKING infra (Tor routing + tested helpers),
read-only, no credentials, no orders. Runs alongside faithbot.

Edge: in the first ~90s of a 15m window, if the REAL price (Binance) is already
clearly past the strike (>=0.2%) — so that side is very likely to win — but
Polymarket still prices that winning side <=40c (book hasn't caught up), buy it
for a 30%+ edge. This tells us, with zero risk, whether the delay STILL EXISTS
in June 2026 and how often.

Logs every [SNIPE] (would-trade), [NEAR] (right setup but book already repriced),
and tracks each flagged window's real outcome to report hypothetical WR/PnL.
"""
import force_tor  # selective: Polymarket->Tor, Binance->direct
import time, json, os
import httpx
from loguru import logger
import market_data
import poly_resolution as pr

logger.remove()
logger.add(lambda m: print(m, end=""), level="INFO")
logger.add("logs/sniper_dryrun.log", level="DEBUG", rotation="20 MB")

COINS = ["BTC", "ETH", "SOL", "XRP"]
SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
WINDOW = 900
SNIPE_WINDOW = int(os.getenv("SNIPE_WINDOW", "90"))     # first N seconds
SHARP_DIST = float(os.getenv("SNIPE_SHARP_DIST", "0.002"))  # 0.2% past strike
MAX_ENTRY = float(os.getenv("SNIPE_MAX_ENTRY", "0.40"))     # buy <=40c
BET = 2.0

_bin = httpx.Client(timeout=5)


def binance_price(coin):
    # US server: binance.us first (binance.com geo-blocks US IPs)
    for host in ("https://api.binance.us", "https://api.binance.com"):
        try:
            j = _bin.get(host + "/api/v3/ticker/price", params={"symbol": SYM[coin]}).json()
            if "price" in j:
                return float(j["price"])
        except Exception:
            continue
    return None


def best_ask(token_id):
    """Lowest sell price on the token's book = what we'd pay to buy (via Tor)."""
    try:
        r = httpx.get("https://clob.polymarket.com/book", params={"token_id": token_id}, timeout=8)
        asks = r.json().get("asks") or []
        prices = [float(a["price"]) for a in asks if float(a.get("size", 0)) > 0]
        return min(prices) if prices else None
    except Exception:
        return None


flagged = {}   # (coin,ws) -> dict(side, ask, dist) awaiting outcome
seen = set()
wins = losses = 0
pnl = 0.0
near = snipes = 0
t_start = time.time()


def settle():
    global wins, losses, pnl
    now = time.time()
    for key in list(flagged):
        coin, ws = key
        if ws + WINDOW + 30 > now:
            continue
        opp = flagged.pop(key)
        try:
            res = pr.resolve_position(coin, opp["side"], ws, "15m")
            if not res or not res.get("winner"):
                continue
            won = res["winner"] == opp["side"]
            shares = BET / opp["ask"]
            p = shares * (1 - opp["ask"]) if won else -BET
            pnl += p
            if won:
                wins += 1
            else:
                losses += 1
            logger.info(f"[SETTLE] {coin} {opp['side']} ws={ws} ask={opp['ask']*100:.0f}c "
                        f"-> {'WIN' if won else 'LOSS'} pnl={p:+.2f} | running {wins}W/{losses}L net=${pnl:+.2f}")
        except Exception as e:
            logger.debug(f"[SETTLE] {coin} err {e}")


logger.info(f"=== SNIPER DRY RUN === first {SNIPE_WINDOW}s, dist>={SHARP_DIST*100:.2f}%, "
            f"buy winning side <= {MAX_ENTRY*100:.0f}c. Detecting if the delay still exists.")
while True:
    now = int(time.time())
    ws = (now // WINDOW) * WINDOW
    age = now - ws
    if age <= SNIPE_WINDOW:
        for coin in COINS:
            key = (coin, ws)
            if key in seen:
                continue
            strike = market_data.get_threshold_from_binance(coin, ws, "15m")
            px = binance_price(coin)
            if not strike or not px:
                continue
            dist = (px - strike) / strike
            if abs(dist) < SHARP_DIST:
                continue
            side = "UP" if dist > 0 else "DOWN"
            m = pr.fetch_market_by_slug(f"{coin.lower()}-updown-15m-{ws}")
            if not m:
                continue
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
            except Exception:
                toks = []
            if len(toks) < 2:
                continue
            ask = best_ask(toks[0] if side == "UP" else toks[1])
            if ask is None:
                continue
            seen.add(key)
            if ask <= MAX_ENTRY:
                snipes += 1
                flagged[key] = {"side": side, "ask": ask, "dist": dist}
                logger.info(f"[SNIPE] {coin} {side} | dist={dist*100:+.3f}% strike={strike:.2f} "
                            f"px={px:.2f} | poly_ask={ask*100:.0f}c <= {MAX_ENTRY*100:.0f}c | "
                            f"age={age}s edge~{(0.85-ask)*100:.0f}%  *** OPPORTUNITY ***")
            else:
                near += 1
                logger.info(f"[NEAR] {coin} {side} | dist={dist*100:+.3f}% | poly_ask={ask*100:.0f}c "
                            f"> {MAX_ENTRY*100:.0f}c (book already repriced) age={age}s")
    settle()
    if int(now) % 600 < 3:
        hrs = (time.time() - t_start) / 3600
        logger.info(f"[STATUS] {hrs:.1f}h | snipes={snipes} near-misses={near} | "
                    f"settled {wins}W/{losses}L net=${pnl:+.2f}")
    time.sleep(3)
