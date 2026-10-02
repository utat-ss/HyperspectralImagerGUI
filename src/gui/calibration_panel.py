"""
The spec document's "Spec. Calibration" tab: run Method 1 or Method 2 on
captured frames and publish the resulting calibration.

Standalone like `extraction_panel` and `axes_panel`, so the tab shell
re-parents it rather than rewriting it.

The panel runs the workflow; it does not implement any of the mathematics --
that is `core.calibration_methods`, which has no Qt and is tested against
ground truth. What lives here is the sequencing a user has to go through:
capture a frame with each filter, or load a reference spectrum and capture
the lamp, then fit.

Frames come from a *frame source* the host sets (`set_frame_source`), rather
than the panel reaching for a camera. That keeps the one rule this codebase
cares about -- no widget touches a backend -- and makes the panel testable
by injecting a synthetic frame instead of a camera.

Every failure is reported in words in the status line. Calibration is the
step where a silent wrong answer is most expensive: a bad mapping does not
look broken, it just relabels the axis with plausible numbers.
"""

from typing import Callable, List, Optional

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.calibration_methods import (
    BandpassObservation,
    CalibrationError,
    CalibrationResult,
    calibrate_from_bandpass,
    calibrate_from_peaks,
    estimate_smile,
    save_calibration,
)
from core.reference_spectrum import (
    ReferenceSpectrum,
    load_reference_spectrum,
)
from core.spectrum_extraction import extract_spectrum

FrameSource = Callable[[], Optional[np.ndarray]]

_DEFAULT_FILTERS = ((545.0, 580.0), (680.0, 700.0))
_DIRECTIONS = ("Infer from data", "Wavelength rises with column", "Wavelength falls with column")


class _FilterRow:
    """One bandpass filter: its passband, and the frame captured through it."""

    def __init__(self, index: int, cut_on: float, cut_off: float):
        self.label = QLabel(f"Bandpass Filter {index + 1}")
        self.cut_on = _number_field(cut_on)
        self.cut_off = _number_field(cut_off)
        self.capture_button = QPushButton("Capture")
        self.status = QLabel("not captured")
        self.frame: Optional[np.ndarray] = None

    def observation(self) -> Optional[BandpassObservation]:
        if self.frame is None:
            return None
        return BandpassObservation(
            extract_spectrum(self.frame),
            float(self.cut_on.text()),
            float(self.cut_off.text()),
        )


