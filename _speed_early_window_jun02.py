"""
Jun 2 PM — act earlier in each 15m window (fix "everything EXPENSIVE").

FINDINGS:
- WARMUP_SEC=30 in .env was DEAD CODE. Predictor only checked HARD_WARMUP_15M=60.
- First [SIGNAL] today never before 121s into window; median 499s (8+ min).
- First [EXPENSIVE] often by 90-160s — market prices before we evaluate.
- 14:00 window: warmup until 60s, first EXPENSIVE at 157s @ 73c.

FIX:
1. predictor.py: effective warmup = min(HARD_WARMUP_15M, WARMUP_SEC) so both env keys work
2. .env: HARD_WARMUP_15M=20, WARMUP_SEC=20, PREDICTOR_MIN_TICKS=8
"""
from pathlib import Path

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ENV = Path("/home/ubuntu/v3-bot/.env")


def patch_predictor():
    text = PRED.read_text()
    old = (
        "        # Jun-2: 15m hard floor 120s -> 60s. Env-tunable via HARD_WARMUP_15M.\n"
        "        _hard_15m = int(os.getenv(\"HARD_WARMUP_15M\", \"60\"))\n"
        "        _warmup_min  = {\"5m\": 30,  \"15m\": _hard_15m, \"1h\": 600}.get(_tf, _hard_15m)\n"
    )
    new = (
        "        # Jun-2 PM: effective warmup = min(hard cap, WARMUP_SEC) — both env keys apply.\n"
        "        # Was: only HARD_WARMUP_15M; WARMUP_SEC in .env was ignored (dead code).\n"
        "        _hard_15m = int(os.getenv(\"HARD_WARMUP_15M\", \"20\"))\n"
        "        _warmup_sec = int(getattr(config, \"WARMUP_SEC\", 20))\n"
        "        _eff_15m = min(_hard_15m, _warmup_sec)\n"
        "        _warmup_min  = {\"5m\": 30,  \"15m\": _eff_15m, \"1h\": 600}.get(_tf, _eff_15m)\n"
    )
    if "Jun-2 PM: effective warmup" in text:
        print("[SKIP] predictor warmup already patched")
        return True
    if old not in text:
        print("[FAIL] predictor warmup block not found")
        return False
    # Remove dead assignment below (warmup = getattr...) - keep it for any other use or update comment
    text = text.replace(old, new, 1)
    PRED.write_text(text)
    print("[OK] predictor uses min(HARD_WARMUP_15M, WARMUP_SEC)")
    return True


def patch_env():
    text = ENV.read_text()
    updates = {
        "HARD_WARMUP_15M": "20",
        "WARMUP_SEC": "20",
        "PREDICTOR_MIN_TICKS": "8",
    }
    for key, val in updates.items():
        import re
        pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
        if pat.search(text):
            text = pat.sub(f"{key}={val}", text)
            print(f"[OK] {key}={val}")
        else:
            text = text.rstrip() + f"\n{key}={val}\n"
            print(f"[OK] added {key}={val}")
    ENV.write_text(text)


if __name__ == "__main__":
    if patch_predictor():
        patch_env()
