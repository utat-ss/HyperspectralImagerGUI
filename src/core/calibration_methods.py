"""
Producers of wavelength calibrations: the algorithms behind the spec
document's "Method 1" and "Method 2", plus smile characterisation and
on-disk persistence.

`core.calibration` defines what a calibration *is* and everything in the GUI
consumes that; this module is what *creates* one. Nothing here imports Qt.

The physics
-----------
Light from a calibration source is dispersed across the sensor, so column
position encodes wavelength. Calibration means recovering that mapping,
lambda(x), from a frame whose true mapping is unknown. Both methods work by
finding features whose wavelength is known independently and fitting a
polynomial through the (column, wavelength) pairs:

**Method 1 -- Bandpass filter.** Put a filter of known cut-on and cut-off
wavelength in the beam. The transmitted band's two edges are then two
features of known wavelength. Two filters give four anchors, enough for a
quadratic. Robust, needs no reference spectrum, but only ever yields a
handful of anchors and says nothing between them.

**Method 2 -- Spectral peaks.** Match emission lines in our frame against
the same lines measured on a commercial spectrometer. Many more anchors and
they are sharp, so the fit is better constrained -- but it needs a reference
file and a source with enough resolvable lines.

Neither method is strictly better, which is why the spec asks for both: they
fail in different ways, and agreeing with each other is real evidence that
the mapping is right.

**Smile** is characterised separately (`estimate_smile`), because it is a
different measurement: not where a wavelength lands, but how that position
drifts from row to row.

Accuracy note
-------------
These are validated against `core.synthetic_spectrograph` ground truth,
which proves the *mathematics* recovers a mapping that was put in. It does
not prove the assumed file format matches a real export, nor that the real
instrument's distortion looks like the model. Both need confirming against a
real instrument.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

import numpy as np

from core.calibration import PolynomialCalibration
from core.peaks import (
    find_peak_indices,
    find_peaks,
    refine_peak_position,
    relative_prominence_threshold,
)
from core.reference_spectrum import ReferenceSpectrum

PathLike = Union[str, Path]

CALIBRATION_FILE_VERSION = 1

METHOD_1_NAME = "Method 1: Bandpass Filter"
METHOD_2_NAME = "Method 2: Spectral Peaks"


class CalibrationError(RuntimeError):
    """The data given cannot support a calibration, with the reason why."""


@dataclass(frozen=True)
class CalibrationResult:
    """
    A fitted calibration plus the evidence behind it.

    The anchors and residuals are carried, not discarded, because a
    calibration a user cannot inspect is a calibration they have to take on
    faith -- and the spec's Spec. Calibration tab displays exactly these.
    """

    calibration: PolynomialCalibration
    columns: np.ndarray
    wavelengths_nm: np.ndarray
    residuals_nm: np.ndarray

    @property
    def rms_residual_nm(self) -> float:
        if self.residuals_nm.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(self.residuals_nm**2)))

    @property
    def max_residual_nm(self) -> float:
        if self.residuals_nm.size == 0:
            return 0.0
        return float(np.max(np.abs(self.residuals_nm)))

    def summary(self) -> str:
        return (
            f"{len(self.columns)} anchors, "
            f"RMS residual {self.rms_residual_nm:.3f} nm, "
            f"max {self.max_residual_nm:.3f} nm"
        )


# ----------------------------------------------------------------------
# Method 2: spectral peaks
# ----------------------------------------------------------------------


def calibrate_from_peaks(
    spectrum: np.ndarray,
    reference: ReferenceSpectrum,
    nominal_range_nm: Tuple[float, float],
    degree: int = 2,
    min_prominence: Optional[float] = None,
    smile: Optional[Sequence[float]] = None,
    name: str = METHOD_2_NAME,
    source: Optional[str] = None,
) -> CalibrationResult:
    """
    Fit lambda(x) by matching our emission lines to the reference's.

    `nominal_range_nm` is the instrument's approximate wavelength span, from
    the optical design. It is needed, not optional: the reference lamp has
    lines outside what we image, and pairing a measured peak with a line
    that never reached the sensor would corrupt the fit while still
    producing a plausible-looking polynomial.

    Matching is by order. Once the reference list is restricted to lines the
    sensor can see and the same number of strongest measured peaks is kept,
    both sequences describe the same lines in the same spatial order, so the
    nth measured peak is the nth reference line. This assumes dispersion is
    monotonic, which it is for any grating spectrograph; it does *not*
    assume the dispersion is linear or even known.
    """
    spectrum = np.asarray(spectrum, dtype=float)
    if spectrum.ndim != 1:
        raise CalibrationError(f"expected a 1D spectrum, got shape {spectrum.shape}")

    if min_prominence is None:
        min_prominence = relative_prominence_threshold(spectrum)

    reference_wavelengths = reference.peak_wavelengths(within_nm=nominal_range_nm)
    if reference_wavelengths.size == 0:
        raise CalibrationError(
            f"the reference spectrum has no peaks between "
            f"{min(nominal_range_nm):.1f} and {max(nominal_range_nm):.1f} nm, "
            "so there is nothing to match our lines against"
        )

    measured_columns = find_peaks(
        spectrum,
        min_prominence=min_prominence,
        min_distance=3,
        max_peaks=int(reference_wavelengths.size),
    )

    needed = degree + 1
    count = min(measured_columns.size, reference_wavelengths.size)
    if count < needed:
        raise CalibrationError(
            f"a degree-{degree} fit needs {needed} matched lines, but only "
            f"{count} could be matched ({measured_columns.size} found in the "
            f"frame, {reference_wavelengths.size} in the reference). Lower the "
            "degree, use a source with more lines, or loosen the detection "
            "threshold."
        )

    columns, wavelengths = _pair_by_order(measured_columns, reference_wavelengths, count)

    if source is None:
        source = f"{reference.source}, {count} matched lines"
    return _fit(columns, wavelengths, degree, name, source, smile)


# ----------------------------------------------------------------------
# Method 1: bandpass filter
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BandpassObservation:
    """One frame's trace taken with a filter of known passband in the beam."""

    spectrum: np.ndarray
    cut_on_nm: float
    cut_off_nm: float

    def __post_init__(self) -> None:
        if self.cut_on_nm >= self.cut_off_nm:
            raise ValueError(
                f"cut-on ({self.cut_on_nm}) must be below cut-off ({self.cut_off_nm})"
            )


