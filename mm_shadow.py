#!/usr/bin/env python3
"""MM SHADOW — the DEFENSIVE MAKER, paper only ($0). BTC 15-minute market.

Something of our own, born from our tape: in these markets a resting bid that
gets filled alone loses 97-99% of the time (mm_replay 2026-09-01) — the fill IS
the resolution arriving in the book. So this engine does what no copied bot
does: it quotes both sides to earn liquidity rewards + rebates + spread, and
PULLS a side the moment the 1Hz book shows the sweep forming against it.

Per window (ws = 15m boundary):
  - quote 50 sh (rewards min size) on UP and DOWN at best bid, inside the
    1.5c rewards band, from open+60s until T-120s
  - triggers (config): ask-drop on our side, bid-rise on the other side, top-2
    depth collapse, and the Φ digital fair value moving against our price
  - pulled sides re-quote after CALM seconds without triggers (to keep
    reward-minutes), never inside the last 120s
  - paper fills = price-through proxy (best ask <= our bid); mark-outs at
    +30s/+60s (mid) and at settlement (Gamma outcomePrices)
  - per-window ledger: reward-qualified seconds, est. Q-share & reward $,
    fills, rebate est., PnL at 50 sh — everything the gate needs
Places NO orders. Touches NO keys. Do not start while another live/paper
hypothesis is on the clock (AGENT_STATUS §0, CYCLE_LAW gate-first).
"""
from __future__ import annotations

import json, math, os, sys, time, urllib.request
from collections import deque

V3 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, V3)

try:
    import binance_ws
except Exception:                       # local dev without the feed
    binance_ws = None
try:
    import telegram_notifier as tg
except Exception:
    tg = None
try:
    from polymarket_ws import get_singleton as poly_ws   # 1Hz book feed (box only)
except Exception:
    poly_ws = None
from research_brain.digital import p_up

STATE = os.path.join(V3, "mm_shadow_state.json")
SEAT_ID = "MM-defensive-btc15m"
COIN, SYM, SLUG = "BTC", "BTCUSDT", "btc-updown-15m-{ws}"
WLEN = 900
SHARES = 50                               # rewards min size on this market
BAND = 0.015                              # rewards max spread (1.5c) from mid
POOL_PER_WINDOW = 7500 / 96               # $7,500/day BTC-15m (verify daily)
# LAB VERDICT (mm_lab on 5.05M snapshots, 2026-09-02): in the LAST 5 MINUTES no
# pull rule crosses zero — best rule net -4.5%/$ vs -6.5% baseline (single-fill
# toxicity drops from -19c to -3c/share but single fills become 70% of windows).
# The last 5 minutes are unquotable at 1Hz reaction speed. So: farm rewards in
# minutes 1-10 and go FLAT at T-300s; triggers stay as a safety net. Whether
# minutes 1-10 are benign is UNMEASURED on our tape — this bot exists to measure it.
T_START, T_PULL_ALL = WLEN - 60, 300      # quote from open+60s, flat by T-300s
MAX_SPREAD = 0.03
CALM_S = 20                               # re-quote after this many quiet seconds
TRIG = {"askdrop": (1, 3), "otherbid": (1, 3), "depth": (0.5, 5),
        "model_margin": 0.10, "sigma_lookback": 180}   # 0.04 thrashed (9/16 pulls in 3h)
MID_LO, MID_HI = 0.20, 0.80   # quote only while BOTH sides are live probabilities;
                              # v1 quoted at 85-91c and got run over (paper -7.50 / 12 windows)
RATE, REBATE = 0.07, 0.20


def log(m):
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + m, flush=True)


def rest_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "mm-shadow"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def discover(ws):
    """(market_id, {UP: token, DOWN: token}) for this window, or None."""
    try:
        d = rest_json("https://gamma-api.polymarket.com/markets?slug=" + SLUG.format(ws=ws))
    except Exception:
        return None
    if not d:
        return None
    m = d[0]
    ids, outs = m.get("clobTokenIds"), m.get("outcomes")
    if isinstance(ids, str): ids = json.loads(ids)
    if isinstance(outs, str): outs = json.loads(outs)
    if not (ids and outs and len(ids) == 2):
        return None
    return m.get("id"), dict(zip([o.upper() for o in outs], ids))


