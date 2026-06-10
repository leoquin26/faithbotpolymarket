#!/usr/bin/env python3
"""Apr 27: Drop DAMPEN size-halving when EXHAUST OVERRIDE fired.

Rationale: A-tier signals (prob>=82% AND edge>=18%) that override an
EXHAUST ABSTAIN should not be double-penalized. The override IS the
safety mechanism (already required A-tier). Stacking DAMPEN halving
on top of Tier-C daily-loss cap = 25% of natural Kelly, which is
overly punitive on the strongest signals we have.

Fix: in run_bot.py, set a new flag `_override_full_size = True` on
the prediction when the override fired. In order_manager.py, skip
the `* 0.5` DAMPEN halving when that flag is present (still keep the
daily-loss tier cap).
"""

import re
import shutil
import time

RUN_BOT = "/home/ubuntu/v3-bot/run_bot.py"
ORDER_MGR = "/home/ubuntu/v3-bot/order_manager.py"
STAMP = time.strftime("%Y%m%d_%H%M%S")


def patch_run_bot():
    with open(RUN_BOT, "r") as f:
        src = f.read()
    shutil.copy(RUN_BOT, f"{RUN_BOT}.bak_apr27_nodp_{STAMP}")

    # In the DAMPEN action handler, after setattr(_p, "_dampened", True),
    # also set _override_full_size when _was_overridden.
    old = (
        '                            # Fix F (apr21): mark dampened so order_manager cuts size 50%\n'
        '                            setattr(_p, "_dampened", True)\n'
        '                            _suffix = " [override: prob/edge unchanged]" if _was_overridden else ""\n'
        '                            logger.info(f"[EXHAUST DAMPEN] {_p.coin} {_p.direction} p={_pre:.2f}->{_p.probability:.2f} (size will be halved){_suffix}")'
    )
    new = (
        '                            # Fix F (apr21): mark dampened so order_manager cuts size 50%\n'
        '                            setattr(_p, "_dampened", True)\n'
        '                            # Fix apr27 (no double penalty): when override fired, skip\n'
        '                            # the size-halving in order_manager. Override already self-\n'
        '                            # selects A-tier signals; double-penalty makes them too small.\n'
        '                            if _was_overridden:\n'
        '                                setattr(_p, "_override_full_size", True)\n'
        '                            _size_note = "(size unchanged, override A-tier)" if _was_overridden else "(size will be halved)"\n'
        '                            _suffix = " [override: prob/edge unchanged]" if _was_overridden else ""\n'
        '                            logger.info(f"[EXHAUST DAMPEN] {_p.coin} {_p.direction} p={_pre:.2f}->{_p.probability:.2f} {_size_note}{_suffix}")'
    )
    if old not in src:
        raise SystemExit("ERROR: run_bot.py DAMPEN block not found exactly")
    src = src.replace(old, new)
    with open(RUN_BOT, "w") as f:
        f.write(src)
    print(f"[OK] Patched run_bot.py (backup: {RUN_BOT}.bak_apr27_nodp_{STAMP})")


def patch_order_manager():
    with open(ORDER_MGR, "r") as f:
        src = f.read()
    shutil.copy(ORDER_MGR, f"{ORDER_MGR}.bak_apr27_nodp_{STAMP}")

    old = (
        '            dampen_tag = ""\n'
        '            if getattr(pred, "_dampened", False):\n'
        '                pre_dampen = size\n'
        '                size = max(kelly_min_bet, size * 0.5)\n'
        '                dampen_tag = f" dampen=50%(pre=${pre_dampen:.2f})"'
    )
    new = (
        '            dampen_tag = ""\n'
        '            if getattr(pred, "_dampened", False):\n'
        '                # Fix apr27 (no double penalty): if EXHAUST OVERRIDE fired,\n'
        '                # skip the 50% size cut. Override already self-selected A-tier.\n'
        '                if getattr(pred, "_override_full_size", False):\n'
        '                    dampen_tag = " dampen=skipped(override)"\n'
        '                else:\n'
        '                    pre_dampen = size\n'
        '                    size = max(kelly_min_bet, size * 0.5)\n'
        '                    dampen_tag = f" dampen=50%(pre=${pre_dampen:.2f})"'
    )
    if old not in src:
        raise SystemExit("ERROR: order_manager.py DAMPEN block not found exactly")
    src = src.replace(old, new)
    with open(ORDER_MGR, "w") as f:
        f.write(src)
    print(f"[OK] Patched order_manager.py (backup: {ORDER_MGR}.bak_apr27_nodp_{STAMP})")


if __name__ == "__main__":
    patch_run_bot()
    patch_order_manager()
    print("[OK] All patches applied. Verify with: python3 -m py_compile run_bot.py order_manager.py")
