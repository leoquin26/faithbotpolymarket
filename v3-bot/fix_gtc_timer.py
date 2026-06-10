#!/usr/bin/env python3
"""Fix GTC stale cancel timer: 2min -> 5min for better fill rate."""

FILE = "/home/ubuntu/v3-bot/order_manager.py"

with open(FILE, "r") as f:
    code = f.read()

original = code

# Extend stale GTC timer from 120s (2min) to 300s (5min)
old = '                if age > 120:'
new = '                if age > 300:'
if old in code:
    code = code.replace(old, new)
    print("[OK] GTC stale timer: 120s -> 300s (5min)")
else:
    print("[SKIP] GTC timer pattern not found")

if code != original:
    with open(FILE, "w") as f:
        f.write(code)
    print("[DONE] order_manager.py updated")
else:
    print("[WARN] No changes made")
