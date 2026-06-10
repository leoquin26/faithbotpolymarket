#!/usr/bin/env python3
"""Compound mode patches using regex (handles mojibake dashes in comments)."""
import py_compile
import re
import sys

PATH = "/home/ubuntu/v3-bot/predictor.py"


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if "COMPOUND CHEAP" not in text:
        text, n = re.subn(
            r"(\s+min_prob = getattr\(config, \"MIN_WIN_PROB\", 0\.65\)\n)"
            r"\s+if win_prob < min_prob:\n"
            r"\s+self\._diag_log\(f\"lowprob-\{coin\}\", "
            r"f\"\[LOW PROB\] \{coin\} \{direction\}: prob=\{win_prob\*100:.0f\}% < \{min_prob\*100:.0f\}%\", 15\.0\)\n"
            r"\s+return None",
            r"""\1        _cheap_ask = float(os.getenv("COMPOUND_CHEAP_ASK", "0.52"))
        _cheap_prob = float(os.getenv("COMPOUND_MIN_PROB", "0.58"))
        if win_prob < min_prob:
            if ask <= _cheap_ask and win_prob >= _cheap_prob:
                logger.debug(
                    f"[COMPOUND CHEAP] {coin} {direction}: prob={win_prob*100:.0f}% "
                    f"ask={ask*100:.0f}c — cheap-entry bypass"
                )
            else:
                self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}%", 15.0)
                return None""",
            text,
            count=1,
        )
        if n != 1:
            sys.exit(f"cheap prob patch failed n={n}")
        print("patched: compound cheap prob")

    if "CONSENSUS BYPASS" not in text:
        text, n = re.subn(
            r"if majority and direction != majority:\n"
            r"\s+self\._diag_log\(\n"
            r"\s+f\"consensus-\{coin\}\",\n"
            r"\s+f\"\[CONSENSUS\].*?return None",
            """if majority and direction != majority:
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
            text,
            count=1,
            flags=re.DOTALL,
        )
        if n != 1:
            sys.exit(f"consensus patch failed n={n}")
        print("patched: consensus bypass")

    if "COMPOUND_DIST_TREND" not in text:
        text, n = re.subn(
            r"(_min_trend_abs = float\(os\.getenv\(\"MIN_TREND_ABS\", \"0\.40\"\)\))\n"
            r"(\s+if abs\(trend_score\) < _min_trend_abs:)",
            r"""\1
            _dist_trend = float(os.getenv("COMPOUND_DIST_TREND", "0.0012"))
            if abs(dist_pct) >= _dist_trend:
                _min_trend_abs = min(_min_trend_abs, float(os.getenv("COMPOUND_MIN_TREND", "0.14")))
\2""",
            text,
            count=1,
        )
        if n != 1:
            sys.exit(f"weak trend patch failed n={n}")
        print("patched: weak trend dist bypass")

    if "COMPOUND_FLIP_MULT" not in text:
        text, n = re.subn(
            r"if opposite >= 3 and abs\(trend_score\) < FLIP_TREND_MIN:",
            """_flip_dist = float(os.getenv("COMPOUND_FLIP_DIST", "0.0010"))
            _flip_min = FLIP_TREND_MIN * float(os.getenv("COMPOUND_FLIP_MULT", "0.65")) if abs(dist_pct) >= _flip_dist else FLIP_TREND_MIN
            if opposite >= 3 and abs(trend_score) < _flip_min:""",
            text,
            count=1,
        )
        if n != 1:
            sys.exit(f"flip guard patch failed n={n}")
        text = text.replace(
            "need |trend|>={FLIP_TREND_MIN} to flip",
            "need |trend|>={_flip_min:.2f} to flip",
            1,
        )
        print("patched: flip guard dist bypass")

    if "\nimport os\n" not in text and "import os" not in text.split("\n")[:30]:
        text = text.replace("import math\nimport time", "import math\nimport os\nimport time", 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)

    py_compile.compile(PATH, doraise=True)
    print("OK:", PATH)


if __name__ == "__main__":
    main()
