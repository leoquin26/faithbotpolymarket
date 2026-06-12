"""
_apply_audit_may27_v7.py — Phase 1B: depth-aware Kelly sizing.

Inspiration: arXiv:2508.03474 ("Unravelling the Probabilistic Forest").
The paper notes top quants cap position at 50% of available book depth
to avoid moving the market against themselves on entry.

Today's SOL UP trade @ 13:02 demonstrated this exact failure: bot tried
to buy 7 shares (cost $3.78) but partial-filled at 5 shares as the book
got eaten. We left edge on the table because we didn't pre-check depth.

Fix: in OrderManager.place_bet, after computing `shares`, look up the
real CLOB book depth at our limit price and cap shares at 50% of the
top-3 ask depth. Logged as `[DEPTH CAP]`.

Idempotent + anchor-verified.
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
                f"({src.count(anchor)})"
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


ORDER_MANAGER_EDITS: List[Tuple[str, str, str]] = [
    (
        "v7: depth-aware Kelly sizing — cap shares at 50% of top-3 ask depth",
        '''        # Fix C: min 2 shares (was 5) so Kelly-tier sizing isn\'t overridden
        # by a floor that costs $3.40 at 68c. 5-share floor was fine when
        # entries were 30-50c; with 65-68c entries it blows Kelly budget.
        shares = max(2, int(size_usd / limit_price))
        actual_cost = shares * limit_price''',
        '''        # Fix C: min 2 shares (was 5) so Kelly-tier sizing isn\'t overridden
        # by a floor that costs $3.40 at 68c. 5-share floor was fine when
        # entries were 30-50c; with 65-68c entries it blows Kelly budget.
        shares = max(2, int(size_usd / limit_price))

        # ── [AUDIT MAY27 v7] depth-aware Kelly cap (paper arXiv:2508.03474) ──
        # Cap shares at 50% of the top-3 ask depth at our limit price so
        # we don\'t move the market against ourselves. Today\'s SOL trade
        # tried 7 shares but only got 5 — wasted edge to slippage.
        try:
            if os.getenv("DEPTH_AWARE_KELLY", "on").lower() == "on":
                _depth = self.get_full_depth(token_id)
                _ask_levels = _depth.get("asks", [])  # list of (price, size) ASC
                _depth_cap_pct = float(os.getenv("DEPTH_CAP_PCT", "0.50"))
                # Sum size of asks at-or-below our limit price (top of book up
                # to where our FOK could hit). Cap shares at 50% of that.
                _hittable = 0.0
                for _p, _s in _ask_levels[:3]:
                    if _p <= limit_price + 1e-6:
                        _hittable += float(_s)
                if _hittable > 0:
                    _max_shares = max(2, int(_hittable * _depth_cap_pct))
                    if shares > _max_shares:
                        logger.info(
                            f"[DEPTH CAP] {coin} {direction}: "
                            f"shares {shares} -> {_max_shares} "
                            f"(top-3 depth {_hittable:.0f} @ {limit_price*100:.0f}c, "
                            f"cap {_depth_cap_pct*100:.0f}%)"
                        )
                        shares = _max_shares
        except Exception as _e_dc:
            logger.debug(f"[DEPTH CAP] check failed: {_e_dc}")

        actual_cost = shares * limit_price''',
    ),
]


def main() -> int:
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v7: depth-aware Kelly")
    print("=" * 64)
    print()
    print("→ order_manager.py")
    p_path = os.path.join(REPO, "order_manager.py")
    n = patch_file(p_path, ORDER_MANAGER_EDITS)
    print(f"  applied {n}/{len(ORDER_MANAGER_EDITS)} edits")
    print()
    print("→ Verifying syntax")
    import py_compile
    try:
        py_compile.compile(p_path, doraise=True)
        print(f"  [OK] order_manager.py")
    except py_compile.PyCompileError as e:
        print(f"  [FAIL] {e}")
        return 2
    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
