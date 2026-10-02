"""
The application shell: a tab per area of the spec document's mockups, and
the owner of every camera connection.

Nothing in this file checks what kind of CameraInterface it has. Device
selection is a string from the Connect tab, handed straight to
camera_factory.open_backend().

Multiple devices may be connected at once; exactly one is *displayed*. Each
connection holds its own CameraSession, but only the displayed one is
started, so per-frame work in the display path stays at one camera's worth
no matter how many are open -- which is what the >= 10 fps requirement
depends on. Switching the displayed device stops the old stream and starts
the new one, which is why the live toggle follows the display rather than
each device keeping its own.
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
    QVBoxLayout,
    QWidget,
)

from core.calibration import CalibrationSet, WavelengthCalibration
from core.camera_interface import CameraInterface
from core.measurement_save import CaptureMetadata, SpectrumMeasurement
from core.qe_curve import load_bundled_qe_curve
from core.spectrum_extraction import ExtractionSettings, extract_spectrum
from gui import camera_factory
from gui.axes_panel import AxesPanel
from gui.calibration_panel import CalibrationPanel
from gui.camera_session import CameraSession
from gui.connect_tab import ConnectTab
from gui.connection_state import ConnectionState
from gui.controls_panel import ControlsPanel
from gui.extraction_panel import ExtractionPanel
from gui.image_view import LiveImageView
from gui.save_panel import SavePanel
from gui.spectrum_view import SpectrumView

DEFAULT_BACKEND = "mock"

# Frames averaged for the achieved-fps readout. Long enough that one slow
# frame does not make the number jump, short enough that a real regression
# shows up while the user is still looking at it.
_FPS_WINDOW_FRAMES = 30

TAB_CONNECT = "Connect to Device"
TAB_LIVE = "Live Data Viewer"
TAB_CALIBRATION = "Spec. Calibration"


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
        self._settings = ExtractionSettings()
        self._latest_frame = None
        self._latest_spectrum = None

        self.image_view = LiveImageView()
        self.spectrum_view = SpectrumView()
        self.extraction = ExtractionPanel()
        self.controls = ControlsPanel()
        self.axes = AxesPanel()
        self.connect_tab = ConnectTab()
        self.calibration_panel = CalibrationPanel()
        self.save_panel = SavePanel()
        # Neither panel touches a camera; the window supplies what they need.
        self.calibration_panel.set_frame_source(self.latest_frame)
        self.save_panel.set_measurement_source(self.build_measurement)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.connect_tab, TAB_CONNECT)
        self.tabs.addTab(self._build_live_tab(), TAB_LIVE)
        self.tabs.addTab(self.calibration_panel, TAB_CALIBRATION)
        self.setCentralWidget(self.tabs)

        self.connect_tab.connect_requested.connect(self.connect_device)
        self.connect_tab.disconnect_requested.connect(self.disconnect_device)
        self.connect_tab.display_requested.connect(self.display_device)

        self.controls.live_toggled.connect(self._on_live_toggled)
        self.extraction.settings_changed.connect(self._on_settings_changed)
        self.image_view.line_row_changed.connect(self.extraction.set_row)

        self.axes.calibration_selected.connect(self._on_calibration_selected)
        self.axes.x_range_changed.connect(self.spectrum_view.set_x_range)
        self.axes.y_range_changed.connect(self.spectrum_view.set_y_range)
        self.axes.marker_toggled.connect(self.spectrum_view.set_marker_visible)
        self.spectrum_view.marker_moved.connect(self.axes.update_marker_readout)
        self.calibration_panel.calibration_produced.connect(
            self._on_calibration_produced
        )

        self._calibrations = CalibrationSet()
        self.axes.set_calibrations(self._calibrations)

        # The shipped vendor QE curve. It is indexed by wavelength, so it
        # cannot become a per-column correction until a calibration exists
        # -- see _refresh_qe_curve.
        self._qe_curve = load_bundled_qe_curve()

        # Achieved frame rate, measured at the display end rather than
        # requested from the camera: exposure caps the real rate regardless
        # of the request, and the corrections that run per frame cost time
        # the camera knows nothing about.
        self._fps_label = QLabel("-- fps")
        self.statusBar().addPermanentWidget(self._fps_label)
        self._frame_times: Deque[float] = deque(maxlen=_FPS_WINDOW_FRAMES)

        self.statusBar().showMessage("No device connected")

        if backend:
            self.connect_device(backend)
            self.tabs.setCurrentIndex(1)  # land on the live view for a demo

    # -- layout --

    def _build_live_tab(self) -> QWidget:
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.controls)
        side_layout.addWidget(self.extraction)
        side_layout.addWidget(self.axes)
        side_layout.addWidget(self.save_panel)

        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.addWidget(self.image_view, 2)
        layout.addWidget(self.spectrum_view, 2)
        layout.addWidget(side, 1)
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
        self._latest_frame = None
        self._latest_spectrum = None
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

    def _on_settings_changed(self, settings: ExtractionSettings) -> None:
        self._settings = settings
        self.image_view.set_line_visible(self.extraction.line_mode_selected())

    def _on_frame(self, frame) -> None:
        self._latest_frame = frame
        spectrum = extract_spectrum(frame, self._settings)
        self._latest_spectrum = spectrum
        self.image_view.show_frame(frame)
        self.spectrum_view.show_spectrum(spectrum)
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

    # -- calibration --

    def _on_calibration_selected(self, calibration) -> None:
        self.spectrum_view.set_calibration(calibration)
        # A calibration that also characterised smile hands its coefficients
        # to the extraction panel, which is what un-greys smile correction.
        smile = calibration.smile_coeffs() if calibration is not None else None
        self.extraction.set_smile_coeffs(smile)
        self._refresh_qe_curve(calibration)

    def _refresh_qe_curve(self, calibration) -> None:
        """
        Resample the QE curve onto sensor columns for the selected
        calibration, or clear it when there is none.

        QE is a function of wavelength and a frame is a function of column,
        so this correction only becomes meaningful once lambda(x) is known.
        With no calibration there is nothing honest to apply, and the
        extraction panel greys the option out.
        """
        if calibration is None or self._qe_curve is None or self._latest_frame is None:
            self.extraction.set_qe_curve(None)
            return
        width = self._latest_frame.shape[1]
        self.extraction.set_qe_curve(
            self._qe_curve.resample_to_columns(calibration, width)
        )

    def add_calibration(self, calibration: WavelengthCalibration) -> None:
        """Publish a calibration to the axis toggle."""
        self._calibrations = self._calibrations.with_added(calibration)
        self.axes.set_calibrations(self._calibrations)

    def latest_frame(self):
        """
        Most recent displayed frame, or None. Handed to the calibration
        panel as its frame source so no widget has to know a camera exists.
        """
        return self._latest_frame

    def build_measurement(self, name: str, comments: str):
        """
        Assemble everything needed to save what is currently on screen.

        The save panel calls this rather than reaching for a camera itself,
        which keeps the rule that no widget touches a backend -- and makes
        the panel testable by injecting a measurement instead.

        Returns None when there is no spectrum yet, so the panel can say so
        rather than writing an empty folder.
        """
        if self._latest_spectrum is None:
            return None

        camera = self.displayed_camera()
        capture = CaptureMetadata()
        if camera is not None:
            capture = CaptureMetadata(
                camera_name=camera.name,
                exposure_us=camera.get_exposure_us(),
                gain=camera.get_gain(),
                frame_rate_hz=camera.get_frame_rate_hz(),
                bit_depth=camera.get_bit_depth(),
            )

        return SpectrumMeasurement(
            spectrum=self._latest_spectrum,
            frame=self._latest_frame,
            name=name,
            comments=comments,
            capture=capture,
            calibration=self.axes.current_calibration(),
            extraction=self._settings.describe(),
        )

    def _on_calibration_produced(self, result) -> None:
        """A method finished: publish it and select it straight away."""
        self.add_calibration(result.calibration)
        self.axes.calibration_combo.setCurrentText(result.calibration.name)

    # -- shutdown --

    def closeEvent(self, event) -> None:
        for kind in tuple(self._connections):
            self.disconnect_device(kind)
        super().closeEvent(event)
