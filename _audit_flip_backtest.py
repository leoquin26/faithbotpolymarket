#!/usr/bin/env python3
"""
Flip-backtest: for every trade in the last N days, compute:
- EXHAUST score at signal time
- Original action (CLEAN/DAMPEN/ABSTAIN/FLIP)
- Outcome (WIN/LOSS)
- Would FLIPping have changed the outcome?
"""
import re
import os
import glob

LOG_DIR = "logs"

SIG_RE = re.compile(r"(\d\d:\d\d:\d\d).*\[SIGNAL\] (\w+) (UP|DOWN) \| Prob=(\d+)% \| Ask=(\d+)c \| Edge=([\d.]+)%")
EXH_RE = re.compile(r"(\d\d:\d\d:\d\d).*\[EXHAUST\] (\w+) (UP|DOWN) @ \d+c \| score=([\d.]+) raw=(\w+) action=(\w+)")
ORD_RE = re.compile(r"(\d\d:\d\d:\d\d).*\[ORDER\] (\w+) (UP|DOWN) \| FOK @ (\d+)c \| (\d+) shares.*cost=\$([\d.]+)")
RES_RE = re.compile(r"(\d\d:\d\d:\d\d).*\[RESOLVE POLY\] (\w+) (UP|DOWN): outcomePrice=([\d.]+).*-> (WIN|LOSS)")


def parse_day(filepath):
    sigs, exhs, orders, results = [], [], [], []
    try:
        with open(filepath) as f:
            for line in f:
                m = SIG_RE.search(line)
                if m:
                    sigs.append((m.group(1), m.group(2), m.group(3), int(m.group(4)), int(m.group(5)), float(m.group(6))))
                m = EXH_RE.search(line)
                if m:
                    exhs.append((m.group(1), m.group(2), m.group(3), float(m.group(4)), m.group(5), m.group(6)))
                m = ORD_RE.search(line)
                if m:
                    orders.append((m.group(1), m.group(2), m.group(3), int(m.group(4)), int(m.group(5)), float(m.group(6))))
                m = RES_RE.search(line)
                if m:
                    results.append((m.group(1), m.group(2), m.group(3), float(m.group(4)), m.group(5)))
    except FileNotFoundError:
        return []

    trades = []
    for o_time, o_coin, o_dir, o_ask, o_shares, o_cost in orders:
        outcome = None
        for r_time, r_coin, r_dir, r_op, r_wl in results:
            if r_coin == o_coin and r_dir == o_dir and r_time > o_time:
                outcome = (r_wl, r_op)
                break
        if outcome is None:
            continue

        last_exh = None
        for e_time, e_coin, e_dir, e_score, e_raw, e_act in reversed(exhs):
            if e_coin == o_coin and e_dir == o_dir and e_time <= o_time:
                last_exh = (e_score, e_raw, e_act)
                break

        last_sig = None
        for s_time, s_coin, s_dir, s_prob, s_ask, s_edge in reversed(sigs):
            if s_coin == o_coin and s_dir == o_dir and s_time <= o_time:
                last_sig = (s_prob, s_ask, s_edge)
                break

        trades.append({
            "date": os.path.basename(filepath).replace("bot_", "").replace(".log", ""),
            "time": o_time,
            "coin": o_coin,
            "dir": o_dir,
            "ask": o_ask,
            "shares": o_shares,
            "cost": o_cost,
            "win_loss": outcome[0],
            "outcome_price": outcome[1],
            "exh_score": last_exh[0] if last_exh else None,
            "exh_raw": last_exh[1] if last_exh else None,
            "exh_action": last_exh[2] if last_exh else None,
            "sig_prob": last_sig[0] if last_sig else None,
            "sig_edge": last_sig[2] if last_sig else None,
        })
    return trades


all_trades = []
for d in sorted(glob.glob(f"{LOG_DIR}/bot_2026-05-*.log")) + sorted(glob.glob(f"{LOG_DIR}/bot_2026-06-*.log")):
    all_trades.extend(parse_day(d))

