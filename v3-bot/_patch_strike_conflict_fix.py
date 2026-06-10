#!/usr/bin/env python3
"""Move STRIKE CONFLICT after direction is assigned (was crashing silently)."""
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
p = ROOT / "predictor.py"


def main():
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))
    text = p.read_text(encoding="utf-8")

    strike_block = """
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

    if strike_block not in text:
        raise SystemExit("strike block to remove not found")
    text = text.replace(strike_block, "\n", 1)

    anchor = """        token_id = info.up_token_id if is_up else info.down_token_id

        # Cross-asset / per-coin direction consistency"""
    insert = """        token_id = info.up_token_id if is_up else info.down_token_id

""" + strike_block + """        # Cross-asset / per-coin direction consistency"""

    if anchor not in text:
        raise SystemExit("insert anchor not found")
    text = text.replace(anchor, insert, 1)

    p.write_text(text, encoding="utf-8")
    print("OK: strike conflict moved after direction assignment")


if __name__ == "__main__":
    main()
