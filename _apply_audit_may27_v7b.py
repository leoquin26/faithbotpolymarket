"""
_apply_audit_may27_v7b.py — wire polymarket_ws into the bot.

Additive design: OrderManager.get_clob_book tries the WS cache first;
if no WS data, transparently falls through to the existing REST call.
On first call to a new token_id, also issues a subscribe() so subsequent
calls hit the cache.

This is the latency win documented in arXiv:2508.03474 (table:
~30s retail polling vs <5ms quant push), without ripping out the REST
plumbing. If the WS connection drops, the bot keeps working on REST.
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple

REPO = "/home/ubuntu/v3-bot"


def patch_file(path: str, edits: List[Tuple[str, str, str]]) -> int:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    applied = 0
    for label, anchor, replacement in edits:
        if replacement in src:
            print(f"  [skip] {label}: replacement already present")
            continue
        if anchor not in src:
            raise RuntimeError(
                f"{path}: anchor for {label!r} not found and replacement "
                "not present — manual intervention needed"
            )
        if src.count(anchor) > 1:
            raise RuntimeError(
                f"{path}: anchor for {label!r} matches multiple times "
                f"({src.count(anchor)})"
            )
        src = src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [done] {label}")
    if applied:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        os.replace(tmp, path)
    return applied


# ── order_manager.py: insert WS cache check at top of get_clob_book ──
ORDER_MANAGER_EDITS: List[Tuple[str, str, str]] = [
    (
        "v7b: WebSocket read-through cache in get_clob_book",
        '''    def get_clob_book(self, token_id: str) -> dict:
        """Single orderbook call via direct HTTP (bypasses Tor proxy)."""
        result = {"ask": None, "bid": None, "mid": None, "depth_ratio": 0.0}
        try:
            http = self._get_direct_http()
            resp = http.get(f"https://clob.polymarket.com/book?token_id={token_id}")''',
        '''    def get_clob_book(self, token_id: str) -> dict:
        """Single orderbook call. May 27 v7b: tries WebSocket cache first
        (sub-ms read), falls through to REST if WS is unavailable."""
        # ── [AUDIT MAY27 v7b] WebSocket read-through cache ──
        try:
            import polymarket_ws as _pws  # noqa
            _ws_book = _pws.get_book(token_id)
            if _ws_book and _ws_book.get("ask") is not None:
                # WS cache hit — return latest pushed book
                return {
                    "ask": _ws_book.get("ask"),
                    "bid": _ws_book.get("bid"),
                    "mid": _ws_book.get("mid"),
                    "depth_ratio": _ws_book.get("depth_ratio", 0.0),
                    "_source": "ws",
                }
            # Not subscribed yet — subscribe so next calls hit the cache
            _pws.subscribe([token_id])
        except Exception:
            pass

        result = {"ask": None, "bid": None, "mid": None, "depth_ratio": 0.0,
                  "_source": "rest"}
        try:
            http = self._get_direct_http()
            resp = http.get(f"https://clob.polymarket.com/book?token_id={token_id}")''',
    ),
]


# ── run_bot.py: start the WS singleton at startup ──
RUN_BOT_EDITS: List[Tuple[str, str, str]] = [
    (
        "v7b: start polymarket_ws singleton at bot startup",
        '''    binance_ws.start()
    time.sleep(2)''',
        '''    binance_ws.start()
    time.sleep(2)
    # ── [AUDIT MAY27 v7b] start Polymarket WebSocket sidecar ──
    try:
        import polymarket_ws as _pws
        if _pws.is_connected() or os.getenv("POLYMARKET_WS_ENABLED", "on").lower() == "on":
            _pws.get_singleton()  # lazy-starts the thread
            logger.info("[POLY-WS] singleton started")
    except Exception as _e_pws:
        logger.warning(f"[POLY-WS] start failed: {_e_pws}")''',
    ),
]


def main() -> int:
    paths_to_compile: list = []
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v7b: wire WebSocket into bot")
    print("=" * 64)

    print()
    print("→ order_manager.py")
    p = os.path.join(REPO, "order_manager.py")
    n = patch_file(p, ORDER_MANAGER_EDITS)
    print(f"  applied {n}/{len(ORDER_MANAGER_EDITS)} edits")
    paths_to_compile.append(p)

    print()
    print("→ run_bot.py")
    p = os.path.join(REPO, "run_bot.py")
    n = patch_file(p, RUN_BOT_EDITS)
    print(f"  applied {n}/{len(RUN_BOT_EDITS)} edits")
    paths_to_compile.append(p)

    print()
    print("→ Verifying syntax")
    import py_compile
    for path in paths_to_compile:
        try:
            py_compile.compile(path, doraise=True)
            print(f"  [OK] {os.path.basename(path)}")
        except py_compile.PyCompileError as e:
            print(f"  [FAIL] {os.path.basename(path)}: {e}")
            return 2
    print()
    print("Done. Restart the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
