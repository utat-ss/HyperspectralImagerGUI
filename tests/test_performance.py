"""
Frame-rate regression guard for the >= 10 fps requirement.

`CameraSession` decouples camera rate from display rate, so for a long time
"is it fast enough?" was trivially yes -- nothing ran per frame. That stops
being true as corrections accumulate: dark subtraction, smile correction and
(from Phase 5) a calibration resample all run on every frame, on the Qt main
thread, between the camera delivering a frame and the user seeing it.

These tests measure the *whole* path at a real sensor size, so a change that
quietly halves throughput fails here rather than being discovered at a demo.

Timing tests are inherently machine-dependent, so the threshold is the
actual requirement (10 fps) rather than a tight fit to current performance.
At the time of writing the full correction stack runs at roughly 46 fps on a
1280x1024 frame, so there is real headroom -- if this test ever fails it
means something got dramatically slower, not that the machine was busy.
"""

import os
import time

import numpy as np
import pytest

# Must be set before Qt is imported, so the GUI half of this module runs on
# a machine (or CI lane) with no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.spectrum_extraction import (  # noqa: E402
    ExtractionMethod,
    ExtractionSettings,
    extract_spectrum,
)
from core.synthetic_spectrograph import (  # noqa: E402
    EmissionLine,
    NoiseModel,
    render,
    truth_for_range,
)

REQUIRED_FPS = 10.0

# The Thorlabs CS135MUN's sensor: the largest frame this project has to
# handle, so it is the honest size to measure at.
SENSOR_WIDTH, SENSOR_HEIGHT = 1280, 1024

_MEASURE_FRAMES = 15
_WARMUP_FRAMES = 3


@pytest.fixture(scope="module")
def sensor_frame():
    truth = truth_for_range(
        (500.0, 1000.0),
        width=SENSOR_WIDTH,
        height=SENSOR_HEIGHT,
        lines=(EmissionLine(560.0), EmissionLine(700.0), EmissionLine(900.0)),
        smile_coeffs=(0.0, 0.0, 4.0),
        noise=NoiseModel(dark_offset=0.01, read_noise_sigma=0.003),
    )
    return truth, render(truth, rng=np.random.default_rng(0))


def measure_fps(work, frames: int = _MEASURE_FRAMES) -> float:
    for _ in range(_WARMUP_FRAMES):  # let caches and any lazy import settle
        work()
    start = time.perf_counter()
    for _ in range(frames):
        work()
    elapsed = time.perf_counter() - start
    return frames / elapsed


def test_full_correction_stack_sustains_the_required_frame_rate(sensor_frame):
    truth, frame = sensor_frame
    settings = ExtractionSettings(
        method=ExtractionMethod.BINNING,
        smile_coeffs=truth.smile_coeffs,
        dark_frame=np.zeros_like(frame),
        qe_curve=np.full(truth.width, 1.0),
    )

    fps = measure_fps(lambda: extract_spectrum(frame, settings))
    assert fps >= REQUIRED_FPS, (
        f"Full correction stack managed {fps:.1f} fps on a "
        f"{SENSOR_WIDTH}x{SENSOR_HEIGHT} frame, below the required {REQUIRED_FPS} fps"
    )


def test_line_mode_is_not_slower_than_binning(sensor_frame):
    """
    Line mode reads one row; binning averages all of them. If line mode
    ever came out slower, something is doing whole-frame work it does not
    need to -- the check costs nothing and catches a whole class of mistake.
    """
    _, frame = sensor_frame
    line = measure_fps(
        lambda: extract_spectrum(frame, ExtractionSettings(ExtractionMethod.LINE, row=0))
    )
    binning = measure_fps(
        lambda: extract_spectrum(frame, ExtractionSettings(ExtractionMethod.BINNING))
    )
    assert line >= binning * 0.5


def test_end_to_end_display_path_sustains_the_required_frame_rate(sensor_frame):
    """
    The measurement that actually matters: camera frame in, corrections
    applied, spectrum extracted, and both views told to render. Extraction
    being fast is not much use if painting is the bottleneck.
    """
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    from PySide6.QtWidgets import QApplication

    from gui.image_view import LiveImageView
    from gui.spectrum_view import SpectrumView

    application = QApplication.instance() or QApplication([])
    truth, frame = sensor_frame

    image_view = LiveImageView()
    spectrum_view = SpectrumView()
    image_view.set_bit_depth(truth.bit_depth)
    spectrum_view.set_bit_depth(truth.bit_depth)

    settings = ExtractionSettings(
        method=ExtractionMethod.BINNING, smile_coeffs=truth.smile_coeffs
    )

    def one_frame():
        image_view.show_frame(frame)
        spectrum_view.show_spectrum(extract_spectrum(frame, settings))
        application.processEvents()

    try:
        fps = measure_fps(one_frame)
    finally:
        image_view.deleteLater()
        spectrum_view.deleteLater()

    assert fps >= REQUIRED_FPS, (
        f"End-to-end display path managed {fps:.1f} fps, "
        f"below the required {REQUIRED_FPS} fps"
    )
