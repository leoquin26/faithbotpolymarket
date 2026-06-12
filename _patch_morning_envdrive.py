"""May 19 PM hotfix: make morning thresholds env-driven + lower P3 to 0.65.

Today's bottleneck:
  12:38 BTC UP @46c  prob=84%  edge=38.2%  trend=+1.60  A++ MONSTER
  EXHAUST DAMPEN drops prob 83% -> 71%
  Morning P3 threshold 78% rejects -> bot ABSTAINs from a near-certain winner.

Fix: lower P3 threshold to 0.65, env-tunable.
P1 stays conservative (0.80 default) — those are early morning thin-market trades.
P3 (noon-2pm) is mid-day with denser liquidity; can be more permissive.
"""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/morning_strategy.py")
text = p.read_text()

if "MAY 19 ENV-DRIVEN" in text:
    print("morning_strategy already patched — skipping")
    raise SystemExit(0)

old = """# Phase boundaries (Lima time)
# Phase 1: 9:00-10:29 "Early Trend" - conservative, liquid coins only
# Phase 2: 10:30-11:59 "US Open Chop" - NO TRADING
# Phase 3: 12:00-13:59 "Midday Trend" - moderate filters, all coins
P1_ALLOWED = {"BTC", "ETH"}
P1_MIN_PROB = 0.80
P1_MIN_EDGE = 0.10
P1_MIN_TREND = 0.60

P3_ALLOWED = {"BTC", "ETH", "SOL", "XRP"}
P3_MIN_PROB = 0.78
P3_MIN_EDGE = 0.08
P3_MIN_TREND = 0.50"""

new = """# Phase boundaries (Lima time)
# Phase 1: 9:00-10:29 "Early Trend" - conservative, liquid coins only
# Phase 2: 10:30-11:59 "US Open Chop" - NO TRADING
# Phase 3: 12:00-13:59 "Midday Trend" - moderate filters, all coins
#
# MAY 19 ENV-DRIVEN: thresholds are now env-tunable so we can relax
# without code changes. P3 lowered 0.78 -> 0.65 because EXHAUST DAMPEN
# mutates pred.probability downward (e.g. 83% -> 71%) and the old 0.78
# bar rejected legitimate A-tier signals like BTC UP @46c prob 84%
# edge 38% trend +1.60 on 2026-05-19.
import os as _os_ms

def _env_set(name, default):
    raw = _os_ms.getenv(name, "")
    return set(c.strip().upper() for c in raw.split(",") if c.strip()) if raw else default

P1_ALLOWED = _env_set("MORNING_P1_ALLOWED", {"BTC", "ETH"})
P1_MIN_PROB = float(_os_ms.getenv("MORNING_P1_MIN_PROB", "0.80"))
P1_MIN_EDGE = float(_os_ms.getenv("MORNING_P1_MIN_EDGE", "0.10"))
P1_MIN_TREND = float(_os_ms.getenv("MORNING_P1_MIN_TREND", "0.60"))

P3_ALLOWED = _env_set("MORNING_P3_ALLOWED", {"BTC", "ETH", "SOL", "XRP"})
P3_MIN_PROB = float(_os_ms.getenv("MORNING_P3_MIN_PROB", "0.65"))
P3_MIN_EDGE = float(_os_ms.getenv("MORNING_P3_MIN_EDGE", "0.08"))
P3_MIN_TREND = float(_os_ms.getenv("MORNING_P3_MIN_TREND", "0.50"))"""

if old not in text:
    raise SystemExit("morning_strategy marker not found")

text = text.replace(old, new, 1)
p.write_text(text)
print("morning_strategy patched (env-driven, P3_MIN_PROB default 0.65)")
