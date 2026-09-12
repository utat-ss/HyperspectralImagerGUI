"""
Extraction panel state machine.

The panel decides which corrections are offered and which are actually
applied. Getting that wrong is not a cosmetic bug: a spectrum presented as
smile-corrected that was not corrected is silently wrong data, and a
correction the user cannot see or switch off is worse than one that is
simply absent.

Both regressions guarded here were real and found by driving the widget:
returning to binning after visiting line mode left smile correction stuck
disabled, and a programmatically-checked disabled box kept feeding its
correction into the emitted settings.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.spectrum_extraction import ExtractionMethod  # noqa: E402
from gui.extraction_panel import ExtractionPanel  # noqa: E402

SMILE = (0.0, 0.0, 2.5)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(application):
    widget = ExtractionPanel()
    yield widget
    widget.deleteLater()


def test_optional_methods_are_disabled_until_their_data_exists(panel):
    for check in (panel.smile_check, panel.dark_check, panel.qe_check):
        assert not check.isEnabled()
        assert check.toolTip()  # must say *why* it is unavailable


def test_dark_and_qe_enable_when_their_data_arrives(panel):
    panel.set_dark_frame(np.zeros((4, 4)))
    panel.set_qe_curve(np.ones(4))
    assert panel.dark_check.isEnabled()
    assert panel.qe_check.isEnabled()


def test_smile_re_enables_after_returning_from_line_mode(panel):
    """Regression: it used to stay disabled once line mode had been visited."""
    panel.set_smile_coeffs(SMILE)
    assert panel.smile_check.isEnabled()

    panel.line_radio.setChecked(True)
    assert not panel.smile_check.isEnabled()  # meaningless for a single row

    panel.binning_radio.setChecked(True)
    assert panel.smile_check.isEnabled()


def test_a_checked_but_disabled_box_does_not_apply_its_correction(panel):
    """
    Regression: setChecked() works on a disabled widget, so a stale check
    could keep a correction running that the user could no longer turn off.
    """
    panel.set_smile_coeffs(SMILE)
    panel.binning_radio.setChecked(True)
    panel.smile_check.setChecked(True)
    assert panel.current_settings().smile_coeffs == SMILE

    panel.line_radio.setChecked(True)  # disables the box, leaves it checked
    assert panel.current_settings().smile_coeffs is None


def test_clearing_a_calibration_unchecks_and_disables_smile(panel):
    panel.set_smile_coeffs(SMILE)
    panel.binning_radio.setChecked(True)
    panel.smile_check.setChecked(True)

    panel.set_smile_coeffs(None)
    assert not panel.smile_check.isEnabled()
    assert not panel.smile_check.isChecked()
    assert panel.current_settings().smile_coeffs is None


def test_method_selection_is_reflected_in_settings(panel):
    panel.line_radio.setChecked(True)
    assert panel.current_settings().method == ExtractionMethod.LINE
    assert panel.line_mode_selected()

    panel.binning_radio.setChecked(True)
    assert panel.current_settings().method == ExtractionMethod.BINNING
    assert not panel.line_mode_selected()


def test_dragging_the_line_updates_the_row_in_settings(panel):
    panel.line_radio.setChecked(True)
    panel.set_row(137)
    assert panel.current_settings().row == 137


def test_settings_changed_fires_when_the_user_changes_anything(panel):
    received = []
    panel.settings_changed.connect(received.append)

    panel.line_radio.setChecked(True)
    panel.set_row(50)
    panel.binning_radio.setChecked(True)

    assert len(received) >= 2
    assert received[-1].method == ExtractionMethod.BINNING
