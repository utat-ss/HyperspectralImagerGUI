"""
Tests for the synthetic spectrograph generator.

Everything downstream -- line extraction, smile correction, wavelength
calibration -- will assert against this module's ground truth. So this
module's own claims have to be checked first: if it reports a line at a
column the frame does not actually put it at, every test built on it is
measuring nothing.

The recurring shape here is: build a frame from a known mapping, measure
the frame, and confirm the measurement matches what the mapping promised.
"""

import numpy as np
import pytest

from core.synthetic_spectrograph import (
    DEMO_TRUTH,
    HYDROGEN_BALMER,
    MERCURY_ARGON,
    EmissionLine,
    NoiseModel,
    SpectrographTruth,
    apply_noise,
    render,
    render_clean,
    truth_for_range,
)


def centroid(row: np.ndarray, column: float, half_window: int = 5) -> float:
    """Intensity-weighted centre of the peak near `column`."""
    low = max(int(round(column)) - half_window, 0)
    high = min(int(round(column)) + half_window + 1, row.size)
    window = row[low:high].astype(float)
    indices = np.arange(low, high, dtype=float)
    return float((window * indices).sum() / window.sum())


@pytest.fixture
def truth():
    """Well-separated lines, so a centroid measures one line and not a blend."""
    return truth_for_range(
        (500.0, 1000.0),
        width=1024,
        height=256,
        lines=(
            EmissionLine(550.0),
            EmissionLine(650.0),
            EmissionLine(800.0),
            EmissionLine(950.0),
        ),
        line_fwhm_px=3.0,
    )


# ----------------------------------------------------------------------
# The mapping itself
# ----------------------------------------------------------------------


def test_dispersion_spans_the_requested_range(truth):
    assert truth.wavelength_at_column(0) == pytest.approx(500.0)
    assert truth.wavelength_at_column(truth.width - 1) == pytest.approx(1000.0)
    assert truth.wavelength_span_nm == pytest.approx((500.0, 1000.0))


def test_column_and_wavelength_round_trip(truth):
    for wavelength in (500.0, 612.5, 823.1, 1000.0):
        column = truth.column_for_wavelength(wavelength)
        assert truth.wavelength_at_column(column) == pytest.approx(wavelength, abs=1e-6)


def test_wavelengths_off_the_sensor_are_nan_not_clamped(truth):
    # np.interp would clamp these to column 0 / width-1, which is a wrong
    # answer that looks like a real one. Guarding this is the whole reason
    # visible_lines() exists.
    assert np.isnan(truth.column_for_wavelength(400.0))
    assert np.isnan(truth.column_for_wavelength(1200.0))


def test_visible_lines_drops_lines_off_the_sensor():
    # Only the 656.3 nm Balmer line falls inside a 500-1000 nm sensor.
    truth = truth_for_range((500.0, 1000.0), lines=HYDROGEN_BALMER)
    visible = truth.visible_lines()
    assert len(visible) == 1
    assert visible[0].wavelength_nm == pytest.approx(656.3)
    assert truth.line_columns().shape == (1,)


def test_non_monotonic_dispersion_is_rejected():
    # A curve that turns around inside the sensor has two columns for one
    # wavelength; inverting it would silently pick one.
    truth = SpectrographTruth(width=1024, wavelength_coeffs=(500.0, 1.0, -0.001))
    with pytest.raises(ValueError, match="monotonic"):
        truth.column_for_wavelength(600.0)


# ----------------------------------------------------------------------
# The rendered frame agrees with the mapping
# ----------------------------------------------------------------------


def test_lines_land_where_the_truth_says_they_do(truth):
    frame = render_clean(truth)
    row = truth.reference_row
    for expected in truth.line_columns(row=row):
        assert centroid(frame[int(row)], expected) == pytest.approx(expected, abs=0.1)


def test_smile_shifts_lines_by_the_stated_amount():
    bow = 2.5
    truth = truth_for_range(
        (500.0, 1000.0),
        width=1024,
        height=256,
        lines=(EmissionLine(750.0),),
        smile_coeffs=(0.0, 0.0, bow),  # pure quadratic: zero at centre, +bow at edges
        line_fwhm_px=3.0,
    )
    frame = render_clean(truth)

    # Smile is zero at the true centre row, which on an even-height sensor
    # falls between two pixel rows (127.5 of 256) -- so assert that on the
    # float centre, and measure the frame on a real integer row.
    assert truth.smile_shift_px(truth.reference_row) == pytest.approx(0.0, abs=1e-9)
    assert truth.smile_shift_px(0) == pytest.approx(bow)

    centre_row = int(truth.reference_row)
    centre = centroid(frame[centre_row], truth.line_columns(row=centre_row)[0])
    top = centroid(frame[0], truth.line_columns(row=0)[0])

    # The frame must actually show the bow, not merely report it.
    predicted = truth.smile_shift_px(0) - truth.smile_shift_px(centre_row)
    assert top - centre == pytest.approx(predicted, abs=0.15)
    assert predicted == pytest.approx(bow, abs=0.01)


def test_smile_is_what_makes_naive_row_binning_worse():
    """
    The motivating claim for the whole calibration phase: collapsing rows
    without correcting smile smears each line across the columns it
    occupied in different rows. Without smile, binning costs nothing.
    """
    lines = (EmissionLine(750.0),)
    common = dict(width=1024, height=256, lines=lines, line_fwhm_px=3.0)

    flat = truth_for_range((500.0, 1000.0), **common)
    bowed = truth_for_range((500.0, 1000.0), smile_coeffs=(0.0, 0.0, 4.0), **common)

    def peak_width(truth):
        # RMS width about the peak's centroid, not a count of pixels above
        # half maximum: the pixel count is an integer and quantises away
        # exactly the sub-pixel broadening this test is trying to see.
        binned = render_clean(truth).mean(axis=0)
        column = int(np.argmax(binned))
        low, high = max(column - 20, 0), min(column + 21, binned.size)
        window = binned[low:high] - binned.min()
        indices = np.arange(low, high, dtype=float)
        weight = window.sum()
        centre = (window * indices).sum() / weight
        return float(np.sqrt((window * (indices - centre) ** 2).sum() / weight))

    assert peak_width(bowed) > peak_width(flat) * 1.05


