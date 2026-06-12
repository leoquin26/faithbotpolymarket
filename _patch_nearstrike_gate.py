"""
Tighten the near-strike accuracy gate (Jun 10 2026, round 2).

Root cause of the ETH/SOL UP losses: bot took bets at dist=+0.11..0.12% with
~13min left and DEAD short momentum (ROC60=0.0bps), then price reverted.
Two holes:
  1. _near_dist ceiling (0.12%) let SOL (0.121%) skip the gate entirely.
  2. Time cushion (max +0.03%) was too small; near-strike + lots of time is a
     coin flip.

Moderate fix (user-approved):
  - widen gate band ceiling to 0.18% (SETTLEMENT_NEAR_DIST 0.0012 -> 0.0018)
  - raise time cushion (NEAR_DIST_TIME_BPS 0.0003 -> 0.0008)
  - NEW: dead-momentum block — inside the band with >8min left, require LIVE
    ROC60 agreeing with the level direction (block flat/against momentum).
"""

f = "predictor.py"
s = open(f).read()

# 1) widen the band default
old1 = '_near_dist = float(os.getenv("SETTLEMENT_NEAR_DIST", "0.0012"))'
new1 = '_near_dist = float(os.getenv("SETTLEMENT_NEAR_DIST", "0.0018"))'
assert old1 in s, "near_dist anchor missing"
s = s.replace(old1, new1)

# 2) raise the time cushion default
old2 = '_time_bps = float(os.getenv("NEAR_DIST_TIME_BPS", "0.0003"))      # +0.03%'
new2 = '_time_bps = float(os.getenv("NEAR_DIST_TIME_BPS", "0.0008"))      # +0.08%'
assert old2 in s, "time_bps anchor missing"
s = s.replace(old2, new2)

# 3) insert dead-momentum block right AFTER the existing near-floor check.
anchor = (
    '            if abs(dist_pct) < _req_floor and not _book_confirms:\n'
    '                self._diag_log(\n'
    '                    f"near-floor-{coin}",\n'
    '                    f"[NEAR FLOOR] {coin} {level_dir}: dist={dist_pct*100:+.3f}% "\n'
    '                    f"< req={_req_floor*100:.3f}% (T={time_remaining:.0f}s) and book "\n'
    '                    f"not confirming (book_up={book_up:.2f}) — skip",\n'
    '                    12.0,\n'
    '                )\n'
    '                return None\n'
)
dead_mom = (
    '\n'
    '            # Dead-momentum block (Jun 10 2026 r2): a near-strike level with\n'
    '            # lots of time left and NO live short-term push (ROC60 flat or\n'
    '            # against the level) is a coin flip that tends to revert. Require\n'
    '            # live ROC60 to agree unless the book strongly confirms.\n'
    '            _dm_time = float(os.getenv("NEAR_DEADMOM_MIN_T", "480"))   # 8 min\n'
    '            _dm_roc = float(os.getenv("NEAR_DEADMOM_MIN_ROC60", "0.00003"))\n'
    '            if time_remaining >= _dm_time and not _book_confirms:\n'
    '                _roc60_dir = _dir_from_sign(roc_60, _dm_roc)\n'
    '                if _roc60_dir != level_dir:\n'
    '                    self._diag_log(\n'
    '                        f"near-deadmom-{coin}",\n'
    '                        f"[NEAR DEADMOM] {coin} {level_dir}: dist={dist_pct*100:+.3f}% "\n'
    '                        f"roc60={roc_60*10000:+.1f}bps not confirming with "\n'
    '                        f"{time_remaining:.0f}s left — coin-flip, skip",\n'
    '                        12.0,\n'
    '                    )\n'
    '                    return None\n'
)
assert anchor in s, "near-floor block anchor missing"
s = s.replace(anchor, anchor + dead_mom)

open(f, "w").write(s)
print("near-strike gate tightened OK")
