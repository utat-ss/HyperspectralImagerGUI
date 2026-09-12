"""
Synthetic spectrograph frames with known ground truth.

This is the test rig the spectrometer half of the project rests on. Nobody
on this team has hardware, so every claim about line extraction, smile
correction and wavelength calibration has to be checked against something
whose correct answer is known in advance. That is what this module is: it
builds a frame *from* a pixel-to-wavelength mapping, and hands back the
mapping it used, so a test can run the recovery algorithms and compare
against truth rather than against a hand-computed constant.

Nothing here imports Qt or touches a camera. It is plain numpy so that the
extraction, calibration and correction tests can all use it.

The model
--------
A dispersive spectrograph images a slit through a grating onto a 2D sensor.
Column position encodes wavelength, row position encodes position along the
slit. Two optical aberrations matter for calibration and are modelled here:

**Smile** -- the image of a straight slit is bowed, so a single wavelength
lands at a slightly different *column* depending on which *row* you look
at. Modelled as a column shift that is a polynomial in the normalised row
coordinate; the quadratic term is the one that looks like a real smile.
This is why "bin all rows together" is not free: without correcting smile
first, binning smears each line across the columns it occupied in different
rows and broadens it.

**Keystone** -- magnification along the slit varies with wavelength, so a
given point on the slit lands at a slightly different *row* depending on
which *column* you look at. Modelled as a row shift that is a polynomial in
the normalised column coordinate.

Coordinates
-----------
Wavelength is a polynomial in raw column index at the reference row:

    lambda(x) = c0 + c1*x + c2*x**2 + ...

Smile and keystone use *normalised* coordinates in [-1, 1] (0 at the
sensor centre) so their coefficients stay readable and independent of
sensor size: a smile quadratic coefficient of 2.0 means "2 pixels of bow
from centre to edge", whatever the sensor is.

Usage::

    truth = SpectrographTruth(width=1024, height=256,
                              wavelength_coeffs=(500.0, 0.488),
                              lines=HYDROGEN_BALMER)
    frame = render(truth)                  # uint16, ready to feed anything
    truth.line_columns(row=128)            # where those lines really are
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence, Tuple

import numpy as np

# Gaussian FWHM = 2*sqrt(2*ln2) * sigma.
_FWHM_TO_SIGMA = 1.0 / (2.0 * math.sqrt(2.0 * math.log(2.0)))


@dataclass(frozen=True)
class EmissionLine:
    """One emission line of a calibration source."""

    wavelength_nm: float
    relative_intensity: float = 1.0


# The doc's own worked example is the hydrogen Balmer series. Relative
# intensities are rough visual weights, not photometric values -- good
# enough to make peak-finding non-trivial, which is the point.
HYDROGEN_BALMER: Tuple[EmissionLine, ...] = (
    EmissionLine(410.2, 0.35),
    EmissionLine(434.0, 0.55),
    EmissionLine(486.1, 0.80),
    EmissionLine(656.3, 1.00),
)

# A denser source, for exercising peak matching where lines can blend.
MERCURY_ARGON: Tuple[EmissionLine, ...] = (
    EmissionLine(546.1, 1.00),
    EmissionLine(576.9, 0.45),
    EmissionLine(579.1, 0.42),  # close pair -- resolvable or not depending on FWHM
    EmissionLine(696.5, 0.30),
    EmissionLine(750.4, 0.50),
    EmissionLine(763.5, 0.60),
    EmissionLine(811.5, 0.70),
    EmissionLine(912.3, 0.25),
)


@dataclass(frozen=True)
class NoiseModel:
    """
    Per-frame noise, all expressed as a fraction of full scale so the model
    is independent of bit depth.

    Defaults are noiseless: a test asserting exact line positions should
    opt in to noise deliberately, not fight it by accident.
    """

    dark_offset: float = 0.0
    read_noise_sigma: float = 0.0
    shot_noise: bool = False


@dataclass(frozen=True)
class SpectrographTruth:
    """
    Everything needed to render a frame -- and therefore everything a test
    needs to know what the right answer is.

    Frozen so a truth object cannot drift out of sync with a frame that was
    rendered from it. Use `dataclasses.replace` to derive a variant.
    """

    width: int = 1024
    height: int = 256
    bit_depth: int = 12

    # lambda(x) at the reference row, ascending powers of raw column index.
    wavelength_coeffs: Tuple[float, ...] = (500.0, 0.488)

    lines: Tuple[EmissionLine, ...] = HYDROGEN_BALMER

    # Column shift vs normalised row, ascending powers. The quadratic term
    # is the characteristic smile bow.
    smile_coeffs: Tuple[float, ...] = (0.0,)

    # Row shift vs normalised column, ascending powers.
    keystone_coeffs: Tuple[float, ...] = (0.0,)

    line_fwhm_px: float = 3.0

    # Fraction of sensor height illuminated by the slit, centred.
    slit_height_frac: float = 0.75

    # Flat background *signal* (not noise) as a fraction of full scale --
    # a real lamp has some continuum under the lines.
    continuum: float = 0.02

    # Peak signal of a relative_intensity==1.0 line, as a fraction of full
    # scale. Below 1.0 so the brightest line does not clip by default.
    peak_level: float = 0.85

    noise: NoiseModel = field(default_factory=NoiseModel)

    # ------------------------------------------------------------------
    # Ground truth -- what tests assert against
    # ------------------------------------------------------------------

    @property
    def max_value(self) -> int:
        return (1 << self.bit_depth) - 1

    @property
    def reference_row(self) -> float:
        """Row where smile is zero by definition: the sensor's centre row."""
        return (self.height - 1) / 2.0

    def _normalised_row(self, row: float) -> float:
        half = (self.height - 1) / 2.0
        return 0.0 if half == 0 else (row - half) / half

    def _normalised_column(self, column: np.ndarray) -> np.ndarray:
        half = (self.width - 1) / 2.0
        if half == 0:
            return np.zeros_like(column, dtype=float)
        return (np.asarray(column, dtype=float) - half) / half

    def smile_shift_px(self, row: float) -> float:
        """Column shift applied to every wavelength at this row."""
        return float(np.polyval(tuple(reversed(self.smile_coeffs)), self._normalised_row(row)))

    def keystone_shift_px(self, column) -> np.ndarray:
        """Row shift applied to the slit image at this column."""
        normalised = self._normalised_column(np.asarray(column, dtype=float))
        return np.polyval(tuple(reversed(self.keystone_coeffs)), normalised)

    def wavelength_at_column(self, column, row: Optional[float] = None):
        """
        Wavelength imaged at a column, on a given row.

        With smile present the same column is a different wavelength on
        different rows -- that is the whole point of the aberration, and the
        reason a naive row-binned spectrum is wrong.
        """
        column = np.asarray(column, dtype=float)
        shift = 0.0 if row is None else self.smile_shift_px(row)
        return np.polyval(tuple(reversed(self.wavelength_coeffs)), column - shift)

    @property
    def wavelength_span_nm(self) -> Tuple[float, float]:
        """(min, max) wavelength the sensor actually sees, at the reference row."""
        edges = np.polyval(
            tuple(reversed(self.wavelength_coeffs)),
            np.array([0.0, self.width - 1.0]),
        )
        return (float(edges.min()), float(edges.max()))

    def column_for_wavelength(self, wavelength_nm, row: Optional[float] = None):
        """
        Inverse of wavelength_at_column(). **NaN for any wavelength the
        sensor cannot see.**

        Solved by interpolating the sampled dispersion curve rather than
        algebraically: it works for any polynomial order without special
        cases, and the curve is monotonic over any sane spectrograph
        design. Raises if it is not, because silently returning a wrong
        column would make every test built on it meaningless.

        The NaN matters just as much. np.interp clamps out-of-range input
        to the end of the curve, which would quietly report a 410 nm line
        as sitting at column 0 of a 500-1000 nm sensor -- a wrong answer
        that looks like a real one, piles multiple lines onto one column,
        and poisons anything asserting against it.
        """
        columns = np.arange(self.width, dtype=float)
        wavelengths = np.polyval(tuple(reversed(self.wavelength_coeffs)), columns)

        differences = np.diff(wavelengths)
        if not (np.all(differences > 0) or np.all(differences < 0)):
            raise ValueError(
                "Dispersion curve is not monotonic across the sensor; "
                "column_for_wavelength() cannot be inverted unambiguously"
            )

        if wavelengths[0] > wavelengths[-1]:  # np.interp needs ascending x
            wavelengths = wavelengths[::-1]
            columns = columns[::-1]

        requested = np.asarray(wavelength_nm, dtype=float)
        base = np.interp(requested, wavelengths, columns)
        base = np.where(
            (requested < wavelengths[0]) | (requested > wavelengths[-1]),
            np.nan,
            base,
        )

        shift = 0.0 if row is None else self.smile_shift_px(row)
        result = base + shift
        return result if result.ndim else float(result)

    def visible_lines(self) -> Tuple[EmissionLine, ...]:
        """
        The subset of `lines` that actually lands on the sensor.

        A real calibration source has lines outside the instrument's range;
        they are not errors, they simply are not imaged. Tests should assert
        against these rather than against every line in the catalogue.
        """
        low, high = self.wavelength_span_nm
        return tuple(
            line for line in self.lines if low <= line.wavelength_nm <= high
        )

    def line_columns(self, row: Optional[float] = None) -> np.ndarray:
        """
        Where this truth's *visible* emission lines actually land, in column
        units, ordered as visible_lines() is.

        This is the single most useful assertion target in the module: run
        an extraction or calibration, then compare its recovered line
        positions against this.
        """
        visible = self.visible_lines()
        if not visible:
            return np.empty(0, dtype=float)
        return np.atleast_1d(
            self.column_for_wavelength(
                np.array([line.wavelength_nm for line in visible]), row=row
            )
        )

    def wavelength_axis(self, row: Optional[float] = None) -> np.ndarray:
        """The per-column wavelength vector a perfect calibration would recover."""
        return self.wavelength_at_column(np.arange(self.width, dtype=float), row=row)


