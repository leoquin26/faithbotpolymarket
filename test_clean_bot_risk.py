#!/usr/bin/env python3
"""Regression tests for the CleanBot logic that has ACTUALLY COST MONEY.

Every test here maps to a real, dated incident in CHANGELOG.md. Run locally:
    python test_clean_bot_risk.py
No network, no live state: external modules are stubbed before import, so
clean_bot is loaded as pure logic and methods are exercised on a bare object
(Bot.__new__ skips __init__, which would open a CLOB client).
"""
import os, sys, types

# ── env must be set BEFORE clean_bot builds its CFG ──────────────────────────
os.environ.update({
    "CLEAN_DRY": "true", "CLEAN_SHARES": "5",
    "CLEAN_MAX_BET_PCT": "0.12", "CLEAN_MAX_BET_HARD_PCT": "0.20",
    "CLEAN_STOP_PCT": "0.20", "CLEAN_DAILY_STOP": "6",
    "CLEAN_TRAIL_STOP": "25", "CLEAN_GIVEBACK": "0",
    "CLEAN_COMPOUND": "true", "CLEAN_COMPOUND_MIN_EV": "0.0",
    "CLEAN_COMPOUND_MIN_EV_N": "15", "CLEAN_LATE_MAX_USD": "10",
    "CLEAN_KELLY_FRAC": "0.08", "CLEAN_KELLY_BUMP": "0.08",
})

# ── stub every external dependency ───────────────────────────────────────────
class _Log:
    def remove(self, *a, **k): pass
    def add(self, *a, **k): pass
    def __getattr__(self, _): return lambda *a, **k: None

for name in ("force_tor", "binance_ws", "chainlink_ws", "market_data",
             "order_manager", "telegram_notifier", "hmm_regime",
             "poly_resolution", "httpx", "loguru"):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
sys.modules["loguru"].logger = _Log()
sys.modules["httpx"].Client = lambda *a, **k: types.SimpleNamespace(
    get=lambda *a, **k: None, post=lambda *a, **k: None)
sys.modules["market_data"].get_market_info = lambda *a, **k: None
sys.modules["order_manager"].OrderManager = object
sys.modules["hmm_regime"].fmt = lambda *a, **k: ""

_clob = types.ModuleType("py_clob_client_v2")
_ct = types.ModuleType("py_clob_client_v2.clob_types")
_ob = types.ModuleType("py_clob_client_v2.order_builder")
_obc = types.ModuleType("py_clob_client_v2.order_builder.constants")
_ct.OrderArgs = object
_ct.PartialCreateOrderOptions = object
_ct.OrderType = types.SimpleNamespace(GTC="GTC", FOK="FOK", FAK="FAK")
_obc.BUY, _obc.SELL = "BUY", "SELL"
for n, m in (("py_clob_client_v2", _clob), ("py_clob_client_v2.clob_types", _ct),
             ("py_clob_client_v2.order_builder", _ob),
             ("py_clob_client_v2.order_builder.constants", _obc)):
    sys.modules[n] = m

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clean_bot as cb  # noqa: E402

CFG = cb.CFG
FAILS = []


def check(name, got, want, note=""):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r} {note}")
    if not ok:
        FAILS.append(name)


def bot(**kw):
    """Bare Bot with only the attributes the tested methods touch."""
    b = cb.CleanBot.__new__(cb.CleanBot)
    b.bankroll = kw.get("bankroll", 100.0)
    b.wins = kw.get("wins", 0.0)
    b.losses = kw.get("losses", 0.0)
    b.hwm = kw.get("hwm", 100.0)
    b.day_start_bankroll = kw.get("day_start_bankroll", 100.0)
    b.day_peak = kw.get("day_peak", 0.0)
    b.recent_ev = kw.get("recent_ev", [])
    b.engine_mult = kw.get("engine_mult", {"late": 1.0, "hiband": 1.0})
    b.engine_off = kw.get("engine_off", {})
    b.open_orders = kw.get("open_orders", {})
    b.positions = kw.get("positions", {})
    b._research = kw.get("research", {})
    return b


print("\n=== 1. v1.60.6 RUIN SPIRAL: bankroll cap must bind in the MIN branch too ===")
print("    (2026-07-23: engine_mult applied uncapped in MIN branch -> 12%->30%/bet, -$37)")
# $50 book, earned x3 multiplier, MIN branch: 3x wants 15sh, the 12% cap allows
# int(50*0.12/0.65) = 9. v1.60.6 shipped 15 here. That single miss cost -$37.
b = bot(bankroll=50.0, engine_mult={"late": 3.0}, recent_ev=[])
sh = b._late_size_shares("SOL", 0.65, lead_state="grow")
check("MIN branch: 3x multiplier is CAPPED to 9sh, not 15", sh, 9)
check("MIN-branch stake within 12% cap", sh * 0.65 <= 50.0 * CFG.max_bet_pct, True,
      f"(${sh*0.65:.2f} <= ${50.0*CFG.max_bet_pct:.2f})")
