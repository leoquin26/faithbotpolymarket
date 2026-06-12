"""V9: Kill the EXHAUST OVERRIDES that are causing systematic losses.

Audit (May 4-13, 53 resolved 15m trades, -$20.36 net PnL):
  EXHAUST raw=ALLOW:     0 trades (detector says ALLOW practically never)
  EXHAUST raw=DAMPEN:    3 trades, 3W/0L, +$ 7.66  (100% WR — kept)
  EXHAUST raw=ABSTAIN:  47 trades, 25W/22L, -$42.10 (overridden, net negative)
    via A_TIER override:    35 trades, 19W/16L, -$36.48  ← KILL THIS
    via HIGH_ENTRY override:12 trades,  6W/ 6L, -$ 5.62  ← keep on 15m (~neutral)
                                                            kill on 5m (saw 2 losses today)

Today's ETH UP @63c loss (5/13 10:02): A_TIER override fired. Without it,
the trade would have been blocked. EXHAUST raw=ABSTAIN with range=1.00 and
breadth=1.00 (pegged components — clear exhaustion).

Two patches:
  1) run_bot.py:    remove the A_TIER override block
  2) run_brain_5m.py: remove the HIGH_ENTRY override block

Kill switches:
  EXHAUST_OVERRIDE_A_TIER=on  (default off after patch)
  EXHAUST_OVERRIDE_HIGH_5M=on (default off after patch)

Idempotent.
"""
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")

# ─────────────────────────────────────────────────────────────────────────
# Patch 1: run_bot.py — neutralize A_TIER override

P1_PATH = ROOT / "run_bot.py"
P1_OLD = """                        # ── Fix apr27: edge-priority override ──
                        # When signal is A-tier (prob>=82% AND edge>=18%),
                        # downgrade EXHAUST ABSTAIN -> DAMPEN. Top-tier signals
                        # historically win 80%+; size still halved by DAMPEN flag.
                        _was_overridden = False
                        if _act == "ABSTAIN" and _p.probability >= 0.82 and _p.edge >= 0.18:
                            logger.info(
                                f"[EXHAUST OVERRIDE] {_p.coin} {_p.direction}: "
                                f"prob={_p.probability:.0%} edge={_p.edge*100:.1f}% — "
                                f"ABSTAIN(score={_res.get('score', 0):.2f}) -> DAMPEN (no haircut)"
                            )
                            _act = "DAMPEN"
                            _was_overridden = True
"""

P1_NEW = """                        # ── V9 (2026-05-13): A_TIER override DISABLED ──
                        # Audit May 4-13 showed this override turned 35 ABSTAIN
                        # signals into trades that net-lost $36.48 (19W/16L,
                        # 54.3% WR — the old "80%+" assumption was stale).
                        # Today's ETH UP @63c loss fired this override and
                        # lost $3.15. Set env EXHAUST_OVERRIDE_A_TIER=on to
                        # re-enable.
                        _was_overridden = False
                        _override_enabled = os.getenv("EXHAUST_OVERRIDE_A_TIER", "off").lower() == "on"
                        if _override_enabled and _act == "ABSTAIN" and _p.probability >= 0.82 and _p.edge >= 0.18:
                            logger.info(
                                f"[EXHAUST OVERRIDE] {_p.coin} {_p.direction}: "
                                f"prob={_p.probability:.0%} edge={_p.edge*100:.1f}% — "
                                f"ABSTAIN(score={_res.get('score', 0):.2f}) -> DAMPEN (no haircut)"
                            )
                            _act = "DAMPEN"
                            _was_overridden = True
"""

# ─────────────────────────────────────────────────────────────────────────
# Patch 2: run_brain_5m.py — neutralize HIGH_ENTRY override on 5m

P2_PATH = ROOT / "run_brain_5m.py"
P2_OLD = """                    # ── Fix apr28: high-entry override (Option A from audit) ──
                    # Audit on 281 ABSTAIN events showed entries >= 63c blocked
                    # by EXHAUST resolve 71% WIN. Only allow DAMPEN (5m uses
                    # fixed $3 size so DAMPEN flag is informational only —
                    # the prob haircut is the real effect).
                    _entry_now = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                    if (_act == "ABSTAIN" and _entry_now >= 0.63
                            and float(_res.get("score", 0) or 0) < 0.65):
                        logger.info(
                            f"[5M EXHAUST OVERRIDE-HIGH-ENTRY] {_p.coin} {_p.direction}: "
                            f"entry={_entry_now*100:.0f}c score={_res.get('score', 0):.2f} -> DAMPEN"
                        )
                        _act = "DAMPEN"
"""

P2_NEW = """                    # ── V9 (2026-05-13): 5m HIGH_ENTRY override DISABLED ──
                    # Today (5/13) the 5m bot lost on 2 SOL UP trades that
                    # fired this override on rapidly falling asks (65c->56c
                    # in 4s right before order). The "71% WIN" audit was apr28
                    # — stale. Set EXHAUST_OVERRIDE_HIGH_5M=on to re-enable.
                    _entry_now = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                    _ovr_enabled = os.getenv("EXHAUST_OVERRIDE_HIGH_5M", "off").lower() == "on"
                    if (_ovr_enabled and _act == "ABSTAIN" and _entry_now >= 0.63
                            and float(_res.get("score", 0) or 0) < 0.65):
                        logger.info(
                            f"[5M EXHAUST OVERRIDE-HIGH-ENTRY] {_p.coin} {_p.direction}: "
                            f"entry={_entry_now*100:.0f}c score={_res.get('score', 0):.2f} -> DAMPEN"
                        )
                        _act = "DAMPEN"
"""


def apply(path, old, new, marker):
    src = path.read_text(encoding="utf-8")
    if marker in src:
        print(f"  [{path.name}] already patched (marker found) — skipping")
        return False
    if old not in src:
        print(f"  [{path.name}] anchor not found — aborting", file=sys.stderr)
        sys.exit(1)
    path.write_text(src.replace(old, new), encoding="utf-8")
    print(f"  [{path.name}] patched ✓")
    return True


def main():
    print("V9: kill EXHAUST OVERRIDES that were causing systematic losses")
    print(f"  A_TIER override (15m):     -$36.48 over 9 days  → DISABLE")
    print(f"  HIGH_ENTRY override (5m):  caused today's 2 losses → DISABLE")
    print()
    apply(P1_PATH, P1_OLD, P1_NEW, "V9 (2026-05-13): A_TIER override DISABLED")
    apply(P2_PATH, P2_OLD, P2_NEW, "V9 (2026-05-13): 5m HIGH_ENTRY override DISABLED")
    print()
    print("Done. Restart both bots to load.")


if __name__ == "__main__":
    main()
