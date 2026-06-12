"""
Hybrid momentum (Jun 10 2026): keep Binance ticks for EWMA/sigma (needs many
samples), but compute the DIRECTIONAL ROC (roc_60, roc_300) from Chainlink ticks
when available, so momentum matches the level's feed without starving volatility.
"""
f = "predictor.py"
s = open(f).read()

# 1) Add a parallel Chainlink momentum store + accessor next to _get_momentum.
anchor = (
    "    def _get_momentum(self, coin: str) -> MomentumAnalyzer:\n"
    "        if coin not in self._momentum:\n"
    "            self._momentum[coin] = MomentumAnalyzer(600)\n"
    "        return self._momentum[coin]\n"
)
addition = (
    "    def _get_momentum(self, coin: str) -> MomentumAnalyzer:\n"
    "        if coin not in self._momentum:\n"
    "            self._momentum[coin] = MomentumAnalyzer(600)\n"
    "        return self._momentum[coin]\n\n"
    "    def _get_cl_momentum(self, coin: str) -> MomentumAnalyzer:\n"
    "        \"\"\"Separate analyzer fed ONLY Chainlink ticks, for directional ROC\n"
    "        that matches the level feed (does not affect EWMA/sigma).\"\"\"\n"
    "        if not hasattr(self, '_cl_momentum'):\n"
    "            self._cl_momentum = {}\n"
    "        if coin not in self._cl_momentum:\n"
    "            self._cl_momentum[coin] = MomentumAnalyzer(600)\n"
    "        return self._cl_momentum[coin]\n"
)
assert anchor in s, "momentum accessor anchor missing"
s = s.replace(anchor, addition)

# 2) After roc_60/roc_120 are computed, override roc_60 (and later roc_300) from
#    Chainlink ticks when we have enough of them.
roc_anchor = (
    "        momentum_raw = mom.get_momentum()\n"
    "        roc_60 = mom._roc(60)\n"
    "        roc_120 = mom._roc(120)\n"
)
roc_new = (
    "        momentum_raw = mom.get_momentum()\n"
    "        roc_60 = mom._roc(60)\n"
    "        roc_120 = mom._roc(120)\n"
    "        # Hybrid: prefer Chainlink-derived ROC for direction (matches level).\n"
    "        _cl_ticks_in = kwargs.get('chainlink_ticks') or None\n"
    "        _cl_mom = None\n"
    "        if _cl_ticks_in and len(_cl_ticks_in) >= 3:\n"
    "            _cl_mom = self._get_cl_momentum(coin)\n"
    "            _cl_last = self._cl_last_fed.get(coin, 0.0) if hasattr(self, '_cl_last_fed') else 0.0\n"
    "            if not hasattr(self, '_cl_last_fed'):\n"
    "                self._cl_last_fed = {}\n"
    "            for _ts, _p in _cl_ticks_in:\n"
    "                if _ts > _cl_last:\n"
    "                    _cl_mom.add_tick(_ts, _p)\n"
    "            self._cl_last_fed[coin] = _cl_ticks_in[-1][0]\n"
    "            _r60 = _cl_mom._roc(60)\n"
    "            if _r60 != 0.0:\n"
    "                roc_60 = _r60\n"
    "            _r120 = _cl_mom._roc(120)\n"
    "            if _r120 != 0.0:\n"
    "                roc_120 = _r120\n"
)
assert roc_anchor in s, "roc anchor missing"
s = s.replace(roc_anchor, roc_new)

# 3) Override roc_300 too (defined a bit later).
roc300_anchor = "        roc_300 = mom._roc(300)  # 5-minute trend (big picture)\n"
roc300_new = (
    "        roc_300 = mom._roc(300)  # 5-minute trend (big picture)\n"
    "        if _cl_mom is not None:\n"
    "            _r300 = _cl_mom._roc(300)\n"
    "            if _r300 != 0.0:\n"
    "                roc_300 = _r300\n"
)
assert roc300_anchor in s, "roc300 anchor missing"
s = s.replace(roc300_anchor, roc300_new)

open(f, "w").write(s)
print("hybrid Chainlink momentum wired")
