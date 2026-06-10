"""Apply PM relax: TRAP_BAND_MAX=0.63 and PM_ENTRY_MAX=0.66 to .env."""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/.env")
lines = p.read_text().splitlines()

out = []
trap_seen = False
pm_seen = False

for line in lines:
    s = line.strip()
    if s.startswith("TRAP_BAND_MAX="):
        out.append("TRAP_BAND_MAX=0.63")
        trap_seen = True
    elif s.startswith("PM_ENTRY_MAX="):
        out.append("PM_ENTRY_MAX=0.66")
        pm_seen = True
    else:
        out.append(line)

if not trap_seen:
    out.append("TRAP_BAND_MAX=0.63")
if not pm_seen:
    out.append("PM_ENTRY_MAX=0.66")

p.write_text("\n".join(out) + "\n")

print("TRAP_BAND_MAX:", "modified" if trap_seen else "appended")
print("PM_ENTRY_MAX:", "modified" if pm_seen else "appended")
