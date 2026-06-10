"""Audit: did EXHAUST OVERRIDE save us money or lose us money over 9 days?

For each ORDER in the 15m logs, check if it was preceded by an
EXHAUST OVERRIDE and what the outcome was. Tally WR + PnL by override type.
"""
import re
from pathlib import Path
from collections import defaultdict

LOG_DIR = Path("/home/ubuntu/v3-bot/logs")
DAYS = ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07",
        "2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11",
        "2026-05-12", "2026-05-13"]

RE_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
RE_SIGNAL = re.compile(
    r"\[SIGNAL\]\s+(\w+)\s+(UP|DOWN)\s+\|.*?Ask=(\d+)c.*?Edge=([\d.]+)%.*?"
    r"Trend=([+-]?[\d.]+).*?ROC60=([+-]?[\d.]+)bps.*?T=(\d+)s"
)
RE_EXHAUST_RAW = re.compile(r"\[EXHAUST\]\s+(\w+)\s+(UP|DOWN).*?score=([\d.]+)\s+raw=(\w+)\s+action=(\w+)")
RE_OVR_HIGH = re.compile(r"\[EXHAUST OVERRIDE-HIGH-ENTRY\]\s+(\w+)\s+(UP|DOWN)")
RE_OVR_AT = re.compile(r"\[EXHAUST OVERRIDE\]\s+(\w+)\s+(UP|DOWN)")
RE_ORDER = re.compile(r"\[ORDER\]\s+(\w+)\s+(UP|DOWN).*?FOK\s+@\s+(\d+)c.*?(\d+)\s+shares.*?cost=\$([\d.]+)")
RE_FILLED = re.compile(r"\[FILLED\]\s+(\w+)\s+(UP|DOWN).*?(\d+)\s+shares\s+@\s+(\d+)c")
RE_WIN = re.compile(r"\[WIN\s+\w+\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+\+\$([\d.]+).*?Entry:\s+(\d+)c\s+x(\d+)")
RE_LOSS = re.compile(r"\[LOSS\s+\w+\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+-\$([\d.]+).*?Entry:\s+(\d+)c\s+x(\d+)")


def t_sec(line):
    m = RE_TIME.match(line)
    if not m:
        return 0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def parse_day(path):
    if not path.exists() or path.stat().st_size < 100:
        return []
    orders = []
    # Track most recent signal/exhaust/override per (coin,dir)
    last_signal = {}
    last_exhaust = {}
    last_ovr = {}
    fills = []
    wins = []
    losses = []
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if "[5M]" in line:
                continue
            ts = t_sec(line)
            m = RE_SIGNAL.search(line)
            if m:
                key = (m.group(1), m.group(2))
                last_signal[key] = {
                    "ts": ts, "ask": int(m.group(3)),
                    "edge": float(m.group(4)),
                    "trend": float(m.group(5)),
                    "roc": float(m.group(6)),
                    "remain": int(m.group(7)),
                }
                continue
            m = RE_EXHAUST_RAW.search(line)
            if m:
                key = (m.group(1), m.group(2))
                last_exhaust[key] = {
                    "ts": ts, "score": float(m.group(3)),
                    "raw": m.group(4), "action": m.group(5),
                }
                continue
            m = RE_OVR_HIGH.search(line)
            if m:
                last_ovr[(m.group(1), m.group(2))] = {"ts": ts, "type": "HIGH_ENTRY"}
                continue
            m = RE_OVR_AT.search(line)
            if m:
                last_ovr[(m.group(1), m.group(2))] = {"ts": ts, "type": "A_TIER"}
                continue
            m = RE_ORDER.search(line)
            if m:
                key = (m.group(1), m.group(2))
                orders.append({
                    "day": path.stem.split("_")[-1], "ts": ts,
                    "coin": m.group(1), "dir": m.group(2),
                    "ask": int(m.group(3)), "shares": int(m.group(4)),
                    "cost": float(m.group(5)),
                    "signal": last_signal.get(key, {}),
                    "exhaust": last_exhaust.get(key, {}),
                    "override": last_ovr.get(key, {}),
                })
                continue
            m = RE_FILLED.search(line)
            if m:
                fills.append({
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "shares": int(m.group(3)), "fill_ask": int(m.group(4)),
                })
                continue
            m = RE_WIN.search(line)
            if m:
                wins.append({
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "amt": float(m.group(3)),
                    "entry": int(m.group(4)), "shares": int(m.group(5)),
                })
                continue
            m = RE_LOSS.search(line)
            if m:
                losses.append({
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "amt": float(m.group(3)),
                    "entry": int(m.group(4)), "shares": int(m.group(5)),
                })
                continue

    for o in orders:
        for f in fills:
            if (f["coin"] == o["coin"] and f["dir"] == o["dir"]
                    and f["shares"] == o["shares"]
                    and 0 <= (f["ts"] - o["ts"]) <= 60):
                o["fill"] = f["fill_ask"]
                break
        else:
            o["fill"] = None
        entry = o.get("fill") or o["ask"]
        o["result"] = None
        o["pnl"] = 0
        for w in wins:
            if (w["coin"] == o["coin"] and w["dir"] == o["dir"]
                    and w["shares"] == o["shares"]
                    and w["entry"] == entry
                    and w["ts"] >= o["ts"]):
                o["result"] = "WIN"
                o["pnl"] = w["amt"]
                break
        if o["result"] is None:
            for l in losses:
                if (l["coin"] == o["coin"] and l["dir"] == o["dir"]
                        and l["shares"] == o["shares"]
                        and l["entry"] == entry
                        and l["ts"] >= o["ts"]):
                    o["result"] = "LOSS"
                    o["pnl"] = -l["amt"]
                    break
    return orders


