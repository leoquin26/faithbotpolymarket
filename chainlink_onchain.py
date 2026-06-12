"""
On-chain Chainlink price reader (fallback for when RTDS WebSocket is blocked).

Polymarket settles 15m crypto on the Chainlink BTC/USD (etc.) *Data Stream*.
The RTDS WebSocket proxies that stream but is aggressively rate-limited. When it
is unavailable, the next-best public source is the Chainlink on-chain aggregator
feed (AggregatorV3Interface.latestRoundData) read over a public RPC. It is the
same Chainlink oracle family and updates every few seconds — far closer to the
settlement price than a Binance spot fallback (which also carries a cross-feed
basis and is geo-blocked from this host).

Public API mirrors chainlink_ws:
    start()             -> begin background polling
    get_price(coin)     -> Optional[float], freshest on-chain price
    is_connected()      -> bool, whether we have recent data
    price_age(coin)     -> Optional[float], seconds since last good read
"""
import os
import threading
import time
import json
import urllib.request
from typing import Dict, Optional

from loguru import logger

# The bot may set HTTP(S)_PROXY=socks5h://… (Tor) globally. Public RPCs must NOT
# go through Tor (slow/blocked), and these hosts aren't in the bot's NO_PROXY
# list. Build a dedicated opener that bypasses all proxies for our RPC calls.
_NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({})
)

# Chainlink AggregatorV3 proxy addresses on Polygon PoS (USD pairs, 8 decimals).
_FEEDS: Dict[str, str] = {
    "BTC": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
    "ETH": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
    "SOL": "0x10C8264C0935b3B9870013e057f330Ff3e9C56dC",
    "XRP": "0x785ba89291f676b5386652eB12b30cF361020694",
}

# latestRoundData() selector (no on-chain decimals call needed; Polygon USD
# feeds are all 8 decimals, but we read decimals once per feed to be safe).
_SEL_LATEST = "0xfeaf968c"
_SEL_DECIMALS = "0x313ce567"

# Public RPCs (no key). Tried in order; rotate on failure.
_RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
]

_POLL_SEC = float(os.getenv("CHAINLINK_ONCHAIN_POLL_SEC", "2.5"))
_MAX_AGE = float(os.getenv("CHAINLINK_ONCHAIN_MAX_AGE", "30"))
_TIMEOUT = float(os.getenv("CHAINLINK_ONCHAIN_TIMEOUT", "6"))

_latest: Dict[str, float] = {}
_updated: Dict[str, float] = {}      # local time we last stored a price
_onchain_ts: Dict[str, int] = {}     # chain updatedAt of last price
_decimals: Dict[str, int] = {}
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_rpc_idx = 0

# Rolling (timestamp, price) history per coin so callers can compute ROC on the
# SAME Chainlink feed used for the level. Keep ~10 min at the poll cadence.
_TICKS: Dict[str, list] = {}
_TICK_MAX = int(os.getenv("CHAINLINK_ONCHAIN_TICK_MAX", "300"))


def _rpc_call(to: str, data: str) -> Optional[str]:
    """eth_call against the current RPC; rotate RPCs on error. Returns hex result."""
    global _rpc_idx
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }).encode()
    for attempt in range(len(_RPCS)):
        rpc = _RPCS[(_rpc_idx + attempt) % len(_RPCS)]
        try:
            req = urllib.request.Request(
                rpc, data=body,
                headers={
                    "content-type": "application/json",
                    # Public RPCs 403 the default Python-urllib UA.
                    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0.0.0 Safari/537.36",
                },
            )
            with _NO_PROXY_OPENER.open(req, timeout=_TIMEOUT) as resp:
                out = json.loads(resp.read().decode())
            res = out.get("result")
            if res and res != "0x":
                if attempt:  # this RPC worked; make it primary
                    _rpc_idx = (_rpc_idx + attempt) % len(_RPCS)
                return res
        except Exception as e:
            logger.debug(f"[CHAINLINK-ONCHAIN] RPC {rpc} err: {str(e)[:60]}")
            continue
    return None


def _get_decimals(coin: str, addr: str) -> int:
    d = _decimals.get(coin)
    if d:
        return d
    res = _rpc_call(addr, _SEL_DECIMALS)
    d = int(res, 16) if res else 8
    _decimals[coin] = d
    return d


def _poll_once():
    for coin, addr in _FEEDS.items():
        res = _rpc_call(addr, _SEL_LATEST)
        if not res:
            continue
        h = res[2:]
        if len(h) < 256:
            continue
        try:
            answer = int(h[64:128], 16)
            updated_at = int(h[192:256], 16)
        except ValueError:
            continue
        if answer <= 0:
            continue
        dec = _get_decimals(coin, addr)
        price = answer / (10 ** dec)
        # Sanity: ignore obviously broken reads.
        if price <= 0:
            continue
        now = time.time()
        with _lock:
            _latest[coin] = price
            _updated[coin] = now
            _onchain_ts[coin] = updated_at
            # Append to rolling history only when the chain value actually moved
            # OR enough time passed, so ROC reflects real ticks (dedupe identical
            # consecutive on-chain reads at the same updatedAt).
            buf = _TICKS.setdefault(coin, [])
            if not buf or buf[-1][1] != price or (now - buf[-1][0]) >= 5.0:
                buf.append((now, price))
                if len(buf) > _TICK_MAX:
                    del buf[: len(buf) - _TICK_MAX]


def _run():
    logger.info("[CHAINLINK-ONCHAIN] poller started (Polygon aggregators)")
    while True:
        try:
            _poll_once()
        except Exception as e:
            logger.debug(f"[CHAINLINK-ONCHAIN] poll error: {str(e)[:80]}")
        time.sleep(_POLL_SEC)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run, daemon=True, name="chainlink-onchain")
    _thread.start()


def get_price(coin: str) -> Optional[float]:
    c = coin.upper()
    with _lock:
        p = _latest.get(c)
        ts = _updated.get(c, 0.0)
    if p and p > 0 and (time.time() - ts) <= _MAX_AGE:
        return p
    return None


def tick_history(coin: str, seconds: float = 300.0) -> list:
    """(timestamp, price) ticks for the last `seconds`, oldest first."""
    c = coin.upper()
    cutoff = time.time() - seconds
    with _lock:
        buf = list(_TICKS.get(c, []))
    return [(ts, p) for ts, p in buf if ts >= cutoff]


def price_age(coin: str) -> Optional[float]:
    c = coin.upper()
    with _lock:
        ts = _updated.get(c)
    return (time.time() - ts) if ts else None


def is_connected() -> bool:
    now = time.time()
    with _lock:
        return any(
            p and p > 0 and (now - _updated.get(c, 0)) <= _MAX_AGE
            for c, p in _latest.items()
        )


if __name__ == "__main__":
    start()
    time.sleep(6)
    for c in _FEEDS:
        print(c, get_price(c), "age", price_age(c))
