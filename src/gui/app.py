"""
GUI entry point.

Run:
    python src/gui/app.py [device]

The optional device is auto-connected on start and shown in the Live Data
Viewer, which is what makes the demo path a single command. Pass nothing to
open on the Connect tab with nothing connected -- what a real user sees.

    python src/gui/app.py spectrograph   # synthetic spectrograph demo
    python src/gui/app.py mock           # structured test pattern
    python src/gui/app.py                # no device; pick one in the UI

See gui.camera_factory.DEVICES for the full list.
"""

import sys
from pathlib import Path

# This file lives inside the gui package it's launching, so `python
# src/gui/app.py` puts src/gui/ on sys.path, not src/ -- `from gui...`
# and `from core...` below would fail without this. Must run before
# the gui/core imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402


def main() -> None:
    backend = sys.argv[1] if len(sys.argv) > 1 else None

    app = QApplication(sys.argv)
    window = MainWindow(backend=backend)
    window.resize(1200, 700)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
