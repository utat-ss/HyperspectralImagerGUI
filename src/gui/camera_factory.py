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

AVAILABLE_BACKENDS = ("mock", "spectrograph", "thorlabs", "basler", "webcam")


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
    elif kind == "spectrograph":
        # Same MockCamera backend, configured to render a synthetic
        # spectrograph image with known ground truth instead of the
        # structured test pattern. This is the hardware-free demo path:
        # it is what the spectrum view, the calibration tab and the
        # showcase all run against when no instrument exists.
        from core.synthetic_spectrograph import DEMO_TRUTH

        cam = MockCamera(spectrograph=DEMO_TRUTH)
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
    elif kind == "basler":
        # Imported lazily for the same reason as thorlabs above. Unlike
        # Thorlabs there is no SDK object to own: pylon keeps one
        # process-wide TlFactory behind GetInstance().
        from core.basler_camera import BaslerCamera

        cam = BaslerCamera()
        cam.connect()
        try:
            yield cam
        finally:
            cam.disconnect()
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
