"""
1D spectrum trace display. Y-axis is fixed to the connected camera's
bit-depth range (same reasoning as LiveImageView: a live plot that
autoscales its y-axis every frame hides real brightness changes instead
of showing them).
"""

import numpy as np
import pyqtgraph as pg


class SpectrumView(pg.PlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._curve = self.plot(pen="y")
        self.setLabel("bottom", "pixel (column index)")
        self.setLabel("left", "mean intensity")
        self.showGrid(x=True, y=True, alpha=0.3)

    def set_bit_depth(self, bit_depth: int) -> None:
        self.setYRange(0, (1 << bit_depth) - 1, padding=0.05)

    def show_spectrum(self, spectrum: np.ndarray) -> None:
        self._curve.setData(spectrum)