def book(token):
    """(bids[(px,sz)...], asks[...]) sorted best-first, or None."""
    try:
        bk = rest_json("https://clob.polymarket.com/book?token_id=" + token)
        bids = sorted(((float(b["price"]), float(b["size"])) for b in bk.get("bids", [])
                       if 0.01 < float(b["price"]) < 0.99), reverse=True)
        asks = sorted(((float(a["price"]), float(a["size"])) for a in bk.get("asks", [])
                       if 0.01 < float(a["price"]) < 0.99))
        return bids, asks
    except Exception:
        return None


def winner(market_id):
    try:
        m = rest_json(f"https://gamma-api.polymarket.com/markets/{market_id}")
        op, outs = m.get("outcomePrices"), m.get("outcomes")
        if isinstance(op, str): op = json.loads(op)
        if isinstance(outs, str): outs = json.loads(outs)
        if op and outs and len(op) == 2:
            hi = 0 if float(op[0]) > 0.5 else 1
            if abs(float(op[hi]) - 1.0) < 0.05:
                return outs[hi].upper()
    except Exception:
        pass
    return None


def candle_open(ws):
    try:
        k = rest_json(f"https://api.binance.com/api/v3/klines?symbol={SYM}&interval=15m"
                      f"&startTime={ws*1000}&limit=1")
        return float(k[0][1]) if k else None
    except Exception:
        return None


def S(spread_c):                          # rewards quadratic score
    v = BAND * 100
    return max(0.0, (v - spread_c) / v) ** 2


def q_share(up_bids, dn_bids, mid, our_px_up, our_px_dn):
    """our per-sample share of the (visible) two-sided Q pool"""
    comp = 0.0
    for px, sz in up_bids:
        s = (mid - px) * 100
        if 0 <= s <= BAND * 100: comp += S(s) * sz
    for px, sz in dn_bids:
        s = ((1 - mid) - px) * 100
        if 0 <= s <= BAND * 100: comp += S(s) * sz
    ours = min(S((mid - our_px_up) * 100), S(((1 - mid) - our_px_dn) * 100)) * SHARES
    return ours / (ours + comp) if ours + comp > 0 else 0.0


class Window:
    def __init__(self, ws, market_id, toks, opn):
        self.ws, self.mid_, self.toks, self.opn = ws, market_id, toks, opn
        self.q = {"UP": None, "DOWN": None}          # resting paper bid px
        self.fill = {"UP": None, "DOWN": None}       # dict when filled
        self.pulled_at = {"UP": None, "DOWN": None}
        self.last_trig = {"UP": 0.0, "DOWN": 0.0}
        self.hist = {"UP": deque(), "DOWN": deque()} # (t, bb, ba, depth2)
        self.qualified_s = 0
        self.shares_sum, self.share_n = 0.0, 0
        self.pulls = {"askdrop": 0, "otherbid": 0, "depth": 0, "model": 0}
        self.settled = False

    def other(self, o): return "DOWN" if o == "UP" else "UP"


_depth_cache = {"t": 0.0, "bk": None}


