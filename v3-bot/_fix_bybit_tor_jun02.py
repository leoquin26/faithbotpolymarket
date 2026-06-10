"""
Jun 2 PM — fix Bybit WS Tor bypass.

Problem: force_tor.py sets ALL_PROXY=socks5h://... which websocket-client
respects for wss:// connections, even with http_no_proxy=['stream.bybit.com'].
Result: Bybit WS fails with "failed CONNECT via proxy status: 501".

Fix: in bybit_ws._run_forever, snapshot proxy env vars and clear them ONLY for
the duration of WebSocketApp creation (~5ms). websocket-client captures
proxy=None at __init__ time, then the env vars are restored so Polymarket REST
calls (which need the Tor proxy) continue to work.

Safety:
  - 5ms window of "no proxy" is shorter than scan interval
  - Restoration in finally block (always runs even on exception)
  - Polymarket REST through Tor still works on next call
"""
from pathlib import Path

BW = Path("/home/ubuntu/v3-bot/bybit_ws.py")


def main():
    text = BW.read_text()

    # Find the run_forever loop and replace it with env-clearing version
    old = (
        "def _run_forever() -> None:\n"
        "    global _ws_app\n"
        "    while True:\n"
        "        try:\n"
        "            _ws_app = websocket.WebSocketApp(\n"
        "                WS_URL,\n"
        "                on_open=_on_open,\n"
        "                on_message=_on_message,\n"
        "                on_error=_on_error,\n"
        "                on_close=_on_close,\n"
        "            )\n"
        "            # Force-disable proxy (Tor)\n"
        "            _ws_app.run_forever(\n"
        "                proxy_type=None,\n"
        "                http_proxy_host=None,\n"
        "                http_proxy_port=None,\n"
        "                http_no_proxy=['stream.bybit.com', '*.bybit.com'],\n"
        "                ping_interval=25,\n"
        "                ping_timeout=10,\n"
        "            )\n"
        "        except Exception as e:  # noqa\n"
        '            logger.warning(f"[BYBIT-WS] run_forever crashed: {e}")\n'
        "        time.sleep(5)\n"
    )

    new = (
        "def _run_forever() -> None:\n"
        "    \"\"\"Run Bybit WS. Clear ALL_PROXY/HTTPS_PROXY during WebSocketApp init only.\n"
        "\n"
        "    Jun-2 fix: websocket-client picks up ALL_PROXY (socks5h://Tor) for wss://\n"
        "    even with http_no_proxy kwarg. We temp-clear proxies during init, then\n"
        "    restore so Polymarket REST still routes through Tor.\n"
        "    \"\"\"\n"
        "    import os as _os\n"
        "    global _ws_app\n"
        "    _proxy_keys = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',\n"
        "                   'http_proxy', 'https_proxy', 'all_proxy')\n"
        "    while True:\n"
        "        _saved = {}\n"
        "        try:\n"
        "            for k in _proxy_keys:\n"
        "                if k in _os.environ:\n"
        "                    _saved[k] = _os.environ.pop(k)\n"
        "            try:\n"
        "                _ws_app = websocket.WebSocketApp(\n"
        "                    WS_URL,\n"
        "                    on_open=_on_open,\n"
        "                    on_message=_on_message,\n"
        "                    on_error=_on_error,\n"
        "                    on_close=_on_close,\n"
        "                )\n"
        "            finally:\n"
        "                for k, v in _saved.items():\n"
        "                    _os.environ[k] = v\n"
        "            _ws_app.run_forever(\n"
        "                proxy_type=None,\n"
        "                http_proxy_host=None,\n"
        "                http_proxy_port=None,\n"
        "                http_no_proxy=['stream.bybit.com', '*.bybit.com'],\n"
        "                ping_interval=25,\n"
        "                ping_timeout=10,\n"
        "            )\n"
        "        except Exception as e:  # noqa\n"
        '            logger.warning(f"[BYBIT-WS] run_forever crashed: {e}")\n'
        "            for k, v in _saved.items():\n"
        "                _os.environ.setdefault(k, v)\n"
        "        time.sleep(5)\n"
    )

    if "Jun-2 fix: websocket-client picks up ALL_PROXY" in text:
        print("[SKIP] bybit env-clear fix already applied")
        return
    if old not in text:
        print("[FAIL] expected pattern not found")
        return
    text = text.replace(old, new, 1)
    BW.write_text(text)
    print("[OK] patched bybit_ws._run_forever to clear proxies during init")


if __name__ == "__main__":
    main()
