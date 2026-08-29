"""
Filesystem helpers for Measurement folders.

Layout (one folder per experiment)::

    <root>/<measurement_id>/
      metadata.json
      thumbnail.png          (optional)
      raw/basler/
      raw/thorlabs/
      raw/calibration/
      raw/datacube.npy       (optional)
      processed/<product_id>/
        product.npy
        preview.png          (optional)
        steps.log

What each entry holds:

    metadata.json
        Small fields only (id, name, comments, acquisition settings, relative
        paths). Large arrays never live here so the folder stays portable.

    thumbnail.png
        Optional gallery image for the whole experiment. Written only if
        set_thumbnail() is called; not required to load a measurement.

    raw/basler/, raw/thorlabs/
        Per-frame captures from each camera (PNG for 8-bit images, .npy
        otherwise). Created empty; frames appear as add_raw_frame() runs.

    raw/calibration/
        Copied calibration files (dark frames, white refs, etc.). Empty
        until add_calibration_file() is used.

    raw/datacube.npy
        Optional assembled hyperspectral cube. Frames can exist without a
        cube; written only by set_datacube().

    processed/<product_id>/
        One independently generated result (e.g. NDVI). product.npy is the
        numeric output; steps.log records the operations that produced it.
        preview.png is an optional viewable snapshot, written only if a
        preview array is passed to add_processed_product(). Display-only:
        pass preview_vmin/preview_vmax for a fixed scale (e.g. NDVI -1..1);
        non-finite pixels are transparent.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from PIL import Image

from core.measurement import (
    SCHEMA_VERSION,
    AcquisitionInfo,
    CameraCaptureConfig,
    Measurement,
    ProcessedProduct,
    ProcessingStep,
    RawData,
    ScanConfig,
)

PathLike = Union[str, Path]

_METADATA_NAME = "metadata.json"
_RAW = "raw"
_BASLER = "basler"
_THORLABS = "thorlabs"
_CALIBRATION = "calibration"
_PROCESSED = "processed"
_DATACUBE_NAME = "datacube.npy"
_PRODUCT_ARRAY = "product.npy"
_PREVIEW_NAME = "preview.png"
_STEPS_LOG = "steps.log"
_THUMBNAIL_NAME = "thumbnail.png"

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_measurement_id(prefix: str = "meas") -> str:
    """e.g. meas_20260822_143052."""
    stamp = _utc_now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}"


def sanitize_id(measurement_id: str) -> str:
    cleaned = _SAFE_ID.sub("_", measurement_id.strip())
    if not cleaned:
        raise ValueError("measurement id is empty after sanitizing")
    return cleaned


class MeasurementStorage:
    """Create, save, and load Measurement folders under a root directory."""

    def __init__(self, root: PathLike):
        self.root = Path(root).resolve()

    def measurement_dir(self, measurement_id: str) -> Path:
        return self.root / sanitize_id(measurement_id)

    def create(
        self,
        measurement_id: Optional[str] = None,
        name: str = "",
        comments: str = "",
        acquisition: Optional[AcquisitionInfo] = None,
    ) -> Measurement:
        """
        Create an empty measurement folder and write initial metadata.json.
        Raises FileExistsError if the folder already exists.
        """
        mid = sanitize_id(measurement_id or make_measurement_id())
        folder = self.measurement_dir(mid)
        if folder.exists():
            raise FileExistsError(f"Measurement folder already exists: {folder}")

        (folder / _RAW / _BASLER).mkdir(parents=True)
        (folder / _RAW / _THORLABS).mkdir(parents=True)
        (folder / _RAW / _CALIBRATION).mkdir(parents=True)
        (folder / _PROCESSED).mkdir(parents=True)

        measurement = Measurement(
            id=mid,
            name=name,
            comments=comments,
            acquisition=acquisition or AcquisitionInfo(),
        )
        self.save(measurement)
        return measurement

    def save(self, measurement: Measurement) -> Path:
        """Write metadata.json for an existing measurement folder."""
        folder = self.measurement_dir(measurement.id)
        if not folder.is_dir():
            raise FileNotFoundError(f"Measurement folder does not exist: {folder}")
        path = folder / _METADATA_NAME
        path.write_text(
            json.dumps(_to_jsonable(measurement), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return path

    def load(self, measurement_id: str) -> Measurement:
        """Load metadata.json and rebuild a Measurement (paths stay relative)."""
        folder = self.measurement_dir(measurement_id)
        meta_path = folder / _METADATA_NAME
        if not meta_path.is_file():
            raise FileNotFoundError(f"No metadata.json in {folder}")
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return _measurement_from_dict(data)

    def add_raw_frame(
        self,
        measurement: Measurement,
        camera: str,
        array: np.ndarray,
        index: Optional[int] = None,
        save_metadata: bool = True,
    ) -> str:
        """
        Save a frame under raw/basler or raw/thorlabs and append its relative path.

        camera: "basler" or "thorlabs"
        """
        camera = camera.lower()
        if camera not in (_BASLER, _THORLABS):
            raise ValueError('camera must be "basler" or "thorlabs"')

        folder = self.measurement_dir(measurement.id)
        target_dir = folder / _RAW / camera
        target_dir.mkdir(parents=True, exist_ok=True)

        frame_list = (
            measurement.raw.basler_frames
            if camera == _BASLER
            else measurement.raw.thorlabs_frames
        )
        if index is None:
            index = len(frame_list)

        relative = _write_array_or_image(target_dir, f"frame_{index:03d}", array)
        relative = f"{_RAW}/{camera}/{relative}"
        frame_list.append(relative)

        if save_metadata:
            self.save(measurement)
        return relative

    def set_datacube(
        self,
        measurement: Measurement,
        array: np.ndarray,
        save_metadata: bool = True,
    ) -> str:
        """Write raw/datacube.npy and set measurement.raw.datacube."""
        folder = self.measurement_dir(measurement.id)
        raw_dir = folder / _RAW
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / _DATACUBE_NAME
        np.save(path, array)
        relative = f"{_RAW}/{_DATACUBE_NAME}"
        measurement.raw.datacube = relative
        if save_metadata:
            self.save(measurement)
        return relative

    def add_calibration_file(
        self,
        measurement: Measurement,
        source: PathLike,
        dest_name: Optional[str] = None,
        save_metadata: bool = True,
    ) -> str:
        """Copy a calibration file into raw/calibration/ and record its path."""
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        folder = self.measurement_dir(measurement.id)
        dest_dir = folder / _RAW / _CALIBRATION
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = dest_name or source.name
        dest = dest_dir / name
        dest.write_bytes(source.read_bytes())
        relative = f"{_RAW}/{_CALIBRATION}/{name}"
        if relative not in measurement.raw.calibration_files:
            measurement.raw.calibration_files.append(relative)
        if save_metadata:
            self.save(measurement)
        return relative

    def add_processed_product(
        self,
        measurement: Measurement,
        product_id: str,
        kind: str,
        array: np.ndarray,
        steps: Optional[list[ProcessingStep]] = None,
        annotation: str = "",
        preview: Optional[np.ndarray] = None,
        preview_vmin: Optional[float] = None,
        preview_vmax: Optional[float] = None,
        save_metadata: bool = True,
    ) -> ProcessedProduct:
        """
        Create processed/<product_id>/ with product.npy, optional preview, steps.log.

        preview_vmin / preview_vmax set a fixed display range for the PNG
        (e.g. -1 and 1 for NDVI). If omitted, finite values are autoscaled.
        """
        pid = sanitize_id(product_id)
        if measurement.find_product(pid) is not None:
            raise ValueError(f"Processed product already exists: {pid}")

        folder = self.measurement_dir(measurement.id)
        product_dir = folder / _PROCESSED / pid
        product_dir.mkdir(parents=True, exist_ok=False)

        np.save(product_dir / _PRODUCT_ARRAY, array)
        data_path = f"{_PROCESSED}/{pid}/{_PRODUCT_ARRAY}"

        preview_path = None
        if preview is not None:
            _save_image(
                product_dir / _PREVIEW_NAME,
                preview,
                vmin=preview_vmin,
                vmax=preview_vmax,
            )
            preview_path = f"{_PROCESSED}/{pid}/{_PREVIEW_NAME}"

        step_list = list(steps or [])
        _write_steps_log(product_dir / _STEPS_LOG, step_list)

        product = ProcessedProduct(
            product_id=pid,
            kind=kind,
            annotation=annotation,
            data_path=data_path,
            preview_path=preview_path,
            steps=step_list,
        )
        measurement.processed.append(product)
        if save_metadata:
            self.save(measurement)
        return product

    def set_thumbnail(
        self,
        measurement: Measurement,
        array: np.ndarray,
        save_metadata: bool = True,
    ) -> str:
        """Write thumbnail.png at the measurement root."""
        folder = self.measurement_dir(measurement.id)
        _save_image(folder / _THUMBNAIL_NAME, array)
        relative = _THUMBNAIL_NAME
        measurement.thumbnail = relative
        if save_metadata:
            self.save(measurement)
        return relative

    def resolve(self, measurement: Measurement, relative: str) -> Path:
        """Turn a relative path from metadata into an absolute path."""
        return (self.measurement_dir(measurement.id) / relative).resolve()

    def load_array(self, measurement: Measurement, relative: str) -> np.ndarray:
        """Load a .npy file referenced by a relative path."""
        path = self.resolve(measurement, relative)
        if path.suffix.lower() != ".npy":
            raise ValueError(f"Expected a .npy path, got {relative}")
        return np.load(path)


# ---------------------------------------------------------------------------
# Array / image writers
# ---------------------------------------------------------------------------

def _write_array_or_image(directory: Path, stem: str, array: np.ndarray) -> str:
    """
    2D/3D uint8 (or convertible) -> PNG; otherwise -> .npy.
    Returns the filename only (not the raw/camera prefix).
    """
    if _looks_like_image(array):
        name = f"{stem}.png"
        _save_image(directory / name, array)
        return name
    name = f"{stem}.npy"
    np.save(directory / name, array)
    return name


def _looks_like_image(array: np.ndarray) -> bool:
    # Only 8-bit (or bool) arrays become PNG. Scientific uint12/16 frames
    # and float products stay as .npy so values are not crushed to 0..255.
    if array.dtype not in (np.uint8, np.bool_):
        return False
    if array.ndim == 2:
        return True
    if array.ndim == 3 and array.shape[2] in (3, 4):
        return True
    return False


def _display_range(
    finite: np.ndarray,
    vmin: Optional[float],
    vmax: Optional[float],
) -> tuple[float, float]:
    """Fixed vmin/vmax if given; otherwise 2nd–98th percentile, else min/max."""
    if finite.size == 0:
        low, high = 0.0, 1.0
    elif finite.size >= 20:
        p_low, p_high = np.percentile(finite, (2.0, 98.0))
        low, high = float(p_low), float(p_high)
    else:
        low, high = float(finite.min()), float(finite.max())
    if vmin is not None:
        low = float(vmin)
    if vmax is not None:
        high = float(vmax)
    return low, high


def _save_image(
    path: Path,
    array: np.ndarray,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> None:
    """
    Write a viewable PNG. uint8 is saved as-is (camera frames).

    Other dtypes are scaled to 0..255 for display only. Pass vmin/vmax for a
    comparable physical range; if omitted, uses the 2nd–98th percentile of
    finite values so a few outliers do not flatten the image.

    Non-finite pixels are written transparent so they are not confused with
    real black or white values.
    """
    arr = np.asarray(array)
    if arr.dtype == np.uint8:
        Image.fromarray(arr).save(path)
        return

    finite_mask = np.isfinite(arr)
    if arr.ndim == 3 and arr.shape[2] in (3, 4):
        pixel_ok = np.all(finite_mask[..., :3], axis=2)
    else:
        pixel_ok = finite_mask

    finite = arr[finite_mask]
    low, high = _display_range(finite, vmin, vmax)
    scaled = np.zeros(arr.shape, dtype=np.uint8)
    if high > low:
        mapped = (arr.astype(np.float64) - low) / (high - low) * 255.0
        mapped = np.clip(mapped, 0.0, 255.0)
        mapped = np.nan_to_num(mapped, nan=0.0, posinf=255.0, neginf=0.0)
        scaled = np.rint(mapped).astype(np.uint8)

    alpha = np.where(pixel_ok, 255, 0).astype(np.uint8)
    if scaled.ndim == 2:
        Image.fromarray(np.dstack([scaled, alpha]), mode="LA").save(path)
    elif scaled.ndim == 3 and scaled.shape[2] == 3:
        Image.fromarray(np.dstack([scaled, alpha]), mode="RGBA").save(path)
    elif scaled.ndim == 3 and scaled.shape[2] == 4:
        scaled = scaled.copy()
        scaled[..., 3] = np.minimum(scaled[..., 3], alpha)
        Image.fromarray(scaled, mode="RGBA").save(path)
    else:
        Image.fromarray(scaled).save(path)


def _write_steps_log(path: Path, steps: list[ProcessingStep]) -> None:
    lines = []
    for i, step in enumerate(steps, start=1):
        ts = step.timestamp.isoformat()
        params = json.dumps(step.parameters, sort_keys=True)
        script = f" script={step.script_path}" if step.script_path else ""
        lines.append(f"{i}. [{ts}] {step.operation}({params}){script}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj).replace("\\", "/")
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _parse_datetime(value: Optional[str]) -> datetime:
    if not value:
        return _utc_now()
    # Support both offset-aware and naive ISO strings.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _camera_from_dict(data: Optional[dict]) -> Optional[CameraCaptureConfig]:
    if data is None:
        return None
    return CameraCaptureConfig(
        name=data.get("name", "unknown"),
        serial=data.get("serial"),
        exposure_us=data.get("exposure_us"),
        gain=data.get("gain"),
        bit_depth=data.get("bit_depth"),
        pixel_format=data.get("pixel_format"),
        extra=dict(data.get("extra") or {}),
    )


def _step_from_dict(data: dict) -> ProcessingStep:
    return ProcessingStep(
        operation=data["operation"],
        parameters=dict(data.get("parameters") or {}),
        timestamp=_parse_datetime(data.get("timestamp")),
        script_path=data.get("script_path"),
    )


def _product_from_dict(data: dict) -> ProcessedProduct:
    return ProcessedProduct(
        product_id=data["product_id"],
        kind=data["kind"],
        created_at=_parse_datetime(data.get("created_at")),
        annotation=data.get("annotation", ""),
        data_path=data.get("data_path"),
        preview_path=data.get("preview_path"),
        steps=[_step_from_dict(s) for s in data.get("steps") or []],
        extra=dict(data.get("extra") or {}),
    )


def _measurement_from_dict(data: dict) -> Measurement:
    acquisition = data.get("acquisition") or {}
    scan = acquisition.get("scan") or {}
    raw = data.get("raw") or {}
    return Measurement(
        id=data["id"],
        created_at=_parse_datetime(data.get("created_at")),
        name=data.get("name", ""),
        comments=data.get("comments", ""),
        thumbnail=data.get("thumbnail"),
        schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        acquisition=AcquisitionInfo(
            basler=_camera_from_dict(acquisition.get("basler")),
            thorlabs=_camera_from_dict(acquisition.get("thorlabs")),
            scan=ScanConfig(
                step_mm=scan.get("step_mm"),
                num_positions=scan.get("num_positions"),
                notes=scan.get("notes", ""),
            ),
        ),
        raw=RawData(
            basler_frames=list(raw.get("basler_frames") or []),
            thorlabs_frames=list(raw.get("thorlabs_frames") or []),
            datacube=raw.get("datacube"),
            calibration_files=list(raw.get("calibration_files") or []),
        ),
        processed=[_product_from_dict(p) for p in data.get("processed") or []],
    )
