"""
Camera parameter controls: exposure, gain (hidden if the connected
backend doesn't support it), and connect/live toggle buttons.

Does not own the CameraInterface lifecycle (connect/disconnect,
start_live/stop_live) -- it only emits user intent (connect_toggled,
live_toggled) and lets MainWindow drive camera_factory/CameraSession.
Once MainWindow has a connected camera it calls configure_for_camera()
to populate ranges and current values from what the camera actually
reports; reset() clears the panel back to its disconnected state.

Gain's visibility is the mechanism that replaces an isinstance check:
CameraInterface.get_gain_range() returning None already means "this
backend has no gain control," so the panel just reads that and hides
the section -- no backend type checks needed anywhere.
"""

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.camera_interface import CameraInterface
from gui.camera_factory import AVAILABLE_BACKENDS

_DEBOUNCE_MS = 100
# Gain ranges are typically much narrower than exposure ranges; QSlider
# is integer-only, so scale gain up for finer steps on the same widget.
_GAIN_SLIDER_SCALE = 100


class ControlsPanel(QWidget):
    connect_toggled = Signal(bool)  # True: user wants to connect. False: disconnect.
    live_toggled = Signal(bool)  # True: start live. False: stop live.

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cam: Optional[CameraInterface] = None

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(AVAILABLE_BACKENDS)

        self.connect_button = QPushButton("Connect")
        self.connect_button.setCheckable(True)
        self.connect_button.toggled.connect(self.connect_toggled)

        self.live_button = QPushButton("Start Live")
        self.live_button.setCheckable(True)
        self.live_button.setEnabled(False)
        self.live_button.toggled.connect(self._on_live_toggled)

        self.exposure_slider = QSlider(Qt.Orientation.Horizontal)
        self.exposure_value_label = QLabel("--")
        self.exposure_slider.valueChanged.connect(self._on_exposure_slider_changed)
        self._exposure_debounce = QTimer(self)
        self._exposure_debounce.setSingleShot(True)
        self._exposure_debounce.setInterval(_DEBOUNCE_MS)
        self._exposure_debounce.timeout.connect(self._apply_exposure)

        self.gain_group = QGroupBox("Gain")
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_value_label = QLabel("--")
        self.gain_slider.valueChanged.connect(self._on_gain_slider_changed)
        self._gain_debounce = QTimer(self)
        self._gain_debounce.setSingleShot(True)
        self._gain_debounce.setInterval(_DEBOUNCE_MS)
        self._gain_debounce.timeout.connect(self._apply_gain)

        self._build_layout()
        self.reset()

    def _build_layout(self) -> None:
        exposure_group = QGroupBox("Exposure (µs)")
        exposure_layout = QHBoxLayout(exposure_group)
        exposure_layout.addWidget(self.exposure_slider)
        exposure_layout.addWidget(self.exposure_value_label)

        gain_layout = QHBoxLayout(self.gain_group)
        gain_layout.addWidget(self.gain_slider)
        gain_layout.addWidget(self.gain_value_label)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.connect_button)
        buttons_layout.addWidget(self.live_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.backend_combo)
        layout.addLayout(buttons_layout)
        layout.addWidget(exposure_group)
        layout.addWidget(self.gain_group)
        layout.addStretch(1)

    # -- backend selection --

    def current_backend(self) -> str:
        return self.backend_combo.currentText()

    def set_current_backend(self, kind: str) -> None:
        index = self.backend_combo.findText(kind)
        if index >= 0:
            self.backend_combo.setCurrentIndex(index)

    # -- lifecycle, driven by MainWindow --

    def configure_for_camera(self, cam: CameraInterface) -> None:
        """Call once right after connecting: populate ranges/current values from what the camera reports."""
        self._cam = cam
        self.backend_combo.setEnabled(False)
        self.connect_button.setText("Disconnect")

        lo, hi = cam.get_exposure_range_us()
        self.exposure_slider.setEnabled(True)
        self.exposure_slider.blockSignals(True)
        self.exposure_slider.setRange(int(lo), int(hi))
        self.exposure_slider.setValue(int(cam.get_exposure_us()))
        self.exposure_slider.blockSignals(False)
        self._update_exposure_label(cam.get_exposure_us())

        gain_range = cam.get_gain_range()
        self.gain_group.setVisible(gain_range is not None)
        if gain_range is not None:
            lo_g, hi_g = gain_range
            current_gain = cam.get_gain()
            self.gain_slider.blockSignals(True)
            self.gain_slider.setRange(int(lo_g * _GAIN_SLIDER_SCALE), int(hi_g * _GAIN_SLIDER_SCALE))
            self.gain_slider.setValue(int(current_gain * _GAIN_SLIDER_SCALE))
            self.gain_slider.blockSignals(False)
            self._update_gain_label(current_gain)

        self.live_button.setEnabled(True)

    def reset(self) -> None:
        """Call on init and on disconnect: clear back to the disconnected state."""
        self._cam = None
        self.backend_combo.setEnabled(True)
        self.connect_button.setText("Connect")

        self.live_button.blockSignals(True)
        self.live_button.setChecked(False)
        self.live_button.blockSignals(False)
        self.live_button.setEnabled(False)
        self.live_button.setText("Start Live")

        self.exposure_slider.setEnabled(False)
        self.exposure_slider.setRange(0, 1)
        self.exposure_value_label.setText("--")

        self.gain_group.setVisible(False)
        self.gain_value_label.setText("--")

    # -- live toggle --

    def _on_live_toggled(self, checked: bool) -> None:
        self.live_button.setText("Stop Live" if checked else "Start Live")
        self.live_toggled.emit(checked)

    # -- exposure --

    def _on_exposure_slider_changed(self, raw_value: int) -> None:
        self._update_exposure_label(float(raw_value))
        self._exposure_debounce.start()

    def _apply_exposure(self) -> None:
        if self._cam is None:
            return
        requested = float(self.exposure_slider.value())
        applied = self._cam.set_exposure_us(requested)
        self._set_exposure_display(applied)

    def _set_exposure_display(self, value: float) -> None:
        self.exposure_slider.blockSignals(True)
        self.exposure_slider.setValue(int(value))
        self.exposure_slider.blockSignals(False)
        self._update_exposure_label(value)

    def _update_exposure_label(self, value: float) -> None:
        self.exposure_value_label.setText(f"{value:,.0f}")

    # -- gain --

    def _on_gain_slider_changed(self, raw_value: int) -> None:
        self._update_gain_label(raw_value / _GAIN_SLIDER_SCALE)
        self._gain_debounce.start()

    def _apply_gain(self) -> None:
        if self._cam is None:
            return
        requested = self.gain_slider.value() / _GAIN_SLIDER_SCALE
        applied = self._cam.set_gain(requested)
        if applied is not None:
            self._set_gain_display(applied)

    def _set_gain_display(self, value: float) -> None:
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(int(value * _GAIN_SLIDER_SCALE))
        self.gain_slider.blockSignals(False)
        self._update_gain_label(value)

    def _update_gain_label(self, value: float) -> None:
        self.gain_value_label.setText(f"{value:.2f}")