def step(win: Window, now, ws=None):
    tr = win.ws + WLEN - now
    if tr <= 0 or tr > T_START:
        return
    # depth (for Q-share + depth trigger) via REST every 10s; top-of-book via WS each loop
    if now - _depth_cache["t"] > 10 or _depth_cache["bk"] is None or _depth_cache.get("ws") != win.ws:
        _depth_cache.update(t=now, ws=win.ws, bk={o: book(win.toks[o]) for o in ("UP", "DOWN")})
    bk = _depth_cache["bk"]
    if not bk or not all(bk.values()) or not bk["UP"][0] or not bk["UP"][1]:
        return
    top = {}
    for o in ("UP", "DOWN"):
        bids, asks = bk[o]
        bb = bids[0][0] if bids else None
        ba = asks[0][0] if asks else None
        if ws is not None:
            try:
                b = ws.get_book(win.toks[o])
                if b and b.get("bid") and b.get("ask"):
                    bb, ba = float(b["bid"]), float(b["ask"])
            except Exception:
                pass
        dep = sum(sz for _, sz in bids[:2])
        top[o] = (bb, ba, dep)
        h = win.hist[o]; h.append((now, bb, ba, dep))
        while h and now - h[0][0] > 12: h.popleft()
    if not (top["UP"][0] and top["UP"][1] and top["DOWN"][0] and top["DOWN"][1]):
        return
    if ws is not None and not all(ws.get_book(win.toks[o]) for o in ("UP", "DOWN")):
        return                      # WS not warmed up: never quote from a stale REST top
    if not (0.90 <= top["UP"][0] + top["DOWN"][0] <= 1.00):
        return                      # inconsistent two-token snapshot (fast move): skip loop
    mid = (top["UP"][0] + top["UP"][1]) / 2
    if not (MID_LO <= mid <= MID_HI):
        for o in ("UP", "DOWN"):            # market decided enough: stay flat
            if win.q[o] is not None and not win.fill[o]:
                win.q[o] = None
        return
    fair = None
    if binance_ws and win.opn:
        spot = binance_ws.get_price(COIN)
        sig = binance_ws.get_realized_vol(COIN, TRIG["sigma_lookback"])
        if spot and sig:
            fair = p_up(spot, win.opn, sig, tr)

    for o in ("UP", "DOWN"):
        if win.fill[o]:
            f = win.fill[o]                              # mark-outs
            m_o = mid if o == "UP" else 1 - mid
            for tag, dt in (("mo30", 30), ("mo60", 60)):
                if tag not in f and now - f["t"] >= dt:
                    f[tag] = round(m_o - f["px"], 4)
            continue
        bb, ba, dep = top[o]
        if not (bb and ba) or (ba - bb) > MAX_SPREAD:
            continue
        # flat before the end
        if tr <= T_PULL_ALL:
            if win.q[o] is not None:
                win.q[o] = None
            continue
        # ---- triggers (evaluated even while pulled, to extend the calm timer)
        trig = None
        h = win.hist[o]
        k, w = TRIG["askdrop"]
        past = [x for x in h if w <= now - x[0] <= w + 2]
        if past and past[-1][2] and ba and past[-1][2] - ba >= k * 0.01 - 1e-9:
            trig = "askdrop"
        k, w = TRIG["otherbid"]
        oh = win.hist[win.other(o)]
        opast = [x for x in oh if w <= now - x[0] <= w + 2]
        obb = top[win.other(o)][0]
        if not trig and opast and opast[-1][1] and obb and obb - opast[-1][1] >= k * 0.01 - 1e-9:
            trig = "otherbid"
        r, w = TRIG["depth"]
        past = [x for x in h if w <= now - x[0] <= w + 2]
        if not trig and past and past[-1][3] and dep <= r * past[-1][3]:
            trig = "depth"
        if not trig and fair is not None and win.q[o] is not None:
            f_o = fair if o == "UP" else 1 - fair
            if f_o < win.q[o] - TRIG["model_margin"]:
                trig = "model"
        if trig:
            win.last_trig[o] = now
            if win.q[o] is not None:
                # same-second fill check first: too late to pull
                if ba <= win.q[o]:
                    win.fill[o] = {"t": now, "px": win.q[o], "tr": tr, "how": "through@trigger"}
                    log(f"[MM FILL] {o} @ {win.q[o]*100:.0f}c (trigger {trig} same second) T={tr:.0f}s")
                else:
                    win.pulls[trig] += 1
                    win.pulled_at[o] = now
                    log(f"[MM PULL] {o} {win.q[o]*100:.0f}c — {trig} T={tr:.0f}s")
                    win.q[o] = None
            continue
        # ---- fill proxy on a resting quote
        if win.q[o] is not None and ba <= win.q[o]:
            win.fill[o] = {"t": now, "px": win.q[o], "tr": tr, "how": "through"}
            log(f"[MM FILL] {o} @ {win.q[o]*100:.0f}c T={tr:.0f}s")
            continue
        # ---- (re)quote: join best bid if not resting and calm
        if win.q[o] is None and now - win.last_trig[o] >= CALM_S:
            win.q[o] = bb
        elif win.q[o] is not None and bb > win.q[o]:
            win.q[o] = bb                                # stay at the touch

    # ---- rewards accounting (per second; Polymarket samples per minute)
    if win.q["UP"] is not None and win.q["DOWN"] is not None:
        if abs(mid - win.q["UP"]) <= BAND and abs((1 - mid) - win.q["DOWN"]) <= BAND:
            win.qualified_s += 1
            if win.qualified_s % 10 == 0:
                sh = q_share(bk["UP"][0], bk["DOWN"][0], mid, win.q["UP"], win.q["DOWN"])
                win.shares_sum += sh; win.share_n += 1


