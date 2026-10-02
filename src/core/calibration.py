"""
Wavelength calibration: the pixel-column -> wavelength mapping.

**This module defines the interface only.** The algorithms that *produce* a
calibration (Method 1: bandpass filter, Method 2: spectral peaks) live in
`core.calibration_methods`. Everything that *consumes* one -- the spectrum
view's x axis, the calibration toggle, smile correction's coefficients -- is
written against what is here, so a producer can change without the display
code changing.

Why a class rather than a bare coefficient array: a calibration has to carry
its own provenance. "500.0 + 0.488*x" tells a user nothing about whether it
came from a real lamp measurement, a vendor datasheet, or a demo default,
and a spectrum axis labelled in nanometres implies a claim about the
instrument that only provenance can justify. `name` and `source` exist so
the GUI can always say where its numbers came from.

The polynomial convention matches core.synthetic_spectrograph throughout:
wavelength is a polynomial in raw column index, ascending powers, and smile
is a column shift polynomial in normalised row (-1..1). A recovered
calibration therefore drops straight into
`ExtractionSettings.smile_coeffs` with no translation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import numpy as np


class WavelengthCalibration(ABC):
    """A pixel-column -> wavelength mapping, plus where it came from."""

    #: Short label for the calibration toggle, e.g. "Bandpass filter".
    name: str = "unknown"

    #: Provenance, shown to the user. Say what measured it.
    source: str = "unknown"

    @abstractmethod
    def wavelengths(self, width: int) -> np.ndarray:
        """Wavelength in nm for each of `width` sensor columns."""
        raise NotImplementedError

    def smile_coeffs(self) -> Optional[Sequence[float]]:
        """
        Column-shift polynomial in normalised row, or None if this
        calibration did not characterise smile.

        Returning None is the "not measured" case, distinct from returning
        all-zero coefficients, which would be a positive claim that the
        instrument has no smile.
        """
        return None

    def describe(self) -> str:
        """One line for the UI: what this is and where it came from."""
        return f"{self.name} ({self.source})"


@dataclass(frozen=True)
class PolynomialCalibration(WavelengthCalibration):
    """
    Wavelength as a polynomial in column index.

    Both calibration methods fit a polynomial, so this is the type they
    return, and the GUI is built against it rather than against a mock.
    """

    coefficients: Tuple[float, ...]
    name: str = "polynomial"
    source: str = "unknown"
    smile: Optional[Tuple[float, ...]] = None

    def wavelengths(self, width: int) -> np.ndarray:
        if width < 1:
            raise ValueError(f"width must be at least 1 column, got {width}")
        columns = np.arange(width, dtype=float)
        return np.polyval(tuple(reversed(self.coefficients)), columns)

    def smile_coeffs(self) -> Optional[Sequence[float]]:
        return self.smile


@dataclass(frozen=True)
class CalibrationSet:
    """
    The calibrations currently available to the user.

    Empty is the normal starting state, and the GUI must stay honest in it:
    with nothing loaded the spectrum axis is a raw pixel index and says so,
    rather than presenting an uncalibrated axis in nanometres.
    """

    calibrations: Tuple[WavelengthCalibration, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.calibrations)

    def __iter__(self):
        return iter(self.calibrations)

    def names(self) -> Tuple[str, ...]:
        return tuple(calibration.name for calibration in self.calibrations)

    def by_name(self, name: str) -> Optional[WavelengthCalibration]:
        for calibration in self.calibrations:
            if calibration.name == name:
                return calibration
        return None

    def with_added(self, calibration: WavelengthCalibration) -> "CalibrationSet":
        """
        Return a new set with `calibration` added, replacing any existing
        entry of the same name -- re-running a method should update its
        result, not stack a second copy in the toggle.
        """
        kept = tuple(c for c in self.calibrations if c.name != calibration.name)
        return CalibrationSet(kept + (calibration,))


#: Label for the "no calibration" entry in the toggle. Not a calibration --
#: the absence of one -- so it is a plain constant rather than an instance.
NO_CALIBRATION_LABEL = "No calibration (pixel index)"

PIXEL_AXIS_LABEL = "pixel (column index)"
WAVELENGTH_AXIS_LABEL = "wavelength (nm)"
