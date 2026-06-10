#!/usr/bin/env python3
"""Restore peak trade rate: fix 75s warmup bug, relax accuracy gates that killed Jun4 PM trading."""
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
    updates = {
        "MIN_WIN_PROB": "0.62",
        "ACCURACY_CONFIRM_SCANS": "1",
        "ACCURACY_GATE_ON": "off",
        "ACCURACY_DIST_PENALTY": "0.0008",
        "CONSENSUS_GATE_ON": "off",
        "HARD_WARMUP_15M": "20",
        "WARMUP_SEC": "20",
        "MIN_TREND_SCORE": "0.22",
        "SIGMA_FLOOR_MIN": "2.5e-4",
    }
    lines = p.read_text(encoding="utf-8").splitlines()
    out, seen = [], set()
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

    old_warmup = """        # Warmup: need at least 30s of data
        warmup = getattr(config, "WARMUP_SEC", 45)
        if window_age < 75:
            self._diag_log(f"warmup-{coin}", f"[WARMUP] {coin}: {window_age}s < 75s hard min", 30.0)
            return None"""
    new_warmup = """        # Warmup: env-driven (was hardcoded 75s — blocked first 75s every window)
        warmup = int(os.getenv("HARD_WARMUP_15M", os.getenv("WARMUP_SEC", "20")))
        if window_age < warmup:
            self._diag_log(
                f"warmup-{coin}",
                f"[WARMUP] {coin}: {window_age}s < {warmup}s min",
                30.0,
            )
            return None"""
    if old_warmup not in text:
        raise SystemExit("warmup block not found")
    text = text.replace(old_warmup, new_warmup, 1)

    old_sigma = "        SIGMA_FLOOR = 5e-04"
    new_sigma = "        SIGMA_FLOOR = float(os.getenv(\"SIGMA_FLOOR_MIN\", \"2.5e-4\"))"
    if old_sigma in text:
        text = text.replace(old_sigma, new_sigma, 1)

    old_weak = """        else:
            if abs(trend_score) < 0.40:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"roc60={roc_60*10000:+.1f}bps roc120={roc_120*10000:+.1f}bps — need 0.40+",
                    15.0,
                )
                return None"""
    new_weak = """        else:
            _min_trend = float(os.getenv("MIN_TREND_SCORE", "0.22"))
            if abs(trend_score) < _min_trend:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"roc60={roc_60*10000:+.1f}bps roc120={roc_120*10000:+.1f}bps — need {_min_trend:.2f}+",
                    15.0,
                )
                return None"""
    if old_weak not in text:
        raise SystemExit("weak trend block not found")
    text = text.replace(old_weak, new_weak, 1)

    old_dist = """        DIST_THRESHOLD = float(os.getenv("ACCURACY_DIST_PENALTY", "0.0015"))
        if abs(dist_pct) < DIST_THRESHOLD:"""
    new_dist = """        DIST_THRESHOLD = float(os.getenv("ACCURACY_DIST_PENALTY", "0.0008"))
        _dist_skip_penalty = abs(dist_pct) >= float(os.getenv("DIST_PENALTY_SKIP_ABOVE", "0.001"))
        if not _dist_skip_penalty and abs(dist_pct) < DIST_THRESHOLD:"""
    if old_dist not in text:
        raise SystemExit("dist penalty block not found")
    text = text.replace(old_dist, new_dist, 1)

    old_vote_start = "        # ── Step 3: Direction vote (dist, 5m ROC, book) — need 2 of 3 ──"
    new_vote_start = "        # ── Step 3: Direction vote (optional; off when ACCURACY_GATE_ON=off) ──\n        _accuracy_on = os.getenv(\"ACCURACY_GATE_ON\", \"on\").lower() not in (\"off\", \"0\", \"false\")\n        if _accuracy_on:"
    if old_vote_start not in text:
        raise SystemExit("vote start not found")
    text = text.replace(old_vote_start, new_vote_start, 1)

    # Indent vote block body (lines until win_prob = combined_prob after vote)
    old_vote_body = """        votes_up = votes_down = 0
        if abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_MIN_DIST", "0.00005")):
            votes_up += 1 if dist_pct > 0 else 0
            votes_down += 1 if dist_pct < 0 else 0
        if abs(roc_300) >= float(os.getenv("ACCURACY_VOTE_MIN_ROC300", "0.00003")):
            votes_up += 1 if roc_300 > 0 else 0
            votes_down += 1 if roc_300 < 0 else 0
        if book_up >= 0.52:
            votes_up += 1
        elif book_up <= 0.48:
            votes_down += 1
        vote_dir = "UP" if votes_up >= 2 else ("DOWN" if votes_down >= 2 else None)
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
            return None
        if vote_dir:
            direction = vote_dir
            is_up = direction == "UP"
        win_prob = combined_prob if is_up else (1.0 - combined_prob)
        if vote_dir:
            book_side = book_up if is_up else (1.0 - book_up)
            win_prob = max(0.01, min(0.99, 0.65 * book_side + 0.35 * win_prob))"""

    new_vote_body = """            votes_up = votes_down = 0
            if abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_MIN_DIST", "0.00005")):
                votes_up += 1 if dist_pct > 0 else 0
                votes_down += 1 if dist_pct < 0 else 0
            if abs(roc_300) >= float(os.getenv("ACCURACY_VOTE_MIN_ROC300", "0.00003")):
                votes_up += 1 if roc_300 > 0 else 0
                votes_down += 1 if roc_300 < 0 else 0
            if book_up >= 0.52:
                votes_up += 1
            elif book_up <= 0.48:
                votes_down += 1
            vote_dir = "UP" if votes_up >= 2 else ("DOWN" if votes_down >= 2 else None)
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
                return None
            if vote_dir:
                direction = vote_dir
                is_up = direction == "UP"
            win_prob = combined_prob if is_up else (1.0 - combined_prob)
            if vote_dir:
                book_side = book_up if is_up else (1.0 - book_up)
                win_prob = max(0.01, min(0.99, 0.65 * book_side + 0.35 * win_prob))
        else:
            is_up = combined_prob >= 0.5
            direction = "UP" if is_up else "DOWN"
            win_prob = combined_prob if is_up else (1.0 - combined_prob)"""

    if old_vote_body not in text:
        raise SystemExit("vote body not found")
    text = text.replace(old_vote_body, new_vote_body, 1)

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_order_manager_fok_gtc():
    p = ROOT / "order_manager.py"
    text = p.read_text(encoding="utf-8")
    if "[FOK->GTC]" in text:
        print("order_manager FOK->GTC already patched")
        return
    backup(p)
    old = """        except Exception as e:
            logger.error(f"[ERROR] Order failed for {coin}: {e}")
            tg.notify_error(f"Order failed: {coin} {direction}\\n{str(e)[:100]}")
            print(f"\\n  [ERROR] {coin} order failed: {e}")
            return False"""
    new = """        except Exception as e:
            err_l = str(e).lower()
            if ("fully filled" in err_l or "killed" in err_l) and not use_gtc and time_left >= 120:
                logger.info(f"[FOK->GTC] {coin}: FOK killed, posting GTC @ {limit_price*100:.0f}c")
                try:
                    gtc_result = self.client.create_and_post_order(
                        order_args, PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC
                    )
                    gtc_m, gtc_p, gtc_oid = self._parse_result(gtc_result)
                    if gtc_m > 0:
                        cost = gtc_m * gtc_p
                        self.set_position(coin, {
                            "coin": coin, "side": direction, "entry_price": gtc_p,
                            "shares": int(gtc_m), "token_id": token_id,
                            "window_start": window_start,
                            "strike": pred.market_info.threshold_price if pred and hasattr(pred, 'market_info') else 0,
                            "slug": getattr(pred.market_info, "slug", "") if pred and hasattr(pred, 'market_info') else "",
                            "strike_source": getattr(pred.market_info, "strike_source", "") if pred and hasattr(pred, 'market_info') else "",
                            "timeframe": getattr(pred.market_info, "timeframe", "15m") if pred and hasattr(pred, 'market_info') else "15m",
                        })
                        self.daily_trades += 1
                        self.mark_window_traded(coin, window_start, direction)
                        logger.info(f"[FILLED] {coin} {direction} | {int(gtc_m)} shares @ {gtc_p*100:.0f}c (GTC)")
                        return True
                    self.active_gtc[gtc_oid or "unknown"] = {
                        "coin": coin, "direction": direction, "token_id": token_id,
                        "price": limit_price, "shares": shares, "placed_at": time.time(),
                        "window_start": window_start, "prediction": pred,
                    }
                    self.mark_window_traded(coin, window_start, direction)
                    logger.info(f"[GTC] Pending after FOK miss: {coin} @ {limit_price*100:.0f}c")
                    return True
                except Exception as gtc_e:
                    logger.warning(f"[FOK->GTC] {coin} fallback failed: {gtc_e}")
            logger.error(f"[ERROR] Order failed for {coin}: {e}")
            tg.notify_error(f"Order failed: {coin} {direction}\\n{str(e)[:100]}")
            print(f"\\n  [ERROR] {coin} order failed: {e}")
            return False"""
    if old not in text:
        raise SystemExit("except block not found in order_manager")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched order_manager FOK->GTC fallback")


def main():
    patch_env()
    patch_predictor()
    patch_order_manager_fok_gtc()
    print("OK — restart run_bot.py")


if __name__ == "__main__":
    main()
