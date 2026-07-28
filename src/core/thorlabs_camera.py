"""
Thorlabs CS135MUN backend.

Requires: pip install pylablib
Requires the ThorCam DLLs to be installed (they ship with the free
ThorCam application) -- pylablib finds them automatically in the
default Program Files location, or point it at a custom path with:

    import pylablib as pll
    pll.par["devices/dlls/thorlabs_tlcam"] = "path/to/dlls"

Docs: https://pylablib.readthedocs.io/en/stable/devices/Thorlabs_TLCamera.html
"""

import threading
import time
from typing import Callable, Optional

import numpy as np

from core.camera_interface import CameraInterface

try:
    from pylablib.devices import Thorlabs
except ImportError:
    Thorlabs = None  # allows the module to be imported on machines without the SDK


class ThorlabsCamera(CameraInterface):
    name = "Thorlabs CS135MUN"

    def __init__(self, serial: Optional[str] = None):
        self._serial = serial
        self._cam = None
        self._live_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    def connect(self) -> bool:
        if Thorlabs is None:
            raise RuntimeError("pylablib not installed / Thorlabs SDK DLLs not found")
        self._cam = Thorlabs.ThorlabsTLCamera(serial=self._serial)
        return self.is_connected()

    def disconnect(self) -> None:
        self.stop_live()
        if self._cam is not None:
            self._cam.close()
            self._cam = None

    def is_connected(self) -> bool:
        return self._cam is not None and self._cam.is_opened()

    def set_exposure_us(self, exposure_us: float) -> None:
        self._cam.set_exposure(exposure_us / 1e6)  # pylablib expects seconds

    def get_exposure_us(self) -> float:
        return self._cam.get_exposure() * 1e6

    def get_frame(self) -> Optional[np.ndarray]:
        if not self.is_connected():
            return None
        return self._cam.snap()

    def start_live(self, on_frame: Callable[[np.ndarray], None]) -> None:
        self._cam.start_acquisition()
        self._stop_flag.clear()

        def _poll_loop():
            while not self._stop_flag.is_set():
                frame = self._cam.read_oldest_image()
                if frame is not None:
                    on_frame(frame)
                time.sleep(0.01)  # ~100 Hz poll, well above the 10 fps target

        self._live_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._live_thread.start()

    def stop_live(self) -> None:
        self._stop_flag.set()
        if self._live_thread is not None:
            self._live_thread.join(timeout=1.0)
            self._live_thread = None
        if self._cam is not None:
            self._cam.stop_acquisition()
