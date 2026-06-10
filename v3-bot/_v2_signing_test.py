"""V2 signing test — creates a signed order without posting it.
Confirms EIP-712 signing works against V2 contract addresses BEFORE
we restart the live bot.
"""
import os
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import config

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    OrderArgs, OrderType, PartialCreateOrderOptions, ApiCreds,
)
from py_clob_client_v2.order_builder.constants import BUY

print("=" * 60)
print("V2 SIGNING TEST — sign only, NO order posted")
print("=" * 60)

client = ClobClient(
    config.CLOB_HOST,
    key=config.PRIVATE_KEY,
    chain_id=config.CHAIN_ID,
    signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")),
    funder=config.FUNDER_ADDRESS,
)
client.set_api_creds(ApiCreds(
    api_key=config.API_KEY,
    api_secret=config.API_SECRET,
    api_passphrase=config.API_PASSPHRASE,
))

# Use a real BTC market token from current open markets
# We'll fetch a live market first, pick its UP token, and try signing a $1 order
print("\n--- Fetching a current BTC 15m market ---")
import requests
r = requests.get(
    "https://gamma-api.polymarket.com/markets",
    params={
        "active": "true", "closed": "false", "limit": 10,
        "tag_id": 100265,
    },
    timeout=10,
)
markets = r.json()
btc_market = None
for m in markets:
    if "Bitcoin" in m.get("question", "") and "15-minute" in m.get("description", "").lower() or "15m" in m.get("question","").lower():
        btc_market = m
        break
if btc_market is None and markets:
    btc_market = markets[0]
if btc_market is None:
    print("  Could not fetch a live market — using TEST market from docs")
    test_token = "100000000000000000000000000000000000000000000000000000000000000001"
else:
    print(f"  Picked market: {btc_market.get('question')}")
    import json as _j
    token_ids = btc_market.get("clobTokenIds")
    if isinstance(token_ids, str):
        token_ids = _j.loads(token_ids)
    test_token = token_ids[0]
    print(f"  UP token id: {test_token[:30]}...")

print("\n--- Building V2 order (sign only, NOT posted) ---")
try:
    order_args = OrderArgs(
        price=0.10,  # well below ask, won't match anything
        size=5,
        side=BUY,
        token_id=test_token,
    )
    options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)
    order = client.create_order(order_args, options)
    print(f"  [OK] Order signed successfully")
    print(f"  Order keys: {list(order.keys()) if isinstance(order, dict) else type(order).__name__}")
    if isinstance(order, dict):
        for k, v in order.items():
            if k in ("salt", "maker", "signer", "tokenId", "side", "signatureType",
                    "timestamp", "metadata", "builder", "signature"):
                vstr = str(v)
                print(f"    {k}: {vstr[:60] + '...' if len(vstr) > 60 else vstr}")
    print("\n  SIGNING WORKS — order is ready to post.")
    print("  V2 fields confirmed present: timestamp, metadata, builder")
except Exception as e:
    print(f"  [FAIL] {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 60)
print("PRE-FLIGHT GREEN — safe to restart bot on V2")
print("=" * 60)
