"""
Extraction tests, asserted against synthetic-spectrograph ground truth.

The point of testing against the generator rather than against hand-built
arrays is that these assertions are about *optics*, not about array
slicing: "line mode at row r recovers the lines where truth says they are
for row r" is a claim that only means something if the frame has real smile
in it. A frame with one bright row would pass a row-extraction test while
telling us nothing about whether the feature works.
"""

import numpy as np
import pytest

from core.spectrum_extraction import (
    ExtractionMethod,
    ExtractionSettings,
    apply_qe_correction,
    bin_rows,
    correct_smile,
    extract_row,
    extract_spectrum,
    subtract_dark,
)
from core.synthetic_spectrograph import (
    EmissionLine,
    NoiseModel,
    render,
    render_clean,
    truth_for_range,
)

SMILE_BOW_PX = 8.0


def centroid(trace: np.ndarray, column: float, half_window: int = 6) -> float:
    low = max(int(round(column)) - half_window, 0)
    high = min(int(round(column)) + half_window + 1, trace.size)
    window = np.asarray(trace[low:high], dtype=float)
    window = window - window.min()
    indices = np.arange(low, high, dtype=float)
    return float((window * indices).sum() / window.sum())


def peak_width(trace: np.ndarray, column: float, half_window: int = 20) -> float:
    """RMS width of the peak near `column` -- sensitive to sub-pixel smear."""
    low = max(int(round(column)) - half_window, 0)
    high = min(int(round(column)) + half_window + 1, trace.size)
    window = np.asarray(trace[low:high], dtype=float)
    window = window - window.min()
    indices = np.arange(low, high, dtype=float)
    weight = window.sum()
    centre = (window * indices).sum() / weight
    return float(np.sqrt((window * (indices - centre) ** 2).sum() / weight))


@pytest.fixture
def bowed():
    """A spectrograph with real smile and well-separated lines."""
    return truth_for_range(
        (500.0, 1000.0),
        width=1024,
        height=256,
        lines=(EmissionLine(560.0), EmissionLine(700.0), EmissionLine(900.0)),
        smile_coeffs=(0.0, 0.0, SMILE_BOW_PX),
        line_fwhm_px=3.0,
    )


# ----------------------------------------------------------------------
# Line cross-section
# ----------------------------------------------------------------------


def test_line_mode_recovers_the_lines_for_the_row_it_reads(bowed):
    frame = render_clean(bowed)
    # Rows far apart, so smile puts the lines at visibly different columns.
    for row in (0, 64, int(bowed.reference_row), 255):
        trace = extract_spectrum(
            frame, ExtractionSettings(method=ExtractionMethod.LINE, row=row)
        )
        for expected in bowed.line_columns(row=row):
            assert centroid(trace, expected) == pytest.approx(expected, abs=0.15)


def test_line_mode_at_different_rows_gives_different_answers(bowed):
    """If it did not, the slide-adjust line would be decorative."""
    frame = render_clean(bowed)

    # Both rows must lie inside the illuminated slit: outside it the signal
    # is the super-Gaussian tail, and a centroid of near-zero intensity
    # measures rounding, not a line position.
    upper_row, centre_row = 40, int(bowed.reference_row)
    upper = extract_spectrum(
        frame, ExtractionSettings(ExtractionMethod.LINE, row=upper_row)
    )
    middle = extract_spectrum(
        frame, ExtractionSettings(ExtractionMethod.LINE, row=centre_row)
    )

    measured = centroid(upper, bowed.line_columns(row=upper_row)[0]) - centroid(
        middle, bowed.line_columns(row=centre_row)[0]
    )
    predicted = bowed.smile_shift_px(upper_row) - bowed.smile_shift_px(centre_row)
    assert measured == pytest.approx(predicted, abs=0.2)
    assert abs(predicted) > 1.0  # the two rows really are meaningfully apart


