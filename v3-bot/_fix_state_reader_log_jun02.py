"""
Jun 2 — fix dashboard_v3/state_reader.py to read today's log, not stale v3_bot.log.

Problem:
  LOG_FILE = BOT_DIR / "v3_bot.log"  # was symlinked to May 7 log!
  tail_log(n) → returns ancient lines from May 7

Fix:
  - Keep LOG_FILE constant for backward-compat
  - Make tail_log() dynamically resolve today's log path
  - Fall back to LOG_FILE (symlink) if today's not found
"""
from pathlib import Path

SR = Path("/home/ubuntu/v3-bot/dashboard_v3/state_reader.py")


def main():
    text = SR.read_text()

    old = (
        "def tail_log(n: int = 200) -> list[str]:\n"
        "    if not LOG_FILE.exists():\n"
        "        return []\n"
        "    try:\n"
        "        # Efficient tail via seek\n"
        "        with open(LOG_FILE, \"rb\") as f:\n"
    )

    new = (
        "def _active_log_path() -> Path:\n"
        "    \"\"\"Jun-2: resolve today's loguru-rotated log file.\"\"\"\n"
        "    from datetime import datetime\n"
        "    today = BOT_DIR / \"logs\" / f\"bot_{datetime.now().strftime('%Y-%m-%d')}.log\"\n"
        "    if today.exists():\n"
        "        return today\n"
        "    # Fall back to legacy symlink (may be stale, but keeps API working)\n"
        "    return LOG_FILE\n"
        "\n"
        "\n"
        "def tail_log(n: int = 200) -> list[str]:\n"
        "    log_file = _active_log_path()\n"
        "    if not log_file.exists():\n"
        "        return []\n"
        "    try:\n"
        "        # Efficient tail via seek\n"
        "        with open(log_file, \"rb\") as f:\n"
    )

    if "_active_log_path" in text:
        print("[SKIP] state_reader already has _active_log_path")
        return
    if old not in text:
        print("[FAIL] expected tail_log block not found")
        return
    text = text.replace(old, new, 1)
    SR.write_text(text)
    print("[OK] patched state_reader.tail_log to use today's log dynamically")


if __name__ == "__main__":
    main()