def summarize(orders, label):
    n = len(orders)
    w = sum(1 for o in orders if o["result"] == "WIN")
    l = sum(1 for o in orders if o["result"] == "LOSS")
    pnl = sum(o["pnl"] for o in orders if o["result"] in ("WIN", "LOSS"))
    wr = (w / (w + l) * 100) if (w + l) else 0
    print(f"  {label:>40}: n={n:>3} W={w:>2} L={l:>2} "
          f"WR={wr:>5.1f}% PnL=${pnl:>+7.2f}")


def main():
    all_orders = []
    for d in DAYS:
        all_orders.extend(parse_day(LOG_DIR / f"bot_{d}.log"))
    seen = set()
    deduped = []
    for o in all_orders:
        k = (o["day"], o["ts"], o["coin"], o["dir"], o["shares"], o["ask"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(o)
    all_orders = deduped
    resolved = [o for o in all_orders if o["result"] in ("WIN", "LOSS")]

    print(f"========= EXHAUST OVERRIDE AUDIT (May 4-13, 15m bot) =========")
    print(f"Total orders parsed: {len(all_orders)}  Resolved: {len(resolved)}")
    print()

    print("=== Overall ===")
    summarize(resolved, "ALL trades")
    print()

    print("=== By EXHAUST raw verdict (what the detector originally said) ===")
    summarize(
        [o for o in resolved if o.get("exhaust", {}).get("raw") == "ALLOW"],
        "Exhaust raw=ALLOW")
    summarize(
        [o for o in resolved if o.get("exhaust", {}).get("raw") == "ABSTAIN"],
        "Exhaust raw=ABSTAIN")
    summarize(
        [o for o in resolved if o.get("exhaust", {}).get("raw") == "DAMPEN"],
        "Exhaust raw=DAMPEN")
    summarize(
        [o for o in resolved if not o.get("exhaust")],
        "(no exhaust data)")
    print()

    print("=== By OVERRIDE type ===")
    summarize(
        [o for o in resolved
         if o.get("exhaust", {}).get("raw") == "ABSTAIN"
         and o.get("override", {}).get("type") == "HIGH_ENTRY"],
        "ABSTAIN -> override HIGH_ENTRY")
    summarize(
        [o for o in resolved
         if o.get("exhaust", {}).get("raw") == "ABSTAIN"
         and o.get("override", {}).get("type") == "A_TIER"],
        "ABSTAIN -> override A_TIER")
    summarize(
        [o for o in resolved
         if o.get("exhaust", {}).get("raw") == "ABSTAIN"
         and not o.get("override")],
        "ABSTAIN with NO override (should be 0)")
    print()

    print("=== Hypothetical: if we KILLED all EXHAUST OVERRIDES on ABSTAIN signals ===")
    blocked = [o for o in resolved
               if o.get("exhaust", {}).get("raw") == "ABSTAIN"
               and o.get("override")]
    saved_pnl = -sum(o["pnl"] for o in blocked)
    print(f"  Trades that would be blocked: {len(blocked)}")
    bw = sum(1 for o in blocked if o["result"] == "WIN")
    bl = sum(1 for o in blocked if o["result"] == "LOSS")
    bpnl = sum(o["pnl"] for o in blocked)
    print(f"    of which: W={bw} L={bl} PnL=${bpnl:+.2f}")
    print(f"  If killed: save {-bpnl:+.2f} of net loss")
    print()

    # Dump losses with their override info
    print("=== Every LOSS in 9 days with its EXHAUST/override state ===")
    print(f"  {'day':>10} {'t':>9} {'coin':>4} {'dir':>5} "
          f"{'ask':>3} {'fill':>4} {'edge':>5} {'trend':>6} "
          f"{'T':>4} {'raw':>8} {'override':>12} {'pnl':>7}")
    for o in sorted([o for o in resolved if o["result"] == "LOSS"],
                    key=lambda x: (x["day"], x["ts"])):
        sig = o.get("signal", {})
        exh = o.get("exhaust", {})
        ovr = o.get("override", {})
        hh, rem = divmod(o["ts"], 3600)
        mm, ss = divmod(rem, 60)
        print(f"  {o['day']:>10} {f'{hh:02d}:{mm:02d}:{ss:02d}':>9} "
              f"{o['coin']:>4} {o['dir']:>5} "
              f"{o['ask']:>3} {(o.get('fill') or 0):>4} "
              f"{sig.get('edge', 0):>5.1f} "
              f"{sig.get('trend', 0):>+6.2f} "
              f"{sig.get('remain', 0):>4} "
              f"{exh.get('raw', '?'):>8} "
              f"{ovr.get('type', '-'):>12} "
              f"{o['pnl']:>+7.2f}")


if __name__ == "__main__":
    main()
