"""
Peak detection for 1D spectra.

Both calibration methods and the smile estimator need to answer "where are
the features in this trace, to sub-pixel accuracy". scipy.signal has this,
but scipy is not a dependency of this project and pulling in ~40 MB for one
function is a poor trade -- so this is a small numpy implementation of the
two things actually needed.

Prominence, not height, is the selection criterion. A threshold on absolute
height cannot separate a real emission line sitting on a bright continuum
from noise sitting on a dark one; prominence -- how far a peak rises above
the higher of the two valleys that enclose it -- is independent of the
baseline it sits on, which is what makes one threshold work across a whole
spectrum.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def find_peak_indices(
    values: np.ndarray,
    min_prominence: float = 0.0,
    min_distance: int = 1,
) -> np.ndarray:
    """
    Indices of local maxima, strongest first by prominence.

    `min_distance` suppresses a weaker peak within that many samples of a
    stronger one, which is what stops a single noisy line being reported as
    three peaks.
    """
    values = np.asarray(values, dtype=float)
    if values.ndim != 1:
        raise ValueError(f"find_peak_indices needs a 1D trace, got shape {values.shape}")
    if values.size < 3:
        return np.empty(0, dtype=int)

    # Strict on one side, non-strict on the other, so a flat-topped peak is
    # reported once rather than not at all.
    higher_than_left = values[1:-1] > values[:-2]
    at_least_right = values[1:-1] >= values[2:]
    candidates = np.flatnonzero(higher_than_left & at_least_right) + 1
    if candidates.size == 0:
        return np.empty(0, dtype=int)

    prominences = np.array([_prominence(values, int(i)) for i in candidates])

    keep = prominences >= min_prominence
    candidates, prominences = candidates[keep], prominences[keep]
    if candidates.size == 0:
        return np.empty(0, dtype=int)

    order = np.argsort(prominences)[::-1]
    candidates, prominences = candidates[order], prominences[order]

    if min_distance <= 1:
        return candidates

    kept: list[int] = []
    for index in candidates:
        if all(abs(index - other) >= min_distance for other in kept):
            kept.append(int(index))
    return np.array(kept, dtype=int)


def _prominence(values: np.ndarray, index: int) -> float:
    """
    Height of `index` above the higher of the two enclosing valleys.

    Walks outward until the trace rises above this peak (or the trace ends),
    tracking the lowest point reached in each direction -- the textbook
    definition, which is what makes the number comparable between a peak on
    a bright continuum and one on a dark background.
    """
    height = values[index]

    left = index
    while left > 0 and values[left - 1] <= height:
        left -= 1
    right = index
    while right < values.size - 1 and values[right + 1] <= height:
        right += 1

    left_min = values[left:index + 1].min()
    right_min = values[index:right + 1].min()
    return float(height - max(left_min, right_min))


def refine_peak_position(
    values: np.ndarray,
    index: int,
    half_window: int = 3,
    baseline: Optional[float] = None,
) -> float:
    """
    Sub-sample peak position by intensity-weighted centroid.

    A whole-sample index is not good enough for calibration: on a 1024-column
    sensor spanning 500 nm, one column is ~0.5 nm, so rounding every line to
    the nearest column puts a floor on calibration accuracy well above what
    the optics deserve.

    The window baseline is subtracted before weighting. Without that, a peak
    on a high continuum has its centroid pulled toward the window centre by
    the pedestal rather than being set by the line shape.
    """
    values = np.asarray(values, dtype=float)
    low = max(index - half_window, 0)
    high = min(index + half_window + 1, values.size)
    window = values[low:high]
    if window.size == 0:
        return float(index)

    window = window - (window.min() if baseline is None else baseline)
    window = np.clip(window, 0.0, None)
    total = window.sum()
    if total <= 0:
        return float(index)

    positions = np.arange(low, high, dtype=float)
    return float((window * positions).sum() / total)


def find_peaks(
    values: np.ndarray,
    min_prominence: float = 0.0,
    min_distance: int = 1,
    max_peaks: Optional[int] = None,
    half_window: int = 3,
) -> np.ndarray:
    """
    Sub-sample peak positions, **sorted by position** (not by strength).

    `max_peaks` keeps the N most prominent before sorting, so callers that
    know how many features to expect can discard noise without also
    discarding a genuine weak line that happens to sit early in the trace.
    """
    indices = find_peak_indices(values, min_prominence, min_distance)
    if indices.size == 0:
        return np.empty(0, dtype=float)
    if max_peaks is not None:
        indices = indices[:max_peaks]  # already strongest-first
    positions = np.array(
        [refine_peak_position(values, int(i), half_window) for i in indices]
    )
    return np.sort(positions)


def relative_prominence_threshold(values: np.ndarray, fraction: float = 0.05) -> float:
    """
    A prominence threshold as a fraction of the trace's own dynamic range.

    Saves every caller hand-tuning an absolute number against whatever bit
    depth and exposure the frame happened to use.
    """
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return 0.0
    return float((values.max() - values.min()) * fraction)
