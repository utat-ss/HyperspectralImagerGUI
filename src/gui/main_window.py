"""
The application shell, and the owner of every camera connection.

Multiple devices may be connected at once; exactly one is *displayed*, and
only that one streams, so per-frame work stays at one camera's worth. The
live toggle therefore follows the display rather than each device.
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabWidget,
    QWidget,
)

from core.camera_interface import CameraInterface
from gui import camera_factory
from gui.camera_session import CameraSession
from gui.connect_tab import ConnectTab
from gui.connection_state import ConnectionState
from gui.controls_panel import ControlsPanel
from gui.image_view import LiveImageView
from gui.spectrum_extraction import extract_spectrum
from gui.spectrum_view import SpectrumView

DEFAULT_BACKEND = "mock"

# Frames averaged for the achieved-fps readout. Long enough that one slow
# frame does not make the number jump, short enough that a real regression
# shows up while the user is still looking at it.
_FPS_WINDOW_FRAMES = 30

TAB_CONNECT = "Connect to Device"
TAB_LIVE = "Live Data Viewer"


@dataclass
class _Connection:
    """One open camera. The context manager owns its resource lifetime."""

    kind: str
    context: Any
    camera: CameraInterface
    session: CameraSession


class MainWindow(QMainWindow):
    def __init__(self, backend: Optional[str] = DEFAULT_BACKEND):
        super().__init__()
        self.setWindowTitle("Hyperspectral Imager")

        self._connections: Dict[str, _Connection] = {}
        self._displayed: Optional[str] = None

        self.image_view = LiveImageView()
        self.spectrum_view = SpectrumView()
        self.controls = ControlsPanel()
        self.connect_tab = ConnectTab()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.connect_tab, TAB_CONNECT)
        self.tabs.addTab(self._build_live_tab(), TAB_LIVE)
        self.setCentralWidget(self.tabs)

        self.connect_tab.connect_requested.connect(self.connect_device)
        self.connect_tab.disconnect_requested.connect(self.disconnect_device)
        self.connect_tab.display_requested.connect(self.display_device)

        self.controls.live_toggled.connect(self._on_live_toggled)

        # Achieved frame rate, measured at the display end rather than
        # requested from the camera: exposure caps the real rate regardless
        # of the request, and the work done per frame costs time the camera
        # knows nothing about.
        self._fps_label = QLabel("-- fps")
        self.statusBar().addPermanentWidget(self._fps_label)
        self._frame_times: Deque[float] = deque(maxlen=_FPS_WINDOW_FRAMES)

        self.statusBar().showMessage("No device connected")

        if backend:
            self.connect_device(backend)
            self.tabs.setCurrentIndex(1)  # land on the live view for a demo

    # -- layout --

    def _build_live_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.addWidget(self.image_view, 2)
        layout.addWidget(self.spectrum_view, 2)
        layout.addWidget(self.controls, 1)
        return tab

    # -- connection lifecycle --

    def connect_device(self, kind: str) -> bool:
        """Open a device. Several may be open at once; returns success."""
        if kind in self._connections:
            return True

        self.connect_tab.set_state(kind, ConnectionState.CONNECTING, "opening...")
        context = camera_factory.open_backend(kind)
        try:
            camera = context.__enter__()
        except Exception as error:  # a backend can fail in many ways
            self.connect_tab.set_state(kind, ConnectionState.ERROR, str(error))
            return False

        session = CameraSession(camera, parent=self)
        session.stream_stalled.connect(self._on_stream_stalled)
        self._connections[kind] = _Connection(kind, context, camera, session)

        self.connect_tab.set_state(
            kind, ConnectionState.READY, f"connected: {camera.name}"
        )
        if self._displayed is None:
            self.display_device(kind)
        return True

    def disconnect_device(self, kind: str) -> None:
        connection = self._connections.pop(kind, None)
        if connection is None:
            return

        if self._displayed == kind:
            self._detach_display()

        connection.session.stop()
        connection.session.stream_stalled.disconnect(self._on_stream_stalled)
        connection.session.deleteLater()
        connection.context.__exit__(None, None, None)

        self.connect_tab.set_state(kind, ConnectionState.DISCONNECTED, "disconnected")

        # Fall back to another open device so the viewer is not left blank
        # while something is still connected.
        if self._displayed is None and self._connections:
            self.display_device(next(iter(self._connections)))
        elif not self._connections:
            self.statusBar().showMessage("No device connected")

    def display_device(self, kind: str) -> None:
        """Route a connected device's frames to the live viewer."""
        if kind not in self._connections or self._displayed == kind:
            return

        self._detach_display()
        connection = self._connections[kind]
        self._displayed = kind

        connection.session.frame_ready.connect(self._on_frame)

        bit_depth = connection.camera.get_bit_depth()
        self.image_view.set_bit_depth(bit_depth)
        self.spectrum_view.set_bit_depth(bit_depth)
        self.controls.configure_for_camera(connection.camera)

        self.connect_tab.set_displayed(kind)
        self.statusBar().showMessage(f"Displaying: {connection.camera.name}")

    def _detach_display(self) -> None:
        """Stop and unhook whatever is currently displayed."""
        if self._displayed is None:
            return
        connection = self._connections.get(self._displayed)
        if connection is not None:
            connection.session.stop()
            connection.session.frame_ready.disconnect(self._on_frame)

        self._displayed = None
        self.controls.reset()
        self._frame_times.clear()
        self._fps_label.setText("-- fps")

    def connected_kinds(self):
        return tuple(self._connections)

    def displayed_kind(self) -> Optional[str]:
        return self._displayed

    def displayed_camera(self) -> Optional[CameraInterface]:
        if self._displayed is None:
            return None
        return self._connections[self._displayed].camera

    # -- live view --

    def _on_live_toggled(self, checked: bool) -> None:
        if self._displayed is None:
            return
        connection = self._connections[self._displayed]
        if checked:
            connection.session.start()
            self.statusBar().showMessage(
                f"Displaying: {connection.camera.name} -- live"
            )
        else:
            connection.session.stop()
            self.statusBar().showMessage(f"Displaying: {connection.camera.name}")

    def _on_frame(self, frame) -> None:
        self.image_view.show_frame(frame)
        self.spectrum_view.show_spectrum(extract_spectrum(frame))
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
        message = "STREAM STALLED"
        self.statusBar().showMessage(message, 5000)
        self.connect_tab.append_log(message)

    # -- shutdown --

    def closeEvent(self, event) -> None:
        for kind in tuple(self._connections):
            self.disconnect_device(kind)
        super().closeEvent(event)
