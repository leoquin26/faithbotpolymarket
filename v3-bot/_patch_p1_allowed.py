#!/usr/bin/env python3
"""Expand P1 allowed coins to BTC/ETH/SOL/XRP — env-driven."""
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/morning_strategy.py"

OLD = """P1_ALLOWED = {"BTC", "ETH"}"""

NEW = """import os as _p1_os
_P1_DEFAULT = "BTC,ETH,SOL,XRP"
P1_ALLOWED = set(s.strip().upper() for s in _p1_os.getenv("P1_ALLOWED", _P1_DEFAULT).split(",") if s.strip())"""


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()
    if "P1_DEFAULT" in text:
        print("already patched")
        return
    if OLD not in text:
        sys.exit("anchor not found")
    text = text.replace(OLD, NEW, 1)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    py_compile.compile(PATH, doraise=True)
    print("OK")


if __name__ == "__main__":
    main()
