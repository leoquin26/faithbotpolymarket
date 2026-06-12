"""
Jun 2 PM — CRITICAL FIX: remove broken no_proxy mutation in polymarket_ws.py.

ROOT CAUSE OF BINANCE WS DEATH:
polymarket_ws.py (lines 55-64) tried to add 'ws-subscriptions-clob.polymarket.com'
to both NO_PROXY (uppercase) and no_proxy (lowercase). Lowercase was unset, so
it created no_proxy containing ONLY polymarket — silently replacing the
existing NO_PROXY (uppercase) which had binance.us, bybit.com, etc.

websocket-client's _is_no_proxy_host reads:
    v := os.environ.get("no_proxy", os.environ.get("NO_PROXY", ""))
                       ^^^^^^^^^^^ lowercase first!

So after this mutation, Binance/Bybit WS no longer bypassed Tor.

FIX: delete the no_proxy mutation. polymarket_ws already passes http_no_proxy
kwarg to run_forever, which is the canonical/correct way to do it.
"""
from pathlib import Path

PWS = Path("/home/ubuntu/v3-bot/polymarket_ws.py")


def main():
    text = PWS.read_text()

    old = (
        "# Add our WS hosts to NO_PROXY so urllib's getproxies() returns \"no proxy\"\n"
        "# for them. This is exactly the pattern force_tor.py uses for Binance/Coinbase.\n"
        "_WS_HOST = \"ws-subscriptions-clob.polymarket.com\"\n"
        "for _np_var in (\"NO_PROXY\", \"no_proxy\"):\n"
        "    _existing = os.environ.get(_np_var, \"\")\n"
        "    if _WS_HOST not in _existing:\n"
        "        os.environ[_np_var] = (\n"
        "            _existing + (\",\" if _existing else \"\") + _WS_HOST\n"
        "        )\n"
    )

    new = (
        "# Jun-2 PM FIX: removed broken NO_PROXY mutation that nuked Binance/Bybit WS.\n"
        "# The original code wrote 'no_proxy' (lowercase) = ONLY polymarket host, which\n"
        "# websocket-client reads first (lowercase precedence), causing Binance/Bybit\n"
        "# to fall back to Tor. PolymarketWS passes http_no_proxy to run_forever\n"
        "# already (line ~278), which is the canonical fix and doesn't break siblings.\n"
        "_WS_HOST = \"ws-subscriptions-clob.polymarket.com\"\n"
    )

    if "Jun-2 PM FIX: removed broken NO_PROXY mutation" in text:
        print("[SKIP] already patched")
        return
    if old not in text:
        print("[FAIL] expected mutation block not found")
        # Try a softer match
        if 'os.environ[_np_var] = (' in text:
            print("       (similar code exists but format differs)")
        return
    text = text.replace(old, new, 1)
    PWS.write_text(text)
    print("[OK] removed broken NO_PROXY mutation; PolymarketWS uses http_no_proxy kwarg")


def fix_force_tor():
    """Add ws-subscriptions-clob.polymarket.com to force_tor's NO_PROXY list.
    
    Previously this was added by polymarket_ws's broken mutation. Now we put it
    in the canonical place.
    """
    ft = Path("/home/ubuntu/v3-bot/force_tor.py")
    text = ft.read_text()
    old = "        'ws-live-data.polymarket.com',\n"
    new = (
        "        'ws-live-data.polymarket.com',\n"
        "        'ws-subscriptions-clob.polymarket.com',\n"
    )
    if "ws-subscriptions-clob.polymarket.com" in text:
        print("[SKIP] force_tor already has ws-subscriptions-clob")
        return
    if old not in text:
        print("[FAIL] anchor not found in force_tor.py")
        return
    ft.write_text(text.replace(old, new, 1))
    print("[OK] added ws-subscriptions-clob.polymarket.com to force_tor NO_PROXY")


if __name__ == "__main__":
    main()
    fix_force_tor()