def render_clean(truth: SpectrographTruth) -> np.ndarray:
    """
    Noise-free image as float in [0, 1] of full scale.

    Kept separate from noise so the static optical pattern can be built
    once and reused for every frame of a live stream -- rebuilding it per
    frame would dominate the frame budget at sensor sizes that matter.
    """
    rows = np.arange(truth.height, dtype=float).reshape(-1, 1)
    columns = np.arange(truth.width, dtype=float).reshape(1, -1)

    # Slit illumination along rows, displaced per column by keystone.
    keystone = truth.keystone_shift_px(columns.ravel()).reshape(1, -1)
    centre_row = (truth.height - 1) / 2.0
    half_slit = max(truth.slit_height_frac * truth.height / 2.0, 1e-6)
    # Super-Gaussian: flat-topped like a real slit image, with soft edges
    # rather than the hard cutoff a boxcar would give.
    slit = np.exp(-0.5 * (((rows - keystone - centre_row) / half_slit) ** 8))

    sigma = max(truth.line_fwhm_px * _FWHM_TO_SIGMA, 1e-6)

    # Smile shifts every line by the same amount within a given row, so
    # compute it once per row rather than once per (row, line).
    half_height = (truth.height - 1) / 2.0
    normalised_rows = (
        np.zeros_like(rows) if half_height == 0 else (rows - half_height) / half_height
    )
    smile = np.polyval(tuple(reversed(truth.smile_coeffs)), normalised_rows)

    spectrum = np.zeros((truth.height, truth.width), dtype=float)
    for line in truth.visible_lines():
        base_column = float(truth.column_for_wavelength(line.wavelength_nm))
        centres = base_column + smile  # (height, 1), broadcast over columns
        spectrum += line.relative_intensity * np.exp(
            -0.5 * (((columns - centres) / sigma) ** 2)
        )

    image = truth.continuum + truth.peak_level * spectrum
    return np.clip(image * slit, 0.0, None)


