"""
Composes the live image view, spectrum view, and camera controls, and
owns the connected-camera lifecycle: which CameraInterface is open (via
camera_factory.open_backend, a context manager) and the CameraSession
that drives it.

Nothing in this file checks what kind of CameraInterface it has -- the
only place backend selection happens is the string passed to
camera_factory.open_backend().
"""

from typing import Optional

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QWidget

from core.camera_interface import CameraInterface
from gui import camera_factory
from gui.camera_session import CameraSession
from gui.controls_panel import ControlsPanel
from gui.image_view import LiveImageView
from gui.spectrum_extraction import extract_spectrum
from gui.spectrum_view import SpectrumView

DEFAULT_BACKEND = "mock"


class MainWindow(QMainWindow):
    def __init__(self, backend: str = DEFAULT_BACKEND):
        super().__init__()
        self.setWindowTitle("Hyperspectral Imager")

        self._backend_kind = backend
        # The open_backend() context manager is driven manually
        # (__enter__/__exit__) rather than with `with`, because "connected"
        # spans multiple GUI events (a connect click ... a later disconnect
        # click), not one lexical block.
        self._backend_cm = None
        self._cam: Optional[CameraInterface] = None
        self._session: Optional[CameraSession] = None

        self.image_view = LiveImageView()
        self.spectrum_view = SpectrumView()
        self.controls = ControlsPanel()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.image_view, 2)
        layout.addWidget(self.spectrum_view, 2)
        layout.addWidget(self.controls, 1)
        self.setCentralWidget(central)

        self.controls.connect_toggled.connect(self._on_connect_toggled)
        self.controls.live_toggled.connect(self._on_live_toggled)

        self.statusBar().showMessage("Disconnected")

    # -- connect / disconnect --

    def _on_connect_toggled(self, checked: bool) -> None:
        if checked:
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        self._backend_cm = camera_factory.open_backend(self._backend_kind)
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

    def _on_frame(self, frame) -> None:
        self.image_view.show_frame(frame)
        self.spectrum_view.show_spectrum(extract_spectrum(frame))

    def _on_stream_stalled(self) -> None:
        self.statusBar().showMessage(f"Connected: {self._cam.name} -- STREAM STALLED", 5000)

    def closeEvent(self, event) -> None:
        if self.controls.connect_button.isChecked():
            self._disconnect()
        super().closeEvent(event)
