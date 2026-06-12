#!/usr/bin/env python3
"""Apr 28: Migrate order_manager.py from py_clob_client (V1) to py_clob_client_v2.

Polymarket cutover happened at 11:00 UTC on Apr 28, 2026.
Every V1 order signature now returns 400 'order_version_mismatch'.

Confirmed via smoke test:
- V2 SDK is drop-in compatible with our keyword-arg constructor
- OrderArgs(price, size, side, token_id) is supported in V2 (default expiration=0,
  builder_code/metadata default to BYTES32_ZERO)
- Wallet pUSD balance = $155.75 (auto-wrapped during cutover)
- V2 Exchange allowance already set
- get_clob_market_info(condition_id) returns fee = 7.2% taker-only

This patch only changes import paths in order_manager.py. No logic change.
"""
import shutil
import time

ORDER_MGR = "/home/ubuntu/v3-bot/order_manager.py"
STAMP = time.strftime("%Y%m%d_%H%M%S")


def patch():
    with open(ORDER_MGR, "r") as f:
        src = f.read()
    shutil.copy(ORDER_MGR, f"{ORDER_MGR}.bak_apr28_v2migrate_{STAMP}")

    old = (
        "from py_clob_client.client import ClobClient\n"
        "from py_clob_client.clob_types import (\n"
        "    OrderArgs, OrderType, PartialCreateOrderOptions, ApiCreds,\n"
        ")\n"
        "from py_clob_client.order_builder.constants import BUY"
    )
    new = (
        "# CLOB V2 migration apr28: switched from py_clob_client to py_clob_client_v2\n"
        "# (V2 went live 2026-04-28 11:00 UTC; V1 returns order_version_mismatch.)\n"
        "from py_clob_client_v2.client import ClobClient\n"
        "from py_clob_client_v2.clob_types import (\n"
        "    OrderArgs, OrderType, PartialCreateOrderOptions, ApiCreds,\n"
        ")\n"
        "from py_clob_client_v2.order_builder.constants import BUY"
    )
    if old not in src:
        raise SystemExit("ERROR: V1 import block not found in order_manager.py")
    src = src.replace(old, new)
    with open(ORDER_MGR, "w") as f:
        f.write(src)
    print(f"[OK] Patched {ORDER_MGR}")
    print(f"[OK] Backup: {ORDER_MGR}.bak_apr28_v2migrate_{STAMP}")


if __name__ == "__main__":
    patch()
    print("[OK] Verify with: python3 -m py_compile order_manager.py")
