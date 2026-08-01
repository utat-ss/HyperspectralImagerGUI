"""
Live 2D frame display, scaled from the connected camera's actual bit
depth rather than the frame's uint16 container dtype -- see
CameraInterface.get_bit_depth().
"""

import numpy as np
import pyqtgraph as pg


class LiveImageView(pg.ImageView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = (0, 1)
        self._first_frame = True

    def set_bit_depth(self, bit_depth: int) -> None:
        self._levels = (0, (1 << bit_depth) - 1)
        self._first_frame = True  # re-fit the view/histogram to the new range on the next frame

    def show_frame(self, frame: np.ndarray) -> None:
        self.setImage(
            frame,
            autoRange=self._first_frame,
            autoLevels=False,
            autoHistogramRange=self._first_frame,
            levels=self._levels,
        )
        self._first_frame = False