def settle(win: Window, s):
    wn = winner(win.mid_)
    if not wn:
        return False
    legs, pnl, reb = [], 0.0, 0.0
    for o, f in win.fill.items():
        if not f:
            continue
        won = wn == o
        leg = ((1 - f["px"]) if won else -f["px"]) * SHARES
        pnl += leg
        reb += REBATE * RATE * f["px"] * (1 - f["px"]) * SHARES
        legs.append({"o": o, "px": f["px"], "won": won, "pnl": round(leg, 2),
                     "tr": round(f["tr"]), "how": f["how"],
                     "mo30": f.get("mo30"), "mo60": f.get("mo60")})
    share = win.shares_sum / win.share_n if win.share_n else 0.0
    est_reward = share * POOL_PER_WINDOW * (win.qualified_s / (WLEN - 60 - T_PULL_ALL))
    rec = {"ws": win.ws, "winner": wn, "legs": legs, "pnl": round(pnl, 2),
           "rebate": round(reb, 3), "qualified_s": win.qualified_s,
           "q_share": round(share, 4), "est_reward": round(est_reward, 3),
           "pulls": win.pulls, "seat": SEAT_ID}
    s["windows"].append(rec)
    tot = sum(x["pnl"] + x["rebate"] + x["est_reward"] for x in s["windows"])
    log(f"[MM SETTLE] ws={win.ws} -> {wn} | legs {len(legs)} pnl {pnl:+.2f} reb {reb:+.3f} "
        f"reward~{est_reward:+.2f} (share {share*100:.1f}% qual {win.qualified_s}s) "
        f"pulls {win.pulls} | cum {tot:+.2f} n={len(s['windows'])}")
    if tg and (legs or len(s["windows"]) % 4 == 0):
        try:
            tg._send(f"🧱 <b>MM SHADOW</b> ws={time.strftime('%H:%M', time.gmtime(win.ws))} "
                     f"→ {wn} | fills {len(legs)} pnl {pnl:+.2f} | reward~{est_reward:+.2f} "
                     f"| cum {tot:+.2f} n={len(s['windows'])} — $0",
                     dedup_key=f"mm-{win.ws}")
        except Exception:
            pass
    return True


def main():
    try:
        s = json.load(open(STATE))
    except Exception:
        s = {"seat": SEAT_ID, "windows": []}
    if binance_ws:
        binance_ws.start()
    feed = None
    if poly_ws:
        try:
            feed = poly_ws()
        except Exception:
            feed = None
    log(f"=== MM SHADOW v2 start | {SEAT_ID} | {SHARES}sh both sides, band {BAND*100:.1f}c, "
        f"mid {MID_LO}-{MID_HI}, flat at T-{T_PULL_ALL}s | triggers {TRIG} | feed "
        f"{'WS' if feed else 'REST'} | $0 | n={len(s['windows'])} ===")
    cur: Window | None = None
    pending: list[Window] = []
    while True:
        now = time.time()
        ws = int(now // WLEN) * WLEN
        try:
            if cur is None or cur.ws != ws:
                if cur is not None:
                    pending.append(cur)
                cur = None
                d = discover(ws)
                if d:
                    cur = Window(ws, d[0], d[1], candle_open(ws))
                    if feed:
                        try:
                            feed.set_subscriptions(list(d[1].values()))
                        except Exception:
                            pass
                    log(f"[MM WINDOW] {ws} market {d[0]} open {cur.opn}")
            if cur:
                step(cur, now, feed)
            for w in list(pending):
                if now > w.ws + WLEN + 45 and settle(w, s):
                    pending.remove(w)
                    json.dump(s, open(STATE, "w"))
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(1.0)


if __name__ == "__main__":
    main()
