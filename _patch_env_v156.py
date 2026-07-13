#!/usr/bin/env python3
"""v1.56 de-overblock knobs. Backup .env.bak_v156 for rollback."""
from pathlib import Path
ENV = Path.home() / "v3-bot" / ".env"
UPDATES = {
    "CLEAN_LATE_MAX_ASK": "0.68",
    "CLEAN_LATE_FLIP_MIN_BPS": "3",
    "CLEAN_LATE_FOK_RETRY": "on",
    "CLEAN_LATE_FOK_RETRY_SLEEP": "0.35",
    # keep safety stack
    "CLEAN_LATE_REQUIRE_EARLY": "on",
    "CLEAN_LATE_REQUIRE_CL_SPOT": "on",
    "CLEAN_LATE_ROC_CL_ONLY": "on",
    "CLEAN_LATE_SKIP_FADING": "on",
    "CLEAN_LATE_COINS": "SOL,ETH",
    "CLEAN_COMPOUND_MIN_EV": "0",
    "CLEAN_LATE_MAX_USD": "3.50",
    "CLEAN_LATE_LIVE": "on",
    "CLEAN_LATE_TAKER": "on",
}
text = ENV.read_text(encoding="utf-8", errors="ignore") if ENV.exists() else ""
out, seen = [], set()
for line in text.splitlines():
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    k = line.split("=", 1)[0].strip()
    if k in UPDATES:
        out.append(f"{k}={UPDATES[k]}")
        seen.add(k)
    else:
        out.append(line)
for k, v in UPDATES.items():
    if k not in seen:
        out.append(f"{k}={v}")
bak = ENV.with_suffix(ENV.suffix + ".bak_v156")
if ENV.exists():
    bak.write_text(text, encoding="utf-8")
ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
print("v1.56 env applied; rollback backup:", bak.name)
for k, v in UPDATES.items():
    print(f"  {k}={v}")
