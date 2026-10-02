"""
The spec document's "Connect to Device" tab.

One row per device from `camera_factory.DEVICES`, each with a Connect
toggle, the three-lamp indicator, and a status line. Below them, the Device
Connection Log.

**Several devices may be connected at once, but only one is displayed
live.** That is the agreed behaviour: it honours the mockup's checkboxes
(you can hold both bands open without reconnecting to switch) while keeping
one camera's worth of per-frame work in the display path, which the >= 10
fps requirement depends on. The "Display" radio picks which connected
device feeds the live viewer.

The tab emits intent and renders state. It never opens a camera itself --
`MainWindow` owns every connection lifecycle, as it always has.
"""

from typing import Dict, Optional

from PySide6.QtCore import QDateTime, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from gui.camera_factory import DEVICES, Device, is_implemented
from gui.connection_state import ConnectionIndicator, ConnectionState

_MAX_LOG_LINES = 200


class _DeviceRow:
    """The widgets for one device."""

    def __init__(self, device: Device):
        self.device = device
        self.label = QLabel(device.label)
        self.band = QLabel(device.band or "—")
        self.connect_button = QPushButton("Connect")
        self.connect_button.setCheckable(True)
        self.display_radio = QRadioButton("Display")
        self.display_radio.setEnabled(False)
        self.indicator = ConnectionIndicator()
        self.status = QLabel("")
        self.status.setWordWrap(True)

        if not is_implemented(device.kind):
            # Listed so the instrument's full complement is visible, but it
            # cannot be opened -- saying why beats a mystery dead control.
            self.connect_button.setEnabled(False)
            self.status.setText(device.note or "Not implemented.")


class ConnectTab(QWidget):
    connect_requested = Signal(str)     # device kind
    disconnect_requested = Signal(str)  # device kind
    display_requested = Signal(str)     # device kind to show in the live viewer

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: Dict[str, _DeviceRow] = {}

        devices_group = QGroupBox("Devices")
        grid = QGridLayout(devices_group)
        for column, heading in enumerate(
            ("Device", "Band", "", "", "State", "Status")
        ):
            grid.addWidget(QLabel(f"<b>{heading}</b>" if heading else ""), 0, column)

        self._display_group = QButtonGroup(self)
        self._display_group.setExclusive(True)

        for index, device in enumerate(DEVICES, start=1):
            row = _DeviceRow(device)
            row.connect_button.toggled.connect(
                lambda checked, k=device.kind: self._on_connect_toggled(k, checked)
            )
            row.display_radio.toggled.connect(
                lambda checked, k=device.kind: checked
                and self.display_requested.emit(k)
            )
            self._display_group.addButton(row.display_radio)

            grid.addWidget(row.label, index, 0)
            grid.addWidget(row.band, index, 1)
            grid.addWidget(row.connect_button, index, 2)
            grid.addWidget(row.display_radio, index, 3)
            grid.addWidget(row.indicator, index, 4)
            grid.addWidget(row.status, index, 5)
            self._rows[device.kind] = row

        log_group = QGroupBox("Device Connection Log")
        log_layout = QVBoxLayout(log_group)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(_MAX_LOG_LINES)
        log_layout.addWidget(self.log)

        outer = QVBoxLayout(self)
        outer.addWidget(devices_group)
        outer.addWidget(log_group, 1)

    # -- intent --

    def _on_connect_toggled(self, kind: str, checked: bool) -> None:
        if checked:
            self.connect_requested.emit(kind)
        else:
            self.disconnect_requested.emit(kind)

    # -- rendering state, driven by MainWindow --

    def set_state(
        self, kind: str, state: ConnectionState, message: str = ""
    ) -> None:
        row = self._rows[kind]
        row.indicator.set_state(state)
        row.status.setText(message)

        connected = state is ConnectionState.READY
        row.display_radio.setEnabled(connected)
        row.connect_button.setText("Disconnect" if connected else "Connect")

        # A failed connect must leave the toggle *up*, or the next click
        # reads as a disconnect of something that was never opened.
        if state in (ConnectionState.DISCONNECTED, ConnectionState.ERROR):
            row.connect_button.blockSignals(True)
            row.connect_button.setChecked(False)
            row.connect_button.blockSignals(False)
            if row.display_radio.isChecked():
                self._display_group.setExclusive(False)
                row.display_radio.setChecked(False)
                self._display_group.setExclusive(True)

        if message:
            self.append_log(f"{row.device.label}: {message}")

    def set_displayed(self, kind: Optional[str]) -> None:
        for device_kind, row in self._rows.items():
            checked = device_kind == kind
            if row.display_radio.isChecked() != checked:
                row.display_radio.blockSignals(True)
                self._display_group.setExclusive(False)
                row.display_radio.setChecked(checked)
                self._display_group.setExclusive(True)
                row.display_radio.blockSignals(False)

    def state_of(self, kind: str) -> ConnectionState:
        return self._rows[kind].indicator.state()

    def displayed_kind(self) -> Optional[str]:
        for kind, row in self._rows.items():
            if row.display_radio.isChecked():
                return kind
        return None

    def append_log(self, message: str) -> None:
        stamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log.appendPlainText(f"[{stamp}] {message}")

    def log_text(self) -> str:
        return self.log.toPlainText()

    def row_widgets(self, kind: str) -> _DeviceRow:
        """Accessor for tests and for MainWindow's initial wiring."""
        return self._rows[kind]