class CalibrationPanel(QWidget):
    #: A finished calibration, ready to be published to the axis toggle.
    calibration_produced = Signal(object)  # CalibrationResult

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._frame_source: Optional[FrameSource] = None
        self._reference: Optional[ReferenceSpectrum] = None
        self._lamp_frame: Optional[np.ndarray] = None
        self._result: Optional[CalibrationResult] = None

        # -- nominal range (from the optical design, not measured) --
        self.range_low = _number_field(500.0)
        self.range_high = _number_field(1000.0)
        range_group = QGroupBox("Nominal wavelength range (optical design)")
        range_layout = QFormLayout(range_group)
        range_layout.addRow("Low (nm):", self.range_low)
        range_layout.addRow("High (nm):", self.range_high)

        # -- Method 1 --
        self.filter_rows: List[_FilterRow] = []
        method1_group = QGroupBox("Method 1: Bandpass Filter")
        grid = QGridLayout(method1_group)
        grid.addWidget(QLabel("Cut-on (nm)"), 0, 1)
        grid.addWidget(QLabel("Cut-off (nm)"), 0, 2)
        for index, (cut_on, cut_off) in enumerate(_DEFAULT_FILTERS):
            row = _FilterRow(index, cut_on, cut_off)
            row.capture_button.clicked.connect(
                lambda _checked=False, r=row: self._capture_filter(r)
            )
            grid.addWidget(row.label, index + 1, 0)
            grid.addWidget(row.cut_on, index + 1, 1)
            grid.addWidget(row.cut_off, index + 1, 2)
            grid.addWidget(row.capture_button, index + 1, 3)
            grid.addWidget(row.status, index + 1, 4)
            self.filter_rows.append(row)

        self.direction_combo = QComboBox()
        self.direction_combo.addItems(_DIRECTIONS)
        grid.addWidget(QLabel("Dispersion direction:"), len(_DEFAULT_FILTERS) + 1, 0)
        grid.addWidget(self.direction_combo, len(_DEFAULT_FILTERS) + 1, 1, 1, 2)

        self.run_method1_button = QPushButton("Update Spectral Calibration (Method 1)")
        self.run_method1_button.clicked.connect(self._run_method_1)
        grid.addWidget(self.run_method1_button, len(_DEFAULT_FILTERS) + 2, 0, 1, 5)

        # -- Method 2 --
        method2_group = QGroupBox("Method 2: Spectral Peaks")
        method2_layout = QVBoxLayout(method2_group)

        self.reference_label = QLabel("No reference spectrum loaded.")
        self.reference_label.setWordWrap(True)
        self.load_reference_button = QPushButton("Load reference spectrum (CSV)…")
        self.load_reference_button.clicked.connect(self._browse_reference)

        self.capture_lamp_button = QPushButton("Capture calibration lamp frame")
        self.capture_lamp_button.clicked.connect(self._capture_lamp)
        self.lamp_label = QLabel("No lamp frame captured.")

        self.run_method2_button = QPushButton("Update Spectral Calibration (Method 2)")
        self.run_method2_button.clicked.connect(self._run_method_2)

        for widget in (
            self.load_reference_button,
            self.reference_label,
            self.capture_lamp_button,
            self.lamp_label,
            self.run_method2_button,
        ):
            method2_layout.addWidget(widget)

        # -- shared options --
        self.estimate_smile_check = QCheckBox("Also measure spectral smile")
        self.estimate_smile_check.setChecked(True)
        self.estimate_smile_check.setToolTip(
            "Measures how far each row's spectrum is shifted along columns, "
            "which is what un-greys smile correction in the live viewer."
        )

        # -- results --
        results_group = QGroupBox("Pixel-to-Wavelength Mapping Info")
        results_layout = QVBoxLayout(results_group)
        self.coefficients_label = QLabel("—")
        self.smile_label = QLabel("—")
        self.residual_label = QLabel("—")
        for caption, widget in (
            ("Polynomial fitting coefficients:", self.coefficients_label),
            ("Spectral smile coefficients:", self.smile_label),
            ("Fit quality:", self.residual_label),
        ):
            results_layout.addWidget(QLabel(caption))
            widget.setWordWrap(True)
            results_layout.addWidget(widget)

        results_layout.addWidget(QLabel("Raw pixel-to-wavelength values:"))
        self.values_box = QPlainTextEdit()
        self.values_box.setReadOnly(True)
        self.values_box.setMaximumHeight(70)
        results_layout.addWidget(self.values_box)

        self.save_button = QPushButton("Save calibration to file…")
        self.save_button.clicked.connect(self._save)
        self.save_button.setEnabled(False)
        results_layout.addWidget(self.save_button)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)

        outer = QVBoxLayout(self)
        for widget in (
            range_group,
            method1_group,
            method2_group,
            self.estimate_smile_check,
            results_group,
            self.status_label,
        ):
            outer.addWidget(widget)
        outer.addStretch(1)

    # -- host wiring --

    def set_frame_source(self, source: Optional[FrameSource]) -> None:
        """Give the panel a way to fetch the current frame (set by the host)."""
        self._frame_source = source

    def set_reference_spectrum(self, reference: Optional[ReferenceSpectrum]) -> None:
        self._reference = reference
        self.reference_label.setText(
            "No reference spectrum loaded."
            if reference is None
            else f"{reference.source}: {reference.wavelengths_nm.size} points, "
            f"{reference.span_nm[0]:.1f}–{reference.span_nm[1]:.1f} nm"
        )

    def result(self) -> Optional[CalibrationResult]:
        return self._result

    # -- capture --

    def _current_frame(self) -> Optional[np.ndarray]:
        if self._frame_source is None:
            self._fail("No camera connected, so there is no frame to capture.")
            return None
        frame = self._frame_source()
        if frame is None:
            self._fail("The camera returned no frame. Is live view running?")
            return None
        return frame

    def _capture_filter(self, row: _FilterRow) -> None:
        frame = self._current_frame()
        if frame is None:
            return
        row.frame = frame
        row.status.setText("captured")
        self._ok(f"Captured a frame for {row.label.text()}.")

    def _capture_lamp(self) -> None:
        frame = self._current_frame()
        if frame is None:
            return
        self._lamp_frame = frame
        self.lamp_label.setText(f"Lamp frame captured ({frame.shape[1]}x{frame.shape[0]}).")
        self._ok("Captured the calibration lamp frame.")

    # -- running --

    def _nominal_range(self):
        try:
            low = float(self.range_low.text())
            high = float(self.range_high.text())
        except ValueError:
            raise CalibrationError("the nominal wavelength range must be two numbers")
        if low >= high:
            raise CalibrationError(
                f"nominal range low ({low}) must be below high ({high})"
            )
        return (low, high)

    def _smile_from(self, frame: Optional[np.ndarray]):
        """Measure smile if asked and possible; never fail the calibration over it."""
        if frame is None or not self.estimate_smile_check.isChecked():
            return None
        try:
            return estimate_smile(frame)
        except CalibrationError as error:
            # A wavelength solution is still useful without smile, so this
            # is reported and stepped over rather than aborting the run.
            self._warn(f"Smile not measured: {error}")
            return None

    def _run_method_1(self) -> None:
        observations = [row.observation() for row in self.filter_rows]
        observations = [o for o in observations if o is not None]
        if not observations:
            self._fail("Capture a frame through at least one bandpass filter first.")
            return

        index = self.direction_combo.currentIndex()
        ascending = {0: None, 1: True, 2: False}[index]

        # Explicit None checks: `a or b` on numpy arrays calls bool() on an
        # array, which raises rather than choosing.
        smile_frame = self._lamp_frame
        if smile_frame is None:
            captured = [r.frame for r in self.filter_rows if r.frame is not None]
            smile_frame = captured[0] if captured else None

        try:
            result = calibrate_from_bandpass(
                observations,
                ascending=ascending,
                smile=self._smile_from(smile_frame),
            )
        except (CalibrationError, ValueError) as error:
            self._fail(str(error))
            return
        self._publish(result)

    def _run_method_2(self) -> None:
        if self._reference is None:
            self._fail("Load a reference spectrum before running Method 2.")
            return
        if self._lamp_frame is None:
            self._fail("Capture a calibration lamp frame before running Method 2.")
            return

        try:
            nominal = self._nominal_range()
            smile = self._smile_from(self._lamp_frame)
            result = calibrate_from_peaks(
                extract_spectrum(self._lamp_frame),
                self._reference,
                nominal,
                smile=smile,
            )
        except (CalibrationError, ValueError) as error:
            self._fail(str(error))
            return
        self._publish(result)

    def _publish(self, result: CalibrationResult) -> None:
        self._result = result
        calibration = result.calibration

        self.coefficients_label.setText(
            ", ".join(
                f"a{power} = {value:.6g}"
                for power, value in enumerate(calibration.coefficients)
            )
        )
        smile = calibration.smile_coeffs()
        self.smile_label.setText(
            "not measured"
            if smile is None
            else ", ".join(f"s{p} = {v:.4g}" for p, v in enumerate(smile))
        )
        self.residual_label.setText(result.summary())

        width = self._lamp_frame.shape[1] if self._lamp_frame is not None else 16
        values = calibration.wavelengths(width)
        preview = ", ".join(f"{v:.2f}" for v in values[:12])
        self.values_box.setPlainText(
            f"[{preview}{', …' if values.size > 12 else ''}]"
        )

        self.save_button.setEnabled(True)
        self._ok(f"{calibration.name} succeeded — {result.summary()}.")
        self.calibration_produced.emit(result)

    # -- saving --

    def _browse_reference(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Load reference spectrum", "", "Spectra (*.csv *.txt *.tsv);;All files (*)"
        )
        if path:
            self.load_reference(path)

    def load_reference(self, path) -> None:
        """Split out from the file dialog so it can be driven directly."""
        try:
            self.set_reference_spectrum(load_reference_spectrum(path))
        except (OSError, ValueError) as error:
            self.set_reference_spectrum(None)
            self._fail(f"Could not read that reference spectrum: {error}")
            return
        self._ok("Reference spectrum loaded.")

    def _save(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save calibration", "calibration.json", "Calibration (*.json)"
        )
        if path:
            self.save_to(path)

    def save_to(self, path) -> None:
        """Split out from the file dialog so it can be driven directly."""
        if self._result is None:
            self._fail("There is no calibration to save yet.")
            return
        width = self._lamp_frame.shape[1] if self._lamp_frame is not None else None
        try:
            written = save_calibration(path, self._result, width=width)
        except OSError as error:
            self._fail(f"Could not write the calibration: {error}")
            return
        self._ok(f"Calibration saved to {written}.")

    # -- status --

    def _ok(self, message: str) -> None:
        self.status_label.setText(message)

    def _warn(self, message: str) -> None:
        self.status_label.setText(f"Warning: {message}")

    def _fail(self, message: str) -> None:
        self.status_label.setText(f"Cannot calibrate: {message}")


def _number_field(value: float) -> QLineEdit:
    field = QLineEdit(f"{value:g}")
    field.setValidator(QDoubleValidator())
    field.setMaximumWidth(90)
    return field
