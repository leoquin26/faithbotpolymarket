"""Phase-2 investigation — WHY does the 15m bot lose on early-window entries?

From V8 counterfactual we know:
  T_remain  0-300s : WR=66.7% PnL=$+0.95   (good)
  T_remain  300-600: WR=61.5% PnL=$-1.99   (ok)
  T_remain  600-800: WR=51.3% PnL=$-35.06  (catastrophic — 71% of trades)

This script slices the 39 early-window trades against many dimensions
to find a clean filter signal:
  - coin (BTC vs ETH vs SOL vs XRP)
  - strategy phase (MORNING P1/P2/P3 vs regular)
  - trend strength bucket
  - edge bucket
  - entry zone (40-50c vs 50-60c vs 60-65c vs >65c)
  - day of week / hour-of-day
  - prob_seen (76%, 78%, 81%, 84%, 86%)
  - prior recent direction history (was it a flip or trend continuation?)

Goal: find a feature that splits the 39 trades into a clean
"profitable subgroup" and a "losing subgroup" with high effect size.

Run on EC2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict
from typing import Optional


LOG_DIR = Path("/home/ubuntu/v3-bot/logs")
DAYS = ["2026-05-04", "2026-05-05", "2026-05-06",
        "2026-05-07", "2026-05-08", "2026-05-09",
        "2026-05-10", "2026-05-11", "2026-05-12"]

RE_SIGNAL = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[SIGNAL\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+Prob=(?P<prob>\d+)%\s+\|\s+Ask=(?P<ask>\d+)c\s+\|\s+"
    r"Edge=(?P<edge>[+-]?[\d.]+)%\s+\|\s+Trend=(?P<trend>[+-]?[\d.]+)\s+"
    r"Dist=(?P<dist>[+-]?[\d.]+)%\s+ROC60=(?P<roc>[+-]?[\d.]+)bps\s+"
    r"σ=(?P<sigma>[\d.eE+-]+)\s+T=(?P<remain>\d+)s"
)
RE_ORDER = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[ORDER\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+FOK\s+@\s+(?P<ask>\d+)c\s+\|\s+(?P<shares>\d+)\s+shares\s+"
    r"\(cost=\$(?P<cost>[\d.]+),\s+sized=\$(?P<sized>[\d.]+)\)"
)
RE_FILLED = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[FILLED\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+(?P<shares>\d+)\s+shares\s+@\s+(?P<ask>\d+)c"
)
RE_WIN = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[WIN\s+(?P<sess>\w+)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+\|\s+\+\$(?P<amt>[\d.]+)\s+\|\s+"
    r"Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)
RE_LOSS = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[LOSS\s+(?P<sess>\w+)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+\|\s+-\$(?P<amt>[\d.]+)\s+\|\s+"
    r"Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)
# MORNING phase tags — emitted alongside each approved trade
RE_MORNING_TRADE = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[MORNING (?P<phase>P[123])(?:\s+TRADE)?\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)"
)
RE_COMMIT = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[COMMIT\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+(?P<regime>CHOPPY|TRENDING)\s+\|\s+history=(?P<history>[A-Z>-]+)"
)


def hms_to_secs(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


@dataclass
class Order:
    day: str
    t_sec: int
    coin: str
    dir: str
    ask: int
    shares: int
    sized: float
    cost: float
    fill_ask: Optional[int] = None
    sig_prob: int = 0
    sig_edge: float = 0.0
    sig_trend: float = 0.0
    sig_roc: float = 0.0
    sig_sigma: float = 0.0
    sig_remain: int = 0
    morning_phase: str = ""
    regime: str = ""
    history: str = ""
    result: Optional[str] = None
    pnl: float = 0.0


def parse_15m_log(path: Path) -> list[Order]:
    day = path.stem.split("_")[-1]
    orders: list[Order] = []
    sigs: list[dict] = []
    fills: list[dict] = []
    wins: list[dict] = []
    losses: list[dict] = []
    morning_tags: list[dict] = []
    commits: list[dict] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if "[5M]" in line:
                continue
            m = RE_SIGNAL.search(line)
            if m:
                sigs.append({
                    "t_sec": hms_to_secs(m["t"]),
                    "coin": m["coin"], "dir": m["dir"],
                    "ask": int(m["ask"]), "edge": float(m["edge"]),
                    "trend": float(m["trend"]), "roc": float(m["roc"]),
                    "prob": int(m["prob"]), "sigma": float(m["sigma"]),
                    "remain": int(m["remain"]),
                })
                continue
            m = RE_ORDER.search(line)
            if m:
                orders.append(Order(
                    day=day, t_sec=hms_to_secs(m["t"]),
                    coin=m["coin"], dir=m["dir"],
                    ask=int(m["ask"]), shares=int(m["shares"]),
                    sized=float(m["sized"]), cost=float(m["cost"]),
                ))
                continue
            m = RE_FILLED.search(line)
            if m:
                fills.append({
                    "t_sec": hms_to_secs(m["t"]), "coin": m["coin"],
                    "dir": m["dir"], "shares": int(m["shares"]),
                    "fill_ask": int(m["ask"]),
                })
                continue
            m = RE_WIN.search(line)
            if m:
                wins.append({
                    "t_sec": hms_to_secs(m["t"]), "coin": m["coin"],
                    "dir": m["dir"], "entry": int(m["entry"]),
                    "shares": int(m["shares"]), "amt": float(m["amt"]),
                })
                continue
            m = RE_LOSS.search(line)
            if m:
                losses.append({
                    "t_sec": hms_to_secs(m["t"]), "coin": m["coin"],
                    "dir": m["dir"], "entry": int(m["entry"]),
                    "shares": int(m["shares"]), "amt": float(m["amt"]),
                })
                continue
            m = RE_MORNING_TRADE.search(line)
            if m:
                morning_tags.append({
                    "t_sec": hms_to_secs(m["t"]),
                    "phase": m["phase"],
                    "coin": m["coin"], "dir": m["dir"],
                })
                continue
            m = RE_COMMIT.search(line)
            if m:
                commits.append({
                    "t_sec": hms_to_secs(m["t"]),
                    "coin": m["coin"], "dir": m["dir"],
                    "regime": m["regime"],
                    "history": m["history"],
                })

    for o in orders:
        for f in fills:
            if (f["coin"] == o.coin and f["dir"] == o.dir
                    and f["shares"] == o.shares
                    and 0 <= (f["t_sec"] - o.t_sec) <= 60):
                o.fill_ask = f["fill_ask"]
                break
        for s in reversed(sigs):
            if s["t_sec"] > o.t_sec:
                continue
            if s["coin"] == o.coin and s["dir"] == o.dir and (o.t_sec - s["t_sec"]) <= 30:
                o.sig_prob = s["prob"]
                o.sig_edge = s["edge"]
                o.sig_trend = s["trend"]
                o.sig_roc = s["roc"]
                o.sig_sigma = s["sigma"]
                o.sig_remain = s["remain"]
                break
        for c in reversed(commits):
            if c["t_sec"] > o.t_sec:
                continue
            if c["coin"] == o.coin and c["dir"] == o.dir and (o.t_sec - c["t_sec"]) <= 5:
                o.regime = c["regime"]
                o.history = c["history"]
                break
        for mt in morning_tags:
            if (mt["coin"] == o.coin and mt["dir"] == o.dir
                    and abs(mt["t_sec"] - o.t_sec) <= 5):
                o.morning_phase = mt["phase"]
                break
        entry_match = o.fill_ask if o.fill_ask is not None else o.ask
        for w in wins:
            if (w["coin"] == o.coin and w["dir"] == o.dir
                    and w["shares"] == o.shares
                    and w["entry"] == entry_match
                    and w["t_sec"] >= o.t_sec):
                o.result = "WIN"
                o.pnl = w["amt"]
                break
        if o.result is None:
            for l in losses:
                if (l["coin"] == o.coin and l["dir"] == o.dir
                        and l["shares"] == o.shares
                        and l["entry"] == entry_match
                        and l["t_sec"] >= o.t_sec):
                    o.result = "LOSS"
                    o.pnl = -l["amt"]
                    break
        if o.result is None:
            o.result = "UNRESOLVED"
    return orders


def report_bucket(orders, key_fn, name):
    by = defaultdict(list)
    for o in orders:
        by[key_fn(o)].append(o)
    print(f"\n=== {name} ===")
    print(f"  {'bucket':>20} {'n':>3} {'W':>3} {'L':>3} {'WR':>6} {'PnL':>8}")
    for k in sorted(by.keys(), key=lambda x: (str(x))):
        arr = by[k]
        w = sum(1 for o in arr if o.result == "WIN")
        l = sum(1 for o in arr if o.result == "LOSS")
        wr = (w / (w + l) * 100) if (w + l) else 0
        pnl = sum(o.pnl for o in arr)
        print(f"  {str(k):>20} {len(arr):>3} {w:>3} {l:>3} "
              f"{wr:>5.1f}% ${pnl:>+7.2f}")


def main():
    all_orders: list[Order] = []
    for d in DAYS:
        p = LOG_DIR / f"bot_{d}.log"
        if not p.exists() or p.stat().st_size < 1000:
            continue
        all_orders.extend(parse_15m_log(p))

    # dedup
    seen = set()
    deduped = []
    for o in all_orders:
        k = (o.day, o.t_sec, o.coin, o.dir, o.ask, o.shares)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(o)
    all_orders = deduped
    resolved = [o for o in all_orders if o.result in ("WIN", "LOSS")]
    early = [o for o in resolved if o.sig_remain >= 600]
    late = [o for o in resolved if o.sig_remain < 600]

    print(f"\n========= PHASE-2 INVESTIGATION =========")
    print(f"Total resolved 15m trades: {len(resolved)}")
    print(f"  Early (T>=600s): {len(early)}  "
          f"W={sum(1 for o in early if o.result=='WIN')} "
          f"L={sum(1 for o in early if o.result=='LOSS')}  "
          f"PnL=${sum(o.pnl for o in early):+.2f}")
    print(f"  Late  (T<600s):  {len(late)}  "
          f"W={sum(1 for o in late if o.result=='WIN')} "
          f"L={sum(1 for o in late if o.result=='LOSS')}  "
          f"PnL=${sum(o.pnl for o in late):+.2f}")

    print(f"\n\n##### EARLY-WINDOW SLICES (T>=600s, n={len(early)}) #####")

    # 1) By coin
    report_bucket(early, lambda o: o.coin, "EARLY: by coin")

    # 2) By morning phase
    report_bucket(early, lambda o: o.morning_phase or "(non-morning)",
                  "EARLY: by morning phase")

    # 3) By |trend|
    def trend_bucket(o):
        t = abs(o.sig_trend)
        if t < 1.0: return "0_weak<1.0"
        if t < 1.5: return "1_mid_1.0-1.5"
        if t < 2.0: return "2_str_1.5-2.0"
        return "3_supr>=2.0"
    report_bucket(early, trend_bucket, "EARLY: by |trend|")

    # 4) By edge
    def edge_bucket(o):
        e = o.sig_edge
        if e < 15: return "0_<15%"
        if e < 20: return "1_15-20%"
        if e < 25: return "2_20-25%"
        return "3_>=25%"
    report_bucket(early, edge_bucket, "EARLY: by edge")

    # 5) By entry zone (fill price)
    def entry_bucket(o):
        a = o.fill_ask if o.fill_ask else o.ask
        if a < 50: return "0_<50c"
        if a < 55: return "1_50-55c"
        if a < 60: return "2_55-60c"
        if a < 65: return "3_60-65c"
        return "4_>=65c"
    report_bucket(early, entry_bucket, "EARLY: by entry zone (fill)")

    # 6) By regime
    report_bucket(early, lambda o: o.regime or "?", "EARLY: by regime")

    # 7) By signal probability
    def prob_bucket(o):
        p = o.sig_prob
        if p < 78: return "0_<78%"
        if p < 82: return "1_78-82%"
        if p < 86: return "2_82-86%"
        return "3_>=86%"
    report_bucket(early, prob_bucket, "EARLY: by prob")

    # 8) By hour-of-day Lima
    def hour_bucket(o):
        h = o.t_sec // 3600
        return f"{h:02d}h"
    report_bucket(early, hour_bucket, "EARLY: by hour-of-day")

    # 9) Combined: coin × morning phase
    print(f"\n=== EARLY: coin × morning phase ===")
    matrix = defaultdict(lambda: defaultdict(list))
    for o in early:
        matrix[o.coin][o.morning_phase or "regular"].append(o)
    coins_seen = sorted(matrix.keys())
    phases_seen = sorted({p for c in matrix.values() for p in c.keys()})
    print(f"  {'coin':>5} | " + " | ".join(f"{p:>15}" for p in phases_seen))
    for c in coins_seen:
        cells = []
        for p in phases_seen:
            arr = matrix[c][p]
            if not arr:
                cells.append(f"{'-':>15}")
                continue
            w = sum(1 for o in arr if o.result == "WIN")
            l = sum(1 for o in arr if o.result == "LOSS")
            wr = (w / (w + l) * 100) if (w + l) else 0
            pnl = sum(o.pnl for o in arr)
            cells.append(f"{w}W/{l}L {wr:>4.0f}% ${pnl:+.1f}")
        print(f"  {c:>5} | " + " | ".join(f"{cell:>15}" for cell in cells))

    # 10) Recent direction history vs result (was it a FLIP or continuation?)
    print(f"\n=== EARLY: direction-history pattern ===")
    def hist_pattern(o):
        h = o.history
        if not h:
            return "?"
        # Look at last 4 entries
        parts = h.split("->")
        if len(parts) < 4:
            return "few"
        last_dir = parts[-1]
        # count how many prior == last_dir
        same = sum(1 for d in parts[-4:-1] if d == last_dir)
        return f"hist_same={same}/3"
    report_bucket(early, hist_pattern, "EARLY: by direction-continuation pattern")

    def test_filter(name, predicate):
        keep = [o for o in early if predicate(o)]
        drop = [o for o in early if not predicate(o)]
        kw = sum(1 for o in keep if o.result == "WIN")
        kl = sum(1 for o in keep if o.result == "LOSS")
        dw = sum(1 for o in drop if o.result == "WIN")
        dl = sum(1 for o in drop if o.result == "LOSS")
        kpnl = sum(o.pnl for o in keep)
        dpnl = sum(o.pnl for o in drop)
        actual = sum(o.pnl for o in early)
        delta = kpnl - actual
        print(f"\n##### {name} #####")
        print(f"  KEEP: n={len(keep):>2} W={kw:>2} L={kl:>2} "
              f"WR={(kw/max(1,kw+kl)*100):>5.1f}% PnL=${kpnl:>+7.2f}")
        print(f"  DROP: n={len(drop):>2} W={dw:>2} L={dl:>2} "
              f"WR={(dw/max(1,dw+dl)*100):>5.1f}% PnL=${dpnl:>+7.2f}")
        print(f"  → BLOCK DROP: early PnL {actual:+.2f} → {kpnl:+.2f}  Δ=${delta:+.2f}")
        return keep, drop

    test_filter("F-HC1: trend>=1.5 AND edge>=20%",
                lambda o: abs(o.sig_trend) >= 1.5 and o.sig_edge >= 20)
    test_filter("F-HC2: trend>=1.2 AND edge>=22%",
                lambda o: abs(o.sig_trend) >= 1.2 and o.sig_edge >= 22)
    test_filter("F-HC3: trend>=1.5 OR edge>=27%",
                lambda o: abs(o.sig_trend) >= 1.5 or o.sig_edge >= 27)
    test_filter("F-HC4: trend>=2.0 (super strict)",
                lambda o: abs(o.sig_trend) >= 2.0)
    test_filter("F-HC5: prob>=85% (high confidence)",
                lambda o: o.sig_prob >= 85)
    test_filter("F-PHASE: NOT MORNING P2 (block 10:30-12:00 P2)",
                lambda o: o.morning_phase != "P2")
    test_filter("F-COMBO1: F-HC1 OR fill>=65c",
                lambda o: (abs(o.sig_trend) >= 1.5 and o.sig_edge >= 20)
                          or (o.fill_ask or o.ask) >= 65)
    test_filter("F-COMBO2: trend>=1.5 AND NOT P2",
                lambda o: abs(o.sig_trend) >= 1.5 and o.morning_phase != "P2")

    # Show the 5 winners that F-HC1 keeps
    print(f"\n\n=== F-HC1 KEEP set (trend>=1.5 AND edge>=20%) ===")
    print(f"{'day':>10} {'t':>9} {'coin':>4} {'dir':>4} "
          f"{'ask':>3} {'fill':>4} {'prob':>4} {'edge':>5} {'trend':>6} "
          f"{'roc':>6} {'T':>4} {'phase':>5} {'pnl':>7}")
    for o in sorted([x for x in early
                     if abs(x.sig_trend) >= 1.5 and x.sig_edge >= 20],
                    key=lambda x: (x.day, x.t_sec)):
        hh, rem = divmod(o.t_sec, 3600)
        mm, ss = divmod(rem, 60)
        print(f"{o.day:>10} {f'{hh:02d}:{mm:02d}:{ss:02d}':>9} "
              f"{o.coin:>4} {o.dir:>4} "
              f"{o.ask:>3} {(o.fill_ask or 0):>4} "
              f"{o.sig_prob:>4} {o.sig_edge:>5.1f} {o.sig_trend:>+6.2f} "
              f"{o.sig_roc:>+6.1f} {o.sig_remain:>4} "
              f"{o.morning_phase or '-':>5} "
              f"{o.pnl:>+7.2f}")

    # Per-trade dump of all early losses
    print(f"\n\n##### Every EARLY LOSS — full feature dump #####")
    print(f"{'day':>10} {'t':>9} {'coin':>4} {'dir':>4} "
          f"{'ask':>3} {'fill':>4} {'prob':>4} {'edge':>5} {'trend':>6} "
          f"{'roc':>6} {'T':>4} {'phase':>5} {'regime':>8} {'pnl':>7}")
    for o in sorted([x for x in early if x.result == "LOSS"],
                    key=lambda x: (x.day, x.t_sec)):
        hh, rem = divmod(o.t_sec, 3600)
        mm, ss = divmod(rem, 60)
        print(f"{o.day:>10} {f'{hh:02d}:{mm:02d}:{ss:02d}':>9} "
              f"{o.coin:>4} {o.dir:>4} "
              f"{o.ask:>3} {(o.fill_ask or 0):>4} "
              f"{o.sig_prob:>4} {o.sig_edge:>5.1f} {o.sig_trend:>+6.2f} "
              f"{o.sig_roc:>+6.1f} {o.sig_remain:>4} "
              f"{o.morning_phase or '-':>5} {o.regime or '-':>8} "
              f"{o.pnl:>+7.2f}")


if __name__ == "__main__":
    main()
