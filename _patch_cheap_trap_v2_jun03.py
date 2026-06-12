#!/usr/bin/env python3
"""Tighten cheap-side traps after ETH UP @39c loss (opposite ~60c, rev risk 0.33)."""
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

    old_trap = """        _trap_opp = float(os.getenv("CHEAP_TRAP_OPPOSITE_ASK", "0.70"))
        _ua = float(up_ask or 0)
        _da = float(down_ask or 0)
        _opp_ask = _ua if direction == "DOWN" else _da
        _book_opposes = _opp_ask >= _trap_opp and _opp_ask > 0.05
        if _book_opposes and ask <= _cheap_ask:"""

    new_trap = """        _trap_opp = float(os.getenv("CHEAP_TRAP_OPPOSITE_ASK", "0.58"))
        _trap_max_ask = float(os.getenv("CHEAP_TRAP_MAX_ASK", "0.48"))
        _ua = float(up_ask or 0)
        _da = float(down_ask or 0)
        _opp_ask = _da if direction == "UP" else _ua
        _book_opposes = _opp_ask >= _trap_opp and _opp_ask > 0.05
        if _book_opposes and ask <= min(_cheap_ask, _trap_max_ask):"""

    if old_trap not in text:
        raise SystemExit("cheap trap block not found")
    text = text.replace(old_trap, new_trap, 1)

    old_dampen = """                    _ra_size_factor *= _dampen_mult
        except Exception as _e_rr:
            logger.warning(f"[REVERSION] compute failed: {_e_rr}")"""

    new_dampen = """                    _ra_size_factor *= _dampen_mult
                    # Jun-3: skip lottery cheap side when book flows against us
                    _rev_trap = os.getenv("REVERSION_TRAP_CHEAP", "on").lower() == "on"
                    _rev_trap_ask = float(os.getenv("REVERSION_TRAP_MAX_ASK", "0.45"))
                    _rev_trap_risk = float(os.getenv("REVERSION_TRAP_MIN_RISK", "0.22"))
                    if (_rev_trap and ask <= _rev_trap_ask
                            and float(_rr_res.get("risk", 0) or 0) >= _rev_trap_risk):
                        self._diag_log(
                            f"rev-trap-{coin}-{direction}",
                            f"[REVERSION TRAP] {coin} {direction}: ask={ask*100:.0f}c "
                            f"risk={_rr_res['risk']:.2f} — cheap side vs book flow, skip",
                            15.0,
                        )
                        return None
        except Exception as _e_rr:
            logger.warning(f"[REVERSION] compute failed: {_e_rr}")"""

    if old_dampen not in text:
        raise SystemExit("reversion dampen anchor not found")
    text = text.replace(old_dampen, new_dampen, 1)

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "CHEAP_TRAP_OPPOSITE_ASK": "0.58",
        "CHEAP_TRAP_MAX_ASK": "0.48",
        "REVERSION_TRAP_CHEAP": "on",
        "REVERSION_TRAP_MAX_ASK": "0.45",
        "REVERSION_TRAP_MIN_RISK": "0.22",
        "MORNING_P3_PATTERN_A_MAX_ASK": "0.50",
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
