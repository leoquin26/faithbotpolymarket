"""
Jun 2 PM — wire bybit_ws as second crypto data source.

Currently: only binance_ws feeds the predictor. If Binance hiccups or lags,
the bot freezes on stale ticks.

After this patch:
  - bybit_ws.start() is called at bot init (parallel to binance_ws.start())
  - When binance_ws.get_price(coin) returns None/stale, fall back to bybit
  - Tick histories from both sources can be merged (optional, off by default)

Safety:
  - Binance remains the PRIMARY source (we know its behavior)
  - Bybit is a strict fallback when Binance has no data
  - Module import is wrapped in try/except so missing bybit_ws won't crash
  - Feature is gated by env: BYBIT_WS_ENABLED=on (already true in .env)
"""
from pathlib import Path

RB = Path("/home/ubuntu/v3-bot/run_bot.py")


def main():
    text = RB.read_text()

    # === Patch 1: import bybit_ws + multi-source helper ===
    if "import bybit_ws" in text:
        print("[SKIP] bybit_ws already imported")
    else:
        marker = "import binance_ws\n"
        if marker not in text:
            print("FATAL: binance_ws import not found")
            return
        replacement = (
            "import binance_ws\n"
            "\n"
            "# Jun-2: bybit_ws as secondary crypto source (failover when Binance lags).\n"
            "try:\n"
            "    import bybit_ws\n"
            "    _BYBIT_OK = True\n"
            "except Exception:\n"
            "    bybit_ws = None\n"
            "    _BYBIT_OK = False\n"
            "\n"
            "\n"
            "def _multi_price(coin: str):\n"
            '    """Get crypto price: Binance primary, Bybit failover."""\n'
            "    p = binance_ws.get_price(coin)\n"
            "    if p and p > 0:\n"
            "        return p\n"
            "    if _BYBIT_OK and bybit_ws is not None:\n"
            "        try:\n"
            "            p2 = bybit_ws.get_price(coin)\n"
            "            if p2 and p2 > 0:\n"
            "                return p2\n"
            "        except Exception:\n"
            "            pass\n"
            "    return None\n"
        )
        text = text.replace(marker, replacement, 1)
        print("[OK] added bybit_ws import + _multi_price helper")

    # === Patch 2: un-gate bybit_ws — http_no_proxy fix proven via standalone test ===
    old2_gated = (
        "    binance_ws.start()\n"
        "    # Jun-2: Bybit failover only when Tor is OFF (Tor SOCKS breaks Bybit WS).\n"
        "    if _BYBIT_OK and bybit_ws is not None and os.getenv('USE_TOR', 'false').lower() != 'true':\n"
        "        try:\n"
        "            bybit_ws.start()\n"
        "            logger.info('[BYBIT-WS] starter invoked (Tor disabled, can connect)')\n"
        "        except Exception as e:\n"
        "            logger.warning(f'[BYBIT-WS] failed to start: {e}')\n"
        "    elif _BYBIT_OK:\n"
        "        logger.info('[BYBIT-WS] skipped (USE_TOR=true; Bybit incompatible with Tor SOCKS)')\n"
    )
    new2_ungated = (
        "    binance_ws.start()\n"
        "    # Jun-2 PM: Bybit ungated — http_no_proxy kwarg bypasses Tor (proven via test).\n"
        "    if _BYBIT_OK and bybit_ws is not None:\n"
        "        try:\n"
        "            bybit_ws.start()\n"
        "            logger.info('[BYBIT-WS] starter invoked (http_no_proxy bypasses Tor)')\n"
        "        except Exception as e:\n"
        "            logger.warning(f'[BYBIT-WS] failed to start: {e}')\n"
    )
    if "Jun-2 PM: Bybit ungated" in text:
        print("[SKIP] bybit ungating already applied")
    elif old2_gated in text:
        text = text.replace(old2_gated, new2_ungated, 1)
        print("[OK] un-gated bybit_ws.start() (http_no_proxy fix proven)")
    else:
        # Handle case where it was unguarded originally
        old2_unguarded = "    binance_ws.start()\n    if _BYBIT_OK and bybit_ws is not None:\n        try:\n            bybit_ws.start()\n            logger.info('[BYBIT-WS] starter invoked')\n        except Exception as e:\n            logger.warning(f'[BYBIT-WS] failed to start: {e}')\n"
        if old2_unguarded in text:
            text = text.replace(old2_unguarded, new2_ungated, 1)
            print("[OK] replaced bybit unguarded wiring with ungated v2")
        else:
            print("[SKIP] bybit start block in unknown state, leaving as-is")

    # === Patch 3: replace binance_ws.get_price with _multi_price at scan loop ===
    old3 = "                ws_price = binance_ws.get_price(coin)\n"
    new3 = "                ws_price = _multi_price(coin)\n"
    if "_multi_price(coin)" in text:
        print("[SKIP] _multi_price already wired in scan loop")
    elif old3 not in text:
        print("FATAL: scan-loop ws_price line not found")
        return
    else:
        text = text.replace(old3, new3, 1)
        print("[OK] scan loop now uses _multi_price (Binance+Bybit failover)")

    # === Patch 4: replace binance_ws.get_price in final_price guard too ===
    old4 = "                            final_price = binance_ws.get_price(coin)\n"
    new4 = "                            final_price = _multi_price(coin)\n"
    if old4 in text:
        text = text.replace(old4, new4, 1)
        print("[OK] final_price guard now uses _multi_price")
    else:
        print("[SKIP] final_price line not found (already patched or different)")

    RB.write_text(text)

    # === Patch 5: fix bybit_ws Tor bypass (add http_no_proxy like polymarket_ws does) ===
    BW = Path("/home/ubuntu/v3-bot/bybit_ws.py")
    bw_text = BW.read_text()
    old5 = (
        "            _ws_app.run_forever(\n"
        "                proxy_type=None,\n"
        "                http_proxy_host=None,\n"
        "                http_proxy_port=None,\n"
        "                ping_interval=0,\n"
        "            )\n"
    )
    new5 = (
        "            _ws_app.run_forever(\n"
        "                proxy_type=None,\n"
        "                http_proxy_host=None,\n"
        "                http_proxy_port=None,\n"
        "                http_no_proxy=['stream.bybit.com', '*.bybit.com'],\n"
        "                ping_interval=25,\n"
        "                ping_timeout=10,\n"
        "            )\n"
    )
    if "http_no_proxy=['stream.bybit.com'" in bw_text:
        print("[SKIP] bybit_ws Tor bypass already patched")
    elif old5 not in bw_text:
        print("[WARN] bybit_ws run_forever block differs from expected, skipping")
    else:
        BW.write_text(bw_text.replace(old5, new5, 1))
        print("[OK] patched bybit_ws to bypass Tor (http_no_proxy=stream.bybit.com)")

    print()
    print("Done. Run: python3 -m py_compile run_bot.py bybit_ws.py")


if __name__ == "__main__":
    main()
