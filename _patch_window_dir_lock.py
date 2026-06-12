#!/usr/bin/env python3
"""Block opposite-direction bets in the same 15m window across coins."""
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/run_bot.py"

ANCHOR = """                                    _real_edge = _best_m.probability - _clob_ask
                                        _best_m.entry_price = _clob_ask
                                        _best_m.edge = _real_edge

                                    # Half Kelly sizing for morning (temporarily)"""

INSERT = """                                    _real_edge = _best_m.probability - _clob_ask
                                        _best_m.entry_price = _clob_ask
                                        _best_m.edge = _real_edge

                                    # Same-window direction lock: don't hedge BTC UP vs ETH DOWN.
                                    _ws = getattr(_best_m.market_info, "window_start", None)
                                    _dir_conflict = False
                                    for _pc, _pp in orders.positions.items():
                                        if _pp.get("window_start") == _ws and _pp.get("direction") != _best_m.direction:
                                            logger.info(
                                                f"[WINDOW DIR LOCK] {_best_m.coin} {_best_m.direction}: "
                                                f"conflicts with {_pc} {_pp.get('direction')} same window"
                                            )
                                            _dir_conflict = True
                                            break
                                    if _dir_conflict:
                                        unlock_window(_best_m.coin, _best_m.market_info.window_start)
                                        time.sleep(config.SCAN_INTERVAL)
                                        continue

                                    # Half Kelly sizing for morning (temporarily)"""

AFTERNOON_ANCHOR = """                            real_edge = best.probability - clob_ask
                            best.entry_price = clob_ask
                            best.edge = real_edge

                            if real_edge < config.MIN_EDGE:"""

AFTERNOON_INSERT = """                            real_edge = best.probability - clob_ask
                            best.entry_price = clob_ask
                            best.edge = real_edge

                            _ws = getattr(best.market_info, "window_start", None)
                            _dir_conflict = False
                            for _pc, _pp in orders.positions.items():
                                if _pp.get("window_start") == _ws and _pp.get("direction") != best.direction:
                                    logger.info(
                                        f"[WINDOW DIR LOCK] {best.coin} {best.direction}: "
                                        f"conflicts with {_pc} {_pp.get('direction')} same window"
                                    )
                                    _dir_conflict = True
                                    break
                            if _dir_conflict:
                                unlock_window(best.coin, best.market_info.window_start)
                                continue

                            if real_edge < config.MIN_EDGE:"""


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if "WINDOW DIR LOCK" in text:
        print("already patched")
        return

    if ANCHOR not in text:
        sys.exit("morning anchor not found")
    text = text.replace(ANCHOR, INSERT, 1)

    if AFTERNOON_ANCHOR in text:
        text = text.replace(AFTERNOON_ANCHOR, AFTERNOON_INSERT, 1)
        print("afternoon patched")

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)

    py_compile.compile(PATH, doraise=True)
    print("OK:", PATH)


if __name__ == "__main__":
    main()
