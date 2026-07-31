"""
Shared helper for making the Thorlabs TSI native DLLs loadable.

Must be called once, before a TLCameraSDK instance is constructed --
TLCameraSDK.__init__() loads thorlabs_tsi_camera_sdk.dll (and its
dependent DLLs) via cdll.LoadLibrary(), which only succeeds if the
dlls/ directory is already on PATH / the DLL search path.

Source: adapted from Thorlabs' Python Toolkit example configure_path().
"""

import os
import sys
from pathlib import Path

_configured = False


def configure_thorlabs_dll_path() -> None:
    """
    Add the bundled dlls/64_lib (or 32_lib, for a 32-bit interpreter)
    directory to PATH and to the process DLL search path. Safe to call
    more than once -- later calls are a no-op.
    """
    global _configured
    if _configured:
        return

    is_64bits = sys.maxsize > 2**32
    lib_dir_name = "64_lib" if is_64bits else "32_lib"

    project_root = Path(__file__).resolve().parents[2]
    dll_dir = project_root / "dlls" / lib_dir_name

    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ["PATH"]

    try:
        # Python 3.8+ dedicated DLL search path API
        os.add_dll_directory(str(dll_dir))
    except AttributeError:
        pass

    _configured = True
