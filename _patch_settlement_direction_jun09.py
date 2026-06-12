#!/usr/bin/env python3
"""Settlement-first direction: predictor.py + order_manager GTC strike fix."""
from pathlib import Path

PRED_OLD = '''        # ── Step 2: Convert trend to probability using sigmoid ──
        # Steepness controls how quickly trend translates to confidence
        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        raw_prob = _sigmoid(trend_score * 3.0)

        # Blend: trend + BS + Polymarket book (improves direction vs spot-only)
        # Book implied UP prob: prefer ask-based (executable), not stale mid
        _ua_b, _da_b = float(up_ask or 0), float(down_ask or 0)
        if _ua_b > 0.02 and _da_b > 0.02:
            book_up = _ua_b / (_ua_b + _da_b)
        elif up_mid > 0.01 and down_mid > 0.01:
            book_up = up_mid / (up_mid + down_mid)
        elif up_mid > 0.01:
            book_up = up_mid
        elif down_mid > 0.01:
            book_up = 1.0 - down_mid
        else:
            book_up = 0.5
        book_up = max(0.01, min(0.99, book_up))
        # Peak blend: trend + BS only (book used in DIR VOTE, not prob poison)
        combined_prob = 0.70 * raw_prob + 0.30 * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))

        # Distance penalty: when price is near strike, dampen confidence toward 50%
        # abs(dist_pct) < 0.05% means price is within 0.05% of strike = coin flip territory
        # Scale: at dist=0 -> 40% penalty, at dist=0.1% -> 0% penalty
        DIST_THRESHOLD = float(os.getenv("ACCURACY_DIST_PENALTY", "0.0008"))
        _dist_skip_penalty = abs(dist_pct) >= float(os.getenv("DIST_PENALTY_SKIP_ABOVE", "0.001"))
        if not _dist_skip_penalty and abs(dist_pct) < DIST_THRESHOLD:
            dist_factor = abs(dist_pct) / DIST_THRESHOLD  # 0.0 at strike, 1.0 at threshold
            penalty = 0.40 * (1.0 - dist_factor)  # 40% pull toward 0.5 when at strike
            combined_prob = combined_prob * (1.0 - penalty) + 0.50 * penalty
            logger.debug(
                f"[DIST PENALTY] {coin}: dist={dist_pct*100:+.4f}% factor={dist_factor:.2f} "
                f"penalty={penalty:.2f} prob_adj={combined_prob:.1%}"
            )

        # ── Step 3: Direction vote (optional; off when ACCURACY_GATE_ON=off) ──
        _accuracy_on = os.getenv("ACCURACY_GATE_ON", "on").lower() not in ("off", "0", "false")
        if _accuracy_on:
            votes_up = votes_down = 0
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
            _dist_clear = abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_SKIP_DIST", "0.0006"))
            _session_vote = sess_cal.get_session().name
            _afternoon_relax = _session_vote in ("AFTERNOON", "MIDDAY")
            _skip_dir_vote = (
                _book_decisive
                or (early_window and _dist_clear)
                or (_afternoon_relax and _dist_clear and abs(trend_score) >= 0.25)
            )
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
            win_prob = combined_prob if is_up else (1.0 - combined_prob)
            votes_up = votes_down = 0
            vote_dir = None'''

