#!/usr/bin/env python3
"""Apply midnight stats fix to dashboard_v3 parsers on EC2 (run on server)."""
from pathlib import Path

P15 = Path("/home/ubuntu/v3-bot/dashboard_v3/log_parser.py")
P5 = Path("/home/ubuntu/v3-bot/dashboard_v3/log_parser_5m.py")


def patch_file(path: Path, is_5m: bool) -> None:
    src = path.read_text()
    orig = src

    # 1) Insert RE_STATS_DAY + _tail_stats_date after _LOG_DIR
    log_dir_line = '_LOG_DIR = Path("/home/ubuntu/v3-bot/logs")\n'
    if "_tail_stats_date" in src:
        print(f"[SKIP] already patched: {path}")
        return

    if log_dir_line not in src:
        raise SystemExit(f"{path}: _LOG_DIR line not found")

    regex = (
        r'^bot_5m_(\d{4}-\d{2}-\d{2})\.log$'
        if is_5m
        else r'^bot_(\d{4}-\d{2}-\d{2})\.log$'
    )
    insert = (
        log_dir_line
        + "\n"
        + f'RE_STATS_DAY = re.compile(r"{regex}")\n'
        + "_tail_stats_date: str | None = None\n"
    )
    src = src.replace(log_dir_line, insert, 1)

    # 2) Replace _active_log_path
    if is_5m:
        old = (
            "def _active_log_path() -> Path:\n"
            "    today_path = _LOG_DIR / f\"bot_5m_{datetime.now().strftime('%Y-%m-%d')}.log\"\n"
            "    if today_path.exists():\n"
            "        return today_path\n"
            "    return _LEGACY_LOG_FILE\n"
        )
        new = (
            "def _active_log_path() -> Path:\n"
            "    # Never fall back to legacy at midnight — stale file poisons WIN/LOSS.\n"
            "    return _LOG_DIR / f\"bot_5m_{datetime.now().strftime('%Y-%m-%d')}.log\"\n"
        )
    else:
        old = (
            "def _active_log_path() -> Path:\n"
            '    """Return today\'s bot log file, falling back to legacy path."""\n'
            "    today_path = _LOG_DIR / f\"bot_{datetime.now().strftime('%Y-%m-%d')}.log\"\n"
            "    if today_path.exists():\n"
            "        return today_path\n"
            "    return _LEGACY_LOG_FILE\n"
        )
        new = (
            "def _active_log_path() -> Path:\n"
            '    """Dated log only — tail loop waits until bot creates the file."""\n'
            "    return _LOG_DIR / f\"bot_{datetime.now().strftime('%Y-%m-%d')}.log\"\n"
        )

    if old not in src:
        raise SystemExit(f"{path}: _active_log_path block mismatch")
    src = src.replace(old, new, 1)

    # 3) _parse_line: stats_day
    src = src.replace(
        "    log_ts = _log_hms_to_epoch(t)\n\n    today = _today_key()\n\n    with _lock:",
        "    log_ts = _log_hms_to_epoch(t)\n\n"
        "    stats_day = _tail_stats_date or _today_key()\n\n    with _lock:",
        1,
    )

    old_sig = (
        "        counters = _today_counters[today]\n"
        '        counters["total"] += 1\n'
    )
    new_sig = (
        "        counters = _today_counters[stats_day]\n"
        '        counters["total"] += 1\n'
    )
    if old_sig not in src:
        raise SystemExit(f"{path}: counters block mismatch")
    src = src.replace(old_sig, new_sig, 1)

    # 4) Trade rows: add day field (single-line dicts in 5m, multi-line in 15m)
    if is_5m:
        src = src.replace(
            '_today_trades.append({\n                "t": t, "type": "ORDER",',
            '_today_trades.append({\n                "day": stats_day, "t": t, "type": "ORDER",',
            1,
        )
        src = src.replace(
            '_today_trades.append({\n                "t": t, "type": "FILLED",',
            '_today_trades.append({\n                "day": stats_day, "t": t, "type": "FILLED",',
            1,
        )
        src = src.replace(
            '_today_trades.append({\n                "t": t, "type": "WIN",',
            '_today_trades.append({\n                "day": stats_day, "t": t, "type": "WIN",',
            1,
        )
        src = src.replace(
            '_today_trades.append({\n                "t": t, "type": "LOSS",',
            '_today_trades.append({\n                "day": stats_day, "t": t, "type": "LOSS",',
            1,
        )
    else:
        src = src.replace(
            '            _today_trades.append({\n                "t": t,\n                "type": "ORDER",',
            '            _today_trades.append({\n                "day": stats_day,\n                "t": t,\n                "type": "ORDER",',
            1,
        )
        src = src.replace(
            '            _today_trades.append({\n                "t": t,\n                "type": "FILLED",',
            '            _today_trades.append({\n                "day": stats_day,\n                "t": t,\n                "type": "FILLED",',
            1,
        )
        src = src.replace(
            '            _today_trades.append({\n                "t": t,\n                "type": "WIN",',
            '            _today_trades.append({\n                "day": stats_day,\n                "t": t,\n                "type": "WIN",',
            1,
        )
        src = src.replace(
            '            _today_trades.append({\n                "t": t,\n                "type": "LOSS",',
            '            _today_trades.append({\n                "day": stats_day,\n                "t": t,\n                "type": "LOSS",',
            1,
        )

    # 5) Tail loop: set global before open()
    needle = (
        "            if not current_path.exists():\n"
        "                time.sleep(2)\n"
        "                continue\n\n"
        "            st = current_path.stat()\n"
    )
    insert = (
        "            if not current_path.exists():\n"
        "                time.sleep(2)\n"
        "                continue\n\n"
        "            global _tail_stats_date\n"
        "            msd = RE_STATS_DAY.match(current_path.name)\n"
        "            _tail_stats_date = msd.group(1) if msd else _today_key()\n\n"
        "            st = current_path.stat()\n"
    )
    if needle not in src:
        raise SystemExit(f"{path}: tail needle not found")
    src = src.replace(needle, insert, 1)

    # Remove duplicate `with open` block — we inserted before st.stat; verify structure
    old_gt = (
        "def get_today_trades() -> list[dict]:\n"
        "    with _lock:\n"
        "        return list(_today_trades)\n"
    )
    new_gt = (
        "def get_today_trades() -> list[dict]:\n"
        "    today = _today_key()\n"
        "    with _lock:\n"
        "        return [t for t in _today_trades if t.get(\"day\") == today]\n"
    )
    if old_gt not in src:
        raise SystemExit(f"{path}: get_today_trades mismatch")
    src = src.replace(old_gt, new_gt, 1)

    if src == orig:
        raise SystemExit(f"{path}: no changes made")

    bak = path.with_suffix(path.suffix + ".bak_midnight_fix")
    bak.write_text(orig)
    path.write_text(src)
    print(f"[OK] patched {path} (backup {bak})")


def main():
    patch_file(P15, is_5m=False)
    patch_file(P5, is_5m=True)
    print("[DONE]")


if __name__ == "__main__":
    main()
