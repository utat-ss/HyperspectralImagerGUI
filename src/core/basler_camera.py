"""
Basler backend for CameraInterface, built on pypylon.

Needs the pylon Camera Software Suite installed on the machine as well as
`pip install pypylon`. Set PYLON_CAMEMU=1 to run against pylon's emulated
camera instead of hardware (see test_basler_emulated.py).

Docs: https://docs.baslerweb.com/pylonapi/
      https://docs.baslerweb.com/features
"""

import re
import threading
from typing import Callable, Optional

import numpy as np
from pypylon import pylon

from core.camera_interface import CameraInterface

_DIGITS = re.compile(r"\d+")

# Deliberately excludes packed names like BayerRG12p, which GetArray()
# cannot unpack.
_UNPACKED_BAYER = re.compile(r"^Bayer[A-Z]{2}(\d+)$")

# Newer cameras expose ExposureTime, older GigE ones ExposureTimeAbs.
# Both are in microseconds.
_EXPOSURE_FEATURES = ("ExposureTime", "ExposureTimeAbs")


class BaslerCamera(CameraInterface):
    """
    One Basler camera behind CameraInterface.

    Takes no SDK argument, unlike ThorlabsCamera: pylon keeps a single
    process-wide TlFactory fetched via GetInstance(), so there is no SDK
    object for the caller to own or dispose.

    Constructor options:
      serial        pick a specific camera by serial number; None = first found
      pixel_format  force a PixelFormat string (e.g. "Mono8"); None = choose
                    automatically from color=
      color=True    HxWx3 uint8 RGB (default)
      color=False   HxW at the sensor's native bit depth
    """

    name = "Basler"

    # Packed formats are excluded because GetArray() cannot unpack them.
    _PREFERRED_MONO_FORMATS = ("Mono16", "Mono12", "Mono10", "Mono8")

    _COLOR_BITS_PER_CHANNEL = 8

    _SNAP_TIMEOUT_MS = 5000

    # Also how often the poll thread rechecks _stop_flag, so it bounds how
    # long stop_live() waits for the thread to notice it should quit.
    _LIVE_RETRIEVE_TIMEOUT_MS = 200

    # A healthy thread needs ~200 ms plus one on_frame call to exit;
    # anything near this limit means a callback has hung.
    _STOP_LIVE_TIMEOUT_S = 10.0

    def __init__(
        self,
        serial: Optional[str] = None,
        pixel_format: Optional[str] = None,
        color: bool = True,
    ):
        self._serial = serial
        self._requested_pixel_format = pixel_format
        self._color = color
        self._converter: Optional[pylon.ImageFormatConverter] = None
        self._cam: Optional[pylon.InstantCamera] = None
        self._live_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        # Set by the live thread if it dies, so callers can see the cause.
        self.live_error: Optional[Exception] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open the first matching Basler and configure its pixel format."""
        if self.is_connected():
            raise RuntimeError(
                "Camera is already connected; call disconnect() before connect()"
            )

        tl_factory = pylon.TlFactory.GetInstance()
        devices = tl_factory.EnumerateDevices()
        if not devices:
            raise RuntimeError("No available Basler cameras found")

        device_info = self._select_device(devices)
        self._cam = pylon.InstantCamera(tl_factory.CreateDevice(device_info))
        self._cam.Open()
        try:
            self.name = f"Basler {device_info.GetModelName()}"
            self._configure_pixel_format()
        except Exception:
            # An open Basler is exclusively locked, so a leaked handle
            # would block every later connect().
            self.disconnect()
            raise
        return self.is_connected()

    def _select_device(self, devices) -> pylon.DeviceInfo:
        if self._serial is None:
            return devices[0]
        for device in devices:
            if device.GetSerialNumber() == self._serial:
                return device
        available = ", ".join(device.GetSerialNumber() for device in devices)
        raise RuntimeError(
            f"No Basler camera with serial {self._serial} (available: {available})"
        )

    def disconnect(self) -> None:
        self.stop_live()
        if self._cam is not None:
            if self._cam.IsOpen():
                self._cam.Close()
            self._cam = None

    def is_connected(self) -> bool:
        return self._cam is not None and self._cam.IsOpen()

    def is_live(self) -> bool:
        """
        True only while the live-view thread is running. Goes False after
        stop_live() and also if the thread died -- check live_error then.
        """
        return self._live_thread is not None and self._live_thread.is_alive()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def set_exposure_us(self, exposure_us: float) -> None:
        self._require_connected("set exposure")
        # Auto exposure overwrites or locks the manual value.
        self._disable_auto("ExposureAuto")
        exposure = self._require_feature(_EXPOSURE_FEATURES, "exposure time")
        self._set_numeric(exposure, float(exposure_us), "exposure time (us)")

    def get_exposure_us(self) -> float:
        self._require_connected("read exposure")
        exposure = self._require_feature(_EXPOSURE_FEATURES, "exposure time")
        return float(exposure.GetValue())

    def set_gain(self, gain: float) -> None:
        """Float dB on modern cameras, integer device units on older ones."""
        self._require_connected("set gain")
        self._disable_auto("GainAuto")

        gain_node = self._find_feature("Gain")
        if gain_node is not None:
            self._set_numeric(gain_node, float(gain), "gain (dB)")
            return
        raw = self._require_feature(("GainRaw",), "gain")
        self._set_numeric(raw, int(gain), "gain (raw device units)")

    def get_bit_depth(self) -> int:
        """
        Bits of real signal per delivered pixel, which is not the array
        dtype: 12-bit data arrives in a uint16 container, so the GUI must
        scale by 4095 rather than 65535.
        """
        if not self.is_connected():
            raise RuntimeError("Cannot read bit depth: camera is not connected")

        if self._converter is not None:
            return self._COLOR_BITS_PER_CHANNEL

        # Mono16 on a 12-bit sensor carries 12 real bits; Mono8 carries 8.
        format_bits = self._parse_digits(
            self._cam.PixelFormat.GetValue(), "PixelFormat"
        )
        adc_bit_depth = self._find_feature("ADCBitDepth")
        if adc_bit_depth is None:
            return format_bits
        adc_bits = self._parse_digits(adc_bit_depth.GetValue(), "ADCBitDepth")
        return min(format_bits, adc_bits)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def get_frame(self) -> Optional[np.ndarray]:
        """
        Software-trigger a single frame: HxWx3 uint8 in colour mode, HxW in
        mono. None if disconnected or the wait timed out; raises if a frame
        arrived but the grab itself failed.
        """
        if not self.is_connected():
            return None
        if self.is_live():
            raise RuntimeError(
                "Cannot snap a frame while live view is running; call stop_live() first"
            )

        self._set_software_trigger()
        self._cam.StartGrabbing(pylon.GrabStrategy_OneByOne)
        try:
            if not self._cam.WaitForFrameTriggerReady(
                self._SNAP_TIMEOUT_MS, pylon.TimeoutHandling_Return
            ):
                return None

            self._cam.ExecuteSoftwareTrigger()
            result = self._cam.RetrieveResult(
                self._SNAP_TIMEOUT_MS, pylon.TimeoutHandling_Return
            )
            try:
                # A timeout yields an invalid result, and GrabSucceeded()
                # throws on those, so IsValid() has to come first.
                if not result.IsValid():
                    return None
                if not result.GrabSucceeded():
                    raise RuntimeError(
                        f"Basler grab failed: {result.GetErrorCode()} "
                        f"{result.GetErrorDescription()}"
                    )
                return self._to_array(result)
            finally:
                # Buffers come from a fixed pool; not releasing starves it.
                result.Release()
        finally:
            self._cam.StopGrabbing()

    def start_live(self, on_frame: Callable[[np.ndarray], None]) -> None:
        """
        Stream continuously, calling on_frame with each new image.

        on_frame runs on a background thread, so a GUI must marshal the
        frame to its main thread before touching widgets. LatestImageOnly
        drops stale frames rather than queueing them when the consumer is
        slower than the camera.
        """
        self._require_connected("start live view")
        if self.is_live():
            raise RuntimeError("Live view is already running")

        self.live_error = None
        self._set_free_running()
        self._stop_flag.clear()

        self._cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

        def _poll_loop():
            try:
                while not self._stop_flag.is_set():
                    result = self._cam.RetrieveResult(
                        self._LIVE_RETRIEVE_TIMEOUT_MS, pylon.TimeoutHandling_Return
                    )
                    try:
                        # Skip timeouts and transient bad transfers.
                        if result.IsValid() and result.GrabSucceeded():
                            on_frame(self._to_array(result))
                    finally:
                        result.Release()
            except Exception as exception:
                # An exception on a background thread reaches no caller,
                # so record it instead of letting it vanish.
                self.live_error = exception
                self._stop_flag.set()

        self._live_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._live_thread.start()

    def stop_live(self) -> None:
        """
        Stop live view, waiting for the poll thread to actually exit.

        StopGrabbing() must not run while that thread is inside
        RetrieveResult, and _live_thread must not be cleared while it is
        alive or is_live() would report False about a thread still calling
        on_frame. Setting _stop_flag does not interrupt the thread; it only
        notices between frames, so a slow callback just makes this wait
        longer. One that never returns is a bug, so past
        _STOP_LIVE_TIMEOUT_S this raises rather than lying about the state.
        """
        self._stop_flag.set()

        thread = self._live_thread
        if thread is not None:
            thread.join(self._STOP_LIVE_TIMEOUT_S)
            if thread.is_alive():
                raise RuntimeError(
                    f"Live view thread did not stop within "
                    f"{self._STOP_LIVE_TIMEOUT_S:g}s; the on_frame callback "
                    f"is most likely blocked. Camera left streaming."
                )
            self._live_thread = None

        if self._cam is not None and self._cam.IsGrabbing():
            self._cam.StopGrabbing()

    def _to_array(self, result) -> np.ndarray:
        """
        GetArray() copies into Python-owned memory, so the array stays
        valid after result.Release().
        """
        if self._converter is None:
            return result.GetArray()
        return self._converter.Convert(result).GetArray()

    # ------------------------------------------------------------------
    # Pixel format and colour conversion
    # ------------------------------------------------------------------

    def _configure_pixel_format(self) -> None:
        # GetSettableValues() reflects what is legal right now; Symbolics
        # can list formats the current configuration blocks.
        available = self._cam.PixelFormat.GetSettableValues()
        chosen = self._choose_pixel_format(available)
        self._cam.PixelFormat.SetValue(chosen)
        self._configure_converter(chosen)

    def _choose_pixel_format(self, available) -> str:
        if self._requested_pixel_format is not None:
            if self._requested_pixel_format not in available:
                raise RuntimeError(
                    f"{self.name} does not support pixel format "
                    f"{self._requested_pixel_format} (supports: {', '.join(available)})"
                )
            return self._requested_pixel_format

        if self._color:
            bayer = self._cheapest_bayer_format(available)
            if bayer is not None:
                return bayer
            raise RuntimeError(
                f"{self.name} offers no unpacked Bayer pixel format, so it "
                f"cannot deliver colour; construct with color=False "
                f"(supports: {', '.join(available)})"
            )

        for pixel_format in self._PREFERRED_MONO_FORMATS:
            if pixel_format in available:
                return pixel_format
        raise RuntimeError(
            f"{self.name} offers no unpacked mono pixel format "
            f"(supports: {', '.join(available)})"
        )

    @staticmethod
    def _cheapest_bayer_format(available) -> Optional[str]:
        """
        Fewest bits, not most: _configure_converter() demosaics to RGB8
        either way, so deeper Bayer only pays USB bandwidth for bits that
        are discarded on arrival -- roughly 5.6 MB vs 11 MB per frame on
        the a2A2600. Pass pixel_format= explicitly to override.
        """
        matches = (_UNPACKED_BAYER.match(entry) for entry in available)
        candidates = [(int(m.group(1)), m.group(0)) for m in matches if m]
        return min(candidates)[1] if candidates else None

    def _configure_converter(self, pixel_format: str) -> None:
        """Build a Bayer -> RGB8 converter, or clear it for mono formats."""
        if not _UNPACKED_BAYER.match(pixel_format):
            self._converter = None
            return
        converter = pylon.ImageFormatConverter()
        converter.OutputPixelFormat = pylon.PixelType_RGB8packed
        converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
        self._converter = converter

    def _set_software_trigger(self) -> None:
        if not self._cam.TriggerSelector.CanSetValue("FrameStart"):
            raise RuntimeError(
                f"{self.name} has no FrameStart trigger, so it cannot be "
                "software-triggered for a single frame"
            )
        self._cam.TriggerSelector.SetValue("FrameStart")
        self._cam.TriggerMode.SetValue("On")
        self._cam.TriggerSource.SetValue("Software")

    def _set_free_running(self) -> None:
        # Every selector, not just FrameStart: any one left armed can
        # still stall the stream.
        for selector in self._cam.TriggerSelector.GetSettableValues():
            self._cam.TriggerSelector.SetValue(selector)
            if self._cam.TriggerMode.CanSetValue("Off"):
                self._cam.TriggerMode.SetValue("Off")

        # SingleFrame would deliver one image and stop.
        acquisition_mode = self._find_feature("AcquisitionMode")
        if acquisition_mode is not None and acquisition_mode.CanSetValue("Continuous"):
            acquisition_mode.SetValue("Continuous")

    def _disable_auto(self, feature: str) -> None:
        auto = self._find_feature(feature)
        if auto is not None and auto.CanSetValue("Off"):
            auto.SetValue("Off")

    # ------------------------------------------------------------------
    # GenICam feature helpers
    # ------------------------------------------------------------------

    def _find_feature(self, *names: str):
        """
        First feature this camera actually has, or None.

        hasattr() is useless here: pypylon returns a placeholder node for
        any name at all, so only IsReadable()/IsWritable() tell the truth.
        """
        for name in names:
            node = getattr(self._cam, name)
            if node.IsReadable() or node.IsWritable():
                return node
        return None

    def _require_feature(self, names, description: str):
        node = self._find_feature(*names)
        if node is None:
            raise RuntimeError(
                f"{self.name} exposes no {description} feature "
                f"(looked for: {', '.join(names)})"
            )
        return node

    def _require_connected(self, action: str) -> None:
        if not self.is_connected():
            raise RuntimeError(f"Cannot {action}: camera is not connected")

    @staticmethod
    def _set_numeric(node, value, description: str) -> None:
        """
        Write a numeric feature, reporting failures as RuntimeError.

        genicam's OutOfRangeException does not inherit from RuntimeError,
        so GUI code wrapping a slider in `except RuntimeError` would miss
        it entirely. The range check exists to give a readable message;
        the try/except also covers increment violations and any other
        constraint the node enforces.
        """
        low, high = node.GetMin(), node.GetMax()
        if not low <= value <= high:
            raise RuntimeError(
                f"{description} of {value:g} is outside this camera's "
                f"allowed range {low:g} to {high:g}"
            )
        try:
            node.SetValue(value)
        except Exception as error:
            raise RuntimeError(
                f"Camera rejected {description} of {value:g}: {error}"
            ) from error

    @staticmethod
    def _parse_digits(value: str, feature: str) -> int:
        """'Mono12' -> 12."""
        match = _DIGITS.search(value)
        if match is None:
            raise RuntimeError(f"Cannot read bit depth from {feature} value {value}")
        return int(match.group())
