"""
Wire chainlink_onchain as a Chainlink-grade fallback between RTDS and Binance.

Touches:
  - run_bot.py       : start the on-chain poller alongside chainlink_ws
  - poly_resolution.py : strike tier  RTDS -> on-chain (chainlink_onchain) -> Binance
  - predictor.py     : live spot      RTDS -> on-chain
  - market_data.py   : live spot      RTDS -> on-chain
"""

# ---------------------------------------------------------------- run_bot.py
rb = "run_bot.py"
s = open(rb).read()
if "chainlink_onchain" not in s:
    # import next to chainlink_ws import
    s = s.replace(
        "    import chainlink_ws as _chainlink_ws\n"
        "    _CHAINLINK_OK = True\n",
        "    import chainlink_ws as _chainlink_ws\n"
        "    _CHAINLINK_OK = True\n"
        "    try:\n"
        "        import chainlink_onchain as _chainlink_onchain\n"
        "    except Exception:\n"
        "        _chainlink_onchain = None\n",
    )
    # start the poller right after chainlink_ws.start() block
    s = s.replace(
        "        try:\n"
        "            _chainlink_ws.start()\n"
        "        except Exception as _cle:\n"
        "            logger.warning(f\"[CHAINLINK-WS] start failed: {_cle}\")\n",
        "        try:\n"
        "            _chainlink_ws.start()\n"
        "        except Exception as _cle:\n"
        "            logger.warning(f\"[CHAINLINK-WS] start failed: {_cle}\")\n"
        "    if _chainlink_onchain is not None:\n"
        "        try:\n"
        "            _chainlink_onchain.start()\n"
        "            logger.info(\"[CHAINLINK-ONCHAIN] on-chain fallback poller started\")\n"
        "        except Exception as _coe:\n"
        "            logger.warning(f\"[CHAINLINK-ONCHAIN] start failed: {_coe}\")\n",
    )
    open(rb, "w").write(s)
    print("run_bot.py wired")
else:
    print("run_bot.py already wired")

# -------------------------------------------------------- poly_resolution.py
pr = "poly_resolution.py"
s = open(pr).read()
anchor = (
    "    # FALLBACK: 1m candle OPEN at window start (only if no Chainlink snapshot —\n"
    "    # e.g. bot started mid-window or Chainlink WS was down at open).\n"
    "    if strike <= 0:\n"
)
onchain_tier = (
    "    # TIER 2: on-chain Chainlink aggregator (same oracle family as the Data\n"
    "    # Stream Polymarket settles on). Used when the RTDS WS is unavailable so\n"
    "    # we still avoid the cross-feed Binance basis. Only at/near window open.\n"
    "    if strike <= 0 and window_age <= _cl_max_age:\n"
    "        try:\n"
    "            import chainlink_onchain\n"
    "            co = chainlink_onchain.get_price(coin)\n"
    "            if co and co > 0:\n"
    "                strike = co\n"
    "                source = \"chainlink_onchain\"\n"
    "        except Exception:\n"
    "            pass\n\n"
)
if "chainlink_onchain" not in s and anchor in s:
    s = s.replace(anchor, onchain_tier + anchor)
    open(pr, "w").write(s)
    print("poly_resolution.py wired")
elif "chainlink_onchain" in s:
    print("poly_resolution.py already wired")
else:
    print("WARN: poly_resolution anchor not found")

# ----------------------------------------------------------------- predictor.py
pd = "predictor.py"
s = open(pd).read()
old = (
    "            import chainlink_ws as _cl_spot\n"
    "            _cl_px = _cl_spot.get_price(coin)\n"
)
new = (
    "            import chainlink_ws as _cl_spot\n"
    "            _cl_px = _cl_spot.get_price(coin)\n"
    "            if not _cl_px or _cl_px <= 0:\n"
    "                try:\n"
    "                    import chainlink_onchain as _cl_oc\n"
    "                    _cl_px = _cl_oc.get_price(coin)\n"
    "                except Exception:\n"
    "                    pass\n"
)
if "_cl_oc" in s:
    print("predictor.py already wired")
elif old in s:
    s = s.replace(old, new)
    open(pd, "w").write(s)
    print("predictor.py wired")
else:
    print("WARN: predictor anchor not found")

# --------------------------------------------------------------- market_data.py
md = "market_data.py"
s = open(md).read()
old = (
    "            import chainlink_ws as _cl\n"
    "            cl_px = _cl.get_price(coin)\n"
)
new = (
    "            import chainlink_ws as _cl\n"
    "            cl_px = _cl.get_price(coin)\n"
    "            if not cl_px or cl_px <= 0:\n"
    "                try:\n"
    "                    import chainlink_onchain as _cl_oc2\n"
    "                    cl_px = _cl_oc2.get_price(coin)\n"
    "                except Exception:\n"
    "                    pass\n"
)
if "_cl_oc2" in s:
    print("market_data.py already wired")
elif old in s:
    s = s.replace(old, new)
    open(md, "w").write(s)
    print("market_data.py wired")
else:
    print("WARN: market_data anchor not found (line 314-315 expected)")