def apply_noise(
    clean: np.ndarray,
    truth: SpectrographTruth,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """
    Add the truth's noise model to a clean [0, 1] image and quantise to the
    sensor's bit depth.

    Shot noise is applied in electron-like units derived from full scale, so
    that it scales with signal the way a real sensor's does -- bright lines
    get noisier in absolute terms, the background stays clean.
    """
    rng = np.random.default_rng() if rng is None else rng
    signal = clean + truth.noise.dark_offset

    if truth.noise.shot_noise:
        full_well = float(truth.max_value)
        signal = rng.poisson(np.clip(signal, 0.0, None) * full_well) / full_well

    if truth.noise.read_noise_sigma > 0.0:
        signal = signal + rng.normal(0.0, truth.noise.read_noise_sigma, signal.shape)

    counts = np.clip(signal * truth.max_value, 0, truth.max_value)
    return counts.astype(np.uint16)


def render(
    truth: SpectrographTruth, rng: Optional[np.random.Generator] = None
) -> np.ndarray:
    """Clean render plus noise: a single ready-to-use uint16 frame."""
    return apply_noise(render_clean(truth), truth, rng=rng)


def truth_for_range(
    wavelength_range_nm: Tuple[float, float] = (500.0, 1000.0),
    width: int = 1024,
    **overrides,
) -> SpectrographTruth:
    """
    Build a truth whose linear dispersion spans an exact wavelength range
    across the sensor -- the common case when setting up a test or a demo.

    Saves hand-computing c1 every time, and guarantees the requested range
    is the range actually rendered.
    """
    low, high = wavelength_range_nm
    if width < 2:
        raise ValueError("width must be at least 2 columns")
    slope = (high - low) / (width - 1)
    return SpectrographTruth(
        width=width, wavelength_coeffs=(low, slope), **overrides
    )


# A realistic default for demos and for MockCamera's spectrograph mode:
# the doc's 500-1000 nm VNIR range, a visible smile bow, gentle keystone,
# and enough noise to look like a sensor without swamping the lines.
DEMO_TRUTH = truth_for_range(
    (500.0, 1000.0),
    width=1024,
    height=256,
    lines=HYDROGEN_BALMER + MERCURY_ARGON,
    smile_coeffs=(0.0, 0.0, 2.5),
    keystone_coeffs=(0.0, 1.2),
    line_fwhm_px=3.5,
    noise=NoiseModel(dark_offset=0.01, read_noise_sigma=0.004, shot_noise=True),
)
