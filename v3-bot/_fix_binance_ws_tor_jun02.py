"""
Jun 2 — CRITICAL: fix binance_ws Tor bypass.

Discovered Binance WS has been failing ALL DAY with:
  "failed CONNECT via proxy status: 501"

Bot fell back to REST polling at 0.5s intervals → predictor starved of ticks
→ ROC60=0bps everywhere → WEAK TREND everywhere → no signals execute.

Root cause: binance_ws.run_forever() doesn't pass http_no_proxy kwarg, so
websocket-client picks up ALL_PROXY=socks5h://127.0.0.1:9050 from env (set by
force_tor.py) and tries to tunnel WSS through Tor. Tor returns 501 for the
HTTP CONNECT method.

Fix (proven to work via standalone test): pass http_no_proxy to run_forever
so websocket-client knows to bypass the proxy for stream.binance.us.

This is the same fix polymarket_ws uses to bypass Tor successfully.
"""
from pathlib import Path

BIN = Path("/home/ubuntu/v3-bot/binance_ws.py")


def main():
    text = BIN.read_text()

    old = (
        "            ws = websocket.WebSocketApp(\n"
        "                url,\n"
        "                on_message=_on_message,\n"
        "                on_error=_on_error,\n"
        "                on_close=_on_close,\n"
        "            )\n"
        "            ws.run_forever(ping_interval=20, ping_timeout=10)\n"
    )

    new = (
        "            ws = websocket.WebSocketApp(\n"
        "                url,\n"
        "                on_message=_on_message,\n"
        "                on_error=_on_error,\n"
        "                on_close=_on_close,\n"
        "            )\n"
        "            # Jun-2: bypass Tor SOCKS for Binance hosts (proven via standalone test).\n"
        "            ws.run_forever(\n"
        "                ping_interval=20,\n"
        "                ping_timeout=10,\n"
        "                proxy_type=None,\n"
        "                http_proxy_host=None,\n"
        "                http_proxy_port=None,\n"
        "                http_no_proxy=[\n"
        "                    'stream.binance.us', 'api.binance.us',\n"
        "                    'stream.binance.com', 'fstream.binance.com',\n"
        "                    'ws-api.binance.com',\n"
        "                ],\n"
        "            )\n"
    )

    if "Jun-2: bypass Tor SOCKS for Binance" in text:
        print("[SKIP] binance_ws already patched")
        return
    if old not in text:
        print("[FAIL] expected run_forever block not found")
        return
    text = text.replace(old, new, 1)
    BIN.write_text(text)
    print("[OK] patched binance_ws.run_forever with http_no_proxy")


if __name__ == "__main__":
    main()
