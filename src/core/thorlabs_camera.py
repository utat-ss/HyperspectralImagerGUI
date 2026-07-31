"""
Thorlabs CS135MUN backend, built on the vendored thorlabs_tsi_sdk
(src/thorlabs_tsi_sdk/) -- not a pip package, and not pylablib.

Docs: Scientific Camera Interfaces\\Thorlabs_Camera_Python_API_Reference.pdf
(ships inside the Windows SDK/Doc download for Scientific Cameras).
"""

import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

import numpy as np

from core.camera_interface import CameraInterface
from core.dll_path import configure_thorlabs_dll_path
from thorlabs_tsi_sdk.tl_camera import Frame, TLCamera, TLCameraSDK
from thorlabs_tsi_sdk.tl_camera_enums import OPERATION_MODE


class ThorlabsCamera(CameraInterface):
    """
    Does not own a TLCameraSDK -- there can only be one TLCameraSDK
    instance alive in the process at a time, so the caller constructs one
    and injects it here (and is responsible for disposing it, after
    disposing every ThorlabsCamera built from it). Script/CLI code should
    go through open_thorlabs_camera() below instead of constructing this
    directly; a future GUI composition root can own the TLCameraSDK
    itself and inject it.
    """

    name = "Thorlabs CS135MUN"

    # Frames to buffer while armed for live view. The test script's arm(2)
    # is fine for a single software-triggered snap, but free-running
    # acquisition polled at ~10ms can outrun a shallow buffer and drop
    # frames before the loop drains them. Needs hardware verification
    # against the CS135MUN's actual free-running frame rate.
    _LIVE_BUFFER_FRAMES = 16

    def __init__(self, sdk: TLCameraSDK, serial: Optional[str] = None):
        self._sdk = sdk
        self._serial = serial
        self._cam: Optional[TLCamera] = None
        self._live_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        # Set by the poll thread if get_pending_frame_or_null()/on_frame
        # raises, so a caller can detect that live view died instead of
        # the thread silently disappearing while is_connected() still
        # reads True.
        self.live_error: Optional[Exception] = None

    def connect(self) -> bool:
        available = self._sdk.discover_available_cameras()
        if not available:
            raise RuntimeError("No available Thorlabs cameras found")
        serial = self._serial or available[0]

        self._cam = self._sdk.open_camera(serial)
        self._cam.operation_mode = OPERATION_MODE.SOFTWARE_TRIGGERED
        self._cam.image_poll_timeout_ms = 0  # non-blocking poll; cadence is controlled by our own sleep loops
        return self.is_connected()

    def disconnect(self) -> None:
        self.stop_live()
        if self._cam is not None:
            self._cam.dispose()
            self._cam = None

    def is_connected(self) -> bool:
        return self._cam is not None

    def is_live(self) -> bool:
        """
        True while the start_live() poll thread is running. Goes False
        both after a normal stop_live() and after the poll thread has
        died from an exception (check live_error for the cause) -- so
        callers can't mistake a dead thread for an active stream.
        """
        return self._live_thread is not None and self._live_thread.is_alive()

    def set_exposure_us(self, exposure_us: float) -> None:
        self._cam.exposure_time_us = int(exposure_us)

    def get_exposure_us(self) -> float:
        return float(self._cam.exposure_time_us)

    def set_gain(self, gain: float) -> None:
        self._cam.gain = int(gain)

    def get_bit_depth(self) -> int:
        if not self.is_connected():
            raise RuntimeError("Cannot read bit depth: camera is not connected")
        return self._cam.bit_depth

    def get_frame(self) -> Optional[np.ndarray]:
        if not self.is_connected():
            return None

        self._cam.frames_per_trigger_zero_for_unlimited = 1
        self._cam.arm(2)
        try:
            self._cam.issue_software_trigger()
            frame = self._poll_for_frame(timeout_s=5.0)
            return None if frame is None else np.copy(frame.image_buffer)
        finally:
            self._cam.disarm()

    def _poll_for_frame(self, timeout_s: float) -> Optional[Frame]:
        start = time.time()
        while time.time() - start < timeout_s:
            frame = self._cam.get_pending_frame_or_null()
            if frame is not None:
                return frame
            time.sleep(0.001)
        return None

    def start_live(self, on_frame: Callable[[np.ndarray], None]) -> None:
        self.live_error = None

        # Free-running: one software trigger with frames_per_trigger set
        # to zero starts continuous video until disarm(). Re-triggering
        # per poll iteration instead would cap the frame rate at the poll
        # interval and add jitter -- needs hardware verification.
        self._cam.frames_per_trigger_zero_for_unlimited = 0
        self._cam.arm(self._LIVE_BUFFER_FRAMES)
        self._stop_flag.clear()
        self._cam.issue_software_trigger()

        def _poll_loop():
            try:
                while not self._stop_flag.is_set():
                    frame = self._cam.get_pending_frame_or_null()
                    if frame is not None:
                        on_frame(np.copy(frame.image_buffer))
                        continue  # queue may already have the next frame -- drain at the camera's rate
                    time.sleep(0.01)  # nothing pending; ~100 Hz idle poll
            except Exception as exception:
                # Record the failure and stop -- is_live() reflects the
                # dead thread so callers can detect it. No re-raise:
                # nothing catches it here, so it would only reach the
                # default threading excepthook, not any caller.
                self.live_error = exception
                self._stop_flag.set()

        self._live_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._live_thread.start()

    def stop_live(self) -> None:
        self._stop_flag.set()
        if self._live_thread is not None:
            self._live_thread.join(timeout=1.0)
            self._live_thread = None
        if self._cam is not None and self._cam.is_armed:
            self._cam.disarm()


@contextmanager
def open_thorlabs_camera(serial: Optional[str] = None) -> Iterator[ThorlabsCamera]:
    """
    Script/CLI convenience that owns a TLCameraSDK for the lifetime of
    the `with` block and disposes it after the camera (correct order,
    since TLCameraSDK.dispose() must happen after every TLCamera built
    from it is disposed):

        with open_thorlabs_camera() as cam:
            cam.set_exposure_us(100000)
            frame = cam.get_frame()

    A GUI composition root that manages its own TLCameraSDK lifetime
    should construct ThorlabsCamera(sdk, serial=...) directly instead of
    using this helper.
    """
    configure_thorlabs_dll_path()
    with TLCameraSDK() as sdk:
        cam = ThorlabsCamera(sdk, serial=serial)
        cam.connect()
        try:
            yield cam
        finally:
            cam.disconnect()
