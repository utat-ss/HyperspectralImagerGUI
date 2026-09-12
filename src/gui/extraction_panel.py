"""
The spec document's "Spectrum Calculation Method" checklist.

Deliberately a standalone widget rather than something wired into the
current window's layout: the tab shell (Phase 2) will re-parent it into the
Live Data Viewer tab, and a widget is a widget regardless of which tab holds
it. Building it this way means the tab work is a move, not a rewrite.

The panel owns *intent* only. It emits an
core.spectrum_extraction.ExtractionSettings describing what the user asked
for; it never touches a frame or a camera. Methods whose input data does
not exist yet (a dark frame, a QE curve, smile coefficients from a
calibration) disable themselves and say why, rather than silently running
as no-ops -- a spectrum labelled "smile corrected" that was not corrected
is worse than one honestly labelled uncorrected.
"""

from typing import Optional, Sequence

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGroupBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.spectrum_extraction import ExtractionMethod, ExtractionSettings

_NO_DARK_FRAME = "no dark frame loaded"
_NO_QE_CURVE = "no QE curve loaded"
_NO_CALIBRATION = "needs a spectral calibration"


class ExtractionPanel(QWidget):
    settings_changed = Signal(object)  # ExtractionSettings

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._row = 0
        self._dark_frame: Optional[np.ndarray] = None
        self._qe_curve: Optional[np.ndarray] = None
        self._smile_coeffs: Optional[Sequence[float]] = None

        self.line_radio = QRadioButton("Slide-Adjust Line Cross Section")
        self.binning_radio = QRadioButton("Horizontal Binning")
        self.binning_radio.setChecked(True)

        self._method_group = QButtonGroup(self)
        self._method_group.addButton(self.line_radio)
        self._method_group.addButton(self.binning_radio)

        self.smile_check = QCheckBox("Horizontal Binning with Smile Correction")
        self.dark_check = QCheckBox("Dark Noise Removal")
        self.qe_check = QCheckBox("Sensor QE Correction")

        group = QGroupBox("Spectrum Calculation Method")
        layout = QVBoxLayout(group)
        for widget in (
            self.line_radio,
            self.binning_radio,
            self.smile_check,
            self.dark_check,
            self.qe_check,
        ):
            layout.addWidget(widget)

        outer = QVBoxLayout(self)
        outer.addWidget(group)
        outer.addStretch(1)

        # Changing the *method* changes which modifiers apply -- smile
        # correction is meaningless in line mode -- so the radios re-run
        # availability rather than just re-emitting. Without this, selecting
        # line mode and then returning to binning leaves smile correction
        # stuck disabled while still contributing to the emitted settings.
        for radio in (self.line_radio, self.binning_radio):
            radio.toggled.connect(self._refresh_availability)
        for check in (self.smile_check, self.dark_check, self.qe_check):
            check.toggled.connect(self._emit_settings)

        self._refresh_availability()

    # -- data the methods depend on --

    def set_dark_frame(self, dark_frame: Optional[np.ndarray]) -> None:
        self._dark_frame = dark_frame
        self._refresh_availability()

    def set_qe_curve(self, qe_curve: Optional[np.ndarray]) -> None:
        self._qe_curve = qe_curve
        self._refresh_availability()

    def set_smile_coeffs(self, smile_coeffs: Optional[Sequence[float]]) -> None:
        """Called when a calibration is loaded or cleared (Phase 5)."""
        self._smile_coeffs = smile_coeffs
        self._refresh_availability()

    def set_row(self, row: int) -> None:
        """Called as the user drags the line on the image view."""
        if row == self._row:
            return
        self._row = row
        if self.line_radio.isChecked():
            self._emit_settings()

    # -- state --

    def current_settings(self) -> ExtractionSettings:
        # A correction counts as requested only if its box is both checked
        # *and* enabled. setChecked() works on a disabled widget, so without
        # the isEnabled() guard a stale check could keep feeding a
        # correction the user can no longer see or switch off.
        def requested(check) -> bool:
            return check.isChecked() and check.isEnabled()

        return ExtractionSettings(
            method=(
                ExtractionMethod.LINE
                if self.line_radio.isChecked()
                else ExtractionMethod.BINNING
            ),
            row=self._row,
            smile_coeffs=self._smile_coeffs if requested(self.smile_check) else None,
            dark_frame=self._dark_frame if requested(self.dark_check) else None,
            qe_curve=self._qe_curve if requested(self.qe_check) else None,
        )

    def line_mode_selected(self) -> bool:
        return self.line_radio.isChecked()

    # -- internals --

    def _refresh_availability(self) -> None:
        """
        Enable each optional method only when the data it needs exists, and
        put the reason in the tooltip so a greyed-out box is explicable
        rather than mysterious.
        """
        for check, available, reason in (
            (self.smile_check, self._smile_coeffs is not None, _NO_CALIBRATION),
            (self.dark_check, self._dark_frame is not None, _NO_DARK_FRAME),
            (self.qe_check, self._qe_curve is not None, _NO_QE_CURVE),
        ):
            check.setEnabled(available)
            check.setToolTip("" if available else reason)
            if not available and check.isChecked():
                check.blockSignals(True)
                check.setChecked(False)
                check.blockSignals(False)

        # Smile correction is a modifier on binning, not a separate method.
        if self.line_radio.isChecked():
            self.smile_check.setEnabled(False)
            self.smile_check.setToolTip("only applies to horizontal binning")

        self._emit_settings()

    def _emit_settings(self, _checked: bool = False) -> None:
        self.settings_changed.emit(self.current_settings())
