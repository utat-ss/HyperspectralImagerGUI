# Project 2 — Spectrometer GUI: requirement status

Handoff document for PAY-Systems. Every written requirement and every element of the
spec document's GUI mockups, mapped to the module that implements it, the test that
proves it, and its current state.

**Updated at the end of every phase.** Last updated: end of Phase 2.5.

Legend: **Done** · **Partial** · **Not started** · **Blocked** (waiting on
information or hardware nobody on this team currently has)

---

## Written requirements

| # | Requirement | Implemented in | Test | State |
|---|---|---|---|---|
| R1 | Windows 10/11 desktop app | `src/gui/app.py` | manual | **Partial** — runs from source, not packaged |
| R2 | Standalone `.exe` via PyInstaller, no Python needed | — | — | **Not started** (Phase 9) |
| R3 | "Connect to Device" tab | — | — | **Not started** (Phase 2) |
| R4 | Connect to Thorlabs CS135MUN (VNIR) | `core/thorlabs_camera.py` | `tests/test_camera_contract.py` (skips, no hardware) | **Done**, unverified on hardware |
| R5 | Connect to FLIR Tau (SWIR), file-based driver | — | — | **Blocked** — SD-card layout, file naming, image format and exposure command all unknown (Phase 7) |
| R6 | Connection indicator LED | — | — | **Not started** — mockup wants three states, not a binary LED; see open question 7 (Phase 2) |
| R7 | Semi-live viewing >= 10 fps | `gui/camera_session.py` | fps regression test (Phase 3) | **Partial** — satisfied today; no test guards it yet |
| R8 | Live view of raw 2D sensor frame | `gui/image_view.py` | — | **Done** |
| R9 | User-selected line cross-section | — | — | **Not started** (Phase 3) |
| R10 | Automatic row binning to a spectral axis | `gui/spectrum_extraction.py` | — | **Partial** — `mean(axis=0)` placeholder, no calibration |
| R11 | Live spectrum side by side with the frame | `gui/spectrum_view.py` + `main_window.py` | — | **Done** |
| R12 | Spectral calibration applied to the spectrum | — | — | **Not started** (Phase 5) |
| R13 | Toggle: no calibration vs each algorithm | — | — | **Not started** (Phase 4) |
| R14 | Control panel: exposure, adjustable on the fly | `gui/controls_panel.py` | contract tests | **Done** |
| R15 | Control panel: gain (optional) | `gui/controls_panel.py` | contract tests | **Done** — hides itself when unsupported |
| R16 | Control panel: frame rate | `core/camera_interface.py` + all 4 backends | `test_set_frame_rate_hz_clamps_into_the_advertised_range` | **Partial** — contract + backends done (Phase 1); GUI control pending (Phase 2) |
| R17 | Spectrum panel: manual spectral range + intensity scale, else autoscale | — | — | **Not started** (Phase 4) |
| R18 | Spectrum panel: axis adjustment and markers | — | — | **Not started** (Phase 4) |
| R19 | Calibration: read reference spectrum CSV | — | — | **Not started**; CSV convention will be **invented** (Phase 5) |
| R20 | Calibration Method 1 + Method 2 | — | — | **Not started** (Phase 5) |
| R21 | Pixel-to-wavelength mapping calculation | — | — | **Not started** (Phase 5) |
| R22 | Smile / keystone distortion evaluation | — | — | **Not started**; keystone scope unconfirmed (Phase 5) |
| R23 | Save: unique folder per measurement | `core/measurement_storage.py` | `src/test_measurement.py` | **Partial** — exists, but pushbroom/datacube-shaped |
| R24 | Save: spectrum + raw 2D image + notes/date/params | `core/measurement_storage.py` | `src/test_measurement.py` | **Partial** — no spectrum-as-CSV path |
| R25 | Spectrum in a widely importable format | — | — | **Not started** — **no CSV support anywhere in the repo today** (Phase 6) |
| R26 | Thorlabs `.SPF2` export | — | — | **Blocked** — proprietary, needs a real sample file |
| R27 | Offer several file types per measurement | — | — | **Not started** (Phase 6) |
| R28 | "Process Data" tab: trace math, smoothing, management | — | — | **Not started** (Phase 8, good-to-have) |