def test_keystone_shifts_the_slit_image_vertically():
    shift = 6.0
    truth = truth_for_range(
        (500.0, 1000.0),
        width=1024,
        height=256,
        lines=(EmissionLine(750.0),),
        keystone_coeffs=(0.0, shift),  # linear across columns: -shift .. +shift
        slit_height_frac=0.5,
    )
    frame = render_clean(truth)

    def slit_centre(column):
        profile = frame[:, column].astype(float)
        rows = np.arange(profile.size, dtype=float)
        return float((profile * rows).sum() / profile.sum())

    left, right = slit_centre(0), slit_centre(truth.width - 1)
    assert right - left == pytest.approx(2 * shift, abs=1.0)


def test_line_intensities_follow_their_relative_weights():
    truth = truth_for_range(
        (500.0, 1000.0),
        width=1024,
        height=256,
        lines=(EmissionLine(600.0, 1.0), EmissionLine(800.0, 0.5)),
        line_fwhm_px=3.0,
    )
    frame = render_clean(truth)
    row = frame[int(truth.reference_row)]
    columns = truth.line_columns(row=truth.reference_row)

    bright = row[int(round(columns[0]))] - truth.continuum
    dim = row[int(round(columns[1]))] - truth.continuum
    assert dim / bright == pytest.approx(0.5, rel=0.05)


# ----------------------------------------------------------------------
# Bit depth and noise
# ----------------------------------------------------------------------


def test_clean_render_never_exceeds_full_scale():
    # Overlapping lines summing past full scale would clip invisibly and
    # move the measured centroid of every line involved.
    frame = render_clean(DEMO_TRUTH)
    assert frame.max() <= 1.0


@pytest.mark.parametrize("bit_depth", [8, 10, 12, 16])
def test_rendered_frame_respects_the_declared_bit_depth(bit_depth):
    truth = truth_for_range((500.0, 1000.0), width=512, height=64, bit_depth=bit_depth)
    frame = render(truth, rng=np.random.default_rng(0))
    assert frame.dtype == np.uint16
    assert frame.max() <= (1 << bit_depth) - 1


def test_noise_is_reproducible_from_a_seed(truth):
    clean = render_clean(truth)
    noisy = truth.__class__(
        **{**truth.__dict__, "noise": NoiseModel(read_noise_sigma=0.01)}
    )
    first = apply_noise(clean, noisy, rng=np.random.default_rng(1234))
    second = apply_noise(clean, noisy, rng=np.random.default_rng(1234))
    assert np.array_equal(first, second)


def test_noise_perturbs_the_frame_but_not_the_line_positions(truth):
    noisy_truth = truth.__class__(
        **{
            **truth.__dict__,
            "noise": NoiseModel(dark_offset=0.01, read_noise_sigma=0.003, shot_noise=True),
        }
    )
    clean = render(truth, rng=np.random.default_rng(0))
    noisy = render(noisy_truth, rng=np.random.default_rng(0))

    assert not np.array_equal(clean, noisy)

    row = int(noisy_truth.reference_row)
    for expected in noisy_truth.line_columns(row=row):
        # Noise widens the tolerance but must not move the line.
        assert centroid(noisy[row], expected) == pytest.approx(expected, abs=0.5)


# ----------------------------------------------------------------------
# MockCamera integration
# ----------------------------------------------------------------------


def test_mock_camera_spectrograph_mode_serves_the_truth_geometry():
    from core.mock_camera import MockCamera

    camera = MockCamera(spectrograph=DEMO_TRUTH)
    camera.connect()
    try:
        frame = camera.get_frame()
        assert frame.shape == (DEMO_TRUTH.height, DEMO_TRUTH.width)
        assert camera.get_bit_depth() == DEMO_TRUTH.bit_depth
        assert frame.max() <= DEMO_TRUTH.max_value
    finally:
        camera.disconnect()


def test_mock_camera_spectrograph_lines_stay_put_across_frames():
    """
    The live view must not make lines wander: the sweeping marker of the
    default pattern would, which is why spectrograph mode drops it.
    """
    from core.mock_camera import MockCamera

    truth = truth_for_range(
        (500.0, 1000.0),
        width=1024,
        height=128,
        lines=(EmissionLine(700.0),),
        line_fwhm_px=3.0,
    )
    camera = MockCamera(spectrograph=truth)
    camera.connect()
    try:
        expected = truth.line_columns(row=truth.reference_row)[0]
        row = int(truth.reference_row)
        for _ in range(5):
            frame = camera.get_frame()
            assert centroid(frame[row], expected) == pytest.approx(expected, abs=0.2)
    finally:
        camera.disconnect()


def test_mock_camera_default_mode_is_unchanged():
    # Spectrograph mode is opt-in; the structured pattern stays the default
    # so existing contract tests and the GUI keep the display-bug-catching
    # image they were written against.
    from core.mock_camera import MockCamera

    camera = MockCamera()
    camera.connect()
    try:
        assert camera.get_frame().shape == (480, 640)
        assert camera.get_bit_depth() == 12
    finally:
        camera.disconnect()
