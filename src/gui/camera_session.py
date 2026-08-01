"""
Owns the interaction with CameraInterface.start_live()/stop_live() and
marshals frames from the camera's background poll thread onto the Qt
main thread -- while also decoupling camera frame rate from display
rate.

Threading and rate decoupling, together:

_on_background_frame() runs on the backend's own poll thread (Mock's or
Thorlabs' -- CameraSession neither knows nor cares which) and does
exactly one thing: stash the frame in a lock-protected slot. It never
touches Qt.

A QTimer -- which fires on whatever thread constructed this QObject,
i.e. the Qt main thread, as long as CameraSession itself is built there
-- wakes up at a fixed display rate (DISPLAY_HZ) and pulls whatever is
in that slot, clearing it as it goes. If the camera is producing faster
than the display rate, every frame between two ticks is simply
overwritten in the same slot and never seen again -- there is no queue
to fall behind on and no backlog to build up. If the camera is slower
than the display rate, some ticks just find nothing to draw. Either
way memory use is O(1) in camera frame rate, not O(frames produced).
This is why MockCamera being able to emit thousands of frames/sec is
safe to point at a GUI: only the display-rate consumer ever runs Qt
code, and it only ever looks at "whatever is newest right now."

frame_ready is emitted from _on_display_tick(), which already runs on
the main thread (via the QTimer), so that emission is an ordinary
same-thread call -- Qt does not need to queue it. The cross-thread hop
already happened at the lock, not at the signal.

live_error/is_live() exist on some backends (ThorlabsCamera,
MockCamera) but are not part of CameraInterface, so this class doesn't
touch them -- checking for them would mean isinstance/hasattr, which is
the same backend-awareness this class exists to avoid. stream_stalled
is derived purely from the CameraInterface contract instead: if no
frame has arrived through on_frame() for STALL_TIMEOUT_S while live,
something is wrong, regardless of backend.
"""

import threading
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from core.camera_interface import CameraInterface


class CameraSession(QObject):
    frame_ready = Signal(object)
    stream_stalled = Signal()

    DISPLAY_HZ = 30.0
    STALL_TIMEOUT_S = 2.0

    def __init__(self, cam: CameraInterface, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._cam = cam

        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._seconds_since_frame = 0.0
        self._stall_notified = False

        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._on_display_tick)

    @property
    def camera(self) -> CameraInterface:
        return self._cam

    def start(self) -> None:
        self._latest_frame = None
        self._seconds_since_frame = 0.0
        self._stall_notified = False
        self._cam.start_live(self._on_background_frame)
        self._display_timer.start(int(1000.0 / self.DISPLAY_HZ))

    def stop(self) -> None:
        self._display_timer.stop()
        self._cam.stop_live()

    def _on_background_frame(self, frame: np.ndarray) -> None:
        # Background poll thread. Nothing but the assignment below may
        # happen here -- no Qt calls, no widget access.
        with self._lock:
            self._latest_frame = frame

    def _on_display_tick(self) -> None:
        with self._lock:
            frame, self._latest_frame = self._latest_frame, None

        if frame is not None:
            self._seconds_since_frame = 0.0
            self._stall_notified = False
            self.frame_ready.emit(frame)
            return

        self._seconds_since_frame += 1.0 / self.DISPLAY_HZ
        if self._seconds_since_frame >= self.STALL_TIMEOUT_S and not self._stall_notified:
            self._stall_notified = True
            self.stream_stalled.emit()
