#!/usr/bin/env python3
"""Fix SOL DOWN loss: honor regime invert; block bets against spot vs strike."""
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    old_no_inv = """                        # Jun-2 PM: keep original direction in first ~2min when edge already strong
                        _early_no_inv_t = float(_os_ra2.getenv("EARLY_REGIME_NO_INVERT_T_SEC", "780"))
                        _early_no_inv_edge = float(_os_ra2.getenv("EARLY_REGIME_NO_INVERT_EDGE", "0.10"))
                        if (time_remaining >= _early_no_inv_t
                                and edge >= _early_no_inv_edge
                                and "strong-trend" in (_ra_action.reason or "")):
                            logger.info(
                                f"[REGIME EARLY NO-INVERT] {coin} {direction}@{ask*100:.0f}c "
                                f"edge={edge*100:.1f}% T={time_remaining:.0f}s — keeping {direction}"
                            )
                            _trap_off_keep = True"""

    new_no_inv = """                        # Jun-3: disabled for strong-trend — was keeping DOWN when
                        # regime said INVERT UP (13:45 SOL loss). Trap-band only via env.
                        _no_inv_on = _os_ra2.getenv("EARLY_REGIME_NO_INVERT", "off").lower() == "on"
                        _early_no_inv_t = float(_os_ra2.getenv("EARLY_REGIME_NO_INVERT_T_SEC", "780"))
                        _early_no_inv_edge = float(_os_ra2.getenv("EARLY_REGIME_NO_INVERT_EDGE", "0.10"))
                        if (_no_inv_on
                                and time_remaining >= _early_no_inv_t
                                and edge >= _early_no_inv_edge
                                and "trap-band" in (_ra_action.reason or "")):
                            logger.info(
                                f"[REGIME EARLY NO-INVERT] {coin} {direction}@{ask*100:.0f}c "
                                f"edge={edge*100:.1f}% T={time_remaining:.0f}s — keeping {direction}"
                            )
                            _trap_off_keep = True"""

    if old_no_inv not in text:
        raise SystemExit("early no-invert block not found")
    text = text.replace(old_no_inv, new_no_inv, 1)

    anchor = "        except Exception as _e_md:\n            logger.debug(f\"[NEAR STRIKE] check failed: {_e_md}\")"
    strike_gate = """
        # Jun-3: spot vs strike — don't buy DOWN above strike / UP below strike
        try:
            if os.getenv("STRIKE_DIRECTION_ENFORCE", "on").lower() == "on":
                _sd_min = float(os.getenv("STRIKE_DIRECTION_MIN_DIST", "0.00015"))
                if dist_pct >= _sd_min and direction == "DOWN":
                    self._diag_log(
                        f"strike-dir-{coin}",
                        f"[STRIKE CONFLICT] {coin} DOWN: price {dist_pct*100:+.3f}% above strike — skip",
                        15.0,
                    )
                    return None
                if dist_pct <= -_sd_min and direction == "UP":
                    self._diag_log(
                        f"strike-dir-{coin}",
                        f"[STRIKE CONFLICT] {coin} UP: price {dist_pct*100:+.3f}% below strike — skip",
                        15.0,
                    )
                    return None
        except Exception as _e_sd:
            logger.debug(f"[STRIKE CONFLICT] check failed: {_e_sd}")
"""
    if anchor not in text:
        raise SystemExit("near strike anchor not found")
    text = text.replace(anchor, anchor + strike_gate, 1)

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "EARLY_REGIME_NO_INVERT": "off",
        "STRIKE_DIRECTION_ENFORCE": "on",
        "STRIKE_DIRECTION_MIN_DIST": "0.00015",
        "NEAR_STRIKE_SKIP_SEC": "0",
    }
    lines = p.read_text(encoding="utf-8").splitlines()
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("patched .env")


def main():
    patch_predictor()
    patch_env()
    print("OK")


if __name__ == "__main__":
    main()
