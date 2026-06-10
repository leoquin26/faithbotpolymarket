#!/usr/bin/env python3
"""
Restore profitable faithbot demo core on EC2 v3-bot.
Keeps: polymarket_ws.py, bybit_ws, force_tor, .env
Grafts: WS set_subscriptions, WS get_clob_book, bybit _multi_price
Disables: regime_aware (rename folder)
"""
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
SRC = Path("/home/ubuntu/faithbot-restore")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

CORE = [
    "predictor.py",
    "run_bot.py",
    "morning_strategy.py",
    "morning_predictor.py",
    "order_manager.py",
]


def backup(p: Path):
    b = p.with_suffix(p.suffix + f".pre_demo_{STAMP}")
    if p.exists():
        shutil.copy2(p, b)
        print(f"backup {b.name}")


def restore_core():
    for f in CORE:
        src = SRC / f
        if not src.exists():
            raise SystemExit(f"missing {src}")
        backup(ROOT / f)
        shutil.copy2(src, ROOT / f)
        print(f"restored {f}")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    text = p.read_text(encoding="utf-8")

    if "_multi_price" not in text:
        text = text.replace(
            "import binance_ws\nfrom market_data",
            """import binance_ws
try:
    import bybit_ws
    _BYBIT_OK = True
except Exception:
    bybit_ws = None
    _BYBIT_OK = False


def _multi_price(coin: str):
    p = binance_ws.get_price(coin)
    if p and p > 0:
        return p
    if _BYBIT_OK and bybit_ws is not None:
        try:
            p2 = bybit_ws.get_price(coin)
            if p2 and p2 > 0:
                return p2
        except Exception:
            pass
    return None

from market_data""",
            1,
        )

    text = text.replace(
        "                ws_price = binance_ws.get_price(coin)",
        "                ws_price = _multi_price(coin)",
        1,
    )

    if "set_subscriptions" not in text:
        text = text.replace(
            "            futures_map = {executor.submit(scan_coin, c): c for c in config.SYMBOLS}",
            """            try:
                import polymarket_ws as _pws_mod
                _batch_ids = []
                for _c in config.SYMBOLS:
                    _inf = get_market_info(_c)
                    if _inf:
                        _batch_ids.extend([_inf.up_token_id, _inf.down_token_id])
                if _batch_ids:
                    _pws_mod.set_subscriptions(_batch_ids)
            except Exception:
                pass

            futures_map = {executor.submit(scan_coin, c): c for c in config.SYMBOLS}""",
            1,
        )

    if "bybit_ws.start" not in text:
        text = text.replace(
            "    binance_ws.start()\n    time.sleep(2)",
            """    binance_ws.start()
    if _BYBIT_OK and bybit_ws is not None:
        try:
            bybit_ws.start()
            logger.info("[BYBIT-WS] started (failover)")
        except Exception as e:
            logger.warning(f"[BYBIT-WS] start failed: {e}")
    time.sleep(2)""",
            1,
        )

    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py (bybit + poly ws)")


def patch_order_manager():
    p = ROOT / "order_manager.py"
    text = p.read_text(encoding="utf-8")

    if "import polymarket_ws as _pws" not in text:
        text = text.replace(
            "import time\n",
            "import time\ntry:\n    import polymarket_ws as _pws\nexcept Exception:\n    _pws = None\n",
            1,
        )

    old = '''    def get_clob_book(self, token_id: str) -> dict:
        """Single orderbook call via direct HTTP (bypasses Tor proxy)."""
        result = {"ask": None, "bid": None, "mid": None, "depth_ratio": 0.0}
        try:
            http = self._get_direct_http()'''

    new = '''    def get_clob_book(self, token_id: str) -> dict:
        """WS cache first, REST fallback."""
        if _pws is not None:
            try:
                _ws_book = _pws.get_book(token_id)
                if _ws_book and _ws_book.get("ask"):
                    _ws_age = time.time() - _ws_book.get("ts", 0)
                    if _ws_age <= 12.0:
                        return {
                            "ask": _ws_book.get("ask"),
                            "bid": _ws_book.get("bid"),
                            "mid": _ws_book.get("mid"),
                            "depth_ratio": _ws_book.get("depth_ratio", 0.0),
                            "source": "ws",
                        }
            except Exception:
                pass
        result = {"ask": None, "bid": None, "mid": None, "depth_ratio": 0.0}
        try:
            http = self._get_direct_http()'''

    if old not in text:
        raise SystemExit("order_manager get_clob_book not found")
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("patched order_manager.py (poly ws)")


def disable_regime_aware():
    ra = ROOT / "regime_aware"
    if ra.exists():
        dest = ROOT / f"regime_aware.disabled_{STAMP}"
        shutil.move(ra, dest)
        print(f"disabled {dest.name}")


def write_readme():
    doc = ROOT / "FAITHBOT_DEMO_RESTORE.md"
    doc.write_text(
        f"""# FaithBot Demo Restore ({STAMP})

## Source
- https://github.com/leoquin26/faithbotpolymarket/tree/demo
- Profitable core: BS + 70% trend blend, ChopDetector, no regime invert

## Restored files
{', '.join(CORE)}

## Kept from v3-bot (speed)
- polymarket_ws.py + set_subscriptions() per scan
- order_manager WS-first get_clob_book
- bybit_ws failover (_multi_price)
- force_tor.py, .env (unchanged)

## Removed / disabled
- regime_aware/ -> regime_aware.disabled_{STAMP}
- exhaustion_detector loop (not in demo run_bot)
- All Jun-3 flip/invert/cheap-trap patches

## Restart
pkill -f 'python3 -u run_bot.py'
cd ~/v3-bot && nohup python3 -u run_bot.py >> logs/bot_$(date +%Y-%m-%d).log 2>&1 &
""",
        encoding="utf-8",
    )
    print(f"wrote {doc.name}")


def main():
    restore_core()
    patch_run_bot()
    patch_order_manager()
    disable_regime_aware()
    write_readme()
    print("OK")


if __name__ == "__main__":
    main()
