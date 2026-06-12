"""May 21 BUG FIX: reset _morning_total_losses + _morning_consec_losses
at midnight Lima.

Root cause: yesterday 5/20 at 13:36 [MORNING CAP] fired at $14.64 (cap=$12).
The warning says "morning disabled until tomorrow" but the code has NO
reset logic for the next day. The morning has been dead since 5/20 13:36
and would stay dead until the bot is restarted.

Today 5/21 at 09:00 Lima we saw A+++ signals (BTC DOWN prob 85%, edge
26.5%, trend -2.40) generate but no [MORNING P1] APPROVED lines —
because the morning was silently disabled.

Fix: track Lima-date of last reset. When the date rolls over, reset both
counters and log it.
"""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/run_bot.py")
text = p.read_text()

if "MORNING DAILY RESET" in text:
    print("morning daily reset already present — skipping")
    raise SystemExit(0)

# Add _last_morning_reset_date variable in the init block
old_init = ("    scan_count = 0\n"
            "    _consec_losses = 0\n"
            "    _morning_consec_losses = 0\n"
            "    # Fix A apr23: track last EXHAUST=ABSTAIN per coin (monotonic epoch)\n"
            "    _last_exhaust_abstain: dict = {}\n"
            "    _morning_total_losses = 0.0\n"
            "    MORNING_LOSS_CAP = 12.0  # hard stop for morning; afternoon unaffected")

new_init = ("    scan_count = 0\n"
            "    _consec_losses = 0\n"
            "    _morning_consec_losses = 0\n"
            "    # Fix A apr23: track last EXHAUST=ABSTAIN per coin (monotonic epoch)\n"
            "    _last_exhaust_abstain: dict = {}\n"
            "    _morning_total_losses = 0.0\n"
            "    MORNING_LOSS_CAP = 12.0  # hard stop for morning; afternoon unaffected\n"
            "    # MORNING DAILY RESET (May 21 fix): track Lima-date of last reset.\n"
            "    # Without this, the MORNING_LOSS_CAP from one day silently disables\n"
            "    # morning trading on every following day until a manual restart.\n"
            "    from zoneinfo import ZoneInfo as _ZI_RESET\n"
            "    _LIMA_RESET = _ZI_RESET(\"America/Lima\")\n"
            "    _last_morning_reset_date = datetime.now(_LIMA_RESET).date()")

if old_init not in text:
    raise SystemExit("init marker not found")
text = text.replace(old_init, new_init, 1)

# Add the reset logic inside the scan loop, just before the morning dispatch
old_dispatch = ("            # ── Morning strategy (9am-2pm Lima): separate, conservative predictor ──\n"
                "            from zoneinfo import ZoneInfo as _ZI\n"
                "            _lima_now = datetime.now(_ZI(\"America/Lima\"))\n"
                "            _is_morning = 9 <= _lima_now.hour < 14\n"
                "            _is_afternoon = 14 <= _lima_now.hour < 17")

new_dispatch = ("            # ── Morning strategy (9am-2pm Lima): separate, conservative predictor ──\n"
                "            from zoneinfo import ZoneInfo as _ZI\n"
                "            _lima_now = datetime.now(_ZI(\"America/Lima\"))\n"
                "            _is_morning = 9 <= _lima_now.hour < 14\n"
                "            _is_afternoon = 14 <= _lima_now.hour < 17\n"
                "\n"
                "            # MORNING DAILY RESET (May 21): roll over the morning-loss budget\n"
                "            # when the Lima date changes. Without this, hitting MORNING_LOSS_CAP\n"
                "            # on day N kills morning trading on day N+1 silently.\n"
                "            _today_date = _lima_now.date()\n"
                "            if _today_date != _last_morning_reset_date:\n"
                "                if _morning_total_losses > 0 or _morning_consec_losses > 0:\n"
                "                    logger.info(\n"
                "                        f\"[MORNING DAILY RESET] new Lima date {_today_date} — \"\n"
                "                        f\"resetting morning_total_losses=${_morning_total_losses:.2f} \"\n"
                "                        f\"and consec_losses={_morning_consec_losses}\"\n"
                "                    )\n"
                "                _morning_total_losses = 0.0\n"
                "                _morning_consec_losses = 0\n"
                "                _last_morning_reset_date = _today_date")

if old_dispatch not in text:
    raise SystemExit("dispatch marker not found")
text = text.replace(old_dispatch, new_dispatch, 1)

p.write_text(text)
print("MORNING DAILY RESET patch applied")
