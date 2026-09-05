r"""
Checklist for ApplicationController (fake cameras, no hardware).

    .\.venv\Scripts\python.exe src\test_controller.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.camera_interface import CameraInterface
from core.controller import ApplicationController, ControllerError


_failures = []


def check(label, actual, expected):
    passed = actual == expected
    print(f"[{'ok  ' if passed else 'FAIL'}] {label}: {actual!r}")
    if not passed:
        _failures.append(label)


def check_true(label, condition):
    print(f"[{'ok  ' if condition else 'FAIL'}] {label}: {condition!r}")
    if not condition:
        _failures.append(label)


class FakeCamera(CameraInterface):
    """Minimal CameraInterface used so the controller can be tested without SDKs."""

    def __init__(self, name: str, frame: np.ndarray):
        self.name = name
        self._frame = frame
        self._connected = False
        self._exposure_us = 10000.0
        self._gain = 0.0
        self._live = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.frames_issued = 0
        self.fail_next_frame = False

    def connect(self) -> bool:
        self.connect_calls += 1
        self._connected = True
        return True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False
        self._live = False

    def is_connected(self) -> bool:
        return self._connected

    def set_exposure_us(self, exposure_us: float) -> None:
        self._exposure_us = exposure_us

    def get_exposure_us(self) -> float:
        return self._exposure_us

    def set_gain(self, gain: float) -> None:
        self._gain = gain

    def get_frame(self) -> Optional[np.ndarray]:
        if not self._connected:
            return None
        self.frames_issued += 1
        if self.fail_next_frame:
            self.fail_next_frame = False
            return None
        return self._frame.copy()

    def start_live(self, on_frame: Callable[[np.ndarray], None]) -> None:
        self._live = True
        on_frame(self._frame.copy())

    def stop_live(self) -> None:
        self._live = False


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="ctrl_test_"))
    try:
        basler_frame = np.zeros((16, 24, 3), dtype=np.uint8)
        basler_frame[:, :, 1] = 180
        thor_frame = np.arange(36, dtype=np.uint16).reshape(6, 6)

        basler = FakeCamera("Basler Fake", basler_frame)
        thorlabs = FakeCamera("Thorlabs Fake", thor_frame)
        controller = ApplicationController(
            tmp,
            basler=basler,
            thorlabs=thorlabs,
        )

        print("--- connect / settings ---")
        check("basler starts disconnected", controller.is_connected("basler"), False)
        check_true("connect basler", controller.connect("basler"))
        check("basler connect calls", basler.connect_calls, 1)
        check("basler connected", controller.is_connected("basler"), True)
        check_true("connect thorlabs", controller.connect("thorlabs"))
        controller.set_exposure_us("basler", 25000.0)
        check("basler exposure", controller.get_exposure_us("basler"), 25000.0)
        controller.set_gain("thorlabs", 1.5)
        check("thorlabs gain", thorlabs._gain, 1.5)

        print("--- capture requires measurement ---")
        try:
            controller.capture_frame("basler")
            check_true("capture without measurement raises", False)
        except ControllerError:
            check_true("capture without measurement raises", True)

        print("--- create + capture ---")
        measurement = controller.create_measurement(
            measurement_id="meas_ctrl_001",
            name="controller demo",
            comments="unit test",
        )
        check("active id", measurement.id, "meas_ctrl_001")
        check("measurement property", controller.measurement is measurement, True)
        result = controller.capture_frame("basler")
        check("result camera", result.camera, "basler")
        check("result measurement", result.measurement_id, "meas_ctrl_001")
        check("result path", result.relative_path, "raw/basler/frame_000.png")
        check("basler frames issued", basler.frames_issued, 1)
        check("basler frame count on measurement", len(measurement.raw.basler_frames), 1)
        check("acquisition basler name", measurement.acquisition.basler.name, "Basler Fake")
        check("acquisition basler exposure", measurement.acquisition.basler.exposure_us, 25000.0)
        check_true(
            "basler file exists",
            (controller.measurement_dir() / "raw" / "basler" / "frame_000.png").is_file(),
        )

        thor_result = controller.capture_frame("thorlabs")
        check("thorlabs path", thor_result.relative_path, "raw/thorlabs/frame_000.npy")
        check("thorlabs frame count", len(measurement.raw.thorlabs_frames), 1)
        check_true("thorlabs frame values", np.array_equal(thor_result.frame, thor_frame))
        check_true(
            "thorlabs file exists",
            (controller.measurement_dir() / "raw" / "thorlabs" / "frame_000.npy").is_file(),
        )

        print("--- live + guards ---")
        seen = []
        controller.start_live("basler", seen.append)
        check("live delivered a frame", len(seen), 1)
        controller.stop_live("basler")
        check("live stopped", basler._live, False)

        try:
            controller.capture_frame("unknown")
            check_true("unknown camera raises", False)
        except ControllerError:
            check_true("unknown camera raises", True)

        basler.fail_next_frame = True
        try:
            controller.capture_frame("basler")
            check_true("empty frame raises", False)
        except ControllerError:
            check_true("empty frame raises", True)

        controller.disconnect("basler")
        try:
            controller.capture_frame("basler")
            check_true("disconnected capture raises", False)
        except ControllerError:
            check_true("disconnected capture raises", True)

        print("--- load round-trip ---")
        controller.disconnect_all()
        check("basler disconnected", controller.is_connected("basler"), False)
        check("thorlabs disconnected", controller.is_connected("thorlabs"), False)

        loaded_ctrl = ApplicationController(tmp, basler=basler, thorlabs=thorlabs)
        loaded = loaded_ctrl.load_measurement("meas_ctrl_001")
        check("loaded name", loaded.name, "controller demo")
        check("loaded basler frames", loaded.raw.basler_frames, ["raw/basler/frame_000.png"])
        check("loaded thorlabs frames", loaded.raw.thorlabs_frames, ["raw/thorlabs/frame_000.npy"])
        check("loaded basler name", loaded.acquisition.basler.name, "Basler Fake")
        check("loaded basler exposure", loaded.acquisition.basler.exposure_us, 25000.0)
        check("loaded thorlabs name", loaded.acquisition.thorlabs.name, "Thorlabs Fake")

        print("--- missing camera object ---")
        sparse = ApplicationController(tmp, basler=None, thorlabs=thorlabs)
        try:
            sparse.connect("basler")
            check_true("missing basler raises", False)
        except ControllerError:
            check_true("missing basler raises", True)

        print()
        if _failures:
            sys.exit(f"ERR: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        print("Controller testing successful!")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
