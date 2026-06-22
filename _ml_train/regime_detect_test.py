#!/usr/bin/env python3
"""THE question: can we detect a reversal/chop regime BEFORE we bet? Test whether
recent reversal behavior predicts the next window's outcome. If 'recently choppy'
-> 'next likely reverses', chop is detectable and we can pause. If not, reversals
are unpredictable and no amount of code fixes that. 46k windows."""
import pandas as pd, numpy as np

df = pd.read_csv("dataset.csv").sort_values(["coin", "ws"]).reset_index(drop=True)
print(f"windows: {len(df)} | overall drift-correct (drift continues): {df.drift_correct.mean()*100:.1f}%\n")

# 1) does the PREVIOUS window's outcome predict THIS one? (regime persistence)
print("=== 1) lag-1: does last window's result predict this one? (per coin) ===")
for coin, g in df.groupby("coin"):
    g = g.sort_values("ws"); prev = g.drift_correct.shift(1)
    after_win = g.drift_correct[prev == 1].mean()
    after_loss = g.drift_correct[prev == 0].mean()
    print(f"  {coin}: after a CONTINUE {after_win*100:4.1f}%  |  after a REVERSAL {after_loss*100:4.1f}%  (gap {abs(after_win-after_loss)*100:.1f}pts)")

# 2) does a RECENT CHOP STREAK (last 5 windows reversal rate) predict the next?
print("\n=== 2) recent reversal-rate (last 5, same coin) -> next window drift-correct ===")
df["recent_rev"] = (1 - df.groupby("coin").drift_correct.transform(
    lambda s: s.shift(1).rolling(5).mean()))
sub = df.dropna(subset=["recent_rev"])
for lo, hi, lab in [(0, .2, "calm (0-20% recent rev)"), (.2, .4, "20-40%"),
                    (.4, .6, "mixed 40-60%"), (.6, .8, "60-80%"), (.8, 1.01, "choppy (80-100%)")]:
    m = (sub.recent_rev >= lo) & (sub.recent_rev < hi)
    if m.sum() > 50:
        print(f"  {lab:<26} next drift-correct {sub.drift_correct[m].mean()*100:4.1f}%  (n={m.sum()})")

# 3) correlation: recent reversal rate vs next outcome
c = np.corrcoef(sub.recent_rev, sub.drift_correct)[0, 1]
print(f"\n=== 3) correlation(recent reversal rate, next outcome) = {c:+.3f} ===")
print("  (near 0 => recent chop does NOT predict the next window => reversals are NOT detectable)")
