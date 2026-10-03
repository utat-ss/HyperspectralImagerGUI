# Project 2: Spectrometer GUI requirement status

Handoff document for PAY-Systems. Every written requirement and every element of the
spec document's GUI mockups, mapped to the module that implements it and its current
state.

**Kept up to date as work lands.**

Legend: **Done** · **Partial** · **Not started** · **Blocked** (waiting on
information or hardware nobody on this team currently has)

---

## Written requirements

| # | Requirement | Implemented in | State |
|---|---|---|---|
| R1 | Windows 10/11 desktop app | `src/gui/app.py` | **Partial**: runs from source, not packaged |
| R2 | Standalone `.exe` via PyInstaller, no Python needed | n/a | **Not started** |
| R3 | "Connect to Device" tab | `gui/connect_tab.py` + `gui/main_window.py` | **Done**: device list, per-device connect, multi-connection |
| R4 | Connect to Thorlabs CS135MUN (VNIR) | `core/thorlabs_camera.py` | **Done**, unverified on hardware |
| R5 | Connect to FLIR Tau (SWIR), file-based driver | n/a | **Blocked**: SD-card layout, file naming, image format and exposure command all unknown |
| R6 | Connection indicator LED | `gui/connection_state.py` | **Done**: three lamps (Connecting / Ready / Error), with the state machine in the GUI rather than on the camera ABC |
| R7 | Semi-live viewing >= 10 fps | `gui/camera_session.py` | **Done**: 66 fps end-to-end at 1280x1024 with corrections applied, and a live fps readout in the status bar so a regression shows during a demo |
| R8 | Live view of raw 2D sensor frame | `gui/image_view.py` | **Done** |
| R9 | User-selected line cross-section | `gui/image_view.py` + `core/spectrum_extraction.py` | **Done**: draggable line, verified against synthetic ground truth |
| R10 | Automatic row binning to a spectral axis | `core/spectrum_extraction.py` | **Done**: binning, smile correction, and a calibrated wavelength axis |
| R11 | Live spectrum side by side with the frame | `gui/spectrum_view.py` + `main_window.py` | **Done** |
| R12 | Spectral calibration applied to the spectrum | `core/calibration_methods.py` + `gui/calibration_panel.py` | **Done**: recovers λ(x) to under 0.1 nm against synthetic ground truth |
| R13 | Toggle: no calibration vs each algorithm | `gui/axes_panel.py` + `core/calibration.py` | **Done**: with none loaded it shows pixel index and says so |
| R14 | Control panel: exposure, adjustable on the fly | `gui/controls_panel.py` | **Done** |
| R15 | Control panel: gain (optional) | `gui/controls_panel.py` | **Done**: hides itself when the backend reports no range |
| R16 | Control panel: frame rate | `core/camera_interface.py` + `gui/controls_panel.py` | **Done**: hides itself when the backend reports no range |
| R17 | Spectrum panel: manual spectral range + intensity scale, else autoscale | `gui/axes_panel.py` + `gui/spectrum_view.py` | **Done** |
| R18 | Spectrum panel: axis adjustment and markers | `gui/axes_panel.py` + `gui/spectrum_view.py` | **Done**: 2 draggable markers with live X/Y readouts |
| R19 | Calibration: read reference spectrum CSV | `core/reference_spectrum.py` | **Done**, but the format is **INVENTED** and still needs confirming against a real instrument export |
| R20 | Calibration Method 1 + Method 2 | `core/calibration_methods.py` | **Done**: both implemented, and checked for mutual agreement |
| R21 | Pixel-to-wavelength mapping calculation | `core/calibration_methods.py` | **Done**: coefficients, per-column values, residuals, save/load |
| R22 | Smile / keystone distortion evaluation | `core/calibration_methods.py:estimate_smile` | **Partial**: smile recovered exactly for a 0-8 px bow; **keystone not measured**, and whether it is in scope this term is unconfirmed |
| R23 | Save: unique folder per measurement | `core/measurement_save.py` | **Done**: timestamped folder per measurement, collision-safe |
| R24 | Save: spectrum + raw 2D image + notes/date/params | `core/measurement_save.py` + `gui/save_panel.py` | **Done**: spectrum, raw frame (.npy + preview .png), notes, full metadata |
| R25 | Spectrum in a widely importable format | `core/measurement_save.py` | **Done**: CSV always written, and loads with `np.loadtxt(delimiter=',', comments='#')` and nothing else |
| R26 | Thorlabs `.SPF2` export | n/a | **Blocked**: proprietary format, needs a real sample file |
| R27 | Offer several file types per measurement | `gui/save_panel.py` | **Done**: CSV (mandatory), TSV, JSON, NPY, TXT |
| R28 | "Process Data" tab: trace math, smoothing, management | n/a | **Not built**: the project timeline lists this tab under "good to-haves, save for last". See "Mockup tabs not built" below |

