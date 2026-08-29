"""
Generic UVC/USB webcam backend via OpenCV (cv2.VideoCapture).

This backend exists to exercise CameraInterface against a webcam-class
device where camera "controls" often aren't real controls at all --
this is precisely the scenario the setter-return-value contract was
built for (see set_exposure_us and get_gain_range below). Confirmed
against a real reachable device in this environment: cv2.VideoCapture's
set(CAP_PROP_EXPOSURE, ...) and set(CAP_PROP_GAIN, ...) both return
False every time and the read-back never moves off -1.0, regardless of
what's requested or whether CAP_PROP_AUTO_EXPOSURE is switched to
manual first. A backend that just wrote the request and returned it
unchecked would be lying on exactly this device.

OpenCV's VideoCapture is a lowest-common-denominator wrapper with no
capability-query API: there is no "does this device support gain" call,
and no min/max range query for any property. Two consequences here:

- Whether a control is real can only be discovered by attempting to
  change it and checking whether the read-back actually moved.
  get_gain_range() is decided this way once, at connect() time (see
  _probe_gain_range), and cached.
- There is no hardware-reported range for exposure or gain the way
  ThorlabsCamera gets one from real SDK calls. _EXPOSURE_RANGE_US and
  the gain range returned by the probe are conventional placeholders,
  not read from the device -- needs verification against whatever
  webcam is actually deployed.

Exposure units are a further, separate uncertainty on top of all this:
cv2.CAP_PROP_EXPOSURE means different things on different OS backends --
DirectShow (Windows) conventionally reports it on a log2(seconds) scale;
V4L2 (Linux) conventionally reports linear hundredths of a millisecond.
_us_to_driver_units/_driver_units_to_us below implement the DirectShow
convention as a best-effort guess, not a verified constant. This does
not undermine the honesty of the contract, though: set_exposure_us
always reads the property back after writing and reports whatever that
read-back converts to -- so even where the unit mapping is wrong for a
given driver, or the property is entirely unsupported (as observed
here, where the read-back is a constant -1.0 regardless of the
request), "requested != returned" still correctly signals that the
request wasn't honored. It just means the specific returned number may
not correspond to a real exposure time on every driver -- which is
exactly why this docstring says so instead of asserting otherwise.
"""

import math
import threading
from typing import Callable, Optional, Tuple

import cv2
import numpy as np

from core.camera_interface import CameraInterface


