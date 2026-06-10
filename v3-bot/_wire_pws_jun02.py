"""
Jun 2 PM — wire polymarket_ws into order_manager.get_clob_book().

Current behavior: REST GET /book for every UP+DOWN of every coin per scan.
  3 coins × 2 sides = 6 REST calls × ~200ms = 1.2s wasted per scan loop.

New behavior:
  1. Init: start polymarket_ws singleton at OrderManager.__init__
  2. get_clob_book(token_id):
       a. Auto-subscribe to token_id (idempotent)
       b. If WS has a fresh book (ts within MAX_STALE_SEC) for token, use it
          (50ms cache lookup, no network)
       c. Else fall back to REST (existing code unchanged)

Safety:
  - REST fallback preserves current behavior if WS fails
  - polymarket_ws bypasses Tor (already correct for WS protocol)
  - Idempotent subscribe: no duplicate subscriptions
  - Per-call max_stale_sec=12 (4 scans worth) prevents stale data
"""
import re
from pathlib import Path

OM = Path("/home/ubuntu/v3-bot/order_manager.py")


def main():
    text = OM.read_text()

    # === Patch 1: import polymarket_ws AFTER the analytics try/except block ===
    if "import polymarket_ws" not in text:
        # Place AFTER the existing analytics try/except (not inside it!)
        marker = "try:\n    from analytics import event_logger as _alog\nexcept Exception:\n    _alog = None\n"
        if marker not in text:
            print("FATAL: analytics try/except marker not found")
            return
        text = text.replace(
            marker,
            marker + "\n# Jun-2: polymarket WS (book cache) — bypasses Tor REST\n"
            "try:\n"
            "    import polymarket_ws as _pws\n"
            "except Exception:\n"
            "    _pws = None\n",
            1,
        )
        print("[OK] added polymarket_ws import")
    else:
        print("[SKIP] polymarket_ws already imported")

    # === Patch 2: replace get_clob_book body to try WS first ===
    old_fn = (
        '    def get_clob_book(self, token_id: str) -> dict:\n'
        '        """Single orderbook call via direct HTTP (bypasses Tor proxy)."""\n'
        '        result = {"ask": None, "bid": None, "mid": None, "depth_ratio": 0.0}\n'
        '        try:\n'
        '            http = self._get_direct_http()\n'
        '            resp = http.get(f"https://clob.polymarket.com/book?token_id={token_id}")\n'
        '            if resp.status_code != 200:\n'
        '                return result\n'
    )

    new_fn = (
        '    def get_clob_book(self, token_id: str) -> dict:\n'
        '        """Get orderbook. Tries WS cache first (50ms), falls back to REST."""\n'
        '        # Jun-2: try polymarket_ws first (50ms cache hit vs 200ms REST).\n'
        '        if _pws is not None:\n'
        '            try:\n'
        '                _pws.subscribe([token_id])  # idempotent\n'
        '                _ws_book = _pws.get_book(token_id)\n'
        '                if _ws_book and _ws_book.get("ask"):\n'
        '                    _ws_age = time.time() - _ws_book.get("ts", 0)\n'
        '                    if _ws_age <= 12.0:  # 4 scans worth of freshness\n'
        '                        return {\n'
        '                            "ask": _ws_book.get("ask"),\n'
        '                            "bid": _ws_book.get("bid"),\n'
        '                            "mid": _ws_book.get("mid"),\n'
        '                            "depth_ratio": _ws_book.get("depth_ratio", 0.0),\n'
        '                            "source": "ws",\n'
        '                        }\n'
        '            except Exception:\n'
        '                pass  # fall through to REST\n'
        '        # REST fallback (original code path).\n'
        '        result = {"ask": None, "bid": None, "mid": None, "depth_ratio": 0.0}\n'
        '        try:\n'
        '            http = self._get_direct_http()\n'
        '            resp = http.get(f"https://clob.polymarket.com/book?token_id={token_id}")\n'
        '            if resp.status_code != 200:\n'
        '                return result\n'
    )

    if "Jun-2: try polymarket_ws first" in text:
        print("[SKIP] get_clob_book already patched")
    elif old_fn not in text:
        print("[FAIL] get_clob_book source pattern not found")
        # Try a more lenient search
        if "def get_clob_book" in text:
            print("       (function exists but body differs from expected)")
        return
    else:
        text = text.replace(old_fn, new_fn, 1)
        print("[OK] patched get_clob_book to use WS-first")

    OM.write_text(text)
    print()
    print("Done. Run: python3 -m py_compile order_manager.py")


if __name__ == "__main__":
    main()
