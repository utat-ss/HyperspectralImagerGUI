"""
Saving one spectrometer measurement: a folder holding the spectrum, the raw
frame it came from, and everything needed to interpret either later.

Why this is not `core/measurement_storage.py`
---------------------------------------------
That module is another teammate's (PR #3) and models a *pushbroom
hyperspectral experiment*: per-camera frame lists, a datacube, derived
products like NDVI. Project 2 asks for something different -- one
spectrometer measurement, saved by hand, in formats other tools can read.
Bending their datacube model into this shape would change a file that is
theirs and give neither use case a clean home, so this sits alongside it.
If the two need to converge later, that is a conversation to have with the
owner, not a refactor to do in passing.

The honesty rule
----------------
**An uncalibrated spectrum is never written with a wavelength column.**
Without a calibration the x axis is a raw column index, and labelling it
`wavelength_nm` would produce a file that looks like a spectrum in
nanometres and is not -- a lie that outlives the session, travels to other
tools, and cannot be detected from the file itself. The column is named
`pixel_index` instead, and the metadata says `"calibration": null`.

Formats
-------
`spectrum.csv` is always written: two columns, with the context and the
column header in `#` comment lines above the data. It then loads with

    numpy.loadtxt(path, delimiter=",", comments="#")

and nothing else -- no `skiprows` the caller would have to derive from how
many notes the user happened to type. Excel, Origin and MATLAB open it too,
and pandas reads it with `comment="#"` plus explicit `names`. That is what
"widely importable" has to mean. The others are opt-in:

- `tsv`  same data, tab separated
- `json` spectrum plus the full metadata in one file, for scripts
- `npy`  exact float64, no decimal-string rounding
- `txt`  whitespace-separated, `numpy.loadtxt`'s oldest default

The raw frame is always saved twice: `raw_frame.npy` is the real data at
full bit depth, and `raw_frame.png` is an 8-bit preview for looking at. The
preview is lossy by design and says so in the metadata, so nobody
reconstructs a measurement from the picture.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import numpy as np

from core.calibration import WavelengthCalibration

PathLike = Union[str, Path]

SCHEMA_VERSION = 1

#: Spectrum formats the save panel offers. CSV is mandatory, never optional.
SPECTRUM_FORMATS: Tuple[str, ...] = ("csv", "tsv", "json", "npy", "txt")
MANDATORY_FORMAT = "csv"

PIXEL_COLUMN = "pixel_index"
WAVELENGTH_COLUMN = "wavelength_nm"
INTENSITY_COLUMN = "intensity"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class CaptureMetadata:
    """What the camera was doing when the frame was taken."""

    camera_name: str = "unknown"
    exposure_us: Optional[float] = None
    gain: Optional[float] = None
    frame_rate_hz: Optional[float] = None
    bit_depth: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_name": self.camera_name,
            "exposure_us": self.exposure_us,
            "gain": self.gain,
            "frame_rate_hz": self.frame_rate_hz,
            "bit_depth": self.bit_depth,
        }


@dataclass(frozen=True)
class SpectrumMeasurement:
    """One saved measurement: the trace, the frame, and the context."""

    spectrum: np.ndarray
    frame: Optional[np.ndarray] = None
    name: str = ""
    comments: str = ""
    capture: CaptureMetadata = field(default_factory=CaptureMetadata)
    calibration: Optional[WavelengthCalibration] = None
    extraction: Dict[str, Any] = field(default_factory=dict)
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if np.asarray(self.spectrum).ndim != 1:
            raise ValueError(
                f"a spectrum must be 1D, got shape {np.asarray(self.spectrum).shape}"
            )
        if np.asarray(self.spectrum).size == 0:
            raise ValueError("cannot save an empty spectrum")

    @property
    def is_calibrated(self) -> bool:
        return self.calibration is not None

    @property
    def x_column_name(self) -> str:
        return WAVELENGTH_COLUMN if self.is_calibrated else PIXEL_COLUMN

    def x_values(self) -> np.ndarray:
        """Wavelengths if calibrated, otherwise raw column indices."""
        width = np.asarray(self.spectrum).size
        if self.calibration is None:
            return np.arange(width, dtype=float)
        return self.calibration.wavelengths(width)

    def folder_name(self) -> str:
        """
        Timestamp first so folders sort chronologically, then the user's
        name. Every measurement gets its own folder, per the requirement.
        """
        stamp = self.created.astimezone().strftime("%Y-%m-%dT%H-%M-%S")
        slug = _UNSAFE.sub("-", self.name.strip()).strip("-")
        return f"{stamp}_{slug}" if slug else stamp

    def metadata(self) -> Dict[str, Any]:
        calibration = self.calibration
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "comments": self.comments,
            "created_utc": self.created.astimezone(timezone.utc).isoformat(),
            "created_local": self.created.astimezone().isoformat(),
            "points": int(np.asarray(self.spectrum).size),
            "x_column": self.x_column_name,
            "x_units": "nm" if self.is_calibrated else "pixel index",
            "x_range": [float(self.x_values()[0]), float(self.x_values()[-1])],
            "capture": self.capture.to_dict(),
            "extraction": dict(self.extraction),
            "calibration": (
                None
                if calibration is None
                else {
                    "name": calibration.name,
                    "source": calibration.source,
                    "wavelength_coefficients": list(
                        getattr(calibration, "coefficients", ())
                    ),
                    "smile_coefficients": (
                        list(calibration.smile_coeffs())
                        if calibration.smile_coeffs() is not None
                        else None
                    ),
                }
            ),
            "raw_frame": (
                None
                if self.frame is None
                else {
                    "shape": list(np.asarray(self.frame).shape),
                    "dtype": str(np.asarray(self.frame).dtype),
                    "data_file": "raw_frame.npy",
                    "preview_file": "raw_frame.png",
                    "preview_note": (
                        "8-bit preview for viewing only; raw_frame.npy is the data"
                    ),
                }
            ),
        }


def save_measurement(
    root: PathLike,
    measurement: SpectrumMeasurement,
    formats: Sequence[str] = (MANDATORY_FORMAT,),
) -> Path:
    """
    Write `measurement` into its own folder under `root`. Returns the folder.

    CSV is always written whether or not it was requested: it is the format
    the requirement exists for, and a measurement folder without one would
    be a folder nobody else's tools can open.
    """
    unknown = [name for name in formats if name not in SPECTRUM_FORMATS]
    if unknown:
        raise ValueError(
            f"unknown spectrum format(s): {', '.join(unknown)} "
            f"(available: {', '.join(SPECTRUM_FORMATS)})"
        )

    folder = _unique_folder(Path(root), measurement.folder_name())
    folder.mkdir(parents=True)

    metadata = measurement.metadata()
    x_values = measurement.x_values()
    spectrum = np.asarray(measurement.spectrum, dtype=float)
    chosen = {MANDATORY_FORMAT, *formats}

    if "csv" in chosen:
        _write_delimited(folder / "spectrum.csv", ",", x_values, spectrum, measurement)
    if "tsv" in chosen:
        _write_delimited(folder / "spectrum.tsv", "\t", x_values, spectrum, measurement)
    if "txt" in chosen:
        _write_delimited(folder / "spectrum.txt", " ", x_values, spectrum, measurement)
    if "npy" in chosen:
        np.save(folder / "spectrum.npy", np.column_stack([x_values, spectrum]))
    if "json" in chosen:
        (folder / "spectrum.json").write_text(
            json.dumps(
                {
                    **metadata,
                    measurement.x_column_name: x_values.tolist(),
                    INTENSITY_COLUMN: spectrum.tolist(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    (folder / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    if measurement.comments.strip():
        (folder / "notes.txt").write_text(measurement.comments, encoding="utf-8")

    if measurement.frame is not None:
        frame = np.asarray(measurement.frame)
        np.save(folder / "raw_frame.npy", frame)
        _write_preview(folder / "raw_frame.png", frame, measurement.capture.bit_depth)

    return folder


def _unique_folder(root: Path, name: str) -> Path:
    """
    A folder that does not already exist.

    Two measurements saved inside the same second would otherwise collide,
    and silently merging their files would corrupt both.
    """
    candidate = root / name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{name}-{suffix}"
        suffix += 1
    return candidate


def _write_delimited(
    path: Path,
    delimiter: str,
    x_values: np.ndarray,
    spectrum: np.ndarray,
    measurement: SpectrumMeasurement,
) -> None:
    """
    Two columns with a header, and the context in `#` comment lines.

    The column header is itself a `#` comment, and deliberately so. With a
    plain header row, `numpy.loadtxt` needs `skiprows` set to however many
    comment lines happen to precede it -- a number that changes with how
    many notes the user typed, so the reader would have to count them. As a
    comment, the whole file loads with

        numpy.loadtxt(path, delimiter=",", comments="#")

    and nothing else. Excel and Origin still open it, and pandas reads it
    with `comment="#"` plus explicit `names`. The header stays last so it
    sits directly above the data it names.
    """
    capture = measurement.capture
    lines = [
        f"# {measurement.name or 'measurement'}",
        f"# saved {measurement.created.astimezone().isoformat()}",
        f"# camera: {capture.camera_name}",
        f"# exposure_us: {capture.exposure_us}",
        f"# gain: {capture.gain}",
        f"# frame_rate_hz: {capture.frame_rate_hz}",
    ]
    if measurement.calibration is None:
        lines.append(
            "# NOT WAVELENGTH CALIBRATED -- the first column is a raw sensor "
            "column index, not nanometres."
        )
    else:
        lines.append(f"# calibration: {measurement.calibration.describe()}")
    for line in measurement.comments.strip().splitlines():
        lines.append(f"# note: {line}")

    lines.append(f"# {measurement.x_column_name}{delimiter}{INTENSITY_COLUMN}")
    lines += [
        f"{x:.6g}{delimiter}{y:.6g}" for x, y in zip(x_values, spectrum)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_preview(path: Path, frame: np.ndarray, bit_depth: Optional[int]) -> None:
    """
    An 8-bit PNG of the raw frame, for looking at.

    Scaled from the sensor's declared bit depth rather than the array's
    dtype range: a 12-bit frame in a uint16 container scaled by 65535 would
    come out almost black. Falls back to the frame's own maximum when the
    bit depth is unknown, which is worse but visible.
    """
    try:
        from PIL import Image
    except ImportError:
        return  # a missing preview must not lose the measurement

    data = np.asarray(frame, dtype=float)
    if bit_depth:
        full_scale = float((1 << bit_depth) - 1)
    else:
        full_scale = float(data.max()) or 1.0

    scaled = np.clip(data / full_scale, 0.0, 1.0) * 255.0
    Image.fromarray(scaled.astype(np.uint8)).save(path)


def load_spectrum_csv(path: PathLike) -> Tuple[np.ndarray, np.ndarray, str]:
    """
    Read back a spectrum written here: (x, intensity, x_column_name).

    Exists so the round trip is testable, and so the Process Data tab has a
    reader when it arrives.
    """
    path = Path(path)
    x_column = PIXEL_COLUMN
    x_values, intensities = [], []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            # The column header is itself a comment (see _write_delimited),
            # so the units have to be recovered from the comment block.
            parts = re.split(r"[,\t ]+", line.lstrip("# ").strip())
            if len(parts) == 2 and parts[1] == INTENSITY_COLUMN:
                x_column = parts[0]
            continue
        parts = re.split(r"[,\t ]+", line)
        if len(parts) < 2:
            continue
        x_values.append(float(parts[0]))
        intensities.append(float(parts[1]))

    return np.array(x_values), np.array(intensities), x_column