def calibrate_from_bandpass(
    observations: Sequence[BandpassObservation],
    degree: Optional[int] = None,
    edge_fraction: float = 0.5,
    ascending: Optional[bool] = None,
    smile: Optional[Sequence[float]] = None,
    name: str = METHOD_1_NAME,
    source: Optional[str] = None,
) -> CalibrationResult:
    """
    Fit lambda(x) from the transmission edges of known bandpass filters.

    Each filter contributes two anchors: the column where transmission
    crosses `edge_fraction` of the band's peak on the way up, and where it
    crosses back down. The half-maximum crossing is used because it is the
    point least sensitive to how bright the source is -- doubling the lamp
    output moves a fixed-threshold crossing but not a half-maximum one.

    The assignment of the two edges to cut-on and cut-off depends on which
    way wavelength runs across the sensor. With two or more filters the data
    decides: both assignments are fitted and the wrong one fits dramatically
    worse, so the smaller residual wins.

    **One filter cannot decide it.** Two anchors define a straight line
    exactly in either direction, so both assignments fit perfectly and the
    "better residual" is a coin toss that would silently return a mirrored
    calibration. In that case `ascending` must be stated explicitly -- True
    if wavelength increases with column index. Refusing to guess is the
    point: a reversed wavelength axis looks entirely plausible on screen.
    """
    if len(observations) == 0:
        raise CalibrationError("no bandpass observations given")

    ascending_anchors = _bandpass_anchors(observations, edge_fraction, ascending=True)
    descending_anchors = _bandpass_anchors(observations, edge_fraction, ascending=False)

    anchor_count = len(ascending_anchors[0])
    if degree is None:
        # One filter gives two anchors: only a straight line is supportable.
        degree = 1 if anchor_count < 3 else 2
    needed = degree + 1
    if anchor_count < needed:
        raise CalibrationError(
            f"a degree-{degree} fit needs {needed} anchors, but "
            f"{len(observations)} filter(s) give only {anchor_count}. "
            "Use more filters or a lower degree."
        )

    if ascending is None and anchor_count <= degree + 1:
        # Exactly-determined fit: every assignment has zero residual, so
        # there is nothing to choose between them.
        raise CalibrationError(
            f"{len(observations)} filter(s) give {anchor_count} anchors, which a "
            f"degree-{degree} fit matches exactly in either direction -- the "
            "residual cannot tell which way wavelength runs across the sensor. "
            "Pass ascending=True (or False), or use another filter."
        )

    if ascending is True:
        options = [ascending_anchors]
    elif ascending is False:
        options = [descending_anchors]
    else:
        options = [ascending_anchors, descending_anchors]

    candidates = []
    for columns, wavelengths in options:
        try:
            candidates.append(
                _fit(columns, wavelengths, degree, name, source or "", smile)
            )
        except CalibrationError:
            continue
    if not candidates:
        raise CalibrationError("could not fit a calibration to the bandpass edges")

    best = min(candidates, key=lambda result: result.rms_residual_nm)
    if source is None:
        source = (
            f"{len(observations)} bandpass filter(s), {anchor_count} edge anchors"
        )
        best = CalibrationResult(
            calibration=PolynomialCalibration(
                coefficients=best.calibration.coefficients,
                name=name,
                source=source,
                smile=best.calibration.smile,
            ),
            columns=best.columns,
            wavelengths_nm=best.wavelengths_nm,
            residuals_nm=best.residuals_nm,
        )
    return best


