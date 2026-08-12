# CYCLE LAW — pre-registered, owner-approved 2026-08-11 (~00:55 UTC Aug 12)

Registered at cycle 4, n=7 (4W/3L, net −$5.38), BEFORE any cycle-4 verdict
exists. Git commit timestamp = proof of pre-registration.

## 1. THE FLOOR RULE
Cycle 4 is the last cycle funded on faith.

- Cycle 4 PASSES (EV/$ ≥ +0.03 at n=40) → cycle 5 proceeds at 8 shares
  (no scale-up without standalone z ≥ +1, per the constitution).
- Cycle 4 FAILS the bar or hits the −$20 stop → the engine RETIRES unless
  the pooled ~160-settle lifetime record shows z ≥ +1. No cycle 5 bought
  with hope.

Consequence: worst case for the whole project is ~$84 equity — still above
the $77.84 launch. The project can no longer end net negative.

## 2. THE BANKING LAW
From the next PASSING cycle onward: 50% of each passed cycle's realized
profit is withdrawn from the trading wallet at cycle close. Banked money
never rides again. The bot's verdict Telegram includes the exact amount.

Rationale: the audit machinery protects against a bad engine; banking
protects a good run from staying fully at risk. Both protections are now
law.

## 3. STANDING PRE-REGISTERED TRIGGERS (unchanged)
- At cycle-4 verdict: if the pooled LIVE 70c+ subgroup is negative,
  MAX_ASK drops to 0.75. (At registration it is POSITIVE: n=13, +0.091.)
- Verdicts are z-gated (|z| ≥ 1 both directions); emergency brake z-free.

## 4. MID-CYCLE CHANGE DISCLOSURE (honesty ledger)
Cycle 4 config at launch (n=0): 8 shares, stop −$20, MIN_MID 0.55 wide-book
guard, fill_s instrumentation.
At n=7 (this document): entry tie-break changed from code-order-first to
TIGHTEST-SPREAD-first among qualifying coins, with [PICK] logging of the
counterfactual. Direction: protective/selective (tight books were the only
split-consistent positive slice, ≤2c: +2.7% ROI both halves; wide books
produced the fake-favourite losses). Bets 1-7 predate this tie-break; the
cycle-4 verdict must note it.

## 5. WHAT THE DATA REFUSED (do not relitigate without NEW large-sample evidence)
Hour-of-day filters (24-bucket noise farm, no split-consistent block).
Coin drops (SOL n=8; population says all three fine). 65-70c sub-band cut
(n=14). Slow-fill cutoff (n=15). Adverse-selection repricing (REFUTED on
121 matched fills: instant fills EV +0.078).