def test_extract_row_defaults_to_the_centre_row():
    frame = np.zeros((101, 10), dtype=np.uint16)
    frame[50, :] = 100
    assert extract_row(frame).max() == 100


def test_extract_row_rejects_a_row_outside_the_frame():
    with pytest.raises(ValueError, match="outside"):
        extract_row(np.zeros((10, 10)), row=10)


# ----------------------------------------------------------------------
# Binning and smile correction
# ----------------------------------------------------------------------


def test_binning_smears_lines_when_smile_is_uncorrected(bowed):
    """
    The claim the calibration phase exists to fix. Binning a bowed frame
    averages each line over every column it visited.
    """
    frame = render_clean(bowed)
    column = bowed.line_columns(row=bowed.reference_row)[0]

    binned = extract_spectrum(frame, ExtractionSettings(ExtractionMethod.BINNING))
    single = extract_spectrum(
        frame,
        ExtractionSettings(ExtractionMethod.LINE, row=int(bowed.reference_row)),
    )
    assert peak_width(binned, column) > peak_width(single, column) * 1.2


def test_smile_correction_restores_the_line_width(bowed):
    frame = render_clean(bowed)
    column = bowed.line_columns(row=bowed.reference_row)[0]

    uncorrected = extract_spectrum(frame, ExtractionSettings(ExtractionMethod.BINNING))
    corrected = extract_spectrum(
        frame,
        ExtractionSettings(
            ExtractionMethod.BINNING, smile_coeffs=bowed.smile_coeffs
        ),
    )
    single = extract_spectrum(
        frame, ExtractionSettings(ExtractionMethod.LINE, row=int(bowed.reference_row))
    )

    assert peak_width(corrected, column) < peak_width(uncorrected, column)
    # Corrected binning should be close to the sharpness of one clean row.
    assert peak_width(corrected, column) == pytest.approx(
        peak_width(single, column), rel=0.25
    )


def test_smile_correction_keeps_lines_at_the_reference_row_columns(bowed):
    """Correction must straighten the lines without translating them."""
    frame = render_clean(bowed)
    corrected = extract_spectrum(
        frame,
        ExtractionSettings(ExtractionMethod.BINNING, smile_coeffs=bowed.smile_coeffs),
    )
    for expected in bowed.line_columns(row=bowed.reference_row):
        assert centroid(corrected, expected) == pytest.approx(expected, abs=0.3)


def test_smile_correction_is_a_noop_without_coefficients(bowed):
    frame = render_clean(bowed)
    plain = extract_spectrum(frame, ExtractionSettings(ExtractionMethod.BINNING))
    zeroed = extract_spectrum(
        frame, ExtractionSettings(ExtractionMethod.BINNING, smile_coeffs=(0.0, 0.0, 0.0))
    )
    assert np.allclose(plain, zeroed)
    # All-zero coefficients must also report themselves as "not enabled",
    # so the GUI cannot label an uncorrected spectrum as corrected.
    assert not ExtractionSettings(smile_coeffs=(0.0, 0.0, 0.0)).smile_correction_enabled
    assert ExtractionSettings(smile_coeffs=(0.0, 0.0, 1.0)).smile_correction_enabled


def test_bin_rows_honours_an_explicit_row_range():
    frame = np.zeros((10, 4), dtype=np.uint16)
    frame[2:5] = 100
    assert np.allclose(bin_rows(frame, row_range=(2, 4)), 100.0)
    assert np.allclose(bin_rows(frame, row_range=(5, 9)), 0.0)


def test_bin_rows_rejects_an_invalid_range():
    with pytest.raises(ValueError, match="invalid"):
        bin_rows(np.zeros((10, 4)), row_range=(5, 20))


# ----------------------------------------------------------------------
# Dark subtraction and QE
# ----------------------------------------------------------------------


def test_dark_subtraction_of_a_known_offset_returns_zero():
    frame = np.full((8, 8), 120, dtype=np.uint16)
    assert np.allclose(subtract_dark(frame, frame), 0.0)


