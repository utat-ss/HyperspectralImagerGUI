"""
Live 2D frame display, scaled from the connected camera's actual bit
depth rather than the frame's uint16 container dtype -- see
CameraInterface.get_bit_depth().

Also owns the slide-adjust line: the draggable horizontal marker the user
positions to choose which sensor row the spectrum is read from. The line
lives here rather than in the extraction code because it is purely a
display affordance -- what it produces is a row index, and
core.spectrum_extraction takes it from there.
"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal


class LiveImageView(pg.ImageView):
    # Emitted with the sensor row the user dragged the line to.
    line_row_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = (0, 1)
        self._first_frame = True
        self._frame_height = 0

        # angle=0 is horizontal, so dragging it picks a row. The view's y
        # axis is the frame's row axis (a HxW frame renders W wide, H tall),
        # which is what makes the line's position a row index directly.
        self._line = pg.InfiniteLine(
            pos=0,
            angle=0,
            movable=True,
            pen=pg.mkPen("r", width=1),
            hoverPen=pg.mkPen("r", width=3),
        )
        self._line.setVisible(False)
        self.getView().addItem(self._line)
        self._line.sigPositionChanged.connect(self._on_line_moved)

    def set_bit_depth(self, bit_depth: int) -> None:
        self._levels = (0, (1 << bit_depth) - 1)
        self._first_frame = True  # re-fit the view/histogram to the new range on the next frame

    def show_frame(self, frame: np.ndarray) -> None:
        if frame.shape[0] != self._frame_height:
            self._frame_height = frame.shape[0]
            self._line.setBounds((0, self._frame_height - 1))
            if self._first_frame:
                self._line.setPos(self._frame_height // 2)

        self.setImage(
            frame,
            autoRange=self._first_frame,
            autoLevels=False,
            autoHistogramRange=self._first_frame,
            levels=self._levels,
        )
        self._first_frame = False

    # -- slide-adjust line --

    def set_line_visible(self, visible: bool) -> None:
        self._line.setVisible(visible)

    def line_row(self) -> int:
        """Current line position as a sensor row index."""
        row = int(round(self._line.value()))
        if self._frame_height:
            row = max(0, min(row, self._frame_height - 1))
        return row

    def set_line_row(self, row: int) -> None:
        self._line.setPos(row)

    def _on_line_moved(self) -> None:
        self.line_row_changed.emit(self.line_row())
