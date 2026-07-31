"""
Shared abstraction so the GUI never needs to know whether it's talking
to the Thorlabs CS135MUN or the FLIR Tau SWIR rig. Same pattern as the
hyperspectral demonstrator's CameraInterface.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional
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
    def set_exposure_us(self, exposure_us: float) -> None:
        """Set exposure time in microseconds."""
        raise NotImplementedError

    @abstractmethod
    def get_exposure_us(self) -> float:
        raise NotImplementedError

    def set_gain(self, gain: float) -> None:
        """Optional. Not all backends support gain control."""
        pass

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
