"""
Measurement data model: one end-to-end hyperspectral experiment.

Holds metadata and path references for raw acquisition and processed
products. Large arrays live on disk; this module does not talk to cameras
or run processing algorithms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CameraCaptureConfig:
    """Snapshot of camera settings used during acquisition (not a live driver)."""

    name: str = "unknown"
    serial: Optional[str] = None
    exposure_us: Optional[float] = None
    gain: Optional[float] = None
    bit_depth: Optional[int] = None
    pixel_format: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanConfig:
    """Pushbroom scan geometry / notes."""

    step_mm: Optional[float] = None
    num_positions: Optional[int] = None
    notes: str = ""


@dataclass
class AcquisitionInfo:
    """Per-experiment acquisition metadata for both cameras + scan."""

    basler: Optional[CameraCaptureConfig] = None
    thorlabs: Optional[CameraCaptureConfig] = None
    scan: ScanConfig = field(default_factory=ScanConfig)


@dataclass
class RawData:
    """
    References to files under the measurement's raw/ directory.

    Paths are relative to the measurement root (e.g. "raw/basler/frame_000.png").
    """

    basler_frames: list[str] = field(default_factory=list)
    thorlabs_frames: list[str] = field(default_factory=list)
    datacube: Optional[str] = None
    calibration_files: list[str] = field(default_factory=list)


@dataclass
class ProcessingStep:
    """One recorded operation in a product's processing history."""

    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utc_now)
    script_path: Optional[str] = None


@dataclass
class ProcessedProduct:
    """One independently generated processed output under processed/<product_id>/."""

    product_id: str
    kind: str
    created_at: datetime = field(default_factory=_utc_now)
    annotation: str = ""
    data_path: Optional[str] = None
    preview_path: Optional[str] = None
    steps: list[ProcessingStep] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Measurement:
    """
    One complete experiment: metadata + raw refs + processed products.

    Create via MeasurementStorage.create(...) so the on-disk folder exists.
    """

    id: str
    created_at: datetime = field(default_factory=_utc_now)
    name: str = ""
    comments: str = ""
    thumbnail: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    acquisition: AcquisitionInfo = field(default_factory=AcquisitionInfo)
    raw: RawData = field(default_factory=RawData)
    processed: list[ProcessedProduct] = field(default_factory=list)

    def find_product(self, product_id: str) -> Optional[ProcessedProduct]:
        for product in self.processed:
            if product.product_id == product_id:
                return product
        return None
