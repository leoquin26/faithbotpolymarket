"""Make dashboard_v3/log_parser.py read today's bot log file (date-aware).

Fix for the dashboard freezing 2h behind the bots: parser was hard-coded to
/home/ubuntu/v3-bot/v3_bot.log, but our restored loguru config writes to
logs/bot_YYYY-MM-DD.log with midnight rotation. Patch:

  1. Replace LOG_FILE constant with _active_log_path() helper that resolves
     today's file and falls back to legacy v3_bot.log only when today's
     file is missing.
  2. _tail_loop() resolves the path every iteration. A path change is
     treated as a rotation (re-bootstrap last N lines so the operator
     sees recent context, then continue tailing).
  3. get_last_file_mtime() uses the resolved path too.
"""
from pathlib import Path

PATH = Path("/home/ubuntu/v3-bot/dashboard_v3/log_parser.py")
src = PATH.read_text()


def replace_once(haystack: str, needle: str, repl: str, label: str) -> str:
    n = haystack.count(needle)
    if n != 1:
        raise SystemExit(f"[FAIL] {label}: expected 1 match, got {n}")
    print(f"[OK] {label}")
    return haystack.replace(needle, repl, 1)


# 1) Replace LOG_FILE constant with a helper that auto-rotates daily.
src = replace_once(
    src,
    'LOG_FILE = Path("/home/ubuntu/v3-bot/v3_bot.log")',
    """# [DASH-PATH-FIX 2026-05-08] Bot writes logs/bot_YYYY-MM-DD.log via loguru
# midnight rotation. Resolve today's path on each tail iteration so the
# parser switches files automatically at midnight.
_LEGACY_LOG_FILE = Path("/home/ubuntu/v3-bot/v3_bot.log")
_LOG_DIR = Path("/home/ubuntu/v3-bot/logs")


def _active_log_path() -> Path:
    \"\"\"Return today's bot log file, falling back to legacy path.\"\"\"
    today_path = _LOG_DIR / f"bot_{datetime.now().strftime('%Y-%m-%d')}.log"
    if today_path.exists():
        return today_path
    return _LEGACY_LOG_FILE


# Backwards-compatible alias used elsewhere in this module.
def _log_file() -> Path:
    return _active_log_path()


# Kept for any external code that still imports LOG_FILE.
LOG_FILE = _LEGACY_LOG_FILE""",
    "constant -> helper",
)

# 2) Update tail loop: resolve path each iteration, handle rotation.
src = replace_once(
    src,
    """def _tail_loop(bootstrap_lines: int = 2000, poll_interval: float = 0.5):
    \"\"\"Run forever. Handles log rotation by detecting inode change.\"\"\"
    global _file_pos, _file_inode
    logger.info(f"tailer starting on {LOG_FILE} (bootstrap={bootstrap_lines})")
    bootstrapped = False

    while True:
        try:
            if not LOG_FILE.exists():
                time.sleep(2)
                continue

            st = LOG_FILE.stat()
            if st.st_ino != _file_inode:
                # New file (first run or rotated) — reset.
                _file_inode = st.st_ino
                _file_pos = 0

            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as fh:
                if not bootstrapped and bootstrap_lines > 0:
                    # Read the last N lines to seed the dashboard.
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    chunk = min(size, 200_000)  # ~200 KB of tail
                    fh.seek(size - chunk)
                    tail = fh.read()
                    lines = tail.splitlines()[-bootstrap_lines:]
                    for ln in lines:
                        _parse_line(ln)
                    _file_pos = size
                    bootstrapped = True
                    logger.info(f"bootstrap complete ({len(lines)} lines parsed)")
                else:
                    fh.seek(_file_pos)
                    new = fh.read()
                    if new:
                        for ln in new.splitlines():
                            _parse_line(ln)
                        _file_pos = fh.tell()

            time.sleep(poll_interval)
        except Exception as e:
            logger.exception(f"tailer error: {e}")
            time.sleep(2)""",
    """def _tail_loop(bootstrap_lines: int = 2000, poll_interval: float = 0.5):
    \"\"\"Run forever. Handles log rotation by detecting inode change AND
    by detecting that the active log path itself changed (date rollover).\"\"\"
    global _file_pos, _file_inode
    current_path = _active_log_path()
    logger.info(f"tailer starting on {current_path} (bootstrap={bootstrap_lines})")
    bootstrapped = False

    while True:
        try:
            new_path = _active_log_path()
            if new_path != current_path:
                # Date rolled over (or we found today's file for the first
                # time). Treat as rotation: bootstrap the new file's tail.
                logger.info(
                    f"active log path changed: {current_path} -> {new_path} "
                    "— bootstrapping new file"
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
                        f"bootstrap complete ({len(lines)} lines parsed from "
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
            logger.exception(f"tailer error: {e}")
            time.sleep(2)""",
    "tail loop -> date-aware",
)

# 3) Update get_last_file_mtime to use resolved path.
src = replace_once(
    src,
    """def get_last_file_mtime() -> float:
    \"\"\"Unix mtime of v3_bot.log — proves the bot is actively writing
    even if no events matched our regexes recently (e.g. pure DEBUG).\"\"\"
    try:
        return LOG_FILE.stat().st_mtime
    except Exception:""",
    """def get_last_file_mtime() -> float:
    \"\"\"Unix mtime of the active bot log — proves the bot is actively
    writing even if no events matched our regexes recently.\"\"\"
    try:
        return _active_log_path().stat().st_mtime
    except Exception:""",
    "get_last_file_mtime -> resolved path",
)

PATH.write_text(src)
print("[DONE] dashboard_v3/log_parser.py patched")
