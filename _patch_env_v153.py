#!/usr/bin/env python3
"""Upsert v1.53 join-quality knobs into ~/v3-bot/.env"""
from pathlib import Path

ENV = Path.home() / "v3-bot" / ".env"
UPDATES = {
    "CLEAN_COMPOUND": "on",
    "CLEAN_KELLY_FRAC": "0.08",
    "CLEAN_KELLY_BUMP": "0.10",
    "CLEAN_KELLY_BUMP_AT": "70",
    "CLEAN_MAX_BET_PCT": "0.12",
    "CLEAN_MAX_OPEN_PCT": "0.35",
    "CLEAN_LATE_LIVE": "on",
    "CLEAN_LATE_TAKER": "on",
    "CLEAN_LATE_COINS": "SOL,ETH",
    "CLEAN_LATE_COIN_MULT": "SOL=1.5,ETH=1.0",
    "CLEAN_LATE_REQUIRE_EARLY": "on",
    "CLEAN_LATE_GROW_MULT": "1.25",
    "CLEAN_LATE_ROC_OPPOSE": "on",
    "CLEAN_LATE_SKIP_FADING": "on",
    "CLEAN_COMPOUND_MIN_EV": "0",
    "CLEAN_COMPOUND_MIN_EV_N": "15",
    "CLEAN_TARGET_BANKROLL": "100",
}

text = ENV.read_text(encoding="utf-8", errors="ignore") if ENV.exists() else ""
lines = text.splitlines()
keys_seen = set()
out = []
for line in lines:
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    k = line.split("=", 1)[0].strip()
    if k in UPDATES:
        out.append(f"{k}={UPDATES[k]}")
        keys_seen.add(k)
    else:
        out.append(line)
for k, v in UPDATES.items():
    if k not in keys_seen:
        out.append(f"{k}={v}")

bak = ENV.with_suffix(ENV.suffix + ".bak_v153")
if ENV.exists():
    bak.write_text(text, encoding="utf-8")
ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
print("v1.53 env updated; backup", bak.name)
for k, v in UPDATES.items():
    print(f"  {k}={v}")
