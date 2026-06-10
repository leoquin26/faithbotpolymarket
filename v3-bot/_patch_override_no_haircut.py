"""Fix: when EXHAUST OVERRIDE downgrades ABSTAIN->DAMPEN, do NOT apply the
0.85 probability haircut. The haircut drops a 83% signal to 70%, which then
fails the MORNING P3 prob>=78% gate — defeating the whole override.

Override = "we already decided this signal is A-tier". Just halve the size
(via _dampened flag) and keep the probability/edge intact.
"""
import shutil, datetime, pathlib

ROOT = pathlib.Path("/home/ubuntu/v3-bot")
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def patch():
    p = ROOT / "run_bot.py"
    src = p.read_text()
    if "_was_overridden" in src:
        print("[no-haircut] already patched, skip")
        return
    bak = p.with_suffix(p.suffix + f".bak_apr27b_{STAMP}")
    shutil.copy2(p, bak)
    print(f"[backup] -> {bak}")

    # 1) Set the override flag when override triggers
    old1 = (
        '                        if _act == "ABSTAIN" and _p.probability >= 0.82 and _p.edge >= 0.18:\n'
        '                            logger.info(\n'
        '                                f"[EXHAUST OVERRIDE] {_p.coin} {_p.direction}: "\n'
        '                                f"prob={_p.probability:.0%} edge={_p.edge*100:.1f}% — "\n'
        '                                f"ABSTAIN(score={_res.get(\'score\', 0):.2f}) -> DAMPEN"\n'
        '                            )\n'
        '                            _act = "DAMPEN"\n'
    )
    new1 = (
        '                        _was_overridden = False\n'
        '                        if _act == "ABSTAIN" and _p.probability >= 0.82 and _p.edge >= 0.18:\n'
        '                            logger.info(\n'
        '                                f"[EXHAUST OVERRIDE] {_p.coin} {_p.direction}: "\n'
        '                                f"prob={_p.probability:.0%} edge={_p.edge*100:.1f}% — "\n'
        '                                f"ABSTAIN(score={_res.get(\'score\', 0):.2f}) -> DAMPEN (no haircut)"\n'
        '                            )\n'
        '                            _act = "DAMPEN"\n'
        '                            _was_overridden = True\n'
    )
    assert old1 in src, "anchor 1 (override block) not found"
    src = src.replace(old1, new1, 1)

    # 2) Skip the probability haircut when overridden
    old2 = (
        '                        elif _act == "DAMPEN":\n'
        '                            _pre = _p.probability\n'
        '                            _p.probability = max(0.01, _p.probability * 0.85)\n'
        '                            _entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price\n'
        '                            _p.edge = _p.probability - _entry\n'
        '                            # Fix F (apr21): mark dampened so order_manager cuts size 50%\n'
        '                            setattr(_p, "_dampened", True)\n'
        '                            logger.info(f"[EXHAUST DAMPEN] {_p.coin} {_p.direction} p={_pre:.2f}->{_p.probability:.2f} (size will be halved)")\n'
    )
    new2 = (
        '                        elif _act == "DAMPEN":\n'
        '                            _pre = _p.probability\n'
        '                            if not _was_overridden:\n'
        '                                # Normal DAMPEN: shave probability AND halve size\n'
        '                                _p.probability = max(0.01, _p.probability * 0.85)\n'
        '                                _entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price\n'
        '                                _p.edge = _p.probability - _entry\n'
        '                            # Fix F (apr21): mark dampened so order_manager cuts size 50%\n'
        '                            setattr(_p, "_dampened", True)\n'
        '                            _suffix = " [override: prob/edge unchanged]" if _was_overridden else ""\n'
        '                            logger.info(f"[EXHAUST DAMPEN] {_p.coin} {_p.direction} p={_pre:.2f}->{_p.probability:.2f} (size will be halved){_suffix}")\n'
    )
    assert old2 in src, "anchor 2 (DAMPEN block) not found"
    src = src.replace(old2, new2, 1)

    p.write_text(src)
    print("[no-haircut] override now keeps prob/edge intact")

if __name__ == "__main__":
    patch()
