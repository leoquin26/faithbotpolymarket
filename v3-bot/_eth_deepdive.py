#!/usr/bin/env python3
"""ETH deep-dive: find any profitable ETH subset."""
import sys, importlib.util
from datetime import datetime, timedelta

spec = importlib.util.spec_from_file_location("a", "_wl_feature_audit.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

today = datetime.now().date()
files = []
for off in range(10):
    d = today - timedelta(days=off)
    f = m.LOG_DIR / f"bot_{d.isoformat()}.log"
    if f.exists():
        files.append(f)

all_trades = []
for f in sorted(files):
    ts = m.parse_day(f)
    all_trades.extend([dict(t, day=f.stem.replace("bot_", "")) for t in ts])

finished = [t for t in all_trades if "outcome" in t]
eth = [t for t in finished if t["coin"] == "ETH"]
print(f"ETH trades: {len(eth)}")

m.summarize(eth, lambda t: f"{t['dir']} {m.hour_bucket(t['fill_ts'])}", "ETH by direction+hour")
m.summarize(eth, lambda t: f"{t['dir']} prob{int(t.get('prob', 0) * 100) // 5 * 5}",
            "ETH by direction+prob bucket")
m.summarize(eth, lambda t: f"{t['dir']} {'OVR' if t.get('ovr') is True else 'NORM'}",
            "ETH by direction+override")
m.summarize(eth, lambda t: f"{t['dir']} entry{int(t.get('fill_price', 0) * 100) // 5 * 5}c",
            "ETH by direction+entry price bucket")

# Drill: list every ETH trade
print("\n=== ALL ETH TRADES (raw) ===")
print(f"{'day':<12} {'time':<10} {'dir':<5} {'prob':<5} {'edge':<6} {'fill':<5} {'tr':<6} "
      f"{'ovr':<5} {'out':<6} {'pnl':<8}")
for t in sorted(eth, key=lambda x: (x['day'], x['fill_ts'])):
    ovr = 'OVR' if t.get('ovr') is True else 'flip' if t.get('ovr') == 'FLIP' else 'norm'
    print(
        f"{t['day']:<12} {t['fill_ts']:<10} {t['dir']:<5} "
        f"{t.get('prob', 0) * 100:>4.0f}% {t.get('edge', 0) * 100:>4.1f}% "
        f"{t.get('fill_price', 0) * 100:>3.0f}c {t.get('trend', 0):>+5.2f} "
        f"{ovr:<5} {t.get('outcome', '?'):<5} ${t.get('pnl', 0):>+6.2f}"
    )
