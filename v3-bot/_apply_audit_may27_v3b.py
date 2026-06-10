"""
_apply_audit_may27_v3b.py — small follow-up patch: thread `trade_id` from
FIRED → RESOLVED so the sqlite ledger's `trades` table can be UPDATEd on
resolution.

The bug: after place_bet succeeds, run_bot tags the position with
`is_morning` but not with the trade_id that log_fired used. So when the
position resolves later, log_resolved receives `trade_id=pos.get("trade_id")`
which is always None.

Fix: also stash the trade_id on the position dict immediately after a
successful place_bet, in both the morning P-phase and the afternoon paths.
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple

REPO = "/home/ubuntu/v3-bot"


def patch_file(path: str, edits: List[Tuple[str, str, str]]) -> int:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    applied = 0
    for label, anchor, replacement in edits:
        if replacement in src:
            print(f"  [skip] {label}: replacement already present")
            continue
        if anchor not in src:
            raise RuntimeError(
                f"{path}: anchor for {label!r} not found and replacement "
                "not present — manual intervention needed"
            )
        if src.count(anchor) > 1:
            raise RuntimeError(
                f"{path}: anchor for {label!r} matches multiple times "
                f"({src.count(anchor)}) — anchor too generic"
            )
        src = src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [done] {label}")
    if applied:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        os.replace(tmp, path)
    return applied


RUN_BOT_EDITS: List[Tuple[str, str, str]] = [
    (
        "T1.morning: tag position with trade_id after successful place_bet",
        '''                                        _success = orders.place_bet(_best_m)
                                        if _success and _best_m.coin in orders.positions:
                                            # Tag position as morning for isolated resolution
                                            orders.positions[_best_m.coin]["is_morning"] = True''',
        '''                                        _success = orders.place_bet(_best_m)
                                        if _success and _best_m.coin in orders.positions:
                                            # Tag position as morning for isolated resolution
                                            orders.positions[_best_m.coin]["is_morning"] = True
                                            # [AUDIT MAY27 T1] thread trade_id for ledger linkage
                                            orders.positions[_best_m.coin]["trade_id"] = getattr(_best_m, "_trade_id", None)''',
    ),
    (
        "T1.afternoon: tag position with trade_id after afternoon place_bet",
        '''                                filled = orders.place_bet(best)
                                if not filled:
                                    unlock_window(best.coin, best.market_info.window_start)
                                    logger.info(f"[UNLOCK] {best.coin}: order failed, window unlocked for retry")''',
        '''                                filled = orders.place_bet(best)
                                if not filled:
                                    unlock_window(best.coin, best.market_info.window_start)
                                    logger.info(f"[UNLOCK] {best.coin}: order failed, window unlocked for retry")
                                elif best.coin in orders.positions:
                                    # [AUDIT MAY27 T1] thread trade_id for ledger linkage
                                    orders.positions[best.coin]["trade_id"] = getattr(best, "_trade_id", None)''',
    ),
]


def main() -> int:
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v3b: trade_id linkage")
    print("=" * 64)
    print()
    print("→ run_bot.py")
    r_path = os.path.join(REPO, "run_bot.py")
    n = patch_file(r_path, RUN_BOT_EDITS)
    print(f"  applied {n}/{len(RUN_BOT_EDITS)} edits")
    print()
    print("→ Verifying syntax")
    import py_compile
    try:
        py_compile.compile(r_path, doraise=True)
        print(f"  [OK] run_bot.py")
    except py_compile.PyCompileError as e:
        print(f"  [FAIL] {e}")
        return 2
    print()
    print("Done. Restart the bot to load the new code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