class WebcamCamera(CameraInterface):
    name = "Webcam (OpenCV)"

    _BIT_DEPTH = 8
    _EXPOSURE_RANGE_US = (100.0, 500_000.0)  # conventional DirectShow-ish placeholder, not hardware-reported
    _MAX_CONSECUTIVE_READ_FAILURES = 30  # ~1s of failed reads at a typical 30fps device before giving up

    def __init__(self, index: int = 0):
        self._index = index
        self._cap: Optional[cv2.VideoCapture] = None
        self._gain_range: Optional[Tuple[float, float]] = None

        self._live_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self.live_error: Optional[Exception] = None

    def connect(self) -> bool:
        if self.is_connected():
            raise RuntimeError("Cannot connect: camera is already connected")
        self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            self._cap.release()
            self._cap = None
            raise RuntimeError(f"Could not open webcam at index {self._index}")

        self._gain_range = self._probe_gain_range()
        return self.is_connected()

    def disconnect(self) -> None:
        self.stop_live()
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def is_connected(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def is_live(self) -> bool:
        """Mirrors ThorlabsCamera/MockCamera: False once the poll thread has exited, normally or via live_error."""
        return self._live_thread is not None and self._live_thread.is_alive()

    def set_exposure_us(self, exposure_us: float) -> float:
        if not self.is_connected():
            raise RuntimeError("Cannot set exposure: camera is not connected")
        lo, hi = self._EXPOSURE_RANGE_US
        clamped_us = min(max(float(exposure_us), lo), hi)
        self._cap.set(cv2.CAP_PROP_EXPOSURE, self._us_to_driver_units(clamped_us))
        return self._driver_units_to_us(self._cap.get(cv2.CAP_PROP_EXPOSURE))

    def get_exposure_us(self) -> float:
        return self._driver_units_to_us(self._cap.get(cv2.CAP_PROP_EXPOSURE))

    def get_exposure_range_us(self) -> Tuple[float, float]:
        return self._EXPOSURE_RANGE_US

    def set_gain(self, gain: float) -> Optional[float]:
        if not self.is_connected():
            raise RuntimeError("Cannot set gain: camera is not connected")
        if self._gain_range is None:
            return None
        lo, hi = self._gain_range
        self._cap.set(cv2.CAP_PROP_GAIN, min(max(float(gain), lo), hi))
        return float(self._cap.get(cv2.CAP_PROP_GAIN))

    def get_gain(self) -> Optional[float]:
        if not self.is_connected():
            raise RuntimeError("Cannot read gain: camera is not connected")
        if self._gain_range is None:
            return None
        return float(self._cap.get(cv2.CAP_PROP_GAIN))

    def get_gain_range(self) -> Optional[Tuple[float, float]]:
        return self._gain_range

    def get_bit_depth(self) -> int:
        if not self.is_connected():
            raise RuntimeError("Cannot read bit depth: camera is not connected")
        return self._BIT_DEPTH

    def get_frame(self) -> Optional[np.ndarray]:
        if not self.is_connected():
            return None
        ok, bgr = self._cap.read()
        if not ok:
            return None
        return self._to_gray_u16(bgr)

    def start_live(self, on_frame: Callable[[np.ndarray], None]) -> None:
        if not self.is_connected():
            raise RuntimeError("Cannot start live view: camera is not connected")

        self.live_error = None
        self._stop_flag.clear()

        def _poll_loop():
            # cv2.VideoCapture.read() blocks until the driver has a
            # frame ready, so there's no need for a manual poll-sleep
            # cadence here the way ThorlabsCamera/MockCamera need one --
            # read() itself paces the loop to the device's frame rate.
            consecutive_failures = 0
            try:
                while not self._stop_flag.is_set():
                    ok, bgr = self._cap.read()
                    if not ok:
                        consecutive_failures += 1
                        if consecutive_failures >= self._MAX_CONSECUTIVE_READ_FAILURES:
                            raise RuntimeError(
                                "Webcam read failed repeatedly; device may have been disconnected"
                            )
                        continue
                    consecutive_failures = 0
                    on_frame(self._to_gray_u16(bgr))
            except Exception as exception:
                # Record and stop -- is_live() reflects the dead thread;
                # no re-raise, nothing catches it here.
                self.live_error = exception
                self._stop_flag.set()

        self._live_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._live_thread.start()

    def stop_live(self) -> None:
        self._stop_flag.set()
        if self._live_thread is not None:
            self._live_thread.join(timeout=1.0)
            self._live_thread = None

    # -- helpers --

    @staticmethod
    def _to_gray_u16(bgr: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return gray.astype(np.uint16)

    def _probe_gain_range(self) -> Optional[Tuple[float, float]]:
        """
        Runs once, at connect() time: nudge CAP_PROP_GAIN and check
        whether the read-back actually moved, then restore whatever was
        there before. If it never moves, treat gain as unsupported
        (returns None) rather than advertising a range nothing backs.
        """
        baseline = self._cap.get(cv2.CAP_PROP_GAIN)
        probe = baseline + 1.0
        self._cap.set(cv2.CAP_PROP_GAIN, probe)
        after = self._cap.get(cv2.CAP_PROP_GAIN)
        self._cap.set(cv2.CAP_PROP_GAIN, baseline)  # restore before doing anything else

        if math.isclose(after, baseline, abs_tol=1e-6):
            return None
        return (0.0, 255.0)  # conventional placeholder -- OpenCV has no range query to read this from

    @staticmethod
    def _us_to_driver_units(exposure_us: float) -> float:
        seconds = max(exposure_us, 1.0) / 1e6
        return math.log2(seconds)

    @staticmethod
    def _driver_units_to_us(value: float) -> float:
        seconds = 2.0**value
        return max(seconds * 1e6, 1.0)
