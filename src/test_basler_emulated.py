r"""
Checklist for BaslerCamera against pylon's built-in camera emulator, so it
runs with no hardware attached. The script sets PYLON_CAMEMU=1 itself.

    .\.venv\Scripts\python.exe src\test_basler_emulated.py

The emulator fakes only the sensor -- TlFactory, InstantCamera and the grab
path are the real pypylon code. So a green run verifies driver plumbing
(shapes, timeouts, live thread, error handling) but not the physics. It
cannot check real colour or USB bandwidth, and it has no ADCBitDepth node
while offering Mono16, so mono reports 16-bit here where the a2A2600 will
report 12.
"""

import os
import sys
import threading
import time

from core.basler_camera import BaslerCamera
from core.camera_interface import CameraInterface

LIVE_SECONDS = 1.5

_failures = []


def check(label, actual, expected):
    passed = actual == expected
    print(f"[{'ok  ' if passed else 'FAIL'}] {label}: {actual!r}")
    if not passed:
        _failures.append(label)


def expect_error(label, call):
    try:
        call()
        print(f"[FAIL] {label}: no error raised")
        _failures.append(label)
    except RuntimeError as error:
        print(f"[ok  ] {label} -> {error}")


#######################################################
#          BASLER CAMERA TESTING (EMULATED)           #
#######################################################
if __name__ == '__main__':
    # Read at EnumerateDevices() time, so it must be set before connect().
    os.environ.setdefault('PYLON_CAMEMU', '1')

    # --- 1) Interface conformance ---
    print('--- interface conformance ---')
    check('no unimplemented abstract methods',
          sorted(BaslerCamera.__abstractmethods__), [])
    camera = BaslerCamera()
    check('is a CameraInterface', isinstance(camera, CameraInterface), True)
    check('is_connected before connect', camera.is_connected(), False)

    # --- 2) Connect and configure ---
    print('--- connect and configure ---')
    camera.connect()
    check('is_connected after connect', camera.is_connected(), True)
    print(f'[info] model: {camera.name}, bit depth: {camera.get_bit_depth()}')

    camera.set_exposure_us(25000)
    check('exposure round-trip', camera.get_exposure_us(), 25000.0)
    camera.set_gain(3.0)

    # genicam's own exceptions aren't RuntimeError, so a GUI's
    # `except RuntimeError` would sail straight past them.
    expect_error('absurd exposure rejected as RuntimeError',
                 lambda: camera.set_exposure_us(9.9e11))
    expect_error('negative exposure rejected as RuntimeError',
                 lambda: camera.set_exposure_us(-1))
    check('exposure unchanged after rejection', camera.get_exposure_us(), 25000.0)

    # Colour demosaics to RGB8, so deeper Bayer would only waste bandwidth.
    check('colour streams 8-bit Bayer',
          camera._cam.PixelFormat.GetValue().endswith('8'), True)

    # --- 3) Colour snap ---
    print('--- software-triggered snap (colour is the default) ---')
    frame = camera.get_frame()
    check('colour frame is HxWx3', frame.ndim, 3)
    check('colour frame is 8-bit', frame.dtype.name, 'uint8')
    check('bit depth matches the delivered channels', camera.get_bit_depth(), 8)
    print(f'[info] shape {frame.shape}, dtype {frame.dtype}, '
          f'range {frame.min()}..{frame.max()}')

    # --- 4) Live view ---
    print('--- live view ---')
    frames = []
    lock = threading.Lock()

    def on_frame(new_frame):
        # Runs on BaslerCamera's background thread, hence the lock.
        with lock:
            frames.append(new_frame)

    camera.start_live(on_frame)
    check('is_live while streaming', camera.is_live(), True)
    time.sleep(LIVE_SECONDS)
    camera.stop_live()
    check('is_live after stop_live', camera.is_live(), False)
    check('live_error stayed clear', camera.live_error, None)

    with lock:
        count = len(frames)
        # Identical frames would mean we re-read one stale buffer.
        distinct = len({f.tobytes()[:4096] for f in frames})
    check('received frames', count > 0, True)
    check('frames are not duplicates', distinct == count, True)
    print(f'[info] {count} frames in {LIVE_SECONDS}s (~{count / LIVE_SECONDS:.0f} fps)')

    check('snap works again after live view', camera.get_frame().ndim, 3)

    # --- 5) Guards ---
    print('--- guards ---')
    camera.start_live(on_frame)
    expect_error('get_frame during live view', camera.get_frame)
    expect_error('double start_live', lambda: camera.start_live(on_frame))
    camera.stop_live()

    # --- 6) Badly behaved on_frame callbacks ---
    # A slow callback used to outlast stop_live()'s 1-second join, which
    # then cleared _live_thread (so is_live() lied) and called
    # StopGrabbing() underneath a thread mid-RetrieveResult.
    print('--- a slow callback must not desync stop_live ---')

    def slow(_frame):
        time.sleep(0.8)

    camera.start_live(slow)
    time.sleep(0.3)
    camera.stop_live()
    check('is_live honest after slow callback', camera.is_live(), False)
    check('slow callback caused no error', camera.live_error, None)
    check('camera still grabs after a slow stop', camera.get_frame().ndim, 3)

    print('--- a raising callback must kill the thread, not hide ---')

    def explode(_frame):
        raise ValueError('boom')

    camera.start_live(explode)
    time.sleep(0.8)
    check('is_live after callback raised', camera.is_live(), False)
    check('live_error captured the cause',
          repr(camera.live_error), repr(ValueError('boom')))
    camera.stop_live()

    # --- 7) Disconnected behaviour ---
    print('--- disconnected behaviour ---')
    camera.disconnect()
    check('is_connected after disconnect', camera.is_connected(), False)
    check('get_frame returns None', camera.get_frame(), None)
    expect_error('get_bit_depth when disconnected', camera.get_bit_depth)
    expect_error('set_exposure_us when disconnected',
                 lambda: camera.set_exposure_us(1000))
    expect_error('start_live when disconnected',
                 lambda: camera.start_live(on_frame))
    camera.stop_live()  # must not crash if already disconnected
    print('[ok  ] stop_live on a disconnected camera is a no-op')

    # --- 8) Greyscale mode ---
    print('--- mono mode ---')
    mono = BaslerCamera(color=False)
    mono.connect()
    mono_frame = mono.get_frame()
    check('mono frame is 2D', mono_frame.ndim, 2)
    check('mono keeps the wide container', mono_frame.dtype.name, 'uint16')
    mono.disconnect()

    # --- 9) Explicit pixel_format= overrides colour and mono defaults ---
    print('--- explicit pixel format overrides both ---')
    mono8 = BaslerCamera(pixel_format='Mono8')
    mono8.connect()
    check('Mono8 bit depth', mono8.get_bit_depth(), 8)
    check('Mono8 dtype', mono8.get_frame().dtype.name, 'uint8')
    check('Mono8 stays 2D', mono8.get_frame().ndim, 2)
    mono8.disconnect()

    # --- 10) Failure paths must not leave the device locked ---
    print('--- failure paths ---')
    expect_error('unknown serial rejected',
                 BaslerCamera(serial='does-not-exist').connect)
    bad_format = BaslerCamera(pixel_format='NotAFormat')
    expect_error('unsupported format rejected', bad_format.connect)
    check('failed connect left no device open', bad_format.is_connected(), False)

    print()
    if _failures:
        sys.exit(f'ERR: {len(_failures)} check(s) failed: {", ".join(_failures)}')

    print('Basler camera testing (emulated) successful!')
