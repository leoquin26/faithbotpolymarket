"""Option A: Edge-priority override on EXHAUST ABSTAIN.

When EXHAUST returns ABSTAIN but the signal is top-tier
(Prob >= 82% AND Edge >= 18%), downgrade ABSTAIN -> DAMPEN.
This re-enables high-quality trades that EXHAUST was killing
in P3 today, while still respecting EXHAUST on weak/medium signals.

Risk-bounded: DAMPEN halves bet size, so even if wrong, loss is ~50% of full.
"""
import shutil, datetime, pathlib

ROOT = pathlib.Path("/home/ubuntu/v3-bot")
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def patch():
    p = ROOT / "run_bot.py"
    src = p.read_text()
    if "EXHAUST OVERRIDE" in src:
        print("[exhaust-override] already patched, skip")
        return
    bak = p.with_suffix(p.suffix + f".bak_apr27_{STAMP}")
    shutil.copy2(p, bak)
    print(f"[backup] -> {bak}")

    old = (
        '                        if _act == "ABSTAIN":\n'
        '                            # ── Fix A apr23: sticky EXHAUST ABSTAIN memory ──\n'
        '                            _last_exhaust_abstain[_p.coin] = time.time()\n'
    )
    new = (
        '                        # ── Fix apr27: edge-priority override ──\n'
        '                        # When signal is A-tier (prob>=82% AND edge>=18%),\n'
        '                        # downgrade EXHAUST ABSTAIN -> DAMPEN. Top-tier signals\n'
        '                        # historically win 80%+; size still halved by DAMPEN flag.\n'
        '                        if _act == "ABSTAIN" and _p.probability >= 0.82 and _p.edge >= 0.18:\n'
        '                            logger.info(\n'
        '                                f"[EXHAUST OVERRIDE] {_p.coin} {_p.direction}: "\n'
        '                                f"prob={_p.probability:.0%} edge={_p.edge*100:.1f}% — "\n'
        '                                f"ABSTAIN(score={_res.get(\'score\', 0):.2f}) -> DAMPEN"\n'
        '                            )\n'
        '                            _act = "DAMPEN"\n'
        '                        if _act == "ABSTAIN":\n'
        '                            # ── Fix A apr23: sticky EXHAUST ABSTAIN memory ──\n'
        '                            _last_exhaust_abstain[_p.coin] = time.time()\n'
    )
    assert old in src, "anchor not found"
    p.write_text(src.replace(old, new, 1))
    print("[exhaust-override] installed")

if __name__ == "__main__":
    patch()
