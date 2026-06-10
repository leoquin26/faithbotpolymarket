"""Check Binance WS health (does it deliver ticks?)."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import binance_ws
import time

binance_ws.start()
print("starting, sleep 8s...")
time.sleep(8)
print("connected:", binance_ws.is_connected())
print("BTC price:", binance_ws.get_price("BTC"))
print("ETH price:", binance_ws.get_price("ETH"))
print("SOL price:", binance_ws.get_price("SOL"))
print("BTC tick count last 60s:", len(binance_ws.get_tick_history("BTC", 60)))