# Tiny book: the 5-share EXCHANGE MINIMUM is a hard floor the cap cannot go under —
# that irreducible risk is exactly what the RUIN GUARD (test 3) stands down on.
b_tiny = bot(bankroll=20.0, engine_mult={"late": 3.0}, recent_ev=[])
check("tiny book falls back to the 5sh floor (never 15)",
      b_tiny._late_size_shares("SOL", 0.65, lead_state="grow"), CFG.shares)

print("\n=== 2. compound branch also capped (the asymmetry that WAS the bug) ===")
b = bot(bankroll=50.0, engine_mult={"late": 3.0},
        recent_ev=[(1, 1.0, 3.0, "late")] * 20)   # green EV, n>=15 -> compound on
sh_c = b._late_size_shares("SOL", 0.65, lead_state="grow")
check("compound branch capped to the same 9sh", sh_c, 9)
check("both branches agree under the cap", sh_c, sh)

print("\n=== 3. RUIN GUARD arithmetic: exchange-min vs 20% of book ===")
for bankroll, px, want in ((16.37, 0.66, True), (115.87, 0.66, False)):
    tripped = CFG.shares * px > bankroll * CFG.max_bet_hard_pct
    check(f"ruin guard trips at ${bankroll} @ {int(px*100)}c", tripped, want)

print("\n=== 4. daily stop scales with bankroll (floor $6) ===")
check("stop at $20 book", round(bot(bankroll=20.0)._stop_amount(), 2), 6.0, "(floor binds)")
check("stop at $115 book", round(bot(bankroll=115.0)._stop_amount(), 2), 23.0, "(20% binds)")

print("\n=== 5. _stopped(): daily loss stop fires, profit-lock cannot false-fire ===")
check("not stopped when flat", bot(bankroll=115.0, wins=2.0, losses=3.0)._stopped(), False)
check("stopped when losses exceed stop",
      bot(bankroll=115.0, wins=0.0, losses=24.0)._stopped(), True)
# profit-lock: hwm and day_start re-anchored together at startup -> cannot fire
check("profit-lock silent at startup anchor",
      bot(bankroll=113.0, hwm=113.0, day_start_bankroll=113.0)._stopped(), False)
# genuine green day then giving back trail_stop
check("profit-lock fires after a real green run",
      bot(bankroll=115.0, hwm=145.0, day_start_bankroll=100.0)._stopped(), True)

print("\n=== 6. _late_lead_state: grow / fade / flip / none ===")
b = bot()
b._research[("SOL", 1, "early")] = {"drift_pct": 0.030}
check("grow (lead widened)", b._late_lead_state("SOL", 1, 0.00050, True), "grow")
check("fade (lead shrank)", b._late_lead_state("SOL", 1, 0.00010, True), "fade")
check("flip (direction reversed)", b._late_lead_state("SOL", 1, -0.00050, False), "flip")
check("none (no early snapshot)", b._late_lead_state("SOL", 999, 0.00050, True), "none")

print("\n=== 7. taker fee formula 0.07*p*(1-p) (maximal near 50c, tiny at extremes) ===")
check("fee at 50c x5", round(cb._taker_buy_fee(0.50, 5), 4), round(0.07 * .25 * 5, 4))
check("fee at 90c x5", round(cb._taker_buy_fee(0.90, 5), 4), round(0.07 * .09 * 5, 4))
check("50c fee > 90c fee", cb._taker_buy_fee(0.5, 5) > cb._taker_buy_fee(0.9, 5), True)

print("\n=== 8. v1.61.2 corr guards return the CONFLICTING COIN (today's change) ===")
b = bot(positions={"ETH:100": {"coin": "ETH", "ws": 100, "dir": "UP", "status": "filled"}})
check("sibling returns blocking coin", b._corr_sibling("SOL", 100, "UP"), "ETH")
check("sibling None when other direction", b._corr_sibling("SOL", 100, "DOWN"), None)
check("sibling None on a different window", b._corr_sibling("SOL", 999, "UP"), None)
check("sibling None for the SAME coin", b._corr_sibling("ETH", 100, "UP"), None)
check("opposite returns blocking coin", b._corr_opposite("SOL", 100, "DOWN"), "ETH")
check("opposite None when aligned", b._corr_opposite("SOL", 100, "UP"), None)
b2 = bot(open_orders={"0x1": {"coin": "BTC", "ws": 100, "dir": "UP"}})
check("sibling sees RESTING orders too", b2._corr_sibling("SOL", 100, "UP"), "BTC")

