#!/usr/bin/env python3
"""Block cheap entries when Polymarket book strongly favors opposite side."""
from pathlib import Path
import shutil
from datetime import datetime

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ENV = Path("/home/ubuntu/v3-bot/.env")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD = """        _cheap_ask = float(os.getenv("COMPOUND_CHEAP_ASK", "0.52"))
        _cheap_prob = float(os.getenv("COMPOUND_MIN_PROB", "0.58"))
        _compound_cheap_ok = ask <= _cheap_ask and win_prob >= _cheap_prob

        if ask <= 0.01:"""

NEW = """        _cheap_ask = float(os.getenv("COMPOUND_CHEAP_ASK", "0.52"))
        _cheap_prob = float(os.getenv("COMPOUND_MIN_PROB", "0.58"))
        _compound_cheap_ok = ask <= _cheap_ask and win_prob >= _cheap_prob
        # Jun-3: cheap != edge — block when book prices opposite side heavily
        # (e.g. DOWN @ 36c while UP @ 95c = trending against you).
        _trap_opp = float(os.getenv("CHEAP_TRAP_OPPOSITE_ASK", "0.70"))
        _ua = float(up_ask or 0)
        _da = float(down_ask or 0)
        _opp_ask = _ua if direction == "DOWN" else _da
        _book_opposes = _opp_ask >= _trap_opp and _opp_ask > 0.05
        if _book_opposes and ask <= _cheap_ask:
            self._diag_log(
                f"cheap-trap-{coin}-{direction}",
                f"[CHEAP TRAP] {coin} {direction}: ask={ask*100:.0f}c "
                f"but opposite={_opp_ask*100:.0f}c >= {_trap_opp*100:.0f}c "
                f"— cheap loser side, book trending away",
                15.0,
            )
            return None
        if _book_opposes:
            _compound_cheap_ok = False

        if ask <= 0.01:"""


def main() -> None:
    text = PRED.read_text(encoding="utf-8")
    if OLD not in text:
        if "[CHEAP TRAP]" in text:
            print("predictor: already patched")
        else:
            raise SystemExit("anchor not found")
    else:
        shutil.copy2(PRED, PRED.with_suffix(f".bak_{ts}"))
        PRED.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        print("predictor: OK")

    env = ENV.read_text(encoding="utf-8")
    if "CHEAP_TRAP_OPPOSITE_ASK=" not in env:
        env = env.rstrip() + "\nCHEAP_TRAP_OPPOSITE_ASK=0.70\n"
        ENV.write_text(env, encoding="utf-8")
        print(".env: OK")


if __name__ == "__main__":
    main()
