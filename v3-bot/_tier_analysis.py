import re
from collections import Counter

A=B=C=overrides=p_only_82=0
override_lines=[]
filled_lines=[]
fired_or_filled=Counter()

with open('v3_bot.log','r',errors='ignore') as f:
    for line in f:
        m = re.search(r'prob=(\d+)% edge=([\d.]+)%', line)
        if not m:
            m2 = re.search(r'prob=(\d+)%', line)
            if m2 and int(m2.group(1))>=82:
                p_only_82 += 1
            continue
        p = int(m.group(1)); e = float(m.group(2))
        if p>=82 and e>=18:
            A += 1
            if 'OVERRIDE' in line:
                overrides += 1
                override_lines.append(line.strip())
        elif p>=78 and e>=15:
            B += 1
        elif p>=70 and e>=10:
            C += 1

with open('v3_bot.log','r',errors='ignore') as f:
    for line in f:
        if '[FILLED]' in line: fired_or_filled['FILLED']+=1; filled_lines.append(line.strip())
        if '[FIRED]' in line: fired_or_filled['FIRED']+=1
        if '[MISS]' in line: fired_or_filled['MISS']+=1

print(f"A-tier hits (prob>=82 AND edge>=18): {A}")
print(f"  via EXHAUST OVERRIDE: {overrides}")
print(f"B-tier hits (prob>=78-81 OR edge 15-17): {B}")
print(f"C-tier hits (prob>=70-77): {C}")
print(f"prob>=82 with any edge (incl <18): {p_only_82}")
print()
print(f"FILLED orders in log: {fired_or_filled['FILLED']}")
print(f"FIRED orders in log: {fired_or_filled['FIRED']}")
print(f"MISS orders in log: {fired_or_filled['MISS']}")
print()
print("Last 5 OVERRIDE entries:")
for l in override_lines[-5:]: print(f"  {l[:140]}")
print()
print("Last 5 FILLED entries:")
for l in filled_lines[-5:]: print(f"  {l[:140]}")