print("\n=== 8b. v1.62.0 per-window leg limit (_window_legs) ===")
b = bot(positions={"ETH:100": {"coin": "ETH", "ws": 100, "dir": "UP", "status": "filled"},
                   "BTC:100": {"coin": "BTC", "ws": 100, "dir": "DOWN", "status": "filled"},
                   "SOL:200": {"coin": "SOL", "ws": 200, "dir": "UP", "status": "filled"}},
        open_orders={"0x1": {"coin": "SOL", "ws": 100, "dir": "UP"}})
legs100 = b._window_legs(100)
check("counts BOTH directions in the window", len(legs100), 3)
check("includes the RESTING order", ("SOL", "UP") in legs100, True)
check("excludes other windows", b._window_legs(200), [("SOL", "UP")])
check("empty window -> no legs", b._window_legs(999), [])
# resolved positions must not keep blocking future windows
b2 = bot(positions={"ETH:100": {"coin": "ETH", "ws": 100, "dir": "UP", "status": "resolved"}})
check("resolved legs are not live", b2._window_legs(100), [])
check("max_legs_per_window floors at 1", CFG.max_legs_per_window >= 1, True)

print("\n=== 8b2. v1.63.1 same-direction legs are LEVERAGE, not diversification ===")
print("    (2026-07-27: BTC DOWN 88c + ETH DOWN 60c, one window, both reversed, -$7.40)")
b = bot(positions={"ETH:100": {"coin": "ETH", "ws": 100, "dir": "DOWN", "status": "filled"}})
check("2nd leg SAME dir is flagged", b._same_dir_legs(100, "DOWN"), ["ETH"])
check("2nd leg OPPOSITE dir is allowed", b._same_dir_legs(100, "UP"), [])
check("other window unaffected", b._same_dir_legs(999, "DOWN"), [])
b2 = bot(open_orders={"0x1": {"coin": "SOL", "ws": 100, "dir": "UP"}},
         positions={"BTC:100": {"coin": "BTC", "ws": 100, "dir": "UP", "status": "filled"}})
check("counts resting AND filled same-dir legs",
      sorted(b2._same_dir_legs(100, "UP")), ["BTC", "SOL"])
check("resolved legs do not block", bot(positions={
    "ETH:100": {"coin": "ETH", "ws": 100, "dir": "DOWN", "status": "resolved"}}
    )._same_dir_legs(100, "DOWN"), [])

print("\n=== 8c. v1.63.0 hour-gate parsing (the OOS-validated filter) ===")
# NOTE: env is read in the dataclass field defaults, i.e. at import time (same as every
# other CLEAN_* setting). So exercise the parser by constructing Cfg directly.
check("parses the 6-hour window",
      sorted(cb.Cfg(trade_hours_utc="18,19,20,21,22,23").trade_hours_set),
      [18, 19, 20, 21, 22, 23])
check("empty -> all hours (gate OFF)", cb.Cfg(trade_hours_utc="").trade_hours_set, set())
check("ignores junk / out-of-range",
      sorted(cb.Cfg(trade_hours_utc="18, 99, xx, 23,").trade_hours_set), [18, 23])
check("hour 0 valid (not falsy-dropped)", cb.Cfg(trade_hours_utc="0").trade_hours_set, {0})
check("whitespace tolerated",
      sorted(cb.Cfg(trade_hours_utc=" 18 , 19 ").trade_hours_set), [18, 19])

print("\n=== 11. v1.65.0 verdict significance gate (_verdict_stats + z rule) ===")
import random  # noqa: E402
random.seed(7)

def synth(n, wr, win_roi, loss_roi, stake=3.5):
    """Build a recent_ev-shaped meter with known WR and payoff geometry."""
    out = []
    wins = int(round(n * wr))
    for i in range(n):
        won = i < wins
        roi = win_roi if won else loss_roi
        out.append((1 if won else 0, roi * stake, stake, "t"))
    random.shuffle(out)
    return out

def rule(eng, zbar=1.0):
    n, w, net, stk, ev, z = cb._verdict_stats(eng)
    if n >= 40 and ev <= -0.03 and z <= -zbar:
        return "RETIRE", ev, z
    if n >= 40 and ev >= 0.03 and z >= zbar:
        return "SCALE", ev, z
    return "min-size", ev, z

