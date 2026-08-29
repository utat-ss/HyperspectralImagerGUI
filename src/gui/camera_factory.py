"""
The only module allowed to name concrete CameraInterface backends.
Everything downstream (CameraSession, MainWindow, ControlsPanel, ...)
sees only CameraInterface -- no isinstance/hasattr checks anywhere else
in the GUI.
"""

from contextlib import contextmanager
from typing import Iterator

from core.camera_interface import CameraInterface
from core.mock_camera import MockCamera

AVAILABLE_BACKENDS = ("mock", "thorlabs", "webcam")


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
