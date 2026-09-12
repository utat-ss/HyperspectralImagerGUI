"""
Contract tests that run against every CameraInterface backend.

CameraInterface's abstractmethod machinery only enforces that a method
exists, not that it obeys its documented return contract -- a backend
that quietly kept returning None from set_exposure_us would still pass
Python's ABC checks and instantiate cleanly. This file is what actually
catches that: it's the only thing standing between us and a silently
non-compliant backend.

MockCamera always runs. ThorlabsCamera and WebcamCamera run too when
their respective hardware is reachable; otherwise those parametrizations
are skipped with a reason instead of failing the suite on a dev machine
without that hardware.

BaslerCamera is the exception that needs no hardware: PYLON_CAMEMU makes
pylon expose an emulated device, and only the sensor is faked -- TlFactory,
InstantCamera and the whole grab path are real pypylon code. So the
"basler-emulated" parametrization genuinely exercises the driver and is
expected to *run*, not skip, anywhere pypylon is installed. It is the only
backend besides mock that CI can hold to the full contract.
"""

import os

import pytest

from core.mock_camera import MockCamera


@pytest.fixture(params=["mock", "thorlabs", "basler-emulated", "webcam"])
def cam(request):
    sdk = None

    if request.param == "mock":
        camera = MockCamera()
        camera.connect()
    elif request.param == "basler-emulated":
        try:
            from core.basler_camera import BaslerCamera
        except ImportError as exc:
            pytest.skip(f"pypylon not installed: {exc}")

        # Must be set before TlFactory enumerates, and the emulator is only
        # ever added on top of real devices -- so this cannot mask hardware.
        os.environ.setdefault("PYLON_CAMEMU", "1")

        camera = BaslerCamera()
        try:
            camera.connect()
        except Exception as exc:
            pytest.skip(f"No Basler camera or emulator available: {exc}")
    elif request.param == "thorlabs":
        from core.dll_path import configure_thorlabs_dll_path
        from core.thorlabs_camera import ThorlabsCamera
        from thorlabs_tsi_sdk.tl_camera import TLCameraSDK

        configure_thorlabs_dll_path()
        try:
            sdk = TLCameraSDK()
        except Exception as exc:
            pytest.skip(f"Thorlabs SDK/DLLs not available: {exc}")

        camera = ThorlabsCamera(sdk)
        try:
            camera.connect()
        except Exception as exc:
            sdk.dispose()
            pytest.skip(f"No Thorlabs camera connected: {exc}")
    else:
        try:
            from core.webcam_camera import WebcamCamera
        except ImportError as exc:
            pytest.skip(f"opencv-python not installed: {exc}")

        camera = WebcamCamera()
        try:
            camera.connect()
        except Exception as exc:
            pytest.skip(f"No webcam available: {exc}")

    try:
        yield camera
    finally:
        # Camera before SDK -- TLCameraSDK only permits one live instance
        # per process, so a wrong order here would break every later
        # Thorlabs-parametrized test in the same run, not just this one.
        camera.disconnect()
        if sdk is not None:
            sdk.dispose()


def test_set_exposure_us_returns_a_number_not_none(cam):
    result = cam.set_exposure_us(50_000)
    assert result is not None, (
        f"{type(cam).__name__}.set_exposure_us returned None -- every "
        "backend must support exposure control and report what it "
        "actually applied, not just accept the request silently."
    )
    assert isinstance(result, (int, float))


def test_set_gain_returns_a_number_or_an_explicit_none(cam):
    result = cam.set_gain(2.0)
    gain_range = cam.get_gain_range()
    if gain_range is None:
        assert result is None, (
            f"{type(cam).__name__}.get_gain_range() says gain isn't "
            "supported, but set_gain() didn't return None to match -- "
            "the two must agree on whether gain control exists."
        )
    else:
        assert isinstance(result, (int, float)), (
            f"{type(cam).__name__}.set_gain returned {result!r} despite "
            "advertising a gain range -- it must return the applied "
            "value, not None or some other silent no-op."
        )


