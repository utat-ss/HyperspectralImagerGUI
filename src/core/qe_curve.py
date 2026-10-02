"""
Sensor quantum efficiency: response versus wavelength, and how to apply it.

QE is the fraction of incident photons a sensor converts to signal, and it
varies strongly across the spectrum -- silicon peaks around 60% near 600 nm
and falls away toward both blue and its ~1100 nm cutoff. So a perfectly flat
white light does not produce a flat measured spectrum; what comes out is

    measured(lambda) ~= true(lambda) x QE(lambda) x (grating) x (optics)

Dividing by QE recovers the shape. Without it, line *positions* are still
correct -- calibration handles those -- but line *heights* are wrong
relative to each other, which is exactly what matters when comparing
intensities across the band.

**A QE curve cannot be applied without a wavelength calibration.** The curve
is indexed by wavelength; a frame is indexed by column. Mapping one to the
other needs lambda(x), which is what `core.calibration` provides. That is
why QE correction stays disabled until a calibration is selected, and why
`resample_to_columns` takes one.

The shipped curve (`data/qe/python1300_nir_mono.csv`) is vendor datasheet
data for the *sensor*, not a measured calibration of the *camera*. Its own
header says so at length. Note that a constant scale error in a QE curve
cancels out under division -- only its shape across wavelength changes the
corrected result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np

from core.calibration import WavelengthCalibration
from core.reference_spectrum import parse_reference_spectrum

PathLike = Union[str, Path]

#: Shipped vendor curve, relative to the repository root.
BUNDLED_QE_RELATIVE = Path("data") / "qe" / "python1300_nir_mono.csv"


@dataclass(frozen=True)
class QECurve:
    """Relative sensor response versus wavelength, plus where it came from."""

    wavelengths_nm: np.ndarray
    response: np.ndarray
    source: str = "unknown"

    def __post_init__(self) -> None:
        if self.wavelengths_nm.shape != self.response.shape:
            raise ValueError(
                f"wavelength and response columns differ in length: "
                f"{self.wavelengths_nm.shape} vs {self.response.shape}"
            )
        if self.wavelengths_nm.size < 2:
            raise ValueError("a QE curve needs at least two points")
        if np.any(self.response < 0):
            raise ValueError("a QE curve cannot have negative response")

    @property
    def span_nm(self) -> Tuple[float, float]:
        return (float(self.wavelengths_nm[0]), float(self.wavelengths_nm[-1]))

    def describe(self) -> str:
        low, high = self.span_nm
        return f"{self.source} ({low:.0f}-{high:.0f} nm)"

    def resample_to_columns(
        self, calibration: WavelengthCalibration, width: int
    ) -> np.ndarray:
        """
        The response at each sensor column, via `calibration`'s lambda(x).

        Columns whose wavelength falls outside the curve's range are given a
        response of **zero**, which `apply_qe_correction` turns into a zero
        output rather than dividing by a number nobody measured. Extrapolating
        a QE curve past its published range would invent sensitivity data and
        then amplify the spectrum by it -- the error would grow exactly where
        the evidence runs out.
        """
        wavelengths = calibration.wavelengths(width)
        curve_low, curve_high = self.span_nm

        response = np.interp(wavelengths, self.wavelengths_nm, self.response)
        outside = (wavelengths < curve_low) | (wavelengths > curve_high)
        response[outside] = 0.0
        return response

    def coverage(self, calibration: WavelengthCalibration, width: int) -> float:
        """Fraction of columns the curve actually covers, for warning the user."""
        wavelengths = calibration.wavelengths(width)
        low, high = self.span_nm
        return float(np.mean((wavelengths >= low) & (wavelengths <= high)))


def load_qe_curve(path: PathLike) -> QECurve:
    """
    Read a QE curve file.

    Deliberately the same permissive format as a reference spectrum (see
    `core.reference_spectrum`): delimiter sniffing, optional header, `#`
    comments, um/nm detection. One format for both means one reader to
    maintain and one thing for a user to learn.
    """
    path = Path(path)
    parsed = parse_reference_spectrum(
        path.read_text(encoding="utf-8-sig"), source=path.name
    )
    return QECurve(parsed.wavelengths_nm, parsed.intensities, source=parsed.source)


def bundled_qe_path() -> Optional[Path]:
    """
    Path to the shipped vendor curve, or None if it is not present.

    Resolved from this file's location so it works from any working
    directory. Returns None rather than raising: a missing data file should
    leave QE correction unavailable, not stop the application starting.
    """
    repository_root = Path(__file__).resolve().parents[2]
    candidate = repository_root / BUNDLED_QE_RELATIVE
    return candidate if candidate.is_file() else None


def load_bundled_qe_curve() -> Optional[QECurve]:
    """The shipped vendor curve, or None if it is missing or unreadable."""
    path = bundled_qe_path()
    if path is None:
        return None
    try:
        return load_qe_curve(path)
    except (OSError, ValueError):
        return None
