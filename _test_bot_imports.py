"""Mimic bot's import chain before testing binance WS."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")

# Import in same order as run_bot.py
import force_tor
import os
import time
import threading
import warnings
from dotenv import load_dotenv
import logging
from loguru import logger
import telegram_notifier as tg
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import config
from morning_predictor import MorningPredictor
import morning_strategy as morn
import binance_ws
from market_data import get_market_info, MarketInfo
from predictor import Predictor, Prediction
import exhaustion_detector as exhaust
from order_manager import OrderManager

print("== imports done ==")
print("websocket modules loaded:")
for m in sorted(sys.modules):
    if "socks" in m.lower() or "websocket" in m.lower():
        print(f"  {m}")

import websocket
print(f"websocket version: {websocket.__version__}")
print(f"websocket file: {websocket.__file__}")

events = []
def _on_open(ws):
    events.append("open"); print("  >> OPEN")
def _on_message(ws, msg):
    events.append("msg"); print(f"  >> MSG"); 
    if len(events) >= 3: ws.close()
def _on_error(ws, err):
    events.append(("err", str(err))); print(f"  >> ERR: {err}")
def _on_close(ws, code, msg):
    events.append("close"); print(f"  >> CLOSE: {code} {msg}")

combined = "/".join(f"{sym.lower()}@aggTrade" for sym in config.SYMBOLS.values())
url = f"wss://stream.binance.us:9443/ws/{combined}"
print(f"URL: {url}")

ws = websocket.WebSocketApp(url, on_open=_on_open, on_message=_on_message,
                            on_error=_on_error, on_close=_on_close)
def go():
    ws.run_forever(
        ping_interval=20, ping_timeout=10,
        proxy_type=None, http_proxy_host=None, http_proxy_port=None,
        http_no_proxy=['stream.binance.us', 'api.binance.us', 
                       'stream.binance.com', 'fstream.binance.com', 'ws-api.binance.com'],
    )
print("\n== connecting binance WS (after bot imports) ==")
t = threading.Thread(target=go, daemon=True)
t.start()
t.join(timeout=12)
print(f"\nevents: {events[:5]}")
