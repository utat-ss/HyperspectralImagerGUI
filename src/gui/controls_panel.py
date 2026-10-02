"""
Camera parameter controls for the displayed device: exposure, gain, frame
rate, and the live toggle.

Device *selection* and connect/disconnect live in the Connect tab -- this
panel only ever configures whichever camera is currently being displayed.

Optional capability is decided by the contract, never by backend type:
`get_gain_range()` or `get_frame_rate_range_hz()` returning `None` means
"this backend has no such control", and the section hides itself on that
basis alone. That is the mechanism that replaces an isinstance check.
"""

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.camera_interface import CameraInterface

_DEBOUNCE_MS = 100
# Gain and frame rate ranges are typically much narrower than exposure
# ranges; QSlider is integer-only, so scale them up for finer steps.
_GAIN_SLIDER_SCALE = 100
_FRAME_RATE_SLIDER_SCALE = 10


class ControlsPanel(QWidget):
    live_toggled = Signal(bool)  # True: start live. False: stop live.

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cam: Optional[CameraInterface] = None

        self.live_button = QPushButton("Start Live")
        self.live_button.setCheckable(True)
        self.live_button.setEnabled(False)
        self.live_button.toggled.connect(self._on_live_toggled)

        self.exposure_group = QGroupBox("Exposure (µs)")
        self.exposure_slider = QSlider(Qt.Orientation.Horizontal)
        self.exposure_value_label = QLabel("--")
        self.exposure_slider.valueChanged.connect(self._on_exposure_slider_changed)
        self._exposure_debounce = self._debounce(self._apply_exposure)

        self.gain_group = QGroupBox("Gain")
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_value_label = QLabel("--")
        self.gain_slider.valueChanged.connect(self._on_gain_slider_changed)
        self._gain_debounce = self._debounce(self._apply_gain)

        self.frame_rate_group = QGroupBox("Frame rate (fps)")
        self.frame_rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_rate_value_label = QLabel("--")
        self.frame_rate_slider.valueChanged.connect(self._on_frame_rate_slider_changed)
        self._frame_rate_debounce = self._debounce(self._apply_frame_rate)

        self._build_layout()
        self.reset()

    def _debounce(self, slot) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(_DEBOUNCE_MS)
        timer.timeout.connect(slot)
        return timer

    def _build_layout(self) -> None:
        for group, slider, value_label in (
            (self.exposure_group, self.exposure_slider, self.exposure_value_label),
            (self.gain_group, self.gain_slider, self.gain_value_label),
            (
                self.frame_rate_group,
                self.frame_rate_slider,
                self.frame_rate_value_label,
            ),
        ):
            row = QHBoxLayout(group)
            row.addWidget(slider)
            row.addWidget(value_label)

        layout = QVBoxLayout(self)
        layout.addWidget(self.live_button)
        layout.addWidget(self.exposure_group)
        layout.addWidget(self.gain_group)
        layout.addWidget(self.frame_rate_group)
        layout.addStretch(1)

    # -- lifecycle, driven by MainWindow --

    def configure_for_camera(self, cam: Optional[CameraInterface]) -> None:
        """
        Point the panel at the displayed camera, or at nothing.

        Called on connect and whenever the displayed device changes, so the
        sliders always describe the camera the user is actually watching.
        """
        if cam is None:
            self.reset()
            return

        self._cam = cam

        lo, hi = cam.get_exposure_range_us()
        self.exposure_slider.setEnabled(True)
        self.exposure_slider.blockSignals(True)
        self.exposure_slider.setRange(int(lo), int(hi))
        self.exposure_slider.setValue(int(cam.get_exposure_us()))
        self.exposure_slider.blockSignals(False)
        self._update_exposure_label(cam.get_exposure_us())

        self._configure_optional(
            self.gain_group,
            self.gain_slider,
            cam.get_gain_range(),
            cam.get_gain(),
            _GAIN_SLIDER_SCALE,
            self._update_gain_label,
        )
        self._configure_optional(
            self.frame_rate_group,
            self.frame_rate_slider,
            cam.get_frame_rate_range_hz(),
            cam.get_frame_rate_hz(),
            _FRAME_RATE_SLIDER_SCALE,
            self._update_frame_rate_label,
        )

        self.live_button.setEnabled(True)

    @staticmethod
    def _configure_optional(group, slider, value_range, current, scale, update_label):
        group.setVisible(value_range is not None)
        if value_range is None or current is None:
            return
        low, high = value_range
        slider.blockSignals(True)
        slider.setRange(int(low * scale), int(high * scale))
        slider.setValue(int(current * scale))
        slider.blockSignals(False)
        update_label(current)

    def reset(self) -> None:
        """Clear back to the no-camera state."""
        self._cam = None

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
        self.frame_rate_group.setVisible(False)
        self.frame_rate_value_label.setText("--")

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
        applied = self._cam.set_exposure_us(float(self.exposure_slider.value()))
        self.exposure_slider.blockSignals(True)
        self.exposure_slider.setValue(int(applied))
        self.exposure_slider.blockSignals(False)
        self._update_exposure_label(applied)

    def _update_exposure_label(self, value: float) -> None:
        self.exposure_value_label.setText(f"{value:,.0f}")

    # -- gain --

    def _on_gain_slider_changed(self, raw_value: int) -> None:
        self._update_gain_label(raw_value / _GAIN_SLIDER_SCALE)
        self._gain_debounce.start()

    def _apply_gain(self) -> None:
        if self._cam is None:
            return
        applied = self._cam.set_gain(self.gain_slider.value() / _GAIN_SLIDER_SCALE)
        if applied is None:
            return
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(int(applied * _GAIN_SLIDER_SCALE))
        self.gain_slider.blockSignals(False)
        self._update_gain_label(applied)

    def _update_gain_label(self, value: float) -> None:
        self.gain_value_label.setText(f"{value:.2f}")

    # -- frame rate --

    def _on_frame_rate_slider_changed(self, raw_value: int) -> None:
        self._update_frame_rate_label(raw_value / _FRAME_RATE_SLIDER_SCALE)
        self._frame_rate_debounce.start()

    def _apply_frame_rate(self) -> None:
        if self._cam is None:
            return
        applied = self._cam.set_frame_rate_hz(
            self.frame_rate_slider.value() / _FRAME_RATE_SLIDER_SCALE
        )
        if applied is None:
            return
        self.frame_rate_slider.blockSignals(True)
        self.frame_rate_slider.setValue(int(applied * _FRAME_RATE_SLIDER_SCALE))
        self.frame_rate_slider.blockSignals(False)
        self._update_frame_rate_label(applied)

    def _update_frame_rate_label(self, value: float) -> None:
        self.frame_rate_value_label.setText(f"{value:.1f}")
