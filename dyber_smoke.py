"""Smoke-test for the DyberPet dev environment (headless / offscreen).

Verifies:
  1. third-party deps import (PySide6, qfluentwidgets, qframelesswindow, ...)
  2. DyberPet project modules import (no missing deps / syntax errors)
  3. A QApplication can be created under QT_QPA_PLATFORM=offscreen (Qt runtime OK)

It does NOT do a full GUI launch (no display in sandbox). Run with the venv
from the repo root so DyberPet's cwd-relative resource loads resolve:
  cd U:/DyberPet
  .venv/Scripts/python.exe dyber_smoke.py
"""
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

report = []


def step(name, fn):
    try:
        fn()
        report.append(f"[OK]   {name}")
    except Exception as e:  # noqa
        report.append(f"[FAIL] {name}: {type(e).__name__}: {e}")


def check_deps():
    import PySide6
    import qfluentwidgets
    import qframelesswindow  # PySideSix-Frameless-Window
    import apscheduler
    import pynput
    import tendo
    versions = {
        "PySide6": getattr(PySide6, "__version__", "?"),
        "qfluentwidgets": getattr(qfluentwidgets, "__version__", "?"),
        "qframelesswindow": getattr(qframelesswindow, "__version__", "?"),
    }
    report.append("       versions: " + ", ".join(f"{k}={v}" for k, v in versions.items()))


def check_project_imports():
    from DyberPet.DyberPet import PetWidget                      # noqa
    from DyberPet.bubbleManager import BubbleManager             # noqa
    from DyberPet.modules import (Animation_worker,             # noqa
                                  Interaction_worker,
                                  Scheduler_worker)
    import DyberPet.settings                                     # noqa  (module-level API)
    from DyberPet.conf import PetData, TaskData, ActData, ItemData  # noqa
    from DyberPet.DyberSettings.DyberControlPanel import ControlMainWindow  # noqa
    from DyberPet.Dashboard.DashboardUI import DashboardMainWindow        # noqa


def check_qt_runtime():
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication.instance() or QApplication(sys.argv[:1])
    w = QLabel("DyberPet smoke test")
    w.resize(100, 30)
    w.show()
    w.close()
    app.quit()


step("third-party deps import", check_deps)
step("DyberPet project modules import", check_project_imports)
step("Qt offscreen runtime (QApplication)", check_qt_runtime)

print("\n".join(report))
ok = all(line.startswith("[OK]") for line in report if line.startswith("["))
print("\nRESULT:", "PASS" if ok else "PARTIAL/FAIL")
sys.exit(0 if ok else 1)
