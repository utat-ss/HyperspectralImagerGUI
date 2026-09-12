"""
Frame -> 1D spectrum extraction.

Moved here from `src/gui/` because none of it is Qt: these are pure
`ndarray -> ndarray` functions, and `src/core/` is where that belongs. The
GUI composes them; it does not implement them.

This module is the single place the "how do we turn a 2D sensor image into
a spectrum" assumption lives, so replacing a placeholder or adding a
correction is a one-file change rather than a hunt through the GUI.

The methods mirror the spec document's "Spectrum Calculation Method"
checklist:

- **Slide-adjust line cross-section** -- take one row. What the user gets
  when they drag the line on the camera feed.
- **Horizontal binning** -- average rows. Better SNR than a single row, but
  only valid if the lines are straight, which smile makes them not.
- **Smile correction** -- straighten the lines before binning. The
  coefficients come from calibration (Phase 5); until a calibration is
  loaded there is nothing to correct with, so the method stays disabled.
- **Dark noise removal** -- subtract a dark frame.
- **Sensor QE correction** -- divide out the per-column response.

Order is not arbitrary. Dark subtraction is a per-pixel sensor artefact, so
it happens on the frame before anything is combined. Smile correction
resamples the frame geometrically, so it must also precede any row
combination -- correcting after binning is impossible, the information is
already smeared. QE correction scales by wavelength, so it applies to the
extracted 1D trace.

Binning uses mean rather than sum deliberately: sum's output range depends
on how many rows were combined, while mean stays in the same
0..(2**bit_depth - 1) range as the source pixels, which is what lets the
spectrum plot share the image view's bit-depth-derived axis. Switching to
sum later means the y-axis needs its own scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import numpy as np


class ExtractionMethod:
    """How rows are collapsed into a single trace."""

    LINE = "line"
    BINNING = "binning"


@dataclass(frozen=True)
class ExtractionSettings:
    """
    One complete description of how to turn a frame into a spectrum.

    Frozen so a settings object cannot be mutated out from under a frame
    mid-extraction by a GUI callback on another event.
    """

    method: str = ExtractionMethod.BINNING

    # For LINE: which sensor row to read. None means the centre row.
    row: Optional[int] = None

    # For BINNING: inclusive row range to average. None means every row.
    row_range: Optional[Tuple[int, int]] = None

    # Column shift as a polynomial in normalised row (-1..1), ascending
    # powers -- the same convention SpectrographTruth.smile_coeffs uses, so
    # a calibration's output can be handed straight over.
    smile_coeffs: Optional[Sequence[float]] = None

    # Per-pixel dark frame, same shape as the incoming frame.
    dark_frame: Optional[np.ndarray] = None

    # Per-column relative quantum efficiency, length == frame width.
    qe_curve: Optional[np.ndarray] = None

    @property
    def smile_correction_enabled(self) -> bool:
        """
        False until a calibration supplies coefficients.

        This is what the GUI reads to grey out the "Horizontal Binning with
        Smile Correction" option: the method is implemented, but with no
        coefficients there is nothing for it to do, and silently running it
        as a no-op would misreport an uncorrected spectrum as corrected.
        """
        return self.smile_coeffs is not None and any(self.smile_coeffs)


def subtract_dark(frame: np.ndarray, dark_frame: np.ndarray) -> np.ndarray:
    """
    Remove the sensor's dark signal, floored at zero.

    Returns float: dark subtraction routinely drives pixels below zero on
    noise alone, and on an unsigned frame that would wrap to full scale --
    a black pixel becoming a white one.
    """
    if dark_frame.shape != frame.shape:
        raise ValueError(
            f"Dark frame shape {dark_frame.shape} does not match frame shape {frame.shape}"
        )
    return np.clip(frame.astype(np.float64) - dark_frame.astype(np.float64), 0.0, None)


def correct_smile(
    frame: np.ndarray,
    smile_coeffs: Sequence[float],
    reference_row: Optional[float] = None,
) -> np.ndarray:
    """
    Straighten curved spectral lines by shifting each row back along
    columns by that row's smile offset.

    A spectrograph images a straight slit as a bowed line, so one
    wavelength lands at slightly different columns on different rows.
    Averaging rows in that state smears each line across every column it
    visited. This resamples each row so a given wavelength sits at the same
    column on all of them, which is what makes binning legitimate.

    Resampling is linear interpolation per row. Columns shifted in from
    outside the sensor have no data and are filled with the row's edge
    value rather than zero, so binning does not acquire a dark notch at the
    frame edges that a later peak-finder could mistake for a feature.

    PERFORMANCE: this is the hot path of the whole display pipeline. On a
    1280x1024 frame it costs ~21 ms, about 87% of the per-frame budget --
    the end-to-end display path runs at ~66 fps with it and ~520 fps
    without. That still clears the 10 fps requirement with room to spare,
    so it is left simple and readable for now, but if Phase 5's calibration
    resample makes the budget tight, the per-row Python loop below is the
    first thing to vectorise (map_coordinates over the whole frame, or a
    precomputed shift matrix reused across frames since the coefficients
    only change when a calibration is loaded).
    """
    if frame.ndim != 2:
        raise ValueError(f"Smile correction needs a 2D frame, got shape {frame.shape}")

    height, width = frame.shape
    if reference_row is None:
        reference_row = (height - 1) / 2.0

    half = (height - 1) / 2.0
    rows = np.arange(height, dtype=float)
    normalised = np.zeros_like(rows) if half == 0 else (rows - half) / half
    shifts = np.polyval(tuple(reversed(tuple(smile_coeffs))), normalised)

    columns = np.arange(width, dtype=float)
    corrected = np.empty((height, width), dtype=np.float64)
    source = frame.astype(np.float64)
    for index in range(height):
        # Sample the original row at (column + shift) to move the feature
        # currently at that offset back onto `column`.
        corrected[index] = np.interp(columns + shifts[index], columns, source[index])
    return corrected


def extract_row(frame: np.ndarray, row: Optional[int] = None) -> np.ndarray:
    """One sensor row as a trace. `row=None` means the centre row."""
    if frame.ndim != 2:
        raise ValueError(f"Row extraction needs a 2D frame, got shape {frame.shape}")
    if row is None:
        row = frame.shape[0] // 2
    if not 0 <= row < frame.shape[0]:
        raise ValueError(f"Row {row} is outside the frame's {frame.shape[0]} rows")
    return frame[row].astype(np.float64)


def bin_rows(
    frame: np.ndarray, row_range: Optional[Tuple[int, int]] = None
) -> np.ndarray:
    """Average rows into one trace. `row_range` is inclusive; None means all."""
    if frame.ndim != 2:
        raise ValueError(f"Row binning needs a 2D frame, got shape {frame.shape}")
    if row_range is None:
        return frame.mean(axis=0, dtype=np.float64)

    first, last = row_range
    if not (0 <= first <= last < frame.shape[0]):
        raise ValueError(
            f"Row range {row_range} is invalid for a frame with {frame.shape[0]} rows"
        )
    return frame[first : last + 1].mean(axis=0, dtype=np.float64)


def apply_qe_correction(spectrum: np.ndarray, qe_curve: np.ndarray) -> np.ndarray:
    """
    Divide out the sensor's relative quantum efficiency.

    Zero-response columns would divide to infinity, so they are returned as
    zero: a wavelength the sensor cannot see has no measurable intensity,
    which is a truthful answer, unlike an infinity that would blow up every
    downstream autoscale.
    """
    if qe_curve.shape != spectrum.shape:
        raise ValueError(
            f"QE curve shape {qe_curve.shape} does not match spectrum shape {spectrum.shape}"
        )
    curve = qe_curve.astype(np.float64)
    return np.divide(
        spectrum, curve, out=np.zeros_like(spectrum, dtype=np.float64), where=curve > 0
    )


def extract_spectrum(
    frame: np.ndarray, settings: Optional[ExtractionSettings] = None
) -> np.ndarray:
    """
    Apply a full extraction pipeline to one frame.

    Called with no settings this is a plain row-binned mean, which is what
    the GUI did before any of the methods existed -- so existing callers
    keep working unchanged.
    """
    if settings is None:
        settings = ExtractionSettings()

    working = frame
    if settings.dark_frame is not None:
        working = subtract_dark(working, settings.dark_frame)

    if settings.smile_correction_enabled:
        working = correct_smile(working, settings.smile_coeffs)

    if settings.method == ExtractionMethod.LINE:
        spectrum = extract_row(working, settings.row)
    elif settings.method == ExtractionMethod.BINNING:
        spectrum = bin_rows(working, settings.row_range)
    else:
        raise ValueError(f"Unknown extraction method: {settings.method!r}")

    if settings.qe_curve is not None:
        spectrum = apply_qe_correction(spectrum, settings.qe_curve)

    return spectrum
