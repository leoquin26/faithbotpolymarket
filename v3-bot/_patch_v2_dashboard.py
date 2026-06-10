#!/usr/bin/env python3
"""Apr 28: Migrate dashboard_v3/clob_adapter.py from V1 to V2 SDK.

The dashboard only does read-only ops (get_trades, get_positions,
get_balances). All exist in V2 with same signatures. The defensive
hasattr() checks on get_positions/get_balances make the code tolerant
of any V2 method renames.

This patch only changes the import paths.
"""
import shutil
import time

ADAPTER = "/home/ubuntu/v3-bot/dashboard_v3/clob_adapter.py"
STAMP = time.strftime("%Y%m%d_%H%M%S")


def patch():
    with open(ADAPTER, "r") as f:
        src = f.read()
    shutil.copy(ADAPTER, f"{ADAPTER}.bak_apr28_v2migrate_{STAMP}")

    old = (
        "try:\n"
        "    from py_clob_client.client import ClobClient\n"
        "    from py_clob_client.clob_types import ApiCreds\n"
        "except Exception as e:  # pragma: no cover\n"
        "    ClobClient = None  # type: ignore\n"
        "    ApiCreds = None  # type: ignore\n"
        "    logger.warning(f\"py_clob_client not available: {e}\")"
    )
    new = (
        "# CLOB V2 migration apr28: switched to py_clob_client_v2\n"
        "# (V2 went live 2026-04-28 11:00 UTC; V1 SDK errors out post-cutover.)\n"
        "try:\n"
        "    from py_clob_client_v2.client import ClobClient\n"
        "    from py_clob_client_v2.clob_types import ApiCreds\n"
        "except Exception as e:  # pragma: no cover\n"
        "    ClobClient = None  # type: ignore\n"
        "    ApiCreds = None  # type: ignore\n"
        "    logger.warning(f\"py_clob_client_v2 not available: {e}\")"
    )
    if old not in src:
        raise SystemExit("ERROR: V1 import block not found in clob_adapter.py")
    src = src.replace(old, new)
    with open(ADAPTER, "w") as f:
        f.write(src)
    print(f"[OK] Patched {ADAPTER}")
    print(f"[OK] Backup: {ADAPTER}.bak_apr28_v2migrate_{STAMP}")


if __name__ == "__main__":
    patch()
    print("[OK] Verify with: python3 -m py_compile dashboard_v3/clob_adapter.py")
