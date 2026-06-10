#!/usr/bin/env python3
"""
Compound mode — more small high-R:R trades without raising bet size.
Patches predictor.py (idempotent anchors).
"""
import os
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/predictor.py"


def patch_once(text, old, new, label):
    if old not in text:
        if new.split("\n", 1)[0] in text:
            print(f"  skip (already): {label}")
            return text
        sys.exit(f"anchor not found: {label}")
    return text.replace(old, new, 1)


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    # 1) CONSENSUS bypass (env was set but never wired)
    text = patch_once(
        text,
        """            if majority and direction != majority:
                self._diag_log(
                    f"consensus-{coin}",
                    f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                    f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                    15.0,
                )
                return None""",
        """            if majority and direction != majority:
                _consensus_bypass = float(os.getenv("CONSENSUS_BYPASS_MIN_PROB", "0.78"))
                _consensus_dist = float(os.getenv("CONSENSUS_BYPASS_MIN_DIST", "0.0012"))
                if win_prob >= _consensus_bypass or abs(dist_pct) >= _consensus_dist:
                    self._diag_log(
                        f"consensus-bypass-{coin}",
                        f"[CONSENSUS BYPASS] {coin} {direction}: minority vs {majority} "
                        f"({up_count}UP/{down_count}DOWN) prob={win_prob*100:.0f}% "
                        f"dist={dist_pct*100:.3f}% — allowing",
                        30.0,
                    )
                else:
                    self._diag_log(
                        f"consensus-{coin}",
                        f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                        f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                        15.0,
                    )
                    return None""",
        "consensus bypass",
    )

    # 2) WEAK TREND — allow smaller |trend| when price has moved off strike
    text = patch_once(
        text,
        """            _min_trend_abs = float(os.getenv("MIN_TREND_ABS", "0.40"))
            if abs(trend_score) < _min_trend_abs:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"roc60={roc_60*10000:+.1f}bps roc120={roc_120*10000:+.1f}bps — need {_min_trend_abs:.2f}+",
                    15.0,
                )
                return None""",
        """            _min_trend_abs = float(os.getenv("MIN_TREND_ABS", "0.40"))
            _dist_trend = float(os.getenv("COMPOUND_DIST_TREND", "0.0012"))
            if abs(dist_pct) >= _dist_trend:
                _min_trend_abs = min(_min_trend_abs, float(os.getenv("COMPOUND_MIN_TREND", "0.14")))
            if abs(trend_score) < _min_trend_abs:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"roc60={roc_60*10000:+.1f}bps roc120={roc_120*10000:+.1f}bps — need {_min_trend_abs:.2f}+",
                    15.0,
                )
                return None""",
        "weak trend dist bypass",
    )

    # 3) FLIP GUARD — softer when price has clear distance from strike
    text = patch_once(
        text,
        """            if opposite >= 3 and abs(trend_score) < FLIP_TREND_MIN:
                self._diag_log(
                    f"flipguard-{coin}",
                    f"[FLIP GUARD] {coin} {direction}: recent={'->'.join(recent_hist)} "
                    f"trend={trend_score:+.2f} — need |trend|>={FLIP_TREND_MIN} to flip",
                    15.0,
                )
                return None""",
        """            _flip_dist = float(os.getenv("COMPOUND_FLIP_DIST", "0.0010"))
            _flip_min = FLIP_TREND_MIN * float(os.getenv("COMPOUND_FLIP_MULT", "0.70")) if abs(dist_pct) >= _flip_dist else FLIP_TREND_MIN
            if opposite >= 3 and abs(trend_score) < _flip_min:
                self._diag_log(
                    f"flipguard-{coin}",
                    f"[FLIP GUARD] {coin} {direction}: recent={'->'.join(recent_hist)} "
                    f"trend={trend_score:+.2f} — need |trend|>={_flip_min:.2f} to flip",
                    15.0,
                )
                return None""",
        "flip guard dist bypass",
    )

    # 4) LOW PROB — allow cheap entries with moderate prob (good R:R for $1-4 bets)
    text = patch_once(
        text,
        """        min_prob = getattr(config, "MIN_WIN_PROB", 0.65)
        if win_prob < min_prob:
            self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}%", 15.0)
            return None""",
        """        min_prob = getattr(config, "MIN_WIN_PROB", 0.65)
        _cheap_ask = float(os.getenv("COMPOUND_CHEAP_ASK", "0.52"))
        _cheap_prob = float(os.getenv("COMPOUND_MIN_PROB", "0.58"))
        if win_prob < min_prob:
            if ask <= _cheap_ask and win_prob >= _cheap_prob:
                logger.debug(
                    f"[COMPOUND CHEAP] {coin} {direction}: prob={win_prob*100:.0f}% "
                    f"ask={ask*100:.0f}c — cheap-entry bypass (floor {_cheap_prob*100:.0f}%)"
                )
            else:
                self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}%", 15.0)
                return None""",
        "compound cheap prob bypass",
    )

    if "import os\nimport time" not in text and "\nimport os\n" not in text:
        text = text.replace("import math\nimport time", "import math\nimport os\nimport time", 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)

    py_compile.compile(PATH, doraise=True)
    print("OK:", PATH)


if __name__ == "__main__":
    main()
