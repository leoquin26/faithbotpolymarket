#!/usr/bin/env python3
"""Restore trading: early entry min, relax afternoon prob, fix CLOB post, env."""
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_env():
    p = ROOT / ".env"
    backup(p)
    text = p.read_text(encoding="utf-8")
    updates = {
        "MIN_WIN_PROB": "0.68",
        "ACCURACY_CONFIRM_SCANS": "2",
        "CONSENSUS_GATE_ON": "off",
        "ENTRY_MIN": "0.35",
    }
    lines = text.splitlines()
    out = []
    seen = set()
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


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    old_vote = """        vote_dir = "UP" if votes_up >= 2 else ("DOWN" if votes_down >= 2 else None)
        is_up = combined_prob >= 0.5
        direction = "UP" if is_up else "DOWN"
        if vote_dir and vote_dir != direction:
            self._diag_log(
                f"dirvote-{coin}",
                f"[DIR VOTE] {coin}: model={direction} vote={vote_dir} "
                f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps book={book_up:.2f} "
                f"{votes_up}UP/{votes_down}DN) — skip",
                12.0,
            )
            return None"""

    new_vote = """        vote_dir = "UP" if votes_up >= 2 else ("DOWN" if votes_down >= 2 else None)
        is_up = combined_prob >= 0.5
        direction = "UP" if is_up else "DOWN"
        _book_decisive = book_up >= 0.85 or book_up <= 0.15
        _dist_clear = abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_SKIP_DIST", "0.001"))
        _skip_dir_vote = _book_decisive or (early_window and _dist_clear)
        if vote_dir and vote_dir != direction and not _skip_dir_vote:
            self._diag_log(
                f"dirvote-{coin}",
                f"[DIR VOTE] {coin}: model={direction} vote={vote_dir} "
                f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps book={book_up:.2f} "
                f"{votes_up}UP/{votes_down}DN) — skip",
                12.0,
            )
            return None"""

    if old_vote not in text:
        raise SystemExit("DIR VOTE block not found")
    text = text.replace(old_vote, new_vote, 1)

    old_consensus = """        # Consensus check: if 2+ coins have signals, check majority
        if len(self._window_trends) >= 2:
            up_count = sum(1 for d in self._window_trends.values() if d == "UP")
            down_count = sum(1 for d in self._window_trends.values() if d == "DOWN")
            majority = "UP" if up_count > down_count else "DOWN" if down_count > up_count else None
            
            if majority and direction != majority:
                self._diag_log(
                    f"consensus-{coin}",
                    f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                    f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                    15.0,
                )
                return None"""

    new_consensus = """        # Consensus check: if 2+ coins have signals, check majority
        _consensus_on = os.getenv("CONSENSUS_GATE_ON", "on").lower() not in ("off", "0", "false")
        if _consensus_on and len(self._window_trends) >= 2:
            up_count = sum(1 for d in self._window_trends.values() if d == "UP")
            down_count = sum(1 for d in self._window_trends.values() if d == "DOWN")
            majority = "UP" if up_count > down_count else "DOWN" if down_count > up_count else None
            
            if majority and direction != majority:
                self._diag_log(
                    f"consensus-{coin}",
                    f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                    f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                    15.0,
                )
                return None"""

    if old_consensus not in text:
        raise SystemExit("CONSENSUS block not found")
    text = text.replace(old_consensus, new_consensus, 1)

    old_entry = """        # Entry price filters
        entry_min = getattr(config, "ENTRY_MIN", 0.10)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)

        if ask <= 0.01:
            self._diag_log(f"noask-{coin}-{direction}", f"[NO ASK] {coin} {direction}: ask=0", 30.0)
            return None

        if ask < entry_min:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c", 30.0)
            return None"""

    new_entry = """        # Entry price filters (early window allows cheaper asks)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)
        if early_window:
            entry_min = float(os.getenv("EARLY_ENTRY_MIN", "0.35"))
        else:
            entry_min = getattr(config, "ENTRY_MIN", 0.10)

        if ask <= 0.01:
            self._diag_log(f"noask-{coin}-{direction}", f"[NO ASK] {coin} {direction}: ask=0", 30.0)
            return None

        if ask < entry_min:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c — skip",
                30.0)
            return None"""

    if old_entry not in text:
        raise SystemExit("ENTRY block not found")
    text = text.replace(old_entry, new_entry, 1)

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_morning():
    p = ROOT / "morning_strategy.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if "P3_MIN_WIN_PROB = float(os.getenv" in text:
        print("morning_strategy.py already env-driven")
        return
    old = """import time
from typing import Optional, List, Tuple
from loguru import logger
from dataclasses import replace

from predictor import Prediction
from market_data import MarketInfo


# Phase boundaries in Lima hour/minute"""
    new = """import os
import time
from typing import Optional, List, Tuple
from loguru import logger
from dataclasses import replace

from predictor import Prediction
from market_data import MarketInfo


# Phase boundaries in Lima hour/minute"""
    if old not in text:
        raise SystemExit("morning imports block not found")
    text = text.replace(old, new, 1)

    old_p1 = "P1_MIN_WIN_PROB = 0.80\nP1_MIN_EDGE = 0.10\nP1_MIN_TREND = 0.60"
    new_p1 = """P1_MIN_WIN_PROB = float(os.getenv("MORNING_P1_MIN_PROB", "0.58"))
P1_MIN_EDGE = float(os.getenv("MORNING_P1_MIN_EDGE", "0.08"))
P1_MIN_TREND = float(os.getenv("MORNING_P1_MIN_TREND", "0.50"))"""
    old_p3 = "P3_MIN_WIN_PROB = 0.78\nP3_MIN_EDGE = 0.08\nP3_MIN_TREND = 0.50"
    new_p3 = """P3_MIN_WIN_PROB = float(os.getenv("MORNING_P3_MIN_PROB", "0.58"))
P3_MIN_EDGE = float(os.getenv("MORNING_P3_MIN_EDGE", "0.08"))
P3_MIN_TREND = float(os.getenv("MORNING_P3_MIN_TREND", "0.40"))"""
    text = text.replace(old_p1, new_p1, 1).replace(old_p3, new_p3, 1)
    p.write_text(text, encoding="utf-8")
    print("patched morning_strategy.py")


def patch_order_manager():
    p = ROOT / "order_manager.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    if "_place_lock" not in text:
        old_imp = "import os\nimport time"
        new_imp = "import os\nimport time\nimport threading"
        text = text.replace(old_imp, new_imp, 1)
        old_init = "class OrderManager:\n    \"\"\"Manages order placement, GTC tracking, and window dedup.\"\"\""
        new_init = "class OrderManager:\n    \"\"\"Manages order placement, GTC tracking, and window dedup.\"\"\"\n\n    _place_lock = threading.Lock()"
        text = text.replace(old_init, new_init, 1)

    old_init_end = "            logger.warning(\"No API creds in .env — derived new ones\")\n        return client"
    new_init_end = """            logger.warning("No API creds in .env — derived new ones")
        try:
            v = client.get_version()
            logger.info(f"[CLOB] backend version={v}")
        except Exception as e:
            logger.warning(f"[CLOB] get_version failed: {e}")
        return client"""
    if old_init_end in text and "[CLOB] backend version" not in text:
        text = text.replace(old_init_end, new_init_end, 1)

    old_place = """        try:
            options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)
            order_args = OrderArgs(
                price=limit_price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            order = self.client.create_order(order_args, options)
            result = self.client.post_order(order, order_type)

            matched, avg_price, order_id = self._parse_result(result)"""

    new_place = """        try:
            from py_clob_client_v2.exceptions import PolyApiException
            options = PartialCreateOrderOptions(tick_size="0.01")
            order_args = OrderArgs(
                price=limit_price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            result = None
            with self._place_lock:
                for attempt in range(3):
                    try:
                        if attempt > 0:
                            self.client._ClobClient__cached_version = None
                            self.client.get_version()
                        result = self.client.create_and_post_order(
                            order_args, options, order_type
                        )
                        break
                    except PolyApiException as e:
                        if "order_version_mismatch" not in str(e).lower() or attempt >= 2:
                            raise
                        logger.warning(
                            f"[CLOB] {coin} order_version_mismatch — retry {attempt + 2}/3"
                        )

            matched, avg_price, order_id = self._parse_result(result)"""

    if old_place not in text:
        raise SystemExit("place_bet order block not found")
    text = text.replace(old_place, new_place, 1)

    p.write_text(text, encoding="utf-8")
    print("patched order_manager.py")


def main():
    patch_env()
    patch_predictor()
    patch_morning()
    patch_order_manager()
    print("OK — restart run_bot.py")


if __name__ == "__main__":
    main()
