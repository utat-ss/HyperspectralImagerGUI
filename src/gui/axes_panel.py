"""
The spec document's "Adjust Axes" box, plus the calibration toggle.

Standalone like `extraction_panel`, so the tab shell can re-parent it into
the Live Data Viewer rather than rewrite it.

Two honesty rules drive the design:

- **Blank means autoscale.** An empty range field is not an error and not a
  zero; it means "use the default" -- the data extent for x, the camera's
  bit-depth range for y. Clearing a field must return the axis to that
  default, not leave it frozen where the user last put it.
- **With no calibration loaded the toggle says so in words.** It reads
  "No calibration (pixel index)" rather than being empty or defaulting to
  something that implies wavelengths nobody measured.
"""

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from core.calibration import (
    NO_CALIBRATION_LABEL,
    CalibrationSet,
    WavelengthCalibration,
)
from gui.spectrum_view import MARKER_COUNT

_MARKER_LABELS = ("Marker 1", "Marker 2")


class AxesPanel(QWidget):
    #: (low, high) or (None, None) to clear the override.
    x_range_changed = Signal(object, object)
    y_range_changed = Signal(object, object)
    #: The selected calibration, or None for raw pixel index.
    calibration_selected = Signal(object)
    marker_toggled = Signal(int, bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._calibrations = CalibrationSet()

        self.calibration_combo = QComboBox()
        self.calibration_combo.addItem(NO_CALIBRATION_LABEL)
        self.calibration_combo.currentIndexChanged.connect(self._on_calibration_changed)

        calibration_group = QGroupBox("Spectral Calibration")
        calibration_layout = QVBoxLayout(calibration_group)
        calibration_layout.addWidget(self.calibration_combo)
        self.calibration_note = QLabel("No calibration loaded — x axis is pixel index.")
        self.calibration_note.setWordWrap(True)
        calibration_layout.addWidget(self.calibration_note)

        self.x_low = self._range_field()
        self.x_high = self._range_field()
        self.y_low = self._range_field()
        self.y_high = self._range_field()

        self.autoscale_check = QCheckBox("Autoscale (clear manual ranges)")
        self.autoscale_check.setChecked(True)
        self.autoscale_check.toggled.connect(self._on_autoscale_toggled)

        axes_group = QGroupBox("Adjust Axes")
        axes_layout = QFormLayout(axes_group)
        axes_layout.addRow("X-axis left:", self.x_low)
        axes_layout.addRow("X-axis right:", self.x_high)
        axes_layout.addRow("Y-axis low:", self.y_low)
        axes_layout.addRow("Y-axis high:", self.y_high)
        axes_layout.addRow(self.autoscale_check)

        self.marker_checks = []
        self.marker_readouts = []
        markers_group = QGroupBox("Markers")
        markers_layout = QVBoxLayout(markers_group)
        for index in range(MARKER_COUNT):
            check = QCheckBox(_MARKER_LABELS[index])
            readout = QLabel("X: --   Y: --")
            check.toggled.connect(
                lambda checked, i=index: self.marker_toggled.emit(i, checked)
            )
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(readout, 1)
            markers_layout.addLayout(row)
            self.marker_checks.append(check)
            self.marker_readouts.append(readout)

        outer = QVBoxLayout(self)
        outer.addWidget(calibration_group)
        outer.addWidget(axes_group)
        outer.addWidget(markers_group)
        outer.addStretch(1)

        for field in (self.x_low, self.x_high):
            field.editingFinished.connect(self._on_x_edited)
        for field in (self.y_low, self.y_high):
            field.editingFinished.connect(self._on_y_edited)

    @staticmethod
    def _range_field() -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText("auto")
        field.setValidator(QDoubleValidator())
        return field

    # -- calibrations --

    def set_calibrations(self, calibrations: CalibrationSet) -> None:
        """
        Repopulate the toggle. Keeps the current selection if it survives,
        so re-running one calibration method does not silently switch the
        user's axis to a different one.
        """
        previous = self.calibration_combo.currentText()
        self._calibrations = calibrations

        self.calibration_combo.blockSignals(True)
        self.calibration_combo.clear()
        self.calibration_combo.addItem(NO_CALIBRATION_LABEL)
        for calibration in calibrations:
            self.calibration_combo.addItem(calibration.name)
        index = self.calibration_combo.findText(previous)
        self.calibration_combo.setCurrentIndex(max(index, 0))
        self.calibration_combo.blockSignals(False)

        self._on_calibration_changed()

    def current_calibration(self) -> Optional[WavelengthCalibration]:
        if self.calibration_combo.currentIndex() <= 0:
            return None
        return self._calibrations.by_name(self.calibration_combo.currentText())

    def _on_calibration_changed(self, _index: int = 0) -> None:
        calibration = self.current_calibration()
        if calibration is None:
            self.calibration_note.setText(
                "No calibration loaded — x axis is pixel index."
                if len(self._calibrations) == 0
                else "Calibration off — x axis is pixel index."
            )
        else:
            self.calibration_note.setText(calibration.describe())

        # Switching units invalidates any manual x range, so the panel's
        # fields must not keep showing numbers that no longer apply.
        self.x_low.clear()
        self.x_high.clear()
        self.calibration_selected.emit(calibration)

    # -- ranges --

    def _on_autoscale_toggled(self, checked: bool) -> None:
        if not checked:
            return
        for field in (self.x_low, self.x_high, self.y_low, self.y_high):
            field.clear()
        self.x_range_changed.emit(None, None)
        self.y_range_changed.emit(None, None)

    def _on_x_edited(self) -> None:
        self._emit_range(self.x_low, self.x_high, self.x_range_changed)

    def _on_y_edited(self) -> None:
        self._emit_range(self.y_low, self.y_high, self.y_range_changed)

    def _emit_range(self, low_field, high_field, signal) -> None:
        low = _parse(low_field.text())
        high = _parse(high_field.text())

        if low is None or high is None:
            signal.emit(None, None)
            return
        if low >= high:
            # Refuse rather than raise: a half-typed range routinely reads
            # as inverted, and an exception out of a Qt slot would take the
            # whole edit down.
            return

        self.autoscale_check.blockSignals(True)
        self.autoscale_check.setChecked(False)
        self.autoscale_check.blockSignals(False)
        signal.emit(low, high)

    # -- marker readouts --

    def update_marker_readout(self, index: int, x: float, y: float) -> None:
        if y != y:  # NaN: marker is off the trace
            self.marker_readouts[index].setText(f"X: {x:,.2f}   Y: --")
        else:
            self.marker_readouts[index].setText(f"X: {x:,.2f}   Y: {y:,.1f}")

    def set_marker_checked(self, index: int, checked: bool) -> None:
        self.marker_checks[index].setChecked(checked)


def _parse(text: str) -> Optional[float]:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
