"""
1D spectrum trace display.

Three things beyond drawing a line:

**Calibrated x axis.** The trace is plotted against wavelength when a
calibration is selected and against raw column index when none is. The axis
label changes with it, because an axis reading in nanometres is a claim
about the instrument that only a calibration can back up.

**Explicit ranges over autoscale.** Y defaults to the camera's bit-depth
range rather than autoscaling per frame: a live plot that rescales every
frame hides real brightness changes instead of showing them. The user can
override either axis explicitly, and clearing the override returns to that
default -- it does not leave the axis frozen wherever it happened to be.

**Markers.** Two draggable vertical markers that report the x and y under
them, for reading a feature's position and height off the live trace.
"""

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal

from core.calibration import (
    PIXEL_AXIS_LABEL,
    WAVELENGTH_AXIS_LABEL,
    WavelengthCalibration,
)

MARKER_COUNT = 2
_MARKER_PENS = ("r", "c")


class SpectrumView(pg.PlotWidget):
    #: (marker index, x, y) each time a marker moves or new data arrives.
    marker_moved = Signal(int, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._curve = self.plot(pen="y")
        self.setLabel("bottom", PIXEL_AXIS_LABEL)
        self.setLabel("left", "mean intensity")
        self.showGrid(x=True, y=True, alpha=0.3)

        self._bit_depth = 12
        self._calibration: Optional[WavelengthCalibration] = None
        self._x_values: Optional[np.ndarray] = None
        self._spectrum: Optional[np.ndarray] = None
        self._x_override: Optional[tuple] = None
        self._y_override: Optional[tuple] = None

        self._markers = []
        # True once a marker is shown with no data to place it against.
        self._marker_pending = [False] * MARKER_COUNT
        for index in range(MARKER_COUNT):
            marker = pg.InfiniteLine(
                pos=0,
                angle=90,
                movable=True,
                pen=pg.mkPen(_MARKER_PENS[index], width=1),
                hoverPen=pg.mkPen(_MARKER_PENS[index], width=3),
            )
            marker.setVisible(False)
            self.addItem(marker)
            marker.sigPositionChanged.connect(
                lambda _line, i=index: self._emit_marker(i)
            )
            self._markers.append(marker)

        self._apply_y_range()

    # -- scaling --

    def set_bit_depth(self, bit_depth: int) -> None:
        self._bit_depth = bit_depth
        self._apply_y_range()

    def set_calibration(self, calibration: Optional[WavelengthCalibration]) -> None:
        """
        Switch the x axis between wavelength and raw column index.

        Clears any manual x override: a range entered in pixel indices is
        meaningless once the axis is in nanometres, and silently keeping it
        would show the user an apparently-empty plot.
        """
        self._calibration = calibration
        self._x_override = None
        self.setLabel(
            "bottom",
            WAVELENGTH_AXIS_LABEL if calibration is not None else PIXEL_AXIS_LABEL,
        )
        if self._spectrum is not None:
            self.show_spectrum(self._spectrum)
        self._refresh_markers()

    def calibration(self) -> Optional[WavelengthCalibration]:
        return self._calibration

    def is_calibrated(self) -> bool:
        return self._calibration is not None

    # -- data --

    def show_spectrum(self, spectrum: np.ndarray) -> None:
        self._spectrum = spectrum
        width = spectrum.shape[0]

        if self._calibration is not None:
            self._x_values = self._calibration.wavelengths(width)
        else:
            self._x_values = np.arange(width, dtype=float)

        self._curve.setData(self._x_values, spectrum)

        if self._x_override is None:
            self.setXRange(
                float(self._x_values[0]), float(self._x_values[-1]), padding=0.0
            )
        self._refresh_markers()

    # -- axis ranges --

    def set_x_range(self, low: Optional[float], high: Optional[float]) -> None:
        """Explicit x range; pass None for either bound to drop the override."""
        if low is None or high is None:
            self._x_override = None
            if self._x_values is not None:
                self.setXRange(
                    float(self._x_values[0]), float(self._x_values[-1]), padding=0.0
                )
            return
        if low >= high:
            raise ValueError(f"x range low ({low}) must be below high ({high})")
        self._x_override = (low, high)
        self.setXRange(low, high, padding=0.0)

    def set_y_range(self, low: Optional[float], high: Optional[float]) -> None:
        """Explicit y range; pass None for either bound to fall back to bit depth."""
        if low is None or high is None:
            self._y_override = None
            self._apply_y_range()
            return
        if low >= high:
            raise ValueError(f"y range low ({low}) must be below high ({high})")
        self._y_override = (low, high)
        self.setYRange(low, high, padding=0.0)

    def x_range(self) -> tuple:
        return tuple(self.getViewBox().viewRange()[0])

    def y_range(self) -> tuple:
        return tuple(self.getViewBox().viewRange()[1])

    def has_x_override(self) -> bool:
        return self._x_override is not None

    def has_y_override(self) -> bool:
        return self._y_override is not None

    def _apply_y_range(self) -> None:
        if self._y_override is not None:
            low, high = self._y_override
        else:
            low, high = 0, (1 << self._bit_depth) - 1
        self.setYRange(low, high, padding=0.05)

    # -- markers --

    def set_marker_visible(self, index: int, visible: bool) -> None:
        marker = self._markers[index]
        marker.setVisible(visible)
        if not visible:
            return

        if self._x_values is None:
            # Nothing to place it against yet; do it on the first frame.
            self._marker_pending[index] = True
        else:
            marker.setPos(self._default_marker_x(index))
            self._marker_pending[index] = False
        self._emit_marker(index)

    def _default_marker_x(self, index: int) -> float:
        span = float(self._x_values[-1] - self._x_values[0])
        return float(self._x_values[0]) + span * (0.33 * (index + 1))

    def _place_marker(self, index: int) -> None:
        """
        Move a marker onto the trace if it is not already on it.

        Two ways a visible marker ends up off the plot, both of which left
        it stuck at an unreadable position reporting no y:

        - enabled before any frame arrived, so there was no extent to place
          it against and it sat at x=0;
        - the calibration toggle switched units under it, turning "column
          300" into "300 nm", which is off a 500-1000 nm axis.

        Re-placing only when it is actually outside the data keeps the
        user's own positioning untouched the rest of the time.
        """
        if self._x_values is None or not self._markers[index].isVisible():
            return

        low = float(min(self._x_values[0], self._x_values[-1]))
        high = float(max(self._x_values[0], self._x_values[-1]))
        out_of_range = not low <= float(self._markers[index].value()) <= high

        # `pending` is needed as well as the range check: a marker enabled
        # before any data sits at x=0, which is legitimately *inside* a
        # pixel-index axis, so the range check alone would leave it there.
        if self._marker_pending[index] or out_of_range:
            self._markers[index].setPos(self._default_marker_x(index))
            self._marker_pending[index] = False

    def marker_visible(self, index: int) -> bool:
        return self._markers[index].isVisible()

    def set_marker_x(self, index: int, x: float) -> None:
        self._markers[index].setPos(x)

    def marker_values(self, index: int) -> tuple:
        """
        (x, y) under a marker. y is the spectrum sampled at the marker's x,
        interpolated between columns, or NaN when there is no data.
        """
        x = float(self._markers[index].value())
        if self._spectrum is None or self._x_values is None:
            return (x, float("nan"))

        values, spectrum = self._x_values, self._spectrum
        if values[0] > values[-1]:  # np.interp needs ascending x
            values, spectrum = values[::-1], spectrum[::-1]
        if x < values[0] or x > values[-1]:
            return (x, float("nan"))  # off the trace: no honest y to report
        return (x, float(np.interp(x, values, spectrum)))

    def _refresh_markers(self) -> None:
        for index, marker in enumerate(self._markers):
            if marker.isVisible():
                self._place_marker(index)
                self._emit_marker(index)

    def _emit_marker(self, index: int) -> None:
        x, y = self.marker_values(index)
        self.marker_moved.emit(index, x, y)