def _bandpass_anchors(
    observations: Sequence[BandpassObservation],
    edge_fraction: float,
    ascending: bool,
) -> Tuple[np.ndarray, np.ndarray]:
    columns, wavelengths = [], []
    for observation in observations:
        left, right = find_band_edges(observation.spectrum, edge_fraction)
        if ascending:
            columns.extend([left, right])
            wavelengths.extend([observation.cut_on_nm, observation.cut_off_nm])
        else:
            columns.extend([left, right])
            wavelengths.extend([observation.cut_off_nm, observation.cut_on_nm])
    return np.array(columns, dtype=float), np.array(wavelengths, dtype=float)


def find_band_edges(spectrum: np.ndarray, edge_fraction: float = 0.5) -> Tuple[float, float]:
    """
    Sub-column positions where a transmitted band crosses `edge_fraction`
    of its own height, rising and falling.

    The threshold is taken between the band's floor and peak rather than
    from zero, so a dark-current pedestal under the band does not shift the
    measured edges.
    """
    spectrum = np.asarray(spectrum, dtype=float)
    if spectrum.ndim != 1 or spectrum.size < 3:
        raise CalibrationError(
            f"band edge detection needs a 1D trace of at least 3 points, "
            f"got shape {spectrum.shape}"
        )

    floor, peak = float(spectrum.min()), float(spectrum.max())
    if peak <= floor:
        raise CalibrationError("this trace is flat -- no transmission band to measure")
    threshold = floor + edge_fraction * (peak - floor)

    above = np.flatnonzero(spectrum >= threshold)
    if above.size == 0:
        raise CalibrationError("no samples reach the band threshold")

    first, last = int(above[0]), int(above[-1])
    if first == 0 or last == spectrum.size - 1:
        raise CalibrationError(
            "the transmission band runs off the edge of the sensor, so its "
            "cut-on/cut-off wavelength is not imaged and cannot anchor a fit"
        )

    return (
        _crossing(spectrum, first - 1, first, threshold),
        _crossing(spectrum, last, last + 1, threshold),
    )


def _crossing(spectrum: np.ndarray, low: int, high: int, threshold: float) -> float:
    """Linearly interpolate where the trace crosses `threshold` between two samples."""
    y0, y1 = spectrum[low], spectrum[high]
    if y1 == y0:
        return float(low)
    return float(low + (threshold - y0) / (y1 - y0))


# ----------------------------------------------------------------------
# Smile
# ----------------------------------------------------------------------


