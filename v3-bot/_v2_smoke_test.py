"""V2 SDK smoke test — read-only.

Verifies:
1. Constructor works with our existing kwargs
2. API creds load
3. Client can fetch balance for funder address (no order placed)
4. Reports collateral token + balance state for migration decisions
"""
import os
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import config

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

print("=" * 60)
print("V2 SDK SMOKE TEST — read-only, no orders placed")
print("=" * 60)
print(f"CLOB_HOST: {config.CLOB_HOST}")
print(f"CHAIN_ID: {config.CHAIN_ID}")
print(f"FUNDER:   {config.FUNDER_ADDRESS}")
print()

print("--- Init client ---")
try:
    client = ClobClient(
        config.CLOB_HOST,
        key=config.PRIVATE_KEY,
        chain_id=config.CHAIN_ID,
        signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")),
        funder=config.FUNDER_ADDRESS,
    )
    if config.API_KEY and config.API_SECRET and config.API_PASSPHRASE:
        client.set_api_creds(ApiCreds(
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
            api_passphrase=config.API_PASSPHRASE,
        ))
    print("  [OK] V2 client initialized")
except Exception as e:
    print(f"  [FAIL] {type(e).__name__}: {e}")
    sys.exit(1)

print()
print("--- Health check (V2 server time) ---")
try:
    server_time = client.get_server_time()
    print(f"  Server time: {server_time}")
    print("  [OK] V2 backend reachable")
except Exception as e:
    print(f"  [FAIL] {type(e).__name__}: {e}")

print()
print("--- Balance + collateral check ---")
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    bal = client.get_balance_allowance(params)
    print(f"  Collateral allowance/balance: {bal}")
except Exception as e:
    print(f"  [FAIL] {type(e).__name__}: {e}")
    print(f"  Likely cause: USDC.e not yet wrapped to pUSD")

print()
print("--- Test market sanity check ---")
TEST_MARKET = "0xaf5e903876ad42de97e1cf02c2ef8484df69bcfc5541b96a400116557d1e504e"
try:
    info = client.get_clob_market_info(TEST_MARKET)
    print(f"  Test market info keys: {list(info.keys()) if isinstance(info, dict) else info}")
    if isinstance(info, dict):
        print(f"    mts (min tick): {info.get('mts')}")
        print(f"    mos (min size): {info.get('mos')}")
        print(f"    fd  (fee): {info.get('fd')}")
except Exception as e:
    print(f"  [FAIL] get_clob_market_info: {type(e).__name__}: {e}")

print()
print("=" * 60)
print("DONE — review above before placing any V2 orders")
print("=" * 60)
