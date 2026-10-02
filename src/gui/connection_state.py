"""
Connection state, and the three-lamp indicator the spec document shows.

**This lives in the GUI on purpose, not on `CameraInterface`.** All three
states are things the caller already knows without asking a camera:
`CONNECTING` is "a connect() call is in flight", `READY` is
`is_connected()`, and `ERROR` is "connect() raised" -- which is also where
the message for the connection log comes from. Putting it on the ABC would
add contract surface every backend must implement and none could answer
better than the code calling it.

The one case the GUI genuinely cannot see is a camera that faults *after*
connecting, and `CameraSession.stream_stalled` already covers that without
reaching for backend-specific attributes.
"""

from enum import Enum
from typing import Optional

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    ERROR = "error"


#: Lamp colours. Grey means "this state is not the current one".
_LIT = {
    ConnectionState.CONNECTING: "#d6a100",
    ConnectionState.READY: "#2e9e4f",
    ConnectionState.ERROR: "#c0392b",
}
_UNLIT = "#d7d7d7"

#: The order the spec document's mockup shows them in.
_LAMP_ORDER = (
    (ConnectionState.CONNECTING, "Connecting"),
    (ConnectionState.READY, "Ready"),
    (ConnectionState.ERROR, "Error"),
)


class ConnectionIndicator(QWidget):
    """
    Three lamps -- Connecting / Ready / Error -- with at most one lit.

    Disconnected is represented by *no* lamp lit rather than a fourth lamp,
    matching the mockup: an idle device should look idle, not like it is
    reporting something.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._state = ConnectionState.DISCONNECTED
        self._lamps = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for state, caption in _LAMP_ORDER:
            row = QHBoxLayout()
            lamp = QLabel()
            lamp.setFixedSize(12, 12)
            row.addWidget(lamp)
            row.addWidget(QLabel(caption))
            row.addStretch(1)
            layout.addLayout(row)
            self._lamps[state] = lamp

        self.set_state(ConnectionState.DISCONNECTED)

    def state(self) -> ConnectionState:
        return self._state

    def set_state(self, state: ConnectionState) -> None:
        self._state = state
        for lamp_state, lamp in self._lamps.items():
            colour = _LIT[lamp_state] if lamp_state is state else _UNLIT
            lamp.setStyleSheet(
                f"background-color: {colour}; border: 1px solid #888; border-radius: 6px;"
            )

    def is_lit(self, state: ConnectionState) -> bool:
        """Whether a particular lamp is currently lit (used by tests)."""
        return self._state is state