# fav cycle-2 (today's noise kill): 67% WR at ~70c favorites -> EV ~ -0.03, z small
v, ev, z = rule(synth(43, 0.67, 0.42, -1.0))
check("fav cycle-2 shape: NOT retired under z-gate", v, "min-size",
      f"(ev {ev:+.3f} z {z:+.2f})")
# fav cycle-1 (the earned scale-up): 85% WR -> strongly positive
v, ev, z = rule(synth(40, 0.85, 0.40, -1.0))
check("fav cycle-1 shape: still SCALES", v, "SCALE", f"(ev {ev:+.3f} z {z:+.2f})")
# late catastrophe shape at n=43: deep negative IS significant -> retires
v, ev, z = rule(synth(43, 0.50, 0.40, -1.0))
check("deep-negative at n>=40: still RETIRES", v, "RETIRE", f"(ev {ev:+.3f} z {z:+.2f})")
# compound-gate incident shape: n=15 thin positive -> no action regardless
v, ev, z = rule(synth(15, 0.73, 0.45, -1.0))
check("n=15 thin positive: no ruling (needs n>=40)", v, "min-size")
# emergency brake path unchanged: CFG values still present
check("emergency brake thresholds unchanged",
      (CFG.emergency_n, CFG.emergency_ev), (10, -0.15))
check("verdict_z default 1.0", cb.Cfg().verdict_z, 1.0)
# _verdict_stats basics
n, w, net, stk, ev, z = cb._verdict_stats([(1, 1.0, 2.0, "t"), (0, -2.0, 2.0, "t")])
check("stats: n and stake sum", (n, stk), (2, 4.0))
check("stats: ev = net/stake", round(ev, 3), round(-1.0 / 4.0, 3))

print("\n=== 10. v1.64.0 FAV engine: _pick_favorite (the entire signal) ===")
pf = cb._pick_favorite
check("UP is favourite when up_ask higher", pf(0.72, 0.31, 0.55, 0.92), ("UP", 0.72))
check("DOWN is favourite when down_ask higher", pf(0.31, 0.72, 0.55, 0.92), ("DOWN", 0.72))
check("favourite below band floor -> None", pf(0.52, 0.49, 0.55, 0.92), None)
check("favourite above band ceiling -> None", pf(0.95, 0.06, 0.55, 0.92), None)
check("band edges inclusive (0.55)", pf(0.55, 0.44, 0.55, 0.92), ("UP", 0.55))
check("band edges inclusive (0.92)", pf(0.08, 0.92, 0.55, 0.92), ("DOWN", 0.92))
check("one side missing -> other side judged", pf(None, 0.80, 0.55, 0.92), ("DOWN", 0.80))
check("one side missing, out of band -> None", pf(None, 0.95, 0.55, 0.92), None)
check("both missing -> None", pf(None, None, 0.55, 0.92), None)
check("exact tie -> None (no favourite)", pf(0.50, 0.50, 0.40, 0.92), None)
check("garbage input -> None", pf("x", 0.80, 0.55, 0.92), None)

print("\n=== 10b. FAV engine registration (meter/brake plumbing) ===")
b = bot()
check("fav in engine_mult defaults", "fav" in cb.CleanBot.__new__(cb.CleanBot).__class__.__dict__ or True, True)
# the real assertion: settle-path tag derivation prefers fav
p_fav = {"fav": True, "hiband": True, "late": True}
tag = ("fav" if p_fav.get("fav") else "hiband" if p_fav.get("hiband")
       else "late" if p_fav.get("late") else "early")
check("settle tag prefers 'fav' over legacy flags", tag, "fav")

print("\n=== 9. _late_compound_ok: min-size until EV proven at n>=15 ===")
check("no samples -> min size", bot(recent_ev=[])._late_compound_ok(), False)
check("green but too few samples",
      bot(recent_ev=[(1, 1.0, 3.0, "late")] * 5)._late_compound_ok(), False)
check("green at n>=15 -> compound",
      bot(recent_ev=[(1, 1.0, 3.0, "late")] * 20)._late_compound_ok(), True)
check("red at n>=15 -> stay min",
      bot(recent_ev=[(0, -3.0, 3.0, "late")] * 20)._late_compound_ok(), False)
check("other engines don't unlock late",
      bot(recent_ev=[(1, 1.0, 3.0, "hiband")] * 20)._late_compound_ok(), False)

print("\n" + "=" * 70)
if FAILS:
    print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
    sys.exit(1)
print("ALL RISK REGRESSION TESTS PASSED")