PRED_NEW = '''        # ── Step 2: Settlement-first direction (level vs strike at expiry) ──
        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        raw_prob = _sigmoid(trend_score * 3.0)

        _ua_b, _da_b = float(up_ask or 0), float(down_ask or 0)
        if _ua_b > 0.02 and _da_b > 0.02:
            book_up = _ua_b / (_ua_b + _da_b)
        elif up_mid > 0.01 and down_mid > 0.01:
            book_up = up_mid / (up_mid + down_mid)
        elif up_mid > 0.01:
            book_up = up_mid
        elif down_mid > 0.01:
            book_up = 1.0 - down_mid
        else:
            book_up = 0.5
        book_up = max(0.01, min(0.99, book_up))

        _near_dist = float(os.getenv("SETTLEMENT_NEAR_DIST", "0.0012"))
        _min_roc = float(os.getenv("SETTLEMENT_MIN_ROC300", "0.00003"))
        _book_edge = float(os.getenv("SETTLEMENT_BOOK_EDGE", "0.02"))
        _bs_edge = float(os.getenv("SETTLEMENT_BS_EDGE", "0.02"))

        def _dir_from_sign(val: float, edge: float = 0.0) -> Optional[str]:
            if val > edge:
                return "UP"
            if val < -edge:
                return "DOWN"
            return None

        settlement_dir: Optional[str] = None
        if abs(dist_pct) < _near_dist:
            level_dir = _dir_from_sign(dist_pct, 0.0)
            if not level_dir:
                self._diag_log(
                    f"settle-atstrike-{coin}",
                    f"[SETTLEMENT] {coin}: at strike dist={dist_pct*100:+.4f}% — abstain",
                    12.0,
                )
                return None
            roc_dir = _dir_from_sign(roc_300, _min_roc)
            if roc_dir and roc_dir != level_dir:
                self._diag_log(
                    f"settle-roc-{coin}",
                    f"[SETTLEMENT] {coin}: dist→{level_dir} roc300→{roc_dir} "
                    f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps) — abstain",
                    12.0,
                )
                return None
            book_dir = "UP" if book_up >= (0.50 + _book_edge) else (
                "DOWN" if book_up <= (0.50 - _book_edge) else None
            )
            if book_dir and book_dir != level_dir:
                self._diag_log(
                    f"settle-book-{coin}",
                    f"[SETTLEMENT] {coin}: dist→{level_dir} book→{book_dir} "
                    f"(book_up={book_up:.2f}) — abstain",
                    12.0,
                )
                return None
            bs_dir = "UP" if base_up_prob >= (0.50 + _bs_edge) else (
                "DOWN" if base_up_prob <= (0.50 - _bs_edge) else None
            )
            if bs_dir and bs_dir != level_dir:
                self._diag_log(
                    f"settle-bs-{coin}",
                    f"[SETTLEMENT] {coin}: dist→{level_dir} BS→{bs_dir} "
                    f"(N(d2)={base_up_prob:.1%}) — abstain",
                    12.0,
                )
                return None
            settlement_dir = level_dir
            combined_prob = 0.50 * base_up_prob + 0.30 * book_up + 0.20 * raw_prob
        else:
            bs_dir = "UP" if base_up_prob >= 0.5 else "DOWN"
            dist_dir = "UP" if dist_pct > 0 else "DOWN"
            if bs_dir != dist_dir:
                self._diag_log(
                    f"settle-far-{coin}",
                    f"[SETTLEMENT] {coin}: BS→{bs_dir} dist→{dist_dir} "
                    f"(dist={dist_pct*100:+.3f}% N(d2)={base_up_prob:.1%}) — abstain",
                    12.0,
                )
                return None
            settlement_dir = bs_dir
            combined_prob = 0.55 * base_up_prob + 0.25 * raw_prob + 0.20 * book_up

        combined_prob = max(0.01, min(0.99, combined_prob))

        DIST_THRESHOLD = float(os.getenv("ACCURACY_DIST_PENALTY", "0.0008"))
        if abs(dist_pct) < DIST_THRESHOLD:
            dist_factor = abs(dist_pct) / DIST_THRESHOLD
            penalty = 0.40 * (1.0 - dist_factor)
            combined_prob = combined_prob * (1.0 - penalty) + 0.50 * penalty

        direction = settlement_dir
        is_up = direction == "UP"
        win_prob = combined_prob if is_up else (1.0 - combined_prob)
        book_side = book_up if is_up else (1.0 - book_up)
        win_prob = max(0.01, min(0.99, 0.60 * book_side + 0.40 * win_prob))

        votes_up = votes_down = 0
        if abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_MIN_DIST", "0.00005")):
            votes_up += 1 if dist_pct > 0 else 0
            votes_down += 1 if dist_pct < 0 else 0
        if abs(roc_300) >= _min_roc:
            votes_up += 1 if roc_300 > 0 else 0
            votes_down += 1 if roc_300 < 0 else 0
        if book_up >= 0.52:
            votes_up += 1
        elif book_up <= 0.48:
            votes_down += 1
        vote_dir = "UP" if votes_up >= 2 else ("DOWN" if votes_down >= 2 else None)
        if vote_dir and vote_dir != direction:
            self._diag_log(
                f"dirvote-{coin}",
                f"[DIR VOTE] {coin}: settlement={direction} vote={vote_dir} "
                f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps book={book_up:.2f} "
                f"{votes_up}UP/{votes_down}DN) — skip",
                12.0,
            )
            return None'''

OM_HELPER = '''    def _strike_fields(self, pred, window_start: int) -> dict:
        mi = pred.market_info if pred and hasattr(pred, "market_info") else None
        return {
            "window_start": window_start,
            "strike": mi.threshold_price if mi else 0,
            "slug": getattr(mi, "slug", "") if mi else "",
            "strike_source": getattr(mi, "strike_source", "") if mi else "",
            "timeframe": getattr(mi, "timeframe", "15m") if mi else "15m",
        }

'''

OM_GTC_PLACE_OLD = '''                self.active_gtc[order_id or "unknown"] = {
                    "coin": coin,
                    "direction": direction,
                    "token_id": token_id,
                    "price": limit_price,
                    "shares": shares,
                    "placed_at": time.time(),
                    "window_start": window_start,
                    "prediction": pred,
                }'''

OM_GTC_PLACE_NEW = '''                self.active_gtc[order_id or "unknown"] = {
                    "coin": coin,
                    "direction": direction,
                    "token_id": token_id,
                    "price": limit_price,
                    "shares": shares,
                    "placed_at": time.time(),
                    "prediction": pred,
                    **self._strike_fields(pred, window_start),
                }'''

