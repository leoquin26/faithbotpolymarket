"""
polymarket_ws.py — WebSocket subscriber for Polymarket CLOB book updates.

Designed as a *sidecar* to the existing REST-based scanner: runs in a
background thread, maintains an in-memory snapshot of best-ask + best-bid
+ top-3 depth per token_id, and the rest of the bot can read it via
`get_book(token_id)`. Falls back gracefully when the connection is
unavailable.

Why: paper arXiv:2508.03474 quantifies the latency hierarchy. Retail bots
poll REST every 30s; quants get push updates in <5ms. Today's BTC #1 loss
showed our 3-second REST poll missed a 9c ask drop in 13 seconds. With
WebSocket, the reversion-risk module can react in real-time.

This module is deliberately conservative on first deploy:
  • Connects to wss://ws-subscriptions-clob.polymarket.com/ws/market
  • Subscribes to the 4 coins' UP+DOWN tokens (8 token_ids)
  • Maintains book snapshots; exposes get_book()
  • Logs `[POLY-WS]` heartbeats every 30s
  • Reconnects on disconnect with exponential backoff
  • If WS is down, callers transparently fall through to REST

Env knobs:
  POLYMARKET_WS_ENABLED    on/off (default on)
  POLYMARKET_WS_URL        wss URL (default the public CLOB market feed)
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional

try:
    from loguru import logger
except Exception:
    import logging as _lg
    logger = _lg.getLogger("polymarket_ws")

try:
    import websocket  # type: ignore  # provided by `websocket-client`
except Exception as _e:
    websocket = None  # type: ignore
    _WS_IMPORT_ERR = _e
else:
    _WS_IMPORT_ERR = None


_DEFAULT_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_FLAG = os.getenv("POLYMARKET_WS_ENABLED", "on").strip().lower()
_ENABLED = _FLAG in ("on", "true", "1", "yes")

# Tor SOCKS proxy doesn't tunnel WebSocket cleanly (returns 501 on CONNECT).
# Jun-2 PM FIX: removed broken NO_PROXY mutation that nuked Binance/Bybit WS.
# The original code wrote 'no_proxy' (lowercase) = ONLY polymarket host, which
# websocket-client reads first (lowercase precedence), causing Binance/Bybit
# to fall back to Tor. PolymarketWS passes http_no_proxy to run_forever
# already (line ~278), which is the canonical fix and doesn't break siblings.
_WS_HOST = "ws-subscriptions-clob.polymarket.com"


class PolymarketWS:
    """Thread-safe Polymarket CLOB WebSocket subscriber."""

    def __init__(self, url: Optional[str] = None,
                 on_book_update: Optional[Callable[[str, dict], None]] = None):
        self.url = url or os.getenv("POLYMARKET_WS_URL", _DEFAULT_URL)
        self._on_book_update = on_book_update
        self._lock = threading.RLock()
        # token_id → {ask, bid, mid, asks: [(price, size)], bids: [(price, size)],
        #             ts, depth_ratio}
        self._books: Dict[str, dict] = {}
        self._subscribed: set = set()
        self._thread: Optional[threading.Thread] = None
        self._ws = None
        self._stop_flag = threading.Event()
        self._last_msg_ts = 0.0
        self._reconnects = 0

    # ── Public API ───────────────────────────────────────────────────────────
    def is_enabled(self) -> bool:
        return _ENABLED and websocket is not None

    def is_connected(self) -> bool:
        if not self.is_enabled():
            return False
        return (self._last_msg_ts > 0
                and (time.time() - self._last_msg_ts) < 10.0)

    def get_book(self, token_id: str) -> Optional[dict]:
        """Return the latest book snapshot for `token_id`, or None if WS
        is down / token not subscribed / no message yet.
        Mirrors the shape of OrderManager.get_clob_book()."""
        if not self.is_connected():
            return None
        with self._lock:
            book = self._books.get(token_id)
            return dict(book) if book else None

    def subscribe(self, token_ids: Iterable[str]) -> None:
        """Add token_ids to the subscription set; (re)sends the subscribe
        message if connected. Prefer set_subscriptions() each scan."""
        new = set(token_ids) - self._subscribed
        if not new:
            return
        self._subscribed.update(new)
        self._send_subscribe(list(new), label="new")

    def set_subscriptions(self, token_ids: Iterable[str]) -> None:
        """Replace active subs with exactly these tokens (current window only).
        Prevents unbounded growth across 15m windows (was 400+ tokens/day)."""
        new_set = {t for t in token_ids if t}
        if new_set == self._subscribed:
            return
        dropped = len(self._subscribed - new_set)
        self._subscribed = new_set
        # Drop stale book cache for old windows
        if dropped:
            with self._lock:
                for tid in list(self._books.keys()):
                    if tid not in new_set:
                        self._books.pop(tid, None)
        if self._subscribed and self._ws:
            self._send_subscribe(list(self._subscribed), label="active")

    def start(self) -> None:
        """Spawn the background thread."""
        if not self.is_enabled():
            if _WS_IMPORT_ERR is not None:
                logger.warning(
                    f"[POLY-WS] websocket-client not installed "
                    f"({_WS_IMPORT_ERR!r}); WS disabled, falling back to REST."
                )
            else:
                logger.info("[POLY-WS] disabled by env (POLYMARKET_WS_ENABLED).")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_forever, name="polymarket-ws", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    # ── Internals ────────────────────────────────────────────────────────────
    def _send_subscribe(self, token_ids: List[str], label: str = "") -> None:
        if not self._ws:
            return
        try:
            msg = {
                "type": "Market",
                "assets_ids": list(token_ids),
            }
            self._ws.send(json.dumps(msg))
            _tag = f" ({label})" if label else ""
            logger.info(
                f"[POLY-WS] subscribed to {len(token_ids)} tokens{_tag} "
                f"[active={len(self._subscribed)}]"
            )
        except Exception as e:
            logger.debug(f"[POLY-WS] send subscribe failed: {e}")

    def _on_message(self, _ws, message: str) -> None:
        self._last_msg_ts = time.time()
        try:
            payload = json.loads(message)
        except Exception:
            return
        # Polymarket pushes an array of events per ping
        events = payload if isinstance(payload, list) else [payload]
        for ev in events:
            if not isinstance(ev, dict):
                continue
            event_type = ev.get("event_type") or ev.get("type") or ""
            asset_id = ev.get("asset_id") or ev.get("token_id") or ev.get("market") or ""
            if not asset_id:
                continue
            try:
                self._update_from_event(asset_id, event_type, ev)
            except Exception as e:
                logger.debug(f"[POLY-WS] update failed: {e}")

    def _update_from_event(self, asset_id: str, event_type: str, ev: dict) -> None:
        """Apply an incoming event to our local book snapshot."""
        with self._lock:
            book = self._books.setdefault(asset_id, {
                "ask": None, "bid": None, "mid": None,
                "asks": [], "bids": [], "depth_ratio": 0.0,
                "ts": 0.0, "source": "ws",
            })

            # Initial book / book diff: rebuild from full snapshots when present
            asks_field = ev.get("asks") or ev.get("ask_levels") or []
            bids_field = ev.get("bids") or ev.get("bid_levels") or []

            def _to_levels(raw):
                out = []
                for it in raw:
                    if isinstance(it, dict):
                        try:
                            p = float(it.get("price", 0))
                            s = float(it.get("size", 0))
                        except (TypeError, ValueError):
                            continue
                    elif isinstance(it, (list, tuple)) and len(it) >= 2:
                        try:
                            p = float(it[0]); s = float(it[1])
                        except (TypeError, ValueError):
                            continue
                    else:
                        continue
                    if p > 0 and s > 0:
                        out.append((p, s))
                return out

            new_asks = _to_levels(asks_field)
            new_bids = _to_levels(bids_field)
            if new_asks:
                new_asks.sort(key=lambda x: x[0])
                book["asks"] = new_asks
            if new_bids:
                new_bids.sort(key=lambda x: x[0], reverse=True)
                book["bids"] = new_bids

            # Maintain best ask/bid/mid + depth_ratio
            if book["asks"]:
                book["ask"] = book["asks"][0][0]
            if book["bids"]:
                book["bid"] = book["bids"][0][0]
            if book["ask"] and book["bid"]:
                book["mid"] = (book["ask"] + book["bid"]) / 2.0
            elif book["ask"]:
                book["mid"] = book["ask"]
            ask_total = sum(p * s for p, s in book["asks"])
            bid_total = sum(p * s for p, s in book["bids"])
            book["depth_ratio"] = bid_total / ask_total if ask_total > 0 else 0.0
            book["ts"] = time.time()
            book["source"] = "ws"

        if self._on_book_update:
            try:
                self._on_book_update(asset_id, book)
            except Exception:
                pass

    def _on_open(self, _ws) -> None:
        logger.info("[POLY-WS] connected")
        if self._subscribed:
            self._send_subscribe(list(self._subscribed), label="reconnect")

    def _on_error(self, _ws, error) -> None:
        logger.debug(f"[POLY-WS] error: {error}")

    def _on_close(self, _ws, status_code, msg) -> None:
        logger.info(f"[POLY-WS] closed: {status_code} {msg}")

    def _run_forever(self) -> None:
        """Keep the connection alive with exponential backoff.

        Critical: bypass the Tor SOCKS proxy that the rest of the bot uses
        for Polymarket REST. The WebSocket protocol's CONNECT request fails
        through Tor (501). We get a direct connection here just like
        OrderManager._get_direct_http does for REST — Tor SOCKS doesn't
        cleanly tunnel WS, and we don't need anonymity for read-only book data.
        """
        # Ensure HTTPS_PROXY / HTTP_PROXY don't route us through Tor.
        # We snapshot them from os.environ at thread-init time and pass
        # explicit no-proxy args to run_forever so websocket-client doesn't
        # auto-discover them via getproxies().
        backoff = 1.0
        while not self._stop_flag.is_set():
            try:
                self._ws = websocket.WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                # Clear proxy env in the call. websocket-client respects
                # http_proxy_host="" / proxy_type=None for "no proxy".
                self._ws.run_forever(
                    ping_interval=25,
                    ping_timeout=10,
                    http_proxy_host=None,
                    http_proxy_port=None,
                    proxy_type=None,
                    http_no_proxy=["ws-subscriptions-clob.polymarket.com",
                                   "*.polymarket.com"],
                )
            except Exception as e:
                logger.debug(f"[POLY-WS] run loop error: {e}")
            if self._stop_flag.is_set():
                break
            self._reconnects += 1
            sleep_for = min(60.0, backoff)
            time.sleep(sleep_for)
            backoff = min(60.0, backoff * 2.0)


# ── Module-level singleton (so callers can `from polymarket_ws import ws` and
#    just call ws.get_book(token_id) without managing the lifecycle) ─────────
_singleton: Optional[PolymarketWS] = None
_singleton_lock = threading.Lock()


def get_singleton() -> PolymarketWS:
    """Returns the process-wide singleton; lazy-starts on first use."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = PolymarketWS()
                _singleton.start()
    return _singleton


def get_book(token_id: str) -> Optional[dict]:
    """Convenience: read the WS book snapshot if available."""
    if not _ENABLED:
        return None
    return get_singleton().get_book(token_id)


def subscribe(token_ids: Iterable[str]) -> None:
    """Convenience: subscribe to a list of token_ids."""
    if not _ENABLED:
        return
    get_singleton().subscribe(token_ids)


def set_subscriptions(token_ids: Iterable[str]) -> None:
    """Convenience: replace subs with current-window token_ids only."""
    if not _ENABLED:
        return
    get_singleton().set_subscriptions(token_ids)


def is_connected() -> bool:
    if not _ENABLED:
        return False
    s = _singleton  # snapshot
    return bool(s and s.is_connected())