def estimate_smile(
    frame: np.ndarray,
    degree: int = 2,
    min_prominence: Optional[float] = None,
    max_lines: int = 8,
    min_row_signal: float = 0.2,
) -> Tuple[float, ...]:
    """
    Measure how far each row's spectrum is shifted along columns relative to
    the centre row, and fit that shift as a polynomial in normalised row.

    The result plugs straight into `ExtractionSettings.smile_coeffs` and
    `PolynomialCalibration.smile` -- same convention as
    `core.synthetic_spectrograph.smile_coeffs`.

    Rows carrying too little signal are excluded (`min_row_signal`, as a
    fraction of the brightest row's line strength). Outside the slit image
    there is no line to centroid, and including those rows would fit the
    polynomial to noise at exactly the extremes where the bow is largest.
    """
    frame = np.asarray(frame, dtype=float)
    if frame.ndim != 2:
        raise CalibrationError(f"smile estimation needs a 2D frame, got {frame.shape}")

    height = frame.shape[0]
    if height < degree + 1:
        raise CalibrationError(
            f"a degree-{degree} smile fit needs at least {degree + 1} rows, got {height}"
        )

    reference_row = (height - 1) // 2
    reference_trace = frame[reference_row]
    if min_prominence is None:
        min_prominence = relative_prominence_threshold(reference_trace)

    line_indices = find_peak_indices(
        reference_trace, min_prominence=min_prominence, min_distance=3
    )[:max_lines]
    if line_indices.size == 0:
        raise CalibrationError(
            "no emission lines found on the centre row, so there is nothing "
            "whose row-to-row drift could be measured"
        )

    # The centroid window must be comfortably wider than the lines, or every
    # measurement is pulled back toward its own window centre and the fitted
    # bow comes out low -- at half_window=3 on FWHM-3 lines it under-reports
    # by ~30%, silently. Sized from the measured line width instead of a
    # magic constant so it stays right if the optics change.
    half_window = _centroid_half_window(reference_trace, int(line_indices[0]))

    reference_columns = np.array(
        [
            refine_peak_position(reference_trace, int(i), half_window)
            for i in line_indices
        ]
    )

    strengths = frame[:, line_indices].max(axis=1) - frame.min()
    usable = strengths >= min_row_signal * strengths.max()

    # Track each line outward from the centre row, re-centring the search
    # window on the previous row's position. Measuring every row against a
    # window fixed at the *reference* row biases the centroid back toward
    # that window's centre once the line has drifted out of it, which
    # under-reports the bow by roughly a quarter at realistic smile.
    measured = {reference_row: 0.0}
    for direction in (-1, 1):
        previous = reference_columns.copy()
        row = reference_row
        while True:
            row += direction
            if not 0 <= row < height or not usable[row]:
                break
            trace = frame[row]
            current = np.array(
                [_track_line(trace, position, half_window) for position in previous]
            )
            measured[row] = float(np.mean(current - reference_columns))
            previous = current

    rows = sorted(measured)
    shifts = [measured[row] for row in rows]

    if len(rows) < degree + 1:
        raise CalibrationError(
            f"only {len(rows)} rows carry enough signal to measure smile, "
            f"but a degree-{degree} fit needs {degree + 1}"
        )

    half = (height - 1) / 2.0
    normalised = (np.array(rows, dtype=float) - half) / (half if half else 1.0)
    coefficients = np.polyfit(normalised, np.array(shifts), degree)
    return tuple(float(c) for c in reversed(coefficients))


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