print(f"=== {len(all_trades)} trades found in logs (last all available days) ===\n")
header = "{date:<12} {time:<10} {coin:<4} {dir:<5} {ask:<4} {cost:<6} {res:<6} {exh:<6} {raw:<10} {action:<10} {flip:<10}"
print(header.format(date="Date", time="Time", coin="Coin", dir="Dir", ask="Ask", cost="Cost",
                     res="Result", exh="Exh", raw="Raw", action="Action", flip="IfFlipped"))
print("-" * 110)

original_pnl = 0.0
flip_pnl = 0.0
abstain_zone_count = 0
abstain_zone_wins_if_flipped = 0
flip_zone_count = 0
flip_zone_wins_if_flipped = 0
dampen_zone_count = 0

for t in all_trades:
    if t["win_loss"] == "WIN":
        orig_pnl = (1.00 - t["ask"] / 100.0) * t["shares"]
        flip_outcome = "LOSS"
        flip_p = -t["cost"]
    else:
        orig_pnl = -t["cost"]
        flip_outcome = "WIN"
        flip_ask = 100 - t["ask"]
        flip_p = (1.00 - flip_ask / 100.0) * t["shares"]

    original_pnl += orig_pnl
    flip_pnl += flip_p

    exh_score = t["exh_score"] or 0.0
    in_dampen_zone = 0.30 <= exh_score < 0.50
    in_abstain_zone = 0.50 <= exh_score < 0.70
    in_flip_zone = exh_score >= 0.70

    if in_dampen_zone:
        dampen_zone_count += 1
    if in_abstain_zone:
        abstain_zone_count += 1
        if flip_outcome == "WIN":
            abstain_zone_wins_if_flipped += 1
    if in_flip_zone:
        flip_zone_count += 1
        if flip_outcome == "WIN":
            flip_zone_wins_if_flipped += 1

    print(header.format(
        date=t["date"], time=t["time"], coin=t["coin"], dir=t["dir"],
        ask=str(t["ask"]) + "c", cost=f"${t['cost']:.2f}",
        res=t["win_loss"], exh=f"{exh_score:.2f}",
        raw=(t["exh_raw"] or "-"), action=(t["exh_action"] or "-"),
        flip=flip_outcome,
    ))

print("\n" + "=" * 110)
print(f"\nORIGINAL DIRECTION P&L:  ${original_pnl:+.2f}")
print(f"FLIP-ALL-TRADES   P&L:   ${flip_pnl:+.2f}")
print(f"DELTA (flip - orig):     ${flip_pnl - original_pnl:+.2f}")
print()
print(f"Trades broken down by EXHAUST score zone:")
print(f"  CLEAN     (<0.30): {sum(1 for t in all_trades if (t['exh_score'] or 0) < 0.30)}")
print(f"  DAMPEN (0.30-0.50): {dampen_zone_count}")
print(f"  ABSTAIN(0.50-0.70): {abstain_zone_count}  --> wins if flipped: {abstain_zone_wins_if_flipped}/{abstain_zone_count}")
print(f"  FLIP      (>=0.70): {flip_zone_count}    --> wins if flipped: {flip_zone_wins_if_flipped}/{flip_zone_count}")
print()
print("Threshold sweep — if TH_FLIP = X, what would have been the P&L?")
for th in [0.55, 0.60, 0.65, 0.70]:
    p = 0.0
    flipped_count = 0
    for t in all_trades:
        score = t["exh_score"] or 0.0
        if t["win_loss"] == "WIN":
            orig_p = (1.00 - t["ask"] / 100.0) * t["shares"]
        else:
            orig_p = -t["cost"]
        if score >= th:
            flipped_count += 1
            if t["win_loss"] == "WIN":
                p += -t["cost"]
            else:
                flip_ask = 100 - t["ask"]
                p += (1.00 - flip_ask / 100.0) * t["shares"]
        else:
            p += orig_p
    print(f"  TH_FLIP={th:.2f}: P&L=${p:+.2f}  ({flipped_count} of {len(all_trades)} trades flipped)")
