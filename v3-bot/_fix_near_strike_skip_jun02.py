"""
Jun 2 PM v2 — 14:45 window: dist=0.000% blocked for 2+ min while book priced UP.

14:45:20-14:47:08  NEAR STRIKE dist=0.000% — never read asks
14:47:40           first EXPENSIVE SOL UP 75c

At window open price IS on strike (dist=0 is normal). NEAR STRIKE must not
blind us to the Polymarket book.

FIX:
1. NEAR_STRIKE_SKIP_SEC=90 — no near-strike block first 90s of window
2. Book-direction bypass — if max(up_ask,down_ask)>=0.52, evaluate anyway
3. EARLY_MIN_WIN_PROB=0.62 (63% SOL was blocked at 14:46:23)
"""
from pathlib import Path
import re

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ENV = Path("/home/ubuntu/v3-bot/.env")

OLD = """                if abs(dist_pct) < _min_dist_pct:
                    self._diag_log(
                        f"near-strike-{coin}",
                        f"[NEAR STRIKE] {coin}: dist={dist_pct*100:.3f}% "
                        f"< {_min_dist_pct*100:.2f}% - abstaining (price too close to strike)",
                        15.0,
                    )
                    return None"""

NEW = """                _skip_near_sec = int(os.getenv("NEAR_STRIKE_SKIP_SEC", "90"))
                if window_age >= _skip_near_sec and abs(dist_pct) < _min_dist_pct:
                    _ua = float(up_ask or 0)
                    _da = float(down_ask or 0)
                    _book_dir_min = float(os.getenv("BOOK_DIRECTION_MIN", "0.52"))
                    _book_has_dir = max(_ua, _da) >= _book_dir_min and min(_ua, _da) > 0.01
                    if _book_has_dir and window_age < _early_sec:
                        logger.debug(
                            f"[NEAR STRIKE BYPASS] {coin}: dist={dist_pct*100:.3f}% "
                            f"book UP={_ua:.2f} DOWN={_da:.2f} — evaluating"
                        )
                    else:
                        self._diag_log(
                            f"near-strike-{coin}",
                            f"[NEAR STRIKE] {coin}: dist={dist_pct*100:.3f}% "
                            f"< {_min_dist_pct*100:.2f}% - abstaining (price too close to strike)",
                            15.0,
                        )
                        return None"""


def main():
    text = PRED.read_text()
    if "NEAR_STRIKE_SKIP_SEC" in text:
        print("[SKIP] near strike skip already patched")
    elif OLD not in text:
        print("[FAIL] near strike block not found")
        return
    else:
        PRED.write_text(text.replace(OLD, NEW, 1))
        print("[OK] NEAR_STRIKE_SKIP_SEC + book-direction bypass")

    env = ENV.read_text()
    for k, v in [
        ("NEAR_STRIKE_SKIP_SEC", "90"),
        ("BOOK_DIRECTION_MIN", "0.52"),
        ("EARLY_MIN_WIN_PROB", "0.62"),
    ]:
        if re.search(rf"^{k}=", env, re.M):
            env = re.sub(rf"^{k}=.*$", f"{k}={v}", env, flags=re.M)
        else:
            env += f"\n{k}={v}\n"
        print(f"[OK] {k}={v}")
    ENV.write_text(env)


if __name__ == "__main__":
    main()