def save_calibration(path: PathLike, result: CalibrationResult, width: Optional[int] = None) -> Path:
    """
    Write a calibration as JSON.

    The per-column wavelength vector is written alongside the coefficients
    when `width` is given. It is redundant -- it can be recomputed from the
    polynomial -- but it is what another tool can read without implementing
    our convention, and the spec's calibration tab offers it for copying.
    """
    path = Path(path)
    calibration = result.calibration
    payload = {
        "version": CALIBRATION_FILE_VERSION,
        "name": calibration.name,
        "source": calibration.source,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "wavelength_coefficients": list(calibration.coefficients),
        "smile_coefficients": (
            list(calibration.smile) if calibration.smile is not None else None
        ),
        "anchors": {
            "columns": [float(c) for c in result.columns],
            "wavelengths_nm": [float(w) for w in result.wavelengths_nm],
            "residuals_nm": [float(r) for r in result.residuals_nm],
        },
        "rms_residual_nm": result.rms_residual_nm,
    }
    if width is not None:
        payload["wavelengths_nm_per_column"] = [
            float(v) for v in calibration.wavelengths(width)
        ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_calibration(path: PathLike) -> PolynomialCalibration:
    """Read a calibration written by `save_calibration`."""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    version = payload.get("version")
    if version != CALIBRATION_FILE_VERSION:
        raise CalibrationError(
            f"{path.name}: calibration file version {version!r} is not supported "
            f"(this build reads version {CALIBRATION_FILE_VERSION})"
        )

    smile = payload.get("smile_coefficients")
    return PolynomialCalibration(
        coefficients=tuple(payload["wavelength_coefficients"]),
        name=payload.get("name", "loaded calibration"),
        source=payload.get("source", f"loaded from {path.name}"),
        smile=tuple(smile) if smile is not None else None,
    )


# ----------------------------------------------------------------------
# shared
# ----------------------------------------------------------------------


def _centroid_half_window(trace: np.ndarray, index: int) -> int:
    """
    A centroid window sized from the line width actually present.

    Measures the strongest line's full width at half maximum and returns
    ~2.5x it. Narrower than about twice the FWHM and the centroid is
    truncated, dragging each measurement toward the window centre; much
    wider and it starts swallowing neighbouring lines. Clamped so a
    pathological width cannot produce a useless window.
    """
    height = float(trace[index])
    floor = float(trace.min())
    half = floor + 0.5 * (height - floor)

    left = index
    while left > 0 and trace[left] > half:
        left -= 1
    right = index
    while right < trace.size - 1 and trace[right] > half:
        right += 1

    fwhm = max(float(right - left), 1.0)
    return int(min(max(round(2.5 * fwhm), 3), 25))


def _track_line(trace: np.ndarray, expected_column: float, half_window: int = 7) -> float:
    """
    Follow one spectral line into the next row: centroid it in a window
    centred on where it sat on the previous row.

    Deliberately no local-maximum search. Smile moves a line by well under a
    column between adjacent rows, so the previous position is already the
    right window centre -- whereas searching for the brightest sample nearby
    will happily snap onto a *different* line. That is not hypothetical: the
    Hg 576.9/579.1 nm pair is about 4.5 columns apart at this dispersion, so
    a search window wide enough to be useful is also wide enough to jump the
    gap, which biased every row's measured shift and put a spurious constant
    term in the fitted smile.

    Centroiding in a fixed-width window can be pulled slightly by a close
    neighbour, but identically on every row -- and since only the row-to-row
    *difference* is used, a constant bias cancels exactly.
    """
    return refine_peak_position(
        trace, int(round(expected_column)), half_window=half_window
    )


def _pair_by_order(columns: np.ndarray, wavelengths: np.ndarray, count: int):
    return np.sort(columns)[:count], np.sort(wavelengths)[:count]


def _fit(
    columns: np.ndarray,
    wavelengths: np.ndarray,
    degree: int,
    name: str,
    source: str,
    smile: Optional[Sequence[float]],
) -> CalibrationResult:
    if columns.size != wavelengths.size:
        raise CalibrationError(
            f"{columns.size} columns but {wavelengths.size} wavelengths"
        )
    if columns.size < degree + 1:
        raise CalibrationError(
            f"a degree-{degree} fit needs {degree + 1} anchors, got {columns.size}"
        )

    coefficients = np.polyfit(columns, wavelengths, degree)
    predicted = np.polyval(coefficients, columns)
    residuals = wavelengths - predicted

    calibration = PolynomialCalibration(
        coefficients=tuple(float(c) for c in reversed(coefficients)),
        name=name,
        source=source,
        smile=tuple(float(c) for c in smile) if smile is not None else None,
    )
    return CalibrationResult(calibration, columns, wavelengths, residuals)