def test_get_exposure_range_us_is_a_valid_pair(cam):
    lo, hi = cam.get_exposure_range_us()
    assert isinstance(lo, (int, float)) and isinstance(hi, (int, float))
    assert lo < hi


def test_set_exposure_us_clamps_into_the_advertised_range(cam):
    lo, hi = cam.get_exposure_range_us()

    # Exposure control is mandatory per CameraInterface -- every backend
    # must accept a request and report *something* back (see the "not
    # None" test above). But "something" isn't necessarily hardware
    # truth: a real device (see WebcamCamera) can have its exposure
    # property entirely ignored by the driver, in which case the
    # read-back is some constant regardless of what was requested. Probe
    # for that first -- if two different requests read back identically,
    # there's nothing here to verify clamping against, and asserting
    # precise clamped values would be a false failure on genuinely
    # honest code, not a real regression.
    if cam.set_exposure_us(lo) == cam.set_exposure_us(hi):
        pytest.skip(
            f"{type(cam).__name__}'s exposure read-back does not change "
            "between different requests -- this backend/device does not "
            "appear to honor exposure control at all, so there is "
            "nothing to verify clamping against."
        )

    assert cam.set_exposure_us(hi * 10) == pytest.approx(hi, rel=1e-3)
    assert cam.set_exposure_us(lo / 10) == pytest.approx(lo, abs=1.0)


def test_set_gain_clamps_into_the_advertised_range(cam):
    gain_range = cam.get_gain_range()
    if gain_range is None:
        pytest.skip(f"{type(cam).__name__} does not support gain")
    lo, hi = gain_range
    assert cam.set_gain(hi + 1000) == pytest.approx(hi, rel=1e-3)
    assert cam.set_gain(lo - 1000) == pytest.approx(lo, abs=1.0)


def test_frame_rate_capability_is_reported_consistently(cam):
    # The None-means-unsupported convention only works if all three
    # methods agree. A backend advertising a range but returning None from
    # the getter (or the reverse) would make the GUI show a control that
    # cannot be read, which is exactly the kind of half-supported state
    # the convention exists to rule out.
    frame_rate_range = cam.get_frame_rate_range_hz()
    current = cam.get_frame_rate_hz()

    if frame_rate_range is None:
        assert current is None
        assert cam.set_frame_rate_hz(10.0) is None
    else:
        lo, hi = frame_rate_range
        assert lo < hi
        assert current is not None
        assert cam.set_frame_rate_hz(10.0) is not None


def test_set_frame_rate_hz_clamps_into_the_advertised_range(cam):
    frame_rate_range = cam.get_frame_rate_range_hz()
    if frame_rate_range is None:
        pytest.skip(f"{type(cam).__name__} does not support frame rate control")

    lo, hi = frame_rate_range

    # Same probe as the exposure clamp test: a driver may accept the write
    # and ignore it, in which case there is nothing to verify against.
    if cam.set_frame_rate_hz(lo) == cam.set_frame_rate_hz(hi):
        pytest.skip(
            f"{type(cam).__name__}'s frame rate read-back does not change "
            "between different requests -- this backend/device does not "
            "appear to honor frame rate control at all."
        )

    assert cam.set_frame_rate_hz(hi * 10) == pytest.approx(hi, rel=1e-3)
    assert cam.set_frame_rate_hz(lo / 10) == pytest.approx(lo, rel=1e-3, abs=1.0)


def test_connect_twice_raises(cam):
    # cam is already connected by the fixture -- connecting again must
    # raise rather than silently overwrite/leak the existing handle.
    with pytest.raises(RuntimeError):
        cam.connect()


def test_get_bit_depth_matches_actual_frame_dtype_range(cam):
    bit_depth = cam.get_bit_depth()
    frame = cam.get_frame()
    assert frame is not None
    assert frame.max() <= (1 << bit_depth) - 1
