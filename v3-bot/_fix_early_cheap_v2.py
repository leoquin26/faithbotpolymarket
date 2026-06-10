"""Patch predictor entry filters - v2 smaller replacements."""
from pathlib import Path
import re

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ENV = Path("/home/ubuntu/v3-bot/.env")
RB = Path("/home/ubuntu/v3-bot/run_bot.py")


def main():
    text = PRED.read_text()

    if "EARLY CHEAP EDGE" not in text:
        text = text.replace(
            "        # Entry price filters\n"
            "        entry_min = getattr(config, \"ENTRY_MIN\", 0.10)\n"
            "        entry_max = getattr(config, \"ENTRY_MAX\", 0.75)\n",
            "        # Entry price filters\n"
            "        entry_min = getattr(config, \"ENTRY_MIN\", 0.10)\n"
            "        entry_max = getattr(config, \"ENTRY_MAX\", 0.75)\n"
            "        _early_entry_sec = int(os.getenv(\"EARLY_ENTRY_SEC\", \"120\"))\n"
            "        _early_entry_min = float(os.getenv(\"EARLY_ENTRY_MIN\", \"0.40\"))\n"
            "        if window_age < _early_entry_sec:\n"
            "            entry_min = min(entry_min, _early_entry_min)\n"
            "        _cheap_ask = float(os.getenv(\"COMPOUND_CHEAP_ASK\", \"0.52\"))\n"
            "        _cheap_prob = float(os.getenv(\"COMPOUND_MIN_PROB\", \"0.58\"))\n"
            "        _compound_cheap_ok = ask <= _cheap_ask and win_prob >= _cheap_prob\n",
            1,
        )
        text = text.replace(
            "        if ask < entry_min:\n"
            "            self._diag_log(\n"
            "                f\"cheap-{coin}-{direction}\",\n"
            "                f\"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c\", 30.0\n"
            "            )\n"
            "            return None\n",
            "        if ask < entry_min and not _compound_cheap_ok:\n"
            "            self._diag_log(\n"
            "                f\"cheap-{coin}-{direction}\",\n"
            "                f\"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c\", 30.0\n"
            "            )\n"
            "            return None\n"
            "        if ask < entry_min and _compound_cheap_ok:\n"
            "            logger.info(\n"
            "                f\"[EARLY CHEAP EDGE] {coin} {direction}: ask={ask*100:.0f}c \"\n"
            "                f\"prob={win_prob*100:.0f}% edge={(win_prob-ask)*100:.1f}% age={window_age}s\"\n"
            "            )\n",
            1,
        )
        text = text.replace(
            "        min_prob = getattr(config, \"MIN_WIN_PROB\", 0.65)\n"
            "        _cheap_ask = float(os.getenv(\"COMPOUND_CHEAP_ASK\", \"0.52\"))\n"
            "        _cheap_prob = float(os.getenv(\"COMPOUND_MIN_PROB\", \"0.58\"))\n"
            "        if win_prob < min_prob:\n"
            "            if ask <= _cheap_ask and win_prob >= _cheap_prob:\n"
            "                logger.debug(\n"
            "                    f\"[COMPOUND CHEAP] {coin} {direction}: prob={win_prob*100:.0f}% \"\n"
            "                    f\"ask={ask*100:.0f}c — cheap-entry bypass\"\n"
            "                )\n",
            "        min_prob = getattr(config, \"MIN_WIN_PROB\", 0.65)\n"
            "        if window_age < _early_entry_sec:\n"
            "            min_prob = min(min_prob, float(os.getenv(\"EARLY_MIN_WIN_PROB\", \"0.65\")))\n"
            "        if win_prob < min_prob:\n"
            "            if _compound_cheap_ok:\n"
            "                logger.info(\n"
            "                    f\"[COMPOUND CHEAP] {coin} {direction}: prob={win_prob*100:.0f}% \"\n"
            "                    f\"ask={ask*100:.0f}c edge={(win_prob-ask)*100:.1f}% — bypass min prob\"\n"
            "                )\n",
            1,
        )
        PRED.write_text(text)
        print("[OK] predictor patched")
    else:
        print("[SKIP] predictor already patched")

    rb = RB.read_text()
    marker = "            futures_map = {executor.submit(scan_coin, c): c for c in config.SYMBOLS}"
    batch = """            try:
                import polymarket_ws as _pws_mod
                _batch_ids = []
                for _c in config.SYMBOLS:
                    _inf = get_market_info(_c)
                    if _inf:
                        _batch_ids.extend([_inf.up_token_id, _inf.down_token_id])
                if _batch_ids:
                    _pws_mod.subscribe(_batch_ids)
            except Exception:
                pass

"""
    if "import polymarket_ws as _pws_mod" not in rb:
        rb = rb.replace(marker, batch + marker, 1)
        RB.write_text(rb)
        print("[OK] run_bot batch subscribe")
    else:
        print("[SKIP] run_bot batch subscribe")

    env = ENV.read_text()
    for k, v in [("EARLY_ENTRY_SEC", "120"), ("EARLY_ENTRY_MIN", "0.40"), ("EARLY_MIN_WIN_PROB", "0.65")]:
        if re.search(rf"^{k}=", env, re.M):
            env = re.sub(rf"^{k}=.*$", f"{k}={v}", env, flags=re.M)
        else:
            env += f"\n{k}={v}\n"
        print(f"[OK] {k}={v}")
    ENV.write_text(env)


if __name__ == "__main__":
    main()
