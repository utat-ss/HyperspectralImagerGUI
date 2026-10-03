"""
Reference spectrum files: a calibration source measured on a commercial
spectrometer, used as the known truth our instrument is calibrated against.

**The format read here is INVENTED and UNCONFIRMED.** Nobody on this project
has seen the real export from the reference instrument yet, so rather than
guessing one exact layout and failing on everything else, the reader is
deliberately permissive: it accepts the shapes such exports commonly take
and says clearly what it could not understand. When a real file turns up,
check it against `SUPPORTED_FORMAT` below -- that docstring is the whole
specification, and this module is the only place that has to change.

Accepted:

- Comma, tab or semicolon separated. The delimiter is sniffed, not assumed.
- An optional header row. If the first row does not parse as numbers it is
  treated as a header and used to identify columns by name.
- Comment lines beginning with `#`, and blank lines, anywhere in the file.
- Two or more columns. With a header, the wavelength and intensity columns
  are found by name; without one, the first two columns are used in order.
- Wavelengths in nanometres, ascending or descending (re-sorted on load).
- Wavelengths in micrometres, detected and converted -- see `_looks_like_um`.

Rejected loudly, rather than guessed at: files with fewer than two usable
numeric rows, non-monotonic wavelengths, and columns that cannot be parsed
as numbers.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

import numpy as np

from core.peaks import find_peaks, relative_prominence_threshold

PathLike = Union[str, Path]

SUPPORTED_FORMAT = __doc__

_DELIMITERS = ",\t;"
_WAVELENGTH_NAMES = ("wavelength", "wave", "lambda", "nm", "wl")
_INTENSITY_NAMES = ("intensity", "counts", "signal", "irradiance", "amplitude")

# Matched only as a whole column name. As substrings these are disastrous:
# "x" appears in "index", "y" in "intensity" and "energy", so a header of
# "index,Intensity,Wavelength" would have picked the row number as the
# wavelength axis and calibrated against it without complaint.
_WAVELENGTH_EXACT = ("x",)
_INTENSITY_EXACT = ("y",)

# Below this, the first column is far more likely to be micrometres than
# nanometres: no useful spectrometer reports a 3 nm wavelength, and 0.4-2.5
# is the ordinary um range for VNIR/SWIR work.
_UM_MAX = 100.0


@dataclass(frozen=True)
class ReferenceSpectrum:
    """A wavelength-indexed spectrum measured on a trusted instrument."""

    wavelengths_nm: np.ndarray
    intensities: np.ndarray
    source: str = "unknown"

    def __post_init__(self) -> None:
        if self.wavelengths_nm.shape != self.intensities.shape:
            raise ValueError(
                f"wavelength and intensity columns differ in length: "
                f"{self.wavelengths_nm.shape} vs {self.intensities.shape}"
            )
        if self.wavelengths_nm.size < 2:
            raise ValueError("a reference spectrum needs at least two points")

    @property
    def span_nm(self) -> Tuple[float, float]:
        return (float(self.wavelengths_nm[0]), float(self.wavelengths_nm[-1]))

    def peak_wavelengths(
        self,
        min_prominence: Optional[float] = None,
        max_peaks: Optional[int] = None,
        within_nm: Optional[Tuple[float, float]] = None,
    ) -> np.ndarray:
        """
        Emission lines in this reference, as wavelengths.

        `within_nm` restricts to what our sensor can actually see. A lamp has
        lines outside the instrument's range and they are not errors, but
        matching against them would pair a measured peak with a wavelength
        that was never imaged -- which silently corrupts the fit.
        """
        if min_prominence is None:
            min_prominence = relative_prominence_threshold(self.intensities)

        positions = find_peaks(
            self.intensities, min_prominence=min_prominence, min_distance=2
        )
        if positions.size == 0:
            return np.empty(0, dtype=float)

        # Peak positions are fractional sample indices; map back to nm.
        indices = np.arange(self.wavelengths_nm.size, dtype=float)
        wavelengths = np.interp(positions, indices, self.wavelengths_nm)

        if within_nm is not None:
            low, high = min(within_nm), max(within_nm)
            wavelengths = wavelengths[(wavelengths >= low) & (wavelengths <= high)]

        if max_peaks is not None and wavelengths.size > max_peaks:
            # Keep the strongest, then restore wavelength order.
            strengths = np.interp(wavelengths, self.wavelengths_nm, self.intensities)
            keep = np.argsort(strengths)[::-1][:max_peaks]
            wavelengths = np.sort(wavelengths[keep])

        return wavelengths


def load_reference_spectrum(path: PathLike) -> ReferenceSpectrum:
    """Read a reference spectrum file. See this module's docstring for the format."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    return parse_reference_spectrum(text, source=path.name)


