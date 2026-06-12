"""Bisect imports to find what breaks binance WS."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor

import os, time, threading

# Import each one and test after
imports_to_try = [
    "from dotenv import load_dotenv",
    "from loguru import logger",
    "import telegram_notifier",
    "import config",
    "from morning_predictor import MorningPredictor",
    "import morning_strategy",
    "from market_data import get_market_info",
    "from predictor import Predictor",
    "import exhaustion_detector",
    "from order_manager import OrderManager",  # likely culprit (uses py_clob_client_v2)
]

def test_ws():
    """Quick WS connection test. Returns True if opens, False otherwise."""
    import websocket
    events = []
    def _on_open(ws):
        events.append("open"); ws.close()
    def _on_error(ws, err):
        events.append(("err", str(err)))
    ws = websocket.WebSocketApp(
        "wss://stream.binance.us:9443/ws/btcusdt@aggTrade",
        on_open=_on_open, on_error=_on_error, on_close=lambda *a: events.append("close"),
    )
    t = threading.Thread(target=lambda: ws.run_forever(
        ping_interval=20,
        proxy_type=None, http_proxy_host=None, http_proxy_port=None,
        http_no_proxy=['stream.binance.us'],
    ), daemon=True)
    t.start()
    t.join(timeout=6)
    return any(e == "open" for e in events), events[:2]

# Baseline
ok, ev = test_ws()
print(f"BASELINE (force_tor only): open={ok} events={ev}")

# Try each import
for imp in imports_to_try:
    print(f"\n>>> Importing: {imp}")
    try:
        exec(imp)
    except Exception as e:
        print(f"  IMPORT FAIL: {e}")
        continue
    socks_loaded = any("socks" in m for m in sys.modules)
    print(f"  socks modules loaded: {socks_loaded}")
    ok, ev = test_ws()
    status = "OK" if ok else "FAIL"
    print(f"  WS test: {status} events={ev}")
    if not ok:
        print(f"\n!!! FAILURE introduced by: {imp}")
        socks_modules = [m for m in sorted(sys.modules) if "socks" in m]
        print(f"!!! socks modules: {socks_modules}")
        break
