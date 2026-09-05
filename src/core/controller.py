"""
Application controller: GUI-facing API over cameras and measurement storage.

The GUI should call these methods instead of talking to CameraInterface or
MeasurementStorage directly. This module coordinates those backends; it does
not implement camera drivers or the on-disk measurement format.

Typical GUI flow (no widgets here)::

    controller = ApplicationController(storage_root="measurements")
    controller.connect("basler")
    controller.create_measurement(name="bench capture")
    result = controller.capture_frame("basler")
    controller.disconnect("basler")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from core.camera_interface import CameraInterface
from core.measurement import CameraCaptureConfig, Measurement
from core.measurement_storage import MeasurementStorage, PathLike

CameraName = str  # "basler" or "thorlabs"

_BASLER = "basler"
_THORLABS = "thorlabs"
_KNOWN_CAMERAS = (_BASLER, _THORLABS)


class ControllerError(RuntimeError):
    """Controller refused the action (missing measurement, camera not connected, ...).

    Not a wrapper for camera-SDK or storage errors; those still propagate as-is.
    """


@dataclass
class CaptureResult:
    """Return value of capture_frame() so the GUI can display and log the grab."""

    camera: str
    frame: np.ndarray
    relative_path: str
    measurement_id: str


class ApplicationController:
    """
    Intermediary between a future GUI and backend objects.

    Cameras are optional so a machine with only one rig (or tests with fakes)
    still works. Pass CameraInterface implementations; Thorlabs defaults to
    ThorlabsCamera if omitted.
    """

    def __init__(
        self,
        storage_root: PathLike,
        *,
        basler: Optional[CameraInterface] = None,
        thorlabs: Optional[CameraInterface] = None,
    ):
        self.storage = MeasurementStorage(storage_root)
        self._cameras: dict[str, Optional[CameraInterface]] = {
            _BASLER: basler,
            _THORLABS: thorlabs if thorlabs is not None else _default_thorlabs(),
        }
        self._measurement: Optional[Measurement] = None

    # ------------------------------------------------------------------
    # Measurement session
    # ------------------------------------------------------------------

    @property
    def measurement(self) -> Optional[Measurement]:
        """Active measurement, or None if create_measurement / load_measurement has not run."""
        return self._measurement

    def create_measurement(
        self,
        measurement_id: Optional[str] = None,
        name: str = "",
        comments: str = "",
    ) -> Measurement:
        """Create a new on-disk measurement folder and make it the active session."""
        self._measurement = self.storage.create(
            measurement_id=measurement_id,
            name=name,
            comments=comments,
        )
        return self._measurement

    def load_measurement(self, measurement_id: str) -> Measurement:
        """Load an existing measurement folder and make it the active session."""
        self._measurement = self.storage.load(measurement_id)
        return self._measurement

    # ------------------------------------------------------------------
    # Camera connection
    # ------------------------------------------------------------------

    def connect(self, camera: CameraName) -> bool:
        """Open the named camera. Returns True on success."""
        return self._require_camera(camera).connect()

    def disconnect(self, camera: CameraName) -> None:
        """Close the named camera (no-op if that camera was never wired in)."""
        cam = self._cameras[self._normalize(camera)]
        if cam is not None:
            cam.disconnect()

    def disconnect_all(self) -> None:
        """Close every camera that is currently attached."""
        for name in _KNOWN_CAMERAS:
            self.disconnect(name)

    def is_connected(self, camera: CameraName) -> bool:
        cam = self._cameras[self._normalize(camera)]
        return cam is not None and cam.is_connected()

    # ------------------------------------------------------------------
    # Camera settings (thin wrappers)
    # ------------------------------------------------------------------

    def set_exposure_us(self, camera: CameraName, exposure_us: float) -> None:
        self._require_camera(camera).set_exposure_us(exposure_us)

    def get_exposure_us(self, camera: CameraName) -> float:
        return self._require_camera(camera).get_exposure_us()

    def set_gain(self, camera: CameraName, gain: float) -> None:
        self._require_camera(camera).set_gain(gain)

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------

    def capture_frame(self, camera: CameraName) -> CaptureResult:
        """
        Grab one frame from the named camera, record it on the active
        measurement, and write it under raw/<camera>/.
        """
        cam = self._require_camera(camera)
        key = self._normalize(camera)
        measurement = self._require_measurement()

        if not cam.is_connected():
            raise ControllerError(f"{key} camera is not connected")

        frame = cam.get_frame()
        if frame is None:
            raise ControllerError(f"{key} camera returned no frame")

        relative = self.storage.add_raw_frame(
            measurement, key, frame, save_metadata=False
        )
        self._record_capture_config(measurement, key, cam)
        self.storage.save(measurement)
        return CaptureResult(
            camera=key,
            frame=frame,
            relative_path=relative,
            measurement_id=measurement.id,
        )

    def start_live(
        self,
        camera: CameraName,
        on_frame: Callable[[np.ndarray], None],
    ) -> None:
        """Start continuous acquisition; on_frame is called from a camera thread."""
        self._require_camera(camera).start_live(on_frame)

    def stop_live(self, camera: CameraName) -> None:
        self._require_camera(camera).stop_live()

    def measurement_dir(self) -> Path:
        """Absolute folder of the active measurement."""
        measurement = self._require_measurement()
        return self.storage.measurement_dir(measurement.id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _normalize(self, camera: CameraName) -> str:
        key = camera.lower().strip()
        if key not in _KNOWN_CAMERAS:
            raise ControllerError(
                f'camera must be "basler" or "thorlabs", got {camera!r}'
            )
        return key

    def _require_camera(self, camera: CameraName) -> CameraInterface:
        key = self._normalize(camera)
        cam = self._cameras[key]
        if cam is None:
            raise ControllerError(
                f"No {key} camera attached. Pass a CameraInterface to "
                f"ApplicationController({key}=...)."
            )
        return cam

    def _require_measurement(self) -> Measurement:
        if self._measurement is None:
            raise ControllerError(
                "No active measurement. Call create_measurement() or load_measurement() first."
            )
        return self._measurement

    def _record_capture_config(
        self,
        measurement: Measurement,
        camera: str,
        cam: CameraInterface,
    ) -> None:
        config = CameraCaptureConfig(
            name=getattr(cam, "name", camera),
            exposure_us=float(cam.get_exposure_us()),
        )
        if camera == _BASLER:
            measurement.acquisition.basler = config
        else:
            measurement.acquisition.thorlabs = config


def _default_thorlabs() -> Optional[CameraInterface]:
    """Construct ThorlabsCamera when the module is available; otherwise leave unset."""
    try:
        from core.thorlabs_camera import ThorlabsCamera
    except ImportError:
        return None
    return ThorlabsCamera()
