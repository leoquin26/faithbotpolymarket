"""Apply two patches:
  1. Weekend mode: unified block from Friday 17:00 Lima through Monday 09:00 Lima.
  2. PM entry cap: afternoon trades rejected when clob_ask > PM_ENTRY_MAX (0.64).

Usage:
  scp this file to EC2 /home/ubuntu/v3-bot/, then `python3 _patch_pm_weekend.py`.
Re-runs are idempotent (checks for markers).
"""
import re
import shutil
import datetime
import pathlib

ROOT = pathlib.Path("/home/ubuntu/v3-bot")
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(path: pathlib.Path):
    bak = path.with_suffix(path.suffix + f".bak_apr24_{STAMP}")
    shutil.copy2(path, bak)
    print(f"[backup] {path} -> {bak}")

def patch_config():
    p = ROOT / "config.py"
    src = p.read_text()
    if "PM_ENTRY_MAX" in src:
        print("[config] already patched, skip")
        return
    backup(p)
    # Insert after ABSOLUTE_MAX_ENTRY line
    needle = "ABSOLUTE_MAX_ENTRY = ENTRY_MAX"
    ins = (
        "ABSOLUTE_MAX_ENTRY = ENTRY_MAX\n"
        "# PM-only tighter entry cap: data shows R:R collapses above 64c in the afternoon\n"
        "PM_ENTRY_MAX = float(os.getenv(\"PM_ENTRY_MAX\", \"0.64\"))\n"
    )
    new = src.replace(needle, ins, 1)
    assert new != src, "config.py replace failed"
    p.write_text(new)
    print("[config] PM_ENTRY_MAX=0.64 added")

def patch_weekend_mode():
    p = ROOT / "run_bot.py"
    src = p.read_text()
    if "[WEEKEND MODE]" in src:
        print("[weekend] already patched, skip")
        return
    backup(p)
    old = (
        '    weekday = now_lima.weekday()\n'
        '    if weekday >= 5:\n'
        '        day_name = "Saturday" if weekday == 5 else "Sunday"\n'
        '        return False, f"[WEEKEND] {day_name} {lima_hour}:00 Lima — no trading on weekends"\n'
        '    if lima_hour < 9 or lima_hour >= 17:\n'
        '        return False, f"[OFF HOURS] {lima_hour}:{now_lima.minute:02d} Lima — trade window 9am-5pm Lima (scanning active)"\n'
    )
    new_block = (
        '    weekday = now_lima.weekday()  # Mon=0, Fri=4, Sat=5, Sun=6\n'
        '    # Unified weekend mode: Fri 17:00+ -> Sat/Sun all day -> Mon <09:00 all blocked\n'
        '    _is_sat_sun      = weekday >= 5\n'
        '    _is_fri_evening  = (weekday == 4) and (lima_hour >= 17)\n'
        '    _is_mon_premarket = (weekday == 0) and (lima_hour < 9)\n'
        '    if _is_sat_sun or _is_fri_evening or _is_mon_premarket:\n'
        '        stamp = now_lima.strftime("%a %H:%M")\n'
        '        return False, f"[WEEKEND MODE] {stamp} Lima — blocked until Monday 09:00 Lima"\n'
        '    if lima_hour < 9 or lima_hour >= 17:\n'
        '        return False, f"[OFF HOURS] {lima_hour}:{now_lima.minute:02d} Lima — trade window 9am-5pm Lima (scanning active)"\n'
    )
    assert old in src, "weekend anchor not found"
    new = src.replace(old, new_block, 1)
    p.write_text(new)
    print("[weekend] unified WEEKEND MODE gate installed")

def patch_pm_entry_cap():
    p = ROOT / "run_bot.py"
    src = p.read_text()
    if "[PM ENTRY CAP]" in src:
        print("[pm-cap] already patched, skip")
        return
    # backup only if weekend didn't already back up
    if not any(x.name.startswith("run_bot.py.bak_apr24_") for x in ROOT.iterdir()):
        backup(p)
    # Anchor: the CLOB RANGE block inside the afternoon fire path.
    old = (
        '                            elif clob_ask < config.ENTRY_MIN or clob_ask > config.ENTRY_MAX:\n'
        '                                logger.info(\n'
        '                                    f"[CLOB RANGE] {best.coin} {best.direction}: "\n'
        '                                    f"CLOB ask={clob_ask*100:.0f}c outside "\n'
        '                                    f"{config.ENTRY_MIN*100:.0f}-{config.ENTRY_MAX*100:.0f}c"\n'
        '                                )\n'
        '                                unlock_window(best.coin, best.market_info.window_start)\n'
    )
    new_block = (
        '                            elif clob_ask < config.ENTRY_MIN or clob_ask > config.ENTRY_MAX:\n'
        '                                logger.info(\n'
        '                                    f"[CLOB RANGE] {best.coin} {best.direction}: "\n'
        '                                    f"CLOB ask={clob_ask*100:.0f}c outside "\n'
        '                                    f"{config.ENTRY_MIN*100:.0f}-{config.ENTRY_MAX*100:.0f}c"\n'
        '                                )\n'
        '                                unlock_window(best.coin, best.market_info.window_start)\n'
        '                            elif _is_afternoon and clob_ask > config.PM_ENTRY_MAX:\n'
        '                                # PM R:R collapses above this price (backfill: 66-69c R:R=0.49, >=69c R:R=0.35)\n'
        '                                logger.info(\n'
        '                                    f"[PM ENTRY CAP] {best.coin} {best.direction}: "\n'
        '                                    f"CLOB ask={clob_ask*100:.0f}c > PM cap {config.PM_ENTRY_MAX*100:.0f}c — R:R too thin"\n'
        '                                )\n'
        '                                unlock_window(best.coin, best.market_info.window_start)\n'
    )
    assert old in src, "pm entry cap anchor not found"
    new = src.replace(old, new_block, 1)
    p.write_text(new)
    print("[pm-cap] PM ENTRY CAP @ 64c installed")

if __name__ == "__main__":
    patch_config()
    patch_weekend_mode()
    patch_pm_entry_cap()
    print("\nAll patches applied. Verify with:\n  python3 -m py_compile run_bot.py config.py")