## Mockup elements

| Tab | Element | State |
|---|---|---|
| Connect to Device | `VNIR ThorCAM` / `SWIR FLIR Tau` selection | **Done**: both listed, and SWIR is shown disabled with its reason |
| Connect to Device | Three-state indicator: Connecting / Ready / Error | **Done**: lives in the GUI, not on the camera ABC |
| Connect to Device | Device Connection Log | **Done**: timestamped, and records every connect, disconnect and failure |
| Connect to Device | Integration time (msec), Camera gain, Frame rate | **Partial**: all three exist as sliders, but in the Live Data Viewer tab rather than the Connect tab. Deliberate: R14 requires exposure adjustable *on the fly*, which means next to the image you are judging it by |
| Live Data Viewer | Camera feed with draggable slide-adjust line | **Done** |
| Live Data Viewer | Play / pause transport | **Partial**: a Start/Stop Live toggle exists |
| Live Data Viewer | Extracted Spectrum Live View (500-1000 nm, 0-1023) | **Done**: the axis switches to nm when a calibration is selected |
| Live Data Viewer | Method: Slide-Adjust Line Cross Section | **Done** |
| Live Data Viewer | Method: Horizontal Binning | **Done** |
| Live Data Viewer | Method: Horizontal Binning **with Smile Correction** | **Done**: un-greys once a calibration measures smile |
| Live Data Viewer | Method: Dark Noise Removal | **Partial**: implemented, but **blocked** on a real dark frame, so disabled in the UI |
| Live Data Viewer | Method: Sensor QE Correction | **Done**: vendor curve shipped at `data/qe/python1300_nir_mono.csv`, and enables once a calibration exists. It is the sensor's typical response, not a measured calibration of this camera, so replacing it is a bench task |
| Live Data Viewer | Adjust Axes: X left/right, Y low/high | **Done** |
| Live Data Viewer | Marker 1 / Marker 2 with X,Y readout | **Done** |
| Spec. Calibration | Method 1: Bandpass Filter, with cut-on/cut-off nm | **Done** |
| Spec. Calibration | Method 2: Spectral Peaks | **Done** |
| Spec. Calibration | Step-by-step progress checklist | **Partial**: per-filter capture status and a status line, but not the full checklist widget |
| Spec. Calibration | Polynomial Fitting Coefficients output | **Done** |
| Spec. Calibration | Raw pixel to wavelength value list | **Done** |
| Spec. Calibration | Spectral Smile Coefficients output | **Done** |
| Spec. Calibration | Calibration saved to file | **Done**: JSON, with anchors and residuals |
| Process Data | Collected Spectra plot, Traces A to D | **Not built** |
| Process Data | Trace math (`C = A - B`) and smoothing | **Not built** |
| Process Data | Save/Load with file-extension dropdown | **Partial**: saving is built, as format checkboxes in the Live Data Viewer (see the note below); loading a spectrum back from file is not |
| Process Data | File metadata block (header, range, integration time) | **Done**: written to `metadata.json` and the CSV comment block |

---

## Mockup tabs not built

The mockup shows five tabs. The application ships three.

**Process Data.** The project timeline lists this tab under "good to-haves, save for
last", alongside the file saving section. Saving is built, in the Live Data Viewer.
The rest of the tab is not: Traces A to D, math between traces such as `C = A - B`,
smoothing, and loading a spectrum back from a file. The tab is left out rather than
shipped empty, so the window shows only tabs that do something.

**Close Application.** Left out because closing the window is what its own close
button does, and this would be the only tab that is really a button rather than a
page.

## A note on where saving lives

The mockup puts Save/Load on the **Process Data** tab. It is implemented in the **Live
Data Viewer** instead, because saving what is on screen is a live-view action and
Process Data is a later, good-to-have addition. The requirements it serves (R23 to R25,
and R27) are core and should not wait on it. `gui/save_panel.py` is standalone, so
the Process Data tab can re-parent it rather than growing a second copy.

`core/measurement_save.py` sits alongside `core/measurement_storage.py` rather than
replacing it. That module belongs to PR #3 and models a pushbroom datacube experiment;
this one models a single hand-saved spectrometer measurement. Converging them is a
conversation to have with its owner, not a refactor to do in passing.