def parse_reference_spectrum(text: str, source: str = "unknown") -> ReferenceSpectrum:
    """Parse reference-spectrum text. Split out from the file read so it is testable without a file."""
    rows = _read_rows(text)
    if not rows:
        raise ValueError(f"{source}: no data rows found")

    header = None
    if not _row_is_numeric(rows[0]):
        header, rows = rows[0], rows[1:]
    if len(rows) < 2:
        raise ValueError(
            f"{source}: need at least two numeric rows, found {len(rows)}"
        )

    wavelength_column, intensity_column = _choose_columns(header, rows)

    wavelengths = _column_as_floats(rows, wavelength_column, source, "wavelength")
    intensities = _column_as_floats(rows, intensity_column, source, "intensity")

    if _looks_like_um(wavelengths):
        wavelengths = wavelengths * 1000.0

    order = np.argsort(wavelengths)
    wavelengths, intensities = wavelengths[order], intensities[order]

    if np.any(np.diff(wavelengths) <= 0):
        raise ValueError(
            f"{source}: wavelength column has duplicate or non-monotonic values, "
            "so it cannot be used as a reference axis"
        )

    return ReferenceSpectrum(wavelengths, intensities, source=source)


# ----------------------------------------------------------------------
# internals
# ----------------------------------------------------------------------


def _read_rows(text: str) -> list:
    stripped = "\n".join(
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not stripped:
        return []

    try:
        dialect = csv.Sniffer().sniff(stripped[:4096], delimiters=_DELIMITERS)
        delimiter = dialect.delimiter
    except csv.Error:
        # Sniffer fails on single-column-looking or very short files; fall
        # back to whichever candidate actually appears.
        delimiter = next(
            (d for d in _DELIMITERS if d in stripped), ","
        )

    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(stripped), delimiter=delimiter)
        if row
    ]
    return [row for row in rows if any(cell for cell in row)]


def _row_is_numeric(row: Sequence[str]) -> bool:
    try:
        for cell in row[:2]:
            float(cell)
    except (TypeError, ValueError):
        return False
    return True


def _choose_columns(header, rows) -> Tuple[int, int]:
    if header is not None:
        wavelength = _match_column(header, _WAVELENGTH_NAMES, _WAVELENGTH_EXACT)
        intensity = _match_column(header, _INTENSITY_NAMES, _INTENSITY_EXACT)
        if wavelength is not None and intensity is not None and wavelength != intensity:
            return wavelength, intensity

    if len(rows[0]) < 2:
        raise ValueError(
            "reference spectrum needs at least two columns "
            "(wavelength and intensity)"
        )
    return 0, 1


def _match_column(
    header: Sequence[str],
    names: Sequence[str],
    exact_names: Sequence[str] = (),
) -> Optional[int]:
    """
    Column whose header contains one of `names`, or equals one of
    `exact_names`. Descriptive names are matched loosely; single letters
    only as whole names, since they occur inside ordinary words.
    """
    lowered = [cell.strip().lower() for cell in header]
    for index, cell in enumerate(lowered):
        if any(name in cell for name in names):
            return index
    for index, cell in enumerate(lowered):
        if cell in exact_names:
            return index
    return None


def _column_as_floats(rows, column: int, source: str, label: str) -> np.ndarray:
    values = []
    for number, row in enumerate(rows, start=1):
        if column >= len(row):
            raise ValueError(
                f"{source}: row {number} has no {label} column (expected column {column + 1})"
            )
        try:
            values.append(float(row[column]))
        except ValueError as error:
            raise ValueError(
                f"{source}: row {number} {label} value {row[column]!r} is not a number"
            ) from error
    return np.array(values, dtype=float)


def _looks_like_um(wavelengths: np.ndarray) -> bool:
    """
    Whole column below 100 => micrometres, not nanometres.

    Guessing units is normally a bad idea, but the two scales are three
    orders of magnitude apart with no overlap in any real instrument's
    range, so this cannot silently mis-fire the way a closer call would.
    """
    return bool(wavelengths.size and np.nanmax(np.abs(wavelengths)) < _UM_MAX)
