# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GUI/control software for UTAT's hyperspectral imager. It targets interchangeable camera backends (a Thorlabs CS135MUN, a Basler/pypylon camera, a generic webcam, and a synthetic `MockCamera`) behind a shared `CameraInterface` abstraction, and a PySide6 GUI (`src/gui/`) that drives whichever backend is connected without ever knowing which one it is.

## Setup

Windows only, Python 3.9+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Camera drivers must also be installed separately (not just the pip packages):
- Thorlabs: install ThorImageCAM (https://www.thorlabs.com/software-pages/thorcam) — this provides the DLLs the SDK loads.
- Basler: install pylon (https://www.baslerweb.com/en/downloads/software/1844667035/)

Thorlabs API docs ship inside the Windows SDK/Doc download as `Scientific Camera Interfaces\Thorlabs_Camera_Python_API_Reference.pdf`. Basler's Python wrapper (pypylon) has limited docs of its own; use the C/C++ pylon API docs instead: https://docs.baslerweb.com/pylonapi/

## Current work

We are finishing the "Project 2 — Spectrometer GUI" deliverable. Two documents carry
the state, and both are part of the deliverable:

- `docs/STATUS.md` — every written requirement and every mockup element mapped to its
  module, its test, and its state. This is the handoff document for PAY-Systems.
  **Update it at the end of every phase.**
- `docs/HARDWARE_CHECKLIST.md` — every assumption that needs a real device, ordered so
  an early failure doesn't waste a bench session.

**Nobody on this project currently has hardware access.** Every feature must be
demonstrable and testable against synthetic data. Hardware-only code paths stay behind
`camera_factory` with guarded imports and tests that skip cleanly with a reason.

## Running things

Three kinds of verification exist:

- `pytest tests/` runs `CameraInterface` contract tests against every backend (`tests/test_camera_contract.py`). These always exercise `MockCamera`; the `ThorlabsCamera` parametrization runs too if the SDK DLLs load and a real camera is connected, otherwise it skips cleanly with a reason rather than failing.
- **CI** (`.github/workflows/tests.yml`) runs that suite on `windows-latest` in three
  lanes: plain, `PYLON_CAMEMU=1` (pylon's built-in camera emulator, which exercises the
  real pypylon grab path with no hardware), and `QT_QPA_PLATFORM=offscreen` (headless
  Qt). `.github/scripts/verify_skips.py` then asserts what pytest's exit code cannot:
  that nothing failed, that hardware-dependent params skipped **with a reason**, and
  that the no-hardware params actually ran rather than silently vanishing.
- Hardware smoke-test scripts, run directly with a camera physically connected:

```bash
python src/test_thorlabs.py   # saves thorlabs_stretched.png on success
python src/test_basler.py     # saves basler.png on success
```

Both scripts are standalone (`if __name__ == '__main__'`) and print/exit with `ERR: ...` on failure — read them as the canonical low-level example of how to drive each SDK (discover → open → configure → software-trigger → poll for frame → clean up in `finally`).

## Architecture

### DLL path handling (Thorlabs only)
`thorlabs_tsi_sdk` (in `src/thorlabs_tsi_sdk/`) is a vendored copy of Thorlabs' native Python wrapper, not a pip package. It requires the native DLLs in `dlls/64_lib/` (or `dlls/32_lib/` on 32-bit Python) to be on `PATH` / added via `os.add_dll_directory` *before* `TLCameraSDK()` is constructed. `src/test_thorlabs.py:configure_path()` does this relative to its own file location — any new entry point that touches `thorlabs_tsi_sdk` needs to call an equivalent path setup first, or camera discovery will silently find nothing / raise a native load error.

### Camera abstraction (`src/core/`)
- `camera_interface.py` defines `CameraInterface`, an ABC every backend implements: `connect`/`disconnect`/`is_connected`, `set_exposure_us`/`get_exposure_us`/`get_exposure_range_us`, optional `set_gain`/`get_gain`/`get_gain_range` (default `None` = unsupported), `get_bit_depth`, `get_frame` (single snap), and `start_live`/`stop_live` for continuous acquisition.
- Setters return the value the camera actually holds after the attempt, not just `None` — a backend may clamp or ignore a request, and the return value is the only honest way to know. `set_gain` returns `None` specifically to mean "this backend has no gain control," distinct from returning an unchanged number.
- `start_live(on_frame)` runs acquisition on a background thread and invokes `on_frame` off that thread — callers must marshal onto the Qt main thread themselves (see `src/gui/camera_session.py`); the interface deliberately does not do this.
- `thorlabs_camera.py` implements `CameraInterface` for the CS135MUN directly on the vendored `thorlabs_tsi_sdk` (not pylablib — that path was removed). It does not own a `TLCameraSDK` (only one instance may be live per process); callers inject one via the constructor, or use the `open_thorlabs_camera()` context manager for script/CLI use, which owns the SDK and disposes camera-before-SDK.
- `mock_camera.py` implements `CameraInterface` with a synthetic backend (`MockCamera`) — visibly structured frames (gradient, grid, corner markers, a sweeping marker) rather than noise, so display bugs are obvious; a realistic clamped exposure/gain range so the clamp-and-report contract is testable without hardware; and `inject_error()`/`is_live()`/`live_error` to exercise a dead poll thread on demand. It's the default backend for both `tests/` and the GUI.
- `webcam_camera.py` implements `CameraInterface` for a generic UVC/USB webcam via OpenCV (`WebcamCamera`). This is the backend where "the driver may just ignore a request" stops being hypothetical: on at least one real reachable device in this environment, `cv2.CAP_PROP_EXPOSURE`/`CAP_PROP_GAIN` writes silently fail and the read-back never moves off a constant sentinel, regardless of what's requested. `get_gain_range()` is decided by probing (nudge the value at `connect()` time, see if the read-back actually moves) since OpenCV has no capability-query API; exposure's declared range is a documented convention (DirectShow-style log2(seconds) units), not hardware-verified, since OpenCV has no range query either. `start_live` needs no manual poll-sleep cadence — `cv2.VideoCapture.read()` blocks until a frame is ready — but does guard against a device disappearing mid-stream (`_MAX_CONSECUTIVE_READ_FAILURES`), verified by forcibly releasing the capture under a live poll loop and confirming `live_error`/`is_live()` catch it.
- `basler_camera.py` implements `CameraInterface` for a Basler camera on `pypylon`. Unlike `ThorlabsCamera` it takes no SDK argument — pylon keeps a single process-wide `TlFactory` behind `GetInstance()`, so there is no SDK object for a caller to own or dispose. **It can be tested with no hardware:** `PYLON_CAMEMU=1` makes pylon expose an emulated device, and only the sensor is faked — `TlFactory`, `InstantCamera` and the whole grab path are the real pypylon code, so the driver plumbing is genuinely exercised. `src/test_basler_emulated.py` is the standalone checklist for this; `src/test_basler.py` talks to `pypylon` directly and is the low-level reference.

### GUI (`src/gui/`)
- `camera_factory.py` is the only module allowed to name a concrete backend class. It exposes `open_backend(kind: str)`, a context manager that also owns any backend-specific resource lifetime (e.g. Thorlabs' `TLCameraSDK`, via `open_thorlabs_camera()`). Everything downstream only ever holds a `CameraInterface` — no `isinstance`/`hasattr` checks anywhere else in the GUI. Optional capability (e.g. gain) is handled by reading `get_gain_range() is None` and hiding the control, not by checking backend type.
- `camera_session.py` (`CameraSession`, a `QObject`) is the only thing that calls `start_live`/`stop_live`, and decouples camera frame rate from display rate: the background poll thread only ever writes the newest frame into a lock-protected slot; a `QTimer` on the Qt main thread pulls it at a fixed ~30Hz and emits it as a `Signal`. Frames between ticks are overwritten in place, not queued, so a fast backend (`MockCamera` can emit thousands of frames/sec) can't build an unbounded backlog — "keep only the latest, drop the rest" is the default. `stream_stalled` is derived purely from missing frames (no callback for N seconds while live), not from backend-specific attributes like `ThorlabsCamera`/`MockCamera`'s `live_error`/`is_live()`, which are intentionally not part of `CameraInterface` and therefore not touched by GUI code.
- `image_view.py`/`spectrum_view.py` scale their display range from `get_bit_depth()`, not the frame's `uint16` container range or per-frame autoscaling.
- `spectrum_extraction.py` collapses a frame to a 1D trace (`mean(axis=0)`) as a placeholder — there is no slit-orientation or wavelength-calibration data in this repo yet. It's deliberately the only file that assumption lives in.
- Qt binding is PySide6, pinned to `6.7.3` in `requirements.txt` — `6.11.1` fails to import (`DLL load failed while importing QtCore`) on at least one dev machine; verify before bumping.

### Vendored SDK (`src/thorlabs_tsi_sdk/`)
Treat this directory as third-party vendor code (Thorlabs' official Python Toolkit), not project code — avoid modifying it; fix integration issues in `src/core/` or the test scripts instead.

## Rules

### Architecture invariants

These are the load-bearing constraints. If a requirement seems to need one of them
broken, that is a design smell — propose a capability-reporting method on the ABC and
raise it, rather than reaching for `isinstance`/`hasattr`.

- Never add `thorlabs_tsi_sdk` to requirements.txt — it is vendored, not a
  pip package.
- GUI code must never import a vendor SDK directly, and must never name a
  concrete `CameraInterface` backend outside `camera_factory.py`. All camera
  access goes through `CameraInterface`.
- **No `isinstance`/`hasattr` checks on cameras anywhere downstream of the factory.**
  Optional capability is reported *by the contract*: `get_gain_range()` returning
  `None` means "this backend has no gain control", and the GUI hides the control on
  that basis alone. Frame rate follows the identical shape
  (`get_frame_rate_range_hz()`), and new optional capabilities must too. All three
  methods of a capability have to agree — a backend advertising a range while its
  getter returns `None` is a half-supported state the convention exists to rule out,
  and `test_frame_rate_capability_is_reported_consistently` enforces it.
- **Frame rate is the *requested* rate, not the achieved one.** Exposure time puts a
  hard ceiling on frame rate that the camera enforces regardless of the request, so
  anything needing the real number must measure delivered frames. Both Thorlabs and
  Basler also gate the feature behind a separate enable flag
  (`is_frame_rate_control_enabled` / `AcquisitionFrameRateEnable`); the backends turn
  it on as part of honouring a request, because a caller asking for a specific rate is
  asking for the rate to be controlled.
- **Setters return the value the camera actually holds** after the attempt, never
  `None`. A driver may clamp or silently ignore a request, and the return value is the
  only honest way for a caller to find out. `set_gain()` returning `None` is reserved
  to mean "no gain control at all" — distinct from returning an unchanged number.
- **`CameraSession` is the only thing that calls `start_live`/`stop_live`.**
- **A backend's poll thread makes no Qt calls.** It writes the newest frame into a
  lock-protected slot and nothing else; a `QTimer` on the main thread pulls it. Frames
  between ticks are overwritten, not queued, so a fast backend cannot build a backlog.
- `frame.image_buffer` from the TSI SDK is a view into a rotating internal
  buffer. Always `np.copy()` before passing it out of the backend.
- `src/core/` carries no Qt imports. Pure `ndarray -> ndarray` work belongs there, not
  in `src/gui/`.
- Do not edit `src/thorlabs_tsi_sdk/` or `dlls/` — vendored third-party code.

### Git protocol

- **Never `git add`, commit, or open a PR without telling the maintainer first**, and
  never merge to `main`.
- **No Claude authorship or attribution** in commit messages or PR bodies — no
  `Co-Authored-By`, no "Generated with" trailers.
- Fixes to code owned by another teammate's merged PR go in their **own small PR
  against `origin/main`**, described for that owner to review — not folded into an
  unrelated feature branch's history.
- Durable project knowledge belongs in this file or in `docs/`, never in a session-only
  note that teammates cannot see.

## Testing against ground truth

`src/core/synthetic_spectrograph.py` builds spectrograph frames *from* a known
pixel-to-wavelength mapping (plus smile and keystone coefficients, a line list, and a
noise model) and hands back the mapping it used. Extraction, smile-correction and
calibration tests assert recovery against that truth rather than against hand-computed
constants — without it there is no way to check any of that maths, since nobody on the
project has an instrument.

Two things it deliberately does:

- `column_for_wavelength()` returns **NaN** for wavelengths the sensor cannot see.
  `np.interp` would clamp them to column 0, quietly reporting a 410 nm line as sitting
  at the edge of a 500-1000 nm sensor — a wrong answer that looks like a real one.
  `visible_lines()` is the filtered list tests should use.
- `MockCamera(spectrograph=...)` drops the sweeping marker the default pattern uses.
  The marker exists to make a frozen live view obvious, but on a spectrograph image it
  would sit on top of the lines being measured. Frame-to-frame variation comes from
  the noise model instead.

`python src/gui/app.py spectrograph` is the hardware-free demo path — the same
`MockCamera` backend preloaded with `DEMO_TRUTH` (500-1000 nm, Hg-Ar plus hydrogen,
visible smile bow, gentle keystone, sensor-like noise).