## Mockup elements

| Tab | Element | State |
|---|---|---|
| Connect to Device | `VNIR ThorCAM` / `SWIR FLIR Tau` selection | **Not started** |
| Connect to Device | Three-state indicator: Connecting / Ready / Error | **Not started** — deliberately *not* added to the ABC; see the open question below |
| Connect to Device | Device Connection Log | **Not started** |
| Connect to Device | Integration time (msec), Camera gain, Frame rate | **Partial** — exposure/gain exist as sliders, not msec fields; no frame rate |
| Live Data Viewer | Camera feed with draggable slide-adjust line | **Partial** — feed yes, line no |
| Live Data Viewer | Play / pause transport | **Partial** — a Start/Stop Live toggle exists |
| Live Data Viewer | Extracted Spectrum Live View (500–1000 nm, 0–1023) | **Partial** — plot exists; axis is pixel index, not nm |
| Live Data Viewer | Method: Slide-Adjust Line Cross Section | **Not started** |
| Live Data Viewer | Method: Horizontal Binning | **Partial** — this is the current placeholder |
| Live Data Viewer | Method: Horizontal Binning **with Smile Correction** | **Not started** |
| Live Data Viewer | Method: Dark Noise Removal | **Blocked** — no dark frame available |
| Live Data Viewer | Method: Sensor QE Correction | **Blocked** — no QE curve available |
| Live Data Viewer | Adjust Axes: X left/right, Y low/high | **Not started** |
| Live Data Viewer | Marker 1 / Marker 2 with X,Y readout | **Not started** |
| Spec. Calibration | Method 1: Bandpass Filter, with cut-on/cut-off nm | **Not started** |
| Spec. Calibration | Method 2: Spectral Peaks | **Not started** |
| Spec. Calibration | Step-by-step progress checklist | **Not started** |
| Spec. Calibration | Polynomial Fitting Coefficients output | **Not started** |
| Spec. Calibration | Raw pixel→wavelength value list | **Not started** |
| Spec. Calibration | Spectral Smile Coefficients output | **Not started** |
| Spec. Calibration | Calibration saved to file | **Not started** |
| Process Data | Collected Spectra plot, Traces A–D | **Not started** |
| Process Data | Trace math (`C = A - B`) and smoothing | **Not started** |
| Process Data | Save/Load with file-extension dropdown | **Not started** |
| Process Data | File metadata block (header, range, integration time) | **Not started** |

---

## Infrastructure

| Item | State |
|---|---|
| `CameraInterface` contract tests, all backends | **Done** |
| CI (GitHub Actions, `windows-latest`, 3 lanes) | **Done** — Phase 0 |
| Basler backend reachable from the GUI | **Done** — registered in the factory; contract-compliant; 7 emulator tests pass. Awaiting PR #1 owner's review |
| Synthetic spectrograph test rig | **Done** — `core/synthetic_spectrograph.py`, 20 tests |
| Hardware-free demo path for the showcase | **Done** — `python src/gui/app.py spectrograph` |

## Open questions with the lead

1. Bit depth: mockup shows 0–1023 (10-bit); MockCamera and the CS135MUN are 12-bit.
2. Wavelength range: 500–1000 nm for VNIR — confirm; SWIR range unknown.
3. Dark frame and QE curve — do they exist, in what format?
4. Is keystone correction in scope this term, or listed but deferred?
5. **Should both cameras be connected simultaneously?** Blocks Phase 2.
6. Is anyone still building on `controller-layer`?
7. **Where does the Connecting / Ready / Error indicator get its state?** The plan
   plotted a `ConnectionState` enum on `CameraInterface`, but on inspection all three
   states are things the *GUI* already knows without asking a camera: `Connecting` is
   "a `connect()` call is in flight", `Ready` is `is_connected()`, and `Error` is
   "`connect()` raised" — which is also where the exception text for the connection
   log comes from. Putting it on the ABC would add contract surface that every backend
   must implement and none can answer better than the caller. The one case the GUI
   cannot see is a camera that faults *after* connecting, and `CameraSession`'s
   existing `stream_stalled` already covers that without backend-specific attributes.
   **Recommendation: keep it a GUI-side state machine in the connect tab.** Decided in
   Phase 2, which is blocked anyway.
