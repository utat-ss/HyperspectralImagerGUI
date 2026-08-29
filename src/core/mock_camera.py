"""
Synthetic CameraInterface backend for development and testing without
physical hardware. Frames carry visible spatial structure (an
asymmetric gradient, a grid, corner markers, a sweeping marker) rather
than flat noise, so display bugs -- axis swaps, stride errors, frozen
frames, wrong bit-depth scaling -- are obvious on screen instead of
hiding inside noise.

Mirrors ThorlabsCamera's live-view threading contract (live_error,
is_live()) so that logic can be exercised without hardware: see
inject_error() to force a poll-thread failure on demand, and the
frame_rate_hz constructor param to drive the drain-without-sleeping
poll loop past its ~100 Hz idle rate.
"""

import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np

from core.camera_interface import CameraInterface


class MockCamera(CameraInterface):
    name = "Mock Camera"

    _BIT_DEPTH = 12
    _MAX_VALUE = (1 << _BIT_DEPTH) - 1  # 4095
    _REFERENCE_EXPOSURE_US = 100_000.0  # exposure at which the pattern hits nominal (unclipped) brightness

    # Plausible CCD-ish bounds -- not real hardware limits, but real
    # enough to exercise the same clamp-and-report contract ThorlabsCamera
    # has against actual firmware, without needing hardware to do it.
    _EXPOSURE_RANGE_US = (10.0, 1_000_000.0)  # 10us .. 1s
    _GAIN_RANGE = (0.0, 10.0)  # brightness multiplier

    # How many frame-periods of backlog the poll loop will race to catch
    # up on after a stall (a slow on_frame callback, a debugger pause)
    # before it just resyncs to "now" -- analogous to a hardware frame
    # buffer overflowing and dropping the oldest frames.
    _MAX_CATCHUP_PERIODS = 32

    def __init__(
        self,
        serial: Optional[str] = None,
        width: int = 640,
        height: int = 480,
        frame_rate_hz: float = 10.0,
    ):
        self._serial = serial
        self._width = width
        self._height = height
        self.frame_rate_hz = frame_rate_hz

        self._connected = False
        self._exposure_us = self._REFERENCE_EXPOSURE_US
        self._gain = 1.0
        self._frame_index = 0

        self._live_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._injected_error: Optional[Exception] = None
        self.live_error: Optional[Exception] = None

        self._pattern = self._build_pattern(width, height)

    # -- CameraInterface --

    def connect(self) -> bool:
        if self._connected:
            raise RuntimeError("Cannot connect: camera is already connected")
        self._connected = True
        return True

    def disconnect(self) -> None:
        self.stop_live()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def is_live(self) -> bool:
        """
        Mirrors ThorlabsCamera.is_live(): False once the poll thread has
        exited, whether via a normal stop_live() or an unhandled/
        injected error (see live_error for the cause).
        """
        return self._live_thread is not None and self._live_thread.is_alive()

    def set_exposure_us(self, exposure_us: float) -> float:
        if not self.is_connected():
            raise RuntimeError("Cannot set exposure: camera is not connected")
        lo, hi = self._EXPOSURE_RANGE_US
        self._exposure_us = min(max(float(exposure_us), lo), hi)
        return self._exposure_us

    def get_exposure_us(self) -> float:
        return self._exposure_us

    def get_exposure_range_us(self) -> Tuple[float, float]:
        return self._EXPOSURE_RANGE_US

    def set_gain(self, gain: float) -> Optional[float]:
        if not self.is_connected():
            raise RuntimeError("Cannot set gain: camera is not connected")
        lo, hi = self._GAIN_RANGE
        self._gain = min(max(float(gain), lo), hi)
        return self._gain

    def get_gain(self) -> Optional[float]:
        return self._gain

    def get_gain_range(self) -> Optional[Tuple[float, float]]:
        return self._GAIN_RANGE

    def get_bit_depth(self) -> int:
        if not self.is_connected():
            raise RuntimeError("Cannot read bit depth: camera is not connected")
        return self._BIT_DEPTH

    def get_frame(self) -> Optional[np.ndarray]:
        if not self.is_connected():
            return None
        frame = self._synthesize_frame(self._frame_index)
        self._frame_index += 1
        return frame

    def start_live(self, on_frame: Callable[[np.ndarray], None]) -> None:
        if not self.is_connected():
            raise RuntimeError("Cannot start live view: camera is not connected")

        self.live_error = None
        self._injected_error = None
        self._stop_flag.clear()

        def _poll_loop():
            frame_period = 1.0 / self.frame_rate_hz
            next_due = time.monotonic()
            try:
                while not self._stop_flag.is_set():
                    if self._injected_error is not None:
                        error, self._injected_error = self._injected_error, None
                        raise error

                    now = time.monotonic()
                    if now < next_due:
                        time.sleep(min(0.01, next_due - now))
                        continue

                    on_frame(self._synthesize_frame(self._frame_index))
                    self._frame_index += 1
                    next_due += frame_period

                    oldest_allowed = now - self._MAX_CATCHUP_PERIODS * frame_period
                    if next_due < oldest_allowed:
                        next_due = oldest_allowed
                    # Don't sleep here -- another frame may already be
                    # due (frame_rate_hz set above the idle poll rate,
                    # or we're still catching up from a stall). This is
                    # the same drain-without-sleeping shape as
                    # ThorlabsCamera's poll loop.
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

    # -- test hook --

    def inject_error(self, error: Optional[Exception] = None) -> None:
        """
        Make the live poll loop raise `error` (default RuntimeError) on
        its next iteration, to exercise live_error / is_live() without
        needing hardware to fail on demand.
        """
        self._injected_error = error or RuntimeError("MockCamera: injected test failure")

    # -- synthetic frame generation --

    def _build_pattern(self, width: int, height: int) -> np.ndarray:
        """
        A static 0..1 template with structure that makes orientation,
        stride, and scaling bugs visually obvious:
        - a shallower gradient across columns than across rows, so a
          transposed or flipped image looks visibly wrong, not just
          dimmer
        - a periodic grid so misalignment/aliasing shows up as moire
        - a bright top-left corner and a dark bottom-right corner, so a
          flip is unambiguous at a glance
        """
        y = np.linspace(0.0, 1.0, height, dtype=np.float32).reshape(-1, 1)
        x = np.linspace(0.0, 1.0, width, dtype=np.float32).reshape(1, -1)
        gradient = 0.3 * x + 0.7 * y  # steeper in y than x -- orientation is unambiguous

        grid_spacing = 32
        grid = np.zeros((height, width), dtype=np.float32)
        grid[::grid_spacing, :] = 1.0
        grid[:, ::grid_spacing] = 1.0

        pattern = np.clip(0.6 * gradient + 0.4 * grid, 0.0, 1.0)

        marker_size = max(4, min(width, height) // 20)
        pattern[:marker_size, :marker_size] = 1.0
        pattern[-marker_size:, -marker_size:] = 0.0
        return pattern

    def _synthesize_frame(self, frame_index: int) -> np.ndarray:
        # Signal scales ~linearly with exposure time and gain, then
        # saturates at the sensor's full-scale value -- the same
        # exposure/brightness relationship a real sensor has.
        exposure_scale = self._exposure_us / self._REFERENCE_EXPOSURE_US
        frame = self._pattern * exposure_scale * self._gain  # fresh array; safe to mutate below

        # A marker that sweeps across the frame each call, so a frozen
        # live view (stale buffer, a thread that died but is still
        # believed to be streaming) is visually obvious rather than
        # looking like a static test image.
        radius = max(4, min(self._width, self._height) // 30)
        cx = int((0.5 + 0.4 * np.sin(frame_index * 0.1)) * self._width)
        cy = int((0.5 + 0.4 * np.cos(frame_index * 0.1)) * self._height)
        y0, y1 = max(0, cy - radius), min(self._height, cy + radius)
        x0, x1 = max(0, cx - radius), min(self._width, cx + radius)
        frame[y0:y1, x0:x1] = exposure_scale * self._gain

        image = np.clip(frame * self._MAX_VALUE, 0, self._MAX_VALUE)
        return image.astype(np.uint16)