def test_dark_subtraction_never_wraps_below_zero():
    # The bug this guards: on uint16, 10 - 50 wraps to 65496, turning the
    # darkest pixels into the brightest ones.
    frame = np.full((4, 4), 10, dtype=np.uint16)
    dark = np.full((4, 4), 50, dtype=np.uint16)
    result = subtract_dark(frame, dark)
    assert result.min() >= 0.0
    assert np.allclose(result, 0.0)


def test_dark_subtraction_removes_the_pedestal_from_a_real_frame():
    truth = truth_for_range(
        (500.0, 1000.0),
        width=256,
        height=32,
        lines=(EmissionLine(700.0),),
        noise=NoiseModel(dark_offset=0.05),
    )
    dark = np.full(
        (truth.height, truth.width),
        round(0.05 * truth.max_value),
        dtype=np.uint16,
    )
    frame = render(truth, rng=np.random.default_rng(0))

    before = extract_spectrum(frame, ExtractionSettings(ExtractionMethod.BINNING))
    after = extract_spectrum(
        frame, ExtractionSettings(ExtractionMethod.BINNING, dark_frame=dark)
    )

    # Dark subtraction removes the *dark pedestal* and nothing else. The
    # truth also has a continuum under the lines, which is real signal from
    # the lamp -- expecting the floor to reach zero would be demanding that
    # dark subtraction delete a genuine measurement.
    assert before.min() - after.min() == pytest.approx(
        0.05 * truth.max_value, abs=2.0
    )


def test_dark_frame_shape_mismatch_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        subtract_dark(np.zeros((4, 4)), np.zeros((5, 5)))


def test_qe_correction_divides_out_the_response():
    spectrum = np.array([10.0, 20.0, 30.0])
    curve = np.array([1.0, 2.0, 3.0])
    assert np.allclose(apply_qe_correction(spectrum, curve), [10.0, 10.0, 10.0])


def test_qe_correction_treats_zero_response_as_zero_not_infinity():
    spectrum = np.array([10.0, 20.0])
    curve = np.array([1.0, 0.0])
    result = apply_qe_correction(spectrum, curve)
    assert np.isfinite(result).all()
    assert result[1] == 0.0


def test_qe_curve_length_mismatch_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        apply_qe_correction(np.zeros(10), np.zeros(11))


# ----------------------------------------------------------------------
# Pipeline composition
# ----------------------------------------------------------------------


def test_default_settings_reproduce_the_old_row_binned_mean(bowed):
    """Callers that predate ExtractionSettings must keep working unchanged."""
    frame = render_clean(bowed)
    assert np.allclose(extract_spectrum(frame), frame.mean(axis=0))


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError, match="Unknown extraction method"):
        extract_spectrum(np.zeros((4, 4)), ExtractionSettings(method="nonsense"))


def test_corrections_compose_in_the_right_order(bowed):
    """
    Dark before smile before binning before QE. Applying dark subtraction
    after binning, for instance, would subtract a 2D frame from a 1D trace;
    getting a sane answer out of the full stack is the check that the order
    is wired as documented.
    """
    frame = render(bowed, rng=np.random.default_rng(3))
    dark = np.zeros_like(frame)
    qe = np.full(bowed.width, 2.0)

    spectrum = extract_spectrum(
        frame,
        ExtractionSettings(
            method=ExtractionMethod.BINNING,
            smile_coeffs=bowed.smile_coeffs,
            dark_frame=dark,
            qe_curve=qe,
        ),
    )
    reference = extract_spectrum(
        frame,
        ExtractionSettings(
            method=ExtractionMethod.BINNING, smile_coeffs=bowed.smile_coeffs
        ),
    )
    assert spectrum.shape == (bowed.width,)
    assert np.allclose(spectrum, reference / 2.0)
