"""
Apply EXHAUST Option A patch to run_bot.py and run_brain_5m.py.

Rule: if EXHAUST verdict is ABSTAIN AND entry >= 0.63 AND score < 0.65,
downgrade to DAMPEN. Audit (apr28, n=281): high-entry blocked signals
resolved 71% WIN; the 171 trades this rule lets through win 68% with
+$25/week recovered EV at half size.

This patch ADDS the override after the existing A-tier override so
both work in tandem (A-tier full-size override stays first).
"""
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")

PATCH_15M_FIND = '''                        # ── Fix apr27: edge-priority override ──
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
                            _was_overridden = True'''

PATCH_15M_REPLACE = '''                        # ── Fix apr27: edge-priority override ──
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
                        # ── Fix apr28: high-entry override (Option A from audit) ──
                        # Audit on 281 ABSTAIN events showed entries >= 63c blocked
                        # by EXHAUST resolve 71% WIN (well above 64% breakeven). The
                        # 171 trades this rule lets through win 68% globally.
                        # Only allows DAMPEN (half size) — not full-size — to stay
                        # cautious. Decisive blocks (score >= 0.65) still ABSTAIN.
                        if (_act == "ABSTAIN" and not _was_overridden
                                and (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) >= 0.63
                                and float(_res.get("score", 0) or 0) < 0.65):
                            _entry_c = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) * 100.0
                            logger.info(
                                f"[EXHAUST OVERRIDE-HIGH-ENTRY] {_p.coin} {_p.direction}: "
                                f"entry={_entry_c:.0f}c score={_res.get('score', 0):.2f} "
                                f"-> DAMPEN (half size; audit apr28 says 68% WR)"
                            )
                            _act = "DAMPEN"'''

PATCH_5M_FIND = '''                    if _act == "ABSTAIN":
                        logger.info(
                            f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped "
                            f"(score={_res.get('score', 0):.2f})"
                        )
                        continue'''

PATCH_5M_REPLACE = '''                    # ── Fix apr28: high-entry override (Option A from audit) ──
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

                    if _act == "ABSTAIN":
                        logger.info(
                            f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped "
                            f"(score={_res.get('score', 0):.2f})"
                        )
                        continue'''


def patch(path, find, replace):
    p = ROOT / path
    text = p.read_text()
    if replace.split("\n", 1)[0] in text and "Fix apr28: high-entry override" in text:
        print(f"[SKIP] {path}: already patched")
        return False
    if find not in text:
        print(f"[FAIL] {path}: anchor not found")
        return False
    new = text.replace(find, replace, 1)
    p.write_text(new)
    print(f"[OK] {path}: patched ({len(text)} -> {len(new)} bytes)")
    return True


changed = False
changed |= patch("run_bot.py", PATCH_15M_FIND, PATCH_15M_REPLACE)
changed |= patch("run_brain_5m.py", PATCH_5M_FIND, PATCH_5M_REPLACE)

if not changed:
    print("Nothing changed.")
    sys.exit(0)

print("\n=== syntax check ===")
import py_compile
for f in ["run_bot.py", "run_brain_5m.py"]:
    try:
        py_compile.compile(str(ROOT / f), doraise=True)
        print(f"  {f}: OK")
    except py_compile.PyCompileError as e:
        print(f"  {f}: FAIL\n{e}")
        sys.exit(1)

print("\nDone. Restart both bots to apply.")
