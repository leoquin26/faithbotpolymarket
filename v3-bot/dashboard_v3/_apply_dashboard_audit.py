"""
_apply_dashboard_audit.py — wire the new audit_panels into dashboard_v3.

Patches:
  D1) Register `audit_panels` blueprint-style routes after the existing
      log_parser_5m.start() call in app.py.
  D2) Add a "🔬 Audit" tab to index.html's nav.
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple

REPO = "/home/ubuntu/v3-bot/dashboard_v3"


def patch_file(path: str, edits: List[Tuple[str, str, str]]) -> int:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    applied = 0
    for label, anchor, replacement in edits:
        if replacement in src:
            print(f"  [skip] {label}: replacement already present")
            continue
        if anchor not in src:
            raise RuntimeError(
                f"{path}: anchor for {label!r} not found and replacement "
                "not present — manual intervention needed"
            )
        if src.count(anchor) > 1:
            raise RuntimeError(
                f"{path}: anchor for {label!r} matches multiple times "
                f"({src.count(anchor)}) — anchor too generic"
            )
        src = src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [done] {label}")
    if applied:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        os.replace(tmp, path)
    return applied


APP_EDITS: List[Tuple[str, str, str]] = [
    (
        "D1: register audit_panels after parser startups",
        """# Start both log tailers as soon as the app imports.
parser_start()
parser_5m.start()""",
        """# Start both log tailers as soon as the app imports.
parser_start()
parser_5m.start()

# ── [AUDIT MAY27] register the audit telemetry panels ──
try:
    import audit_panels as _audit_panels
    _audit_panels.register(app)
    logger.info("[audit_panels] registered at /audit + /api/v3/audit/*")
except Exception as _e_ap:
    logger.warning(f"[audit_panels] register failed: {_e_ap}")""",
    ),
]

INDEX_EDITS: List[Tuple[str, str, str]] = [
    (
        "D2: add 🔬 Audit tab to index nav",
        """    <nav class="nav-tabs">
      <a href="/" class="active">Dashboard</a>
      <a href="/charts">📈 Charts</a>
    </nav>""",
        """    <nav class="nav-tabs">
      <a href="/" class="active">Dashboard</a>
      <a href="/charts">📈 Charts</a>
      <a href="/audit">🔬 Audit Telemetry</a>
    </nav>""",
    ),
]


def main() -> int:
    print("=" * 64)
    print("  Wiring audit_panels into dashboard_v3")
    print("=" * 64)
    print()
    print("→ app.py")
    n = patch_file(os.path.join(REPO, "app.py"), APP_EDITS)
    print(f"  applied {n}/{len(APP_EDITS)} edits")
    print()
    print("→ templates/index.html")
    n = patch_file(os.path.join(REPO, "templates/index.html"), INDEX_EDITS)
    print(f"  applied {n}/{len(INDEX_EDITS)} edits")
    print()
    print("→ Verifying app.py syntax")
    import py_compile
    try:
        py_compile.compile(os.path.join(REPO, "app.py"), doraise=True)
        print("  [OK] app.py")
    except py_compile.PyCompileError as e:
        print(f"  [FAIL] {e}")
        return 2
    print()
    print("Done. Restart the dashboard to load the new code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
