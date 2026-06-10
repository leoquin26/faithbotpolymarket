"""Same date-aware fix for 5m parser. Bot writes logs/bot_5m_YYYY-MM-DD.log."""
from pathlib import Path

PATH = Path("/home/ubuntu/v3-bot/dashboard_v3/log_parser_5m.py")
src = PATH.read_text()


def replace_once(haystack: str, needle: str, repl: str, label: str) -> str:
    n = haystack.count(needle)
    if n != 1:
        raise SystemExit(f"[FAIL] {label}: expected 1 match, got {n}")
    print(f"[OK] {label}")
    return haystack.replace(needle, repl, 1)


# 1) LOG_FILE constant -> helper
src = replace_once(
    src,
    'LOG_FILE = Path("/home/ubuntu/v3-bot/v3_bot_5m.log")',
    """# [DASH-PATH-FIX 2026-05-08] 5m bot writes logs/bot_5m_YYYY-MM-DD.log via
# loguru midnight rotation. Resolve today's path each tail iteration.
_LEGACY_LOG_FILE = Path("/home/ubuntu/v3-bot/v3_bot_5m.log")
_LOG_DIR = Path("/home/ubuntu/v3-bot/logs")


def _active_log_path() -> Path:
    today_path = _LOG_DIR / f"bot_5m_{datetime.now().strftime('%Y-%m-%d')}.log"
    if today_path.exists():
        return today_path
    return _LEGACY_LOG_FILE


LOG_FILE = _LEGACY_LOG_FILE""",
    "5m: constant -> helper",
)

# 2) tail loop
src = replace_once(
    src,
    """def _tail_loop(bootstrap_lines: int = 1500, poll_interval: float = 0.5):
    global _file_pos, _file_inode
    logger.info(f"5m tailer starting on {LOG_FILE} (bootstrap={bootstrap_lines})")
    bootstrapped = False

    while True:
        try:
            if not LOG_FILE.exists():
                time.sleep(2)
                continue

            st = LOG_FILE.stat()
            if st.st_ino != _file_inode:
                _file_inode = st.st_ino
                _file_pos = 0

            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as fh:
                if not bootstrapped and bootstrap_lines > 0:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    chunk = min(size, 200_000)
                    fh.seek(size - chunk)
                    tail = fh.read()
                    lines = tail.splitlines()[-bootstrap_lines:]
                    for ln in lines:
                        _parse_line(ln)
                    _file_pos = size
                    bootstrapped = True
                    logger.info(f"5m bootstrap complete ({len(lines)} lines)")
                else:
                    fh.seek(_file_pos)
                    new = fh.read()
                    if new:
                        for ln in new.splitlines():
                            _parse_line(ln)
                        _file_pos = fh.tell()

            time.sleep(poll_interval)
        except Exception as e:
            logger.exception(f"5m tailer error: {e}")
            time.sleep(2)""",
    """def _tail_loop(bootstrap_lines: int = 1500, poll_interval: float = 0.5):
    global _file_pos, _file_inode
    current_path = _active_log_path()
    logger.info(f"5m tailer starting on {current_path} (bootstrap={bootstrap_lines})")
    bootstrapped = False

    while True:
        try:
            new_path = _active_log_path()
            if new_path != current_path:
                logger.info(
                    f"5m active log path changed: {current_path} -> {new_path}"
                )
                current_path = new_path
                bootstrapped = False
                _file_pos = 0
                _file_inode = -1

            if not current_path.exists():
                time.sleep(2)
                continue

            st = current_path.stat()
            if st.st_ino != _file_inode:
                _file_inode = st.st_ino
                _file_pos = 0

            with open(current_path, "r", encoding="utf-8", errors="ignore") as fh:
                if not bootstrapped and bootstrap_lines > 0:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    chunk = min(size, 200_000)
                    fh.seek(max(0, size - chunk))
                    tail = fh.read()
                    lines = tail.splitlines()[-bootstrap_lines:]
                    for ln in lines:
                        _parse_line(ln)
                    _file_pos = size
                    bootstrapped = True
                    logger.info(
                        f"5m bootstrap complete ({len(lines)} lines from "
                        f"{current_path.name})"
                    )
                else:
                    fh.seek(_file_pos)
                    new = fh.read()
                    if new:
                        for ln in new.splitlines():
                            _parse_line(ln)
                        _file_pos = fh.tell()

            time.sleep(poll_interval)
        except Exception as e:
            logger.exception(f"5m tailer error: {e}")
            time.sleep(2)""",
    "5m: tail loop date-aware",
)

# 3) mtime helper if it uses LOG_FILE.stat()
src = replace_once(
    src,
    "        return LOG_FILE.stat().st_mtime",
    "        return _active_log_path().stat().st_mtime",
    "5m: mtime -> resolved path",
)

PATH.write_text(src)
print("[DONE] dashboard_v3/log_parser_5m.py patched")
