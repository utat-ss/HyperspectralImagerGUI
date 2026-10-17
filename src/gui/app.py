"""
GUI entry point: `python src/gui/app.py [device]`.

A device given here is auto-connected and shown in the Live Data Viewer;
with none, the app opens on the Connect tab. See camera_factory.DEVICES.
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
