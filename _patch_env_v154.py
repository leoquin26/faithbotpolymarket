#!/usr/bin/env python3
from pathlib import Path
ENV = Path.home() / "v3-bot" / ".env"
UPDATES = {
    "CLEAN_LATE_MAX_ASK": "0.66",
    "CLEAN_LATE_MAX_USD": "3.50",
    "CLEAN_LATE_FLIP_MIN_BPS": "5",
    "CLEAN_LATE_COINS": "SOL,ETH",
    "CLEAN_LATE_REQUIRE_EARLY": "on",
    "CLEAN_COMPOUND_MIN_EV": "0",
    "CLEAN_COMPOUND_MIN_EV_N": "15",
    "CLEAN_LATE_COIN_MULT": "SOL=1.5,ETH=1.0",
    "CLEAN_COMPOUND": "on",
    "CLEAN_LATE_LIVE": "on",
    "CLEAN_LATE_TAKER": "on",
}
text = ENV.read_text(encoding="utf-8", errors="ignore") if ENV.exists() else ""
lines, seen, out = text.splitlines(), set(), []
for line in lines:
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        out.append(line); continue
    k = line.split("=", 1)[0].strip()
    if k in UPDATES:
        out.append(f"{k}={UPDATES[k]}"); seen.add(k)
    else:
        out.append(line)
for k, v in UPDATES.items():
    if k not in seen:
        out.append(f"{k}={v}")
bak = ENV.with_suffix(ENV.suffix + ".bak_v154")
if ENV.exists():
    bak.write_text(text, encoding="utf-8")
ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
print("v1.54 env ok", bak.name)
for k, v in UPDATES.items():
    print(f"  {k}={v}")