OM_GTC_FOK_OLD = '''                    self.active_gtc[gtc_oid or "unknown"] = {
                        "coin": coin, "direction": direction, "token_id": token_id,
                        "price": limit_price, "shares": shares, "placed_at": time.time(),
                        "window_start": window_start, "prediction": pred,
                    }'''

OM_GTC_FOK_NEW = '''                    self.active_gtc[gtc_oid or "unknown"] = {
                        "coin": coin, "direction": direction, "token_id": token_id,
                        "price": limit_price, "shares": shares, "placed_at": time.time(),
                        "prediction": pred,
                        **self._strike_fields(pred, window_start),
                    }'''

OM_FILL_OLD = '''                    self.set_position(info["coin"], {
                        "coin": info["coin"],
                        "side": info["direction"],
                        "entry_price": fill_price,
                        "shares": int(filled_qty) if filled_qty > 0 else info["shares"],
                        "token_id": info["token_id"],
                        "window_start": info["window_start"],
                        "strike": info.get("strike", 0),
                    })'''

OM_FILL_NEW = '''                    self.set_position(info["coin"], {
                        "coin": info["coin"],
                        "side": info["direction"],
                        "entry_price": fill_price,
                        "shares": int(filled_qty) if filled_qty > 0 else info["shares"],
                        "token_id": info["token_id"],
                        "window_start": info.get("window_start", 0),
                        "strike": info.get("strike", 0),
                        "slug": info.get("slug", ""),
                        "strike_source": info.get("strike_source", ""),
                        "timeframe": info.get("timeframe", "15m"),
                    })'''


def patch_file(path: Path, replacements: list) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new, label in replacements:
        if old not in text:
            raise SystemExit(f"MISSING [{label}] in {path}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"OK {path}")


PRED_STEP1_OLD = '''        # ── Step 1: Trend-based direction (primary signal) ──
        # Use actual price movement to determine direction, not BS math'''

PRED_STEP1_NEW = '''        # ── Step 1: Trend score (confidence only — direction set in Step 2 settlement) ──'''

ENGINE_FLIP_OLD = '''            if _mom_down and _book_screams_down and direction == "UP":
                direction = "DOWN"
                is_up = False
                win_prob = max(0.01, min(0.99, 1.0 - combined_prob))
                ask = down_ask
                mid = down_mid
                depth = down_depth
                token_id = info.down_token_id
                self._engine_conviction[coin] = "DOWN"
                _forced = True
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONVICTION] {coin}: mom+book DOWN "
                    f"(roc60={roc_60*10000:+.1f}bps roc300={roc_300*10000:+.1f}bps "
                    f"book={book_up:.2f} up_ask={_ua*100:.0f}c down_ask={_da*100:.0f}c) "
                    f"— blocked UP flip, betting DOWN",
                    12.0,
                )
            elif _mom_up and _book_screams_up and direction == "DOWN":
                direction = "UP"
                is_up = True
                win_prob = max(0.01, min(0.99, combined_prob))
                ask = up_ask
                mid = up_mid
                depth = up_depth
                token_id = info.up_token_id
                self._engine_conviction[coin] = "UP"
                _forced = True
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONVICTION] {coin}: mom+book UP "
                    f"(roc60={roc_60*10000:+.1f}bps roc300={roc_300*10000:+.1f}bps "
                    f"book={book_up:.2f}) — blocked DOWN flip, betting UP",
                    12.0,
                )'''

ENGINE_FLIP_NEW = '''            if _mom_down and _book_screams_down and direction == "UP":
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONFLICT] {coin}: settlement UP vs mom+book DOWN — skip",
                    12.0,
                )
                return None
            elif _mom_up and _book_screams_up and direction == "DOWN":
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONFLICT] {coin}: settlement DOWN vs mom+book UP — skip",
                    12.0,
                )
                return None'''


def main():
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    pred = root / "predictor.py"
    om = root / "order_manager.py"

    patch_file(pred, [
        (PRED_STEP1_OLD, PRED_STEP1_NEW, "step1 comment"),
        (PRED_OLD, PRED_NEW, "predictor settlement"),
        (ENGINE_FLIP_OLD, ENGINE_FLIP_NEW, "engine flip disable"),
    ])
    if "def _strike_fields" not in om.read_text(encoding="utf-8"):
        om_text = om.read_text(encoding="utf-8")
        anchor = "    def place_bet(self, pred: Prediction) -> bool:"
        if anchor not in om_text:
            raise SystemExit("place_bet anchor missing")
        om.write_text(om_text.replace(anchor, OM_HELPER + anchor, 1), encoding="utf-8")
    patch_file(om, [
        (OM_GTC_PLACE_OLD, OM_GTC_PLACE_NEW, "gtc place"),
        (OM_GTC_FOK_OLD, OM_GTC_FOK_NEW, "gtc fok fallback"),
        (OM_FILL_OLD, OM_FILL_NEW, "gtc fill strike"),
    ])
    print("PATCH OK")


if __name__ == "__main__":
    main()
