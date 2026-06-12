"""Check env state diff before/after polymarket_ws import."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor
import os

before = {k: os.environ.get(k) for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy')}

import polymarket_ws

after = {k: os.environ.get(k) for k in before.keys()}

print("=== BEFORE polymarket_ws ===")
for k, v in before.items():
    print(f"  {k} = {v[:100] if v else None}{'...' if v and len(v) > 100 else ''}")

print("\n=== AFTER polymarket_ws ===")
for k, v in after.items():
    print(f"  {k} = {v[:100] if v else None}{'...' if v and len(v) > 100 else ''}")

print("\n=== DIFF ===")
for k in before:
    if before[k] != after[k]:
        print(f"  {k}:")
        print(f"    BEFORE: {before[k]}")
        print(f"    AFTER:  {after[k]}")
