"""
Composes the live image view, spectrum view, and camera controls, and
owns the connected-camera lifecycle: which CameraInterface is open (via
camera_factory.open_backend, a context manager) and the CameraSession
that drives it.

Nothing in this file checks what kind of CameraInterface it has -- the
only place backend selection happens is the string read from
ControlsPanel's backend dropdown and passed to camera_factory.open_backend().
"""

import time
from collections import deque
from typing import Deque, Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from core.camera_interface import CameraInterface
from core.spectrum_extraction import ExtractionSettings, extract_spectrum
from gui import camera_factory
from gui.camera_session import CameraSession
from gui.controls_panel import ControlsPanel
from gui.extraction_panel import ExtractionPanel
from gui.image_view import LiveImageView
from gui.spectrum_view import SpectrumView

DEFAULT_BACKEND = "mock"

# Frames averaged for the achieved-fps readout. Long enough that one slow
# frame does not make the number jump, short enough that a real regression
# shows up while the user is still looking at it.
_FPS_WINDOW_FRAMES = 30


class MainWindow(QMainWindow):
    def __init__(self, backend: str = DEFAULT_BACKEND):
        super().__init__()
        self.setWindowTitle("Hyperspectral Imager")

        # The open_backend() context manager is driven manually
        # (__enter__/__exit__) rather than with `with`, because "connected"
        # spans multiple GUI events (a connect click ... a later disconnect
        # click), not one lexical block.
        self._backend_cm = None
        self._cam: Optional[CameraInterface] = None
        self._session: Optional[CameraSession] = None

        self._settings = ExtractionSettings()

        self.image_view = LiveImageView()
        self.spectrum_view = SpectrumView()
        self.extraction = ExtractionPanel()
        self.controls = ControlsPanel()
        self.controls.set_current_backend(backend)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.controls)
        side_layout.addWidget(self.extraction)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.image_view, 2)
        layout.addWidget(self.spectrum_view, 2)
        layout.addWidget(side, 1)
        self.setCentralWidget(central)

        self.controls.connect_toggled.connect(self._on_connect_toggled)
        self.controls.live_toggled.connect(self._on_live_toggled)
        self.extraction.settings_changed.connect(self._on_settings_changed)
        self.image_view.line_row_changed.connect(self.extraction.set_row)

        # Achieved frame rate, measured at the display end of the pipeline
        # rather than requested from the camera: exposure caps the real rate
        # regardless of what was asked for, and the corrections and
        # calibration resample that run per frame cost time the camera knows
        # nothing about. This is the number that tells the truth about
        # whether the >= 10 fps requirement still holds.
        self._fps_label = QLabel("-- fps")
        self.statusBar().addPermanentWidget(self._fps_label)
        self._frame_times: Deque[float] = deque(maxlen=_FPS_WINDOW_FRAMES)

        self.statusBar().showMessage("Disconnected")

    # -- connect / disconnect --

    def _on_connect_toggled(self, checked: bool) -> None:
        if checked:
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        self._backend_cm = camera_factory.open_backend(self.controls.current_backend())
        self._cam = self._backend_cm.__enter__()

        self._session = CameraSession(self._cam, parent=self)
        self._session.frame_ready.connect(self._on_frame)
        self._session.stream_stalled.connect(self._on_stream_stalled)

        bit_depth = self._cam.get_bit_depth()
        self.image_view.set_bit_depth(bit_depth)
        self.spectrum_view.set_bit_depth(bit_depth)

        self.controls.configure_for_camera(self._cam)
        self.statusBar().showMessage(f"Connected: {self._cam.name}")

    def _disconnect(self) -> None:
        if self._session is not None:
            self._session.stop()
            self._session.frame_ready.disconnect(self._on_frame)
            self._session.stream_stalled.disconnect(self._on_stream_stalled)
            self._session.deleteLater()
            self._session = None

        if self._backend_cm is not None:
            self._backend_cm.__exit__(None, None, None)
            self._backend_cm = None
        self._cam = None

        self.controls.reset()
        # Stale timings from the last session would otherwise be averaged
        # into the next one's first readings.
        self._frame_times.clear()
        self._fps_label.setText("-- fps")
        self.statusBar().showMessage("Disconnected")

    # -- live view --

    def _on_live_toggled(self, checked: bool) -> None:
        if self._session is None:
            return
        if checked:
            self._session.start()
            self.statusBar().showMessage(f"Connected: {self._cam.name} -- live")
        else:
            self._session.stop()
            self.statusBar().showMessage(f"Connected: {self._cam.name}")

    def _on_settings_changed(self, settings: ExtractionSettings) -> None:
        self._settings = settings
        self.image_view.set_line_visible(self.extraction.line_mode_selected())

    def _on_frame(self, frame) -> None:
        self.image_view.show_frame(frame)
        self.spectrum_view.show_spectrum(extract_spectrum(frame, self._settings))
        self._record_frame_time()

    def _record_frame_time(self) -> None:
        self._frame_times.append(time.perf_counter())
        if len(self._frame_times) < 2:
            return
        elapsed = self._frame_times[-1] - self._frame_times[0]
        if elapsed <= 0:
            return
        fps = (len(self._frame_times) - 1) / elapsed
        self._fps_label.setText(f"{fps:.1f} fps")

    def _on_stream_stalled(self) -> None:
        self.statusBar().showMessage(f"Connected: {self._cam.name} -- STREAM STALLED", 5000)

    def closeEvent(self, event) -> None:
        if self.controls.connect_button.isChecked():
            self._disconnect()
        super().closeEvent(event)
