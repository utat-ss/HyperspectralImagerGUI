"""
Shared abstraction so the GUI never needs to know whether it's talking
to the Thorlabs CS135MUN or the FLIR Tau SWIR rig. Same pattern as the
hyperspectral demonstrator's CameraInterface.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional, Tuple
import numpy as np


class CameraInterface(ABC):
    """Base class every camera backend must implement."""

    name: str = "unknown"

    @abstractmethod
    def connect(self) -> bool:
        """Open the connection. Return True on success."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def set_exposure_us(self, exposure_us: float) -> float:
        """
        Request an exposure time in microseconds. The backend may not be
        able to apply the request exactly -- it may clamp to whatever
        get_exposure_range_us() reports, or ignore it entirely on
        hardware that doesn't actually support the change.

        Returns the exposure time the camera actually holds after the
        attempt. Compare it against the requested value to detect
        clamping instead of assuming the request took effect.
        """
        raise NotImplementedError

    @abstractmethod
    def get_exposure_us(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_exposure_range_us(self) -> Tuple[float, float]:
        """
        Return the (min, max) exposure time in microseconds the camera
        will accept, so a caller (e.g. a GUI slider) can bound a control
        up front instead of discovering limits by trial.
        """
        raise NotImplementedError

    def set_gain(self, gain: float) -> Optional[float]:
        """
        Optional. Not all backends support gain control.

        Returns the gain the camera actually holds after the attempt
        (which may differ from the request -- see get_gain_range()), or
        None if this backend has no gain control at all, in which case
        nothing was changed. The default implementation is the "no gain
        control" case: it returns None without touching anything.
        """
        return None

    def get_gain(self) -> Optional[float]:
        """
        Optional counterpart to set_gain(). Returns None if this backend
        has no gain control.
        """
        return None

    def get_gain_range(self) -> Optional[Tuple[float, float]]:
        """
        Optional. Returns the (min, max) gain the camera will accept, or
        None if this backend has no gain control.
        """
        return None

    def set_frame_rate_hz(self, frame_rate_hz: float) -> Optional[float]:
        """
        Optional. Request a frame rate in frames per second.

        Same contract as set_gain(): returns the frame rate the camera
        actually holds after the attempt (which may be clamped -- see
        get_frame_rate_range_hz()), or None if this backend has no frame
        rate control at all, in which case nothing was changed. The
        default implementation is the "no frame rate control" case.

        Note this is the *requested* rate, not the achieved one. Exposure
        time places a hard ceiling on frame rate that the camera will
        enforce regardless of what is requested here, so a caller that
        needs to know what it is really getting must measure delivered
        frames rather than trust this value.
        """
        return None

    def get_frame_rate_hz(self) -> Optional[float]:
        """
        Optional counterpart to set_frame_rate_hz(). Returns None if this
        backend has no frame rate control.
        """
        return None

    def get_frame_rate_range_hz(self) -> Optional[Tuple[float, float]]:
        """
        Optional. Returns the (min, max) frame rate the camera will
        accept, or None if this backend has no frame rate control -- which
        is what a GUI reads to decide whether to show a frame rate control
        at all, exactly as it does with get_gain_range().
        """
        return None

    @abstractmethod
    def get_bit_depth(self) -> int:
        """
        Return the sensor's native ADC bit depth (e.g. 12 for a 12-bit
        sensor). Frames from get_frame()/start_live() may be delivered in
        a wider container dtype (e.g. uint16) than this -- use this value,
        not the container dtype's range, to normalize/stretch pixel
        values for display.

        Raises RuntimeError if the camera is not connected.
        """
        raise NotImplementedError

    @abstractmethod
    def get_frame(self) -> Optional[np.ndarray]:
        """Return the latest 2D frame as a numpy array, or None if unavailable."""
        raise NotImplementedError

    @abstractmethod
    def start_live(self, on_frame: Callable[[np.ndarray], None]) -> None:
        """
        Start continuous acquisition. on_frame is called from a background
        thread/timer with each new frame -- the GUI layer marshals it onto
        the Qt main thread before touching any widgets.
        """
        raise NotImplementedError

    @abstractmethod
    def stop_live(self) -> None:
        raise NotImplementedError
