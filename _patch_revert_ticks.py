"""Revert the blunt tick-swap in run_bot.py (Chainlink ticks were feeding EWMA
too, risking over-abstention). Instead pass Chainlink ticks as a SEPARATE kwarg
so the predictor can use them for directional ROC only, keeping Binance for vol.
"""
rb = "run_bot.py"
s = open(rb).read()

# 1) Remove the blunt swap block.
swap = (
    "                # Momentum must measure the SAME feed as the level (dist_pct).\n"
    "                # Use a Chainlink tick history when available; the level uses\n"
    "                # Chainlink, so ROC/trend should too. Falls back to Binance.\n"
    "                try:\n"
    "                    _cl_ticks = _chainlink_tick_history(coin, 300)\n"
    "                    if _cl_ticks and len(_cl_ticks) >= 5:\n"
    "                        ticks = _cl_ticks\n"
    "                except Exception:\n"
    "                    pass\n"
)
if swap in s:
    s = s.replace(swap, "")
    print("removed blunt swap")
else:
    print("blunt swap not present (ok)")

# 2) Pass chainlink ticks as a separate kwarg to predict().
old_call = "                    ticks=ticks,\n                )\n                return info, pred\n"
new_call = (
    "                    ticks=ticks,\n"
    "                    chainlink_ticks=_chainlink_tick_history(coin, 300),\n"
    "                )\n                return info, pred\n"
)
if "chainlink_ticks=" in s:
    print("chainlink_ticks kwarg already present")
elif old_call in s:
    s = s.replace(old_call, new_call)
    print("added chainlink_ticks kwarg")
else:
    print("WARN: predict() call anchor not found")

open(rb, "w").write(s)
print("run_bot.py written")
