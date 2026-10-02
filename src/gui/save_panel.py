"""
Saving a measurement: name it, annotate it, choose formats, write it.

Standalone like the other panels, so the Process Data tab can adopt it
later rather than growing its own copy.

It lives in the Live Data Viewer rather than the Process Data tab the
mockup shows, because saving what is on screen is a live-view action and
Process Data is a later addition. The requirement it serves (a folder per
measurement, spectrum plus raw frame plus notes, in widely importable
formats) is core, so it should not wait on a good-to-have tab.

Like the calibration panel, it never touches a camera: `MainWindow` hands
it an assembled `SpectrumMeasurement` on request, so this widget can be
tested by injecting one.
"""

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

from core.measurement_save import (
    MANDATORY_FORMAT,
    SPECTRUM_FORMATS,
    SpectrumMeasurement,
    save_measurement,
)

#: (name, comments) -> a measurement ready to write, or None if there is nothing.
MeasurementSource = Callable[[str, str], Optional[SpectrumMeasurement]]

DEFAULT_ROOT = "measurements"

_FORMAT_CAPTIONS = {
    "csv": "CSV (always written)",
    "tsv": "TSV (tab separated)",
    "json": "JSON (spectrum + metadata)",
    "npy": "NPY (exact float64)",
    "txt": "TXT (whitespace)",
}


class SavePanel(QWidget):
    measurement_saved = Signal(object)  # pathlib.Path of the folder written

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._source: Optional[MeasurementSource] = None

        self.name_field = QLineEdit()
        self.name_field.setPlaceholderText("measurement name")

        self.comments_field = QPlainTextEdit()
        self.comments_field.setPlaceholderText("notes about this measurement…")
        self.comments_field.setMaximumHeight(60)

        self.root_field = QLineEdit(DEFAULT_ROOT)
        self.browse_button = QPushButton("…")
        self.browse_button.setMaximumWidth(30)
        self.browse_button.clicked.connect(self._browse_root)

        self.format_checks = {}
        formats_group = QGroupBox("File types")
        formats_layout = QGridLayout(formats_group)
        for index, name in enumerate(SPECTRUM_FORMATS):
            check = QCheckBox(_FORMAT_CAPTIONS[name])
            if name == MANDATORY_FORMAT:
                # CSV is the whole point of the requirement: a measurement
                # folder nobody else's tools can open is not a saved
                # measurement. Offered visibly, but not optional.
                check.setChecked(True)
                check.setEnabled(False)
                check.setToolTip("CSV is always written")
            formats_layout.addWidget(check, index // 2, index % 2)
            self.format_checks[name] = check

        self.save_button = QPushButton("Save measurement")
        self.save_button.clicked.connect(self.save)

        self.status_label = QLabel("Nothing saved yet.")
        self.status_label.setWordWrap(True)

        group = QGroupBox("Save Measurement")
        layout = QVBoxLayout(group)
        layout.addWidget(QLabel("Name:"))
        layout.addWidget(self.name_field)
        layout.addWidget(QLabel("Comments:"))
        layout.addWidget(self.comments_field)

        root_row = QHBoxLayout()
        root_row.addWidget(QLabel("Folder:"))
        root_row.addWidget(self.root_field, 1)
        root_row.addWidget(self.browse_button)
        layout.addLayout(root_row)

        layout.addWidget(formats_group)
        layout.addWidget(self.save_button)
        layout.addWidget(self.status_label)

        outer = QVBoxLayout(self)
        outer.addWidget(group)
        outer.addStretch(1)

    # -- host wiring --

    def set_measurement_source(self, source: Optional[MeasurementSource]) -> None:
        """Give the panel a way to assemble the current measurement."""
        self._source = source

    def selected_formats(self):
        return tuple(
            name for name, check in self.format_checks.items() if check.isChecked()
        )

    # -- saving --

    def _browse_root(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self, "Choose where measurements are saved", self.root_field.text()
        )
        if folder:
            self.root_field.setText(folder)

    def save(self) -> Optional[Path]:
        """Assemble and write the current measurement. Returns the folder."""
        if self._source is None:
            self._fail("No camera connected, so there is nothing to save.")
            return None

        measurement = self._source(
            self.name_field.text(), self.comments_field.toPlainText()
        )
        if measurement is None:
            self._fail(
                "No spectrum to save yet — start live view or capture a frame first."
            )
            return None

        root = self.root_field.text().strip() or DEFAULT_ROOT
        try:
            folder = save_measurement(root, measurement, self.selected_formats())
        except (OSError, ValueError) as error:
            self._fail(f"Could not save: {error}")
            return None

        note = (
            ""
            if measurement.is_calibrated
            else "  (uncalibrated — x column is pixel index)"
        )
        self.status_label.setText(f"Saved to {folder}{note}")
        self.measurement_saved.emit(folder)
        return folder

    def _fail(self, message: str) -> None:
        self.status_label.setText(f"Cannot save: {message}")
