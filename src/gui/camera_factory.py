"""
The only module allowed to name concrete CameraInterface backends.
Everything downstream (CameraSession, MainWindow, ControlsPanel, ...)
sees only CameraInterface -- no isinstance/hasattr checks anywhere else
in the GUI.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

from core.camera_interface import CameraInterface
from core.mock_camera import MockCamera

AVAILABLE_BACKENDS = ("mock", "thorlabs", "webcam")


@dataclass(frozen=True)
class Device:
    """
    One selectable device, as the Connect tab presents it.

    This is the only place a user-facing device name is tied to a backend
    kind. The connect tab renders whatever is listed here and passes `kind`
    straight back to open_backend(), so it never names a backend class and
    the factory stays the single point of truth.
    """

    kind: str
    label: str
    note: str = ""


#: Instrument devices first, then the hardware-free ones used for
#: development and the showcase demo.
DEVICES: Tuple[Device, ...] = (
    Device("thorlabs", "Thorlabs hyperspectral linescan (CS135MUN)"),
    Device("mock", "Mock camera (test pattern)"),
    Device("webcam", "Webcam (OpenCV)"),
)


def device_for(kind: str) -> Optional[Device]:
    for device in DEVICES:
        if device.kind == kind:
            return device
    return None


def is_implemented(kind: str) -> bool:
    """False for devices listed in the UI but with no backend yet."""
    return kind in AVAILABLE_BACKENDS


@contextmanager
def open_backend(kind: str) -> Iterator[CameraInterface]:
    """
    Construct, connect, and (on exit) disconnect the named backend.
    Owns whatever backend-specific resource lifetime that requires --
    e.g. Thorlabs' TLCameraSDK, which permits only one live instance per
    process and must be disposed after the camera, not before -- so
    callers never have to know that constraint exists.
    """
    if kind == "mock":
        cam = MockCamera()
        cam.connect()
        try:
            yield cam
        finally:
            cam.disconnect()
    elif kind == "thorlabs":
        # Imported lazily: importing thorlabs_camera constructs nothing
        # by itself, but there's no reason to require the vendored SDK's
        # DLLs to be loadable just to run the mock backend.
        from core.thorlabs_camera import open_thorlabs_camera

        with open_thorlabs_camera() as cam:
            yield cam
    # NOTE: "basler" is deliberately absent. core/basler_camera.py does not
    # implement the abstract get_exposure_range_us, so BaslerCamera cannot be
    # instantiated; registering it here would only produce a TypeError at
    # connect time.
    elif kind == "webcam":
        # Imported lazily for the same reason as thorlabs above: opencv
        # shouldn't be a hard requirement just to run the mock backend.
        from core.webcam_camera import WebcamCamera

        cam = WebcamCamera()
        cam.connect()
        try:
            yield cam
        finally:
            cam.disconnect()
    else:
        raise ValueError(f"Unknown camera backend: {kind!r} (available: {AVAILABLE_BACKENDS})")
