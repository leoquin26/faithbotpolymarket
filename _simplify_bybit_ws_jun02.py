"""
Jun 2 PM — simplify bybit_ws to use ONLY http_no_proxy (proven works).
Remove the env-clearing complexity which was a wrong guess.
"""
from pathlib import Path

BW = Path("/home/ubuntu/v3-bot/bybit_ws.py")


def main():
    text = BW.read_text()

    # Replace the complex env-clearing _run_forever with a clean one
    old_marker = "def _run_forever() -> None:\n    \"\"\"Run Bybit WS. Clear ALL_PROXY/HTTPS_PROXY during WebSocketApp init only."
    if old_marker not in text:
        print("[SKIP] bybit already simplified or unknown state")
        return

    # Find the end of the old function and the body
    start = text.find("def _run_forever() -> None:")
    if start < 0:
        print("[FAIL] can't find _run_forever")
        return
    # Find the next top-level def or end-of-file marker
    end_search = text.find("\n\n# -----", start + 1)
    if end_search < 0:
        end_search = text.find("\ndef ", start + 100)
    if end_search < 0:
        print("[FAIL] can't find end of _run_forever")
        return

    new_block = (
        "def _run_forever() -> None:\n"
        "    \"\"\"Run Bybit WS using http_no_proxy to bypass Tor SOCKS.\n"
        "\n"
        "    Jun-2 PM: simplified — http_no_proxy kwarg is correctly honored by\n"
        "    websocket-client (verified via standalone test that opened successfully\n"
        "    even with ALL_PROXY/HTTPS_PROXY/HTTP_PROXY all set to socks5h://Tor).\n"
        "    \"\"\"\n"
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
        "            _ws_app.run_forever(\n"
        "                ping_interval=25,\n"
        "                ping_timeout=10,\n"
        "                proxy_type=None,\n"
        "                http_proxy_host=None,\n"
        "                http_proxy_port=None,\n"
        "                http_no_proxy=['stream.bybit.com', 'api.bybit.com'],\n"
        "            )\n"
        "        except Exception as e:  # noqa\n"
        "            logger.warning(f\"[BYBIT-WS] run_forever crashed: {e}\")\n"
        "        time.sleep(5)\n"
    )

    text = text[:start] + new_block + text[end_search:]
    BW.write_text(text)
    print("[OK] simplified bybit_ws._run_forever — http_no_proxy only")


if __name__ == "__main__":
    main()
