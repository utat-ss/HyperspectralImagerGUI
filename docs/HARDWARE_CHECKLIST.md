# Hardware verification checklist

Everything in this project is developed and tested **without hardware**. That buys
correctness of the logic, not correctness of the assumptions about real devices. This
file lists every assumption that needs a physical camera, the exact command or GUI
steps to confirm it, and a pass/fail column.

**The order matters.** Each section depends on the previous one working, so a failure
high in the list invalidates everything below it — stop and fix rather than pressing
on. Budget the session top-to-bottom.

How to use: work down the list, fill in Result and Notes, commit the filled copy (or
paste it into the team log) so the next person knows what was actually confirmed.

Session date: ____________  Operator: ____________  Camera serial: ____________

---

## A. Does the SDK load at all? (blocks everything below)

| # | Assumption | How to confirm | Expect | Result |
|---|---|---|---|---|
| A1 | The bundled DLLs load from `dlls/64_lib` | `python -c "from core.dll_path import configure_thorlabs_dll_path as c; c(); from thorlabs_tsi_sdk.tl_camera import TLCameraSDK; print(TLCameraSDK())"` (run from `src/`) | An SDK object, no `OSError` | ☐ pass ☐ fail |
| A2 | ThorImageCAM is installed and its drivers are present | Device Manager shows the camera, not an unknown device | Camera listed | ☐ pass ☐ fail |
| A3 | The camera is discovered | `python src/test_thorlabs.py` | Prints a serial, saves `thorlabs_stretched.png` | ☐ pass ☐ fail |

**If A fails:** nothing else in this document can be attempted. Most likely causes are
ThorImageCAM not installed, or a 32/64-bit Python mismatch against `dlls/`.

## B. Does the contract hold on real hardware? (blocks C and D)

The contract tests skip without hardware. With a camera attached they run for real —
this is the first time the `ThorlabsCamera` parametrization has ever executed.

| # | Assumption | How to confirm | Expect | Result |
|---|---|---|---|---|
| B1 | All 7 contract tests pass on hardware | `pytest tests/ -v -k thorlabs` | 7 passed, 0 skipped | ☐ pass ☐ fail |
| B2 | `get_exposure_range_us()` reports the CS135MUN's real limits | Note the values printed in B1 | Plausible, non-zero | ☐ pass ☐ fail |
| B3 | Exposure is actually honoured, not silently ignored | Set a short then a long exposure in the GUI; watch image brightness | Visible brightness change | ☐ pass ☐ fail |
| B4 | `get_bit_depth()` returns 12, and frames really use 12 bits | B1's `test_get_bit_depth_matches_actual_frame_dtype_range` | 12, max pixel <= 4095 | ☐ pass ☐ fail |
| B5 | Gain: does the CS135MUN support it? | Is the gain slider visible in the GUI? | Recorded either way | ☐ supported ☐ not |

**Unverified assumption to watch:** `ThorlabsCamera._LIVE_BUFFER_FRAMES = 16` is a
guess. If B/C show dropped frames, this is the first thing to tune.

## C. Does live view sustain the required rate? (blocks the demo)

| # | Assumption | How to confirm | Expect | Result |
|---|---|---|---|---|
| C1 | Live view runs without dropping to a stall | GUI → Start Live, watch 60 s | No "STREAM STALLED" | ☐ pass ☐ fail |
| C2 | **>= 10 fps sustained** through the full extract-and-render path | Read the achieved-fps readout in the UI | >= 10 fps | ☐ pass ☐ fail |
| C3 | The frame buffer does not overflow at the camera's free-running rate | Live view 5 min, watch for stalls | Stable | ☐ pass ☐ fail |
| C4 | Exposure changes apply in near-real-time while live | Drag the exposure slider during live view | Brightness tracks within ~1 s | ☐ pass ☐ fail |

## D. Optics and calibration (the physics — do last, needs the bench)

Requires the spectrometer assembled, a calibration lamp, and a reference spectrum
already measured on a commercial instrument.

| # | Assumption | How to confirm | Expect | Result |
|---|---|---|---|---|
| D1 | The real reference CSV parses | Load it in the Spec. Calibration tab | Parses, plots | ☐ pass ☐ fail |
| D2 | **The invented CSV convention matches the real file** | Compare our reader's assumptions against the actual export | Confirm or correct | ☐ pass ☐ fail |
| D3 | Emission lines are visible and resolved on the sensor | Live view with the lamp | Distinct lines | ☐ pass ☐ fail |
| D4 | Smile is real and measurable | Compare line centre at top vs bottom row | Recorded, in pixels | ☐ ____ px |
| D5 | Keystone magnitude | Same, across the slit | Recorded | ☐ ____ px |
| D6 | Method 1 (Bandpass Filter) recovers a sane mapping | Run it; compare to the reference | Lines land within ____ nm | ☐ pass ☐ fail |
| D7 | Method 2 (Spectral Peaks) recovers a sane mapping | Run it; compare to the reference | Agrees with D6 | ☐ pass ☐ fail |
| D8 | The synthetic generator's smile magnitude is realistic | Compare D4 against the value used in tests | Same order of magnitude | ☐ pass ☐ fail |
| D9 | Actual usable wavelength range | Read off the calibrated axis | ____ to ____ nm | ☐ recorded |
| D10 | A dark frame can be captured | Cap the aperture, save a frame | Usable dark | ☐ pass ☐ fail |

## E. SWIR / FLIR Tau — entirely unverified

**Every item here rests on an invented convention.** Confirm with the PAY
Elec/Firmware team *before* trusting any of it.

| # | Assumption | Expect | Result |
|---|---|---|---|
| E1 | Frames are readable from the SD card as files | — | ☐ pass ☐ fail |
| E2 | Directory layout and file naming | Record the real scheme | ☐ recorded |
| E3 | Image file format and bit depth | Record | ☐ recorded |
| E4 | How exposure is commanded from the laptop | Record | ☐ recorded |
| E5 | Whether new frames can be detected while streaming | — | ☐ pass ☐ fail |

## F. Packaged build

| # | Assumption | How to confirm | Expect | Result |
|---|---|---|---|---|
| F1 | The `.exe` launches with no Python installed | Run on a clean machine | Launches | ☐ pass ☐ fail |
| F2 | **Bundled DLLs are found from `_MEIPASS`** | Connect Thorlabs in the packaged build | Camera found | ☐ pass ☐ fail |
| F3 | A missing camera reports "no camera", not a DLL error | Run the `.exe` with nothing attached | Clean message | ☐ pass ☐ fail |

**F2 is the known risk:** `dll_path.py` resolves DLLs relative to `__file__`, which
under PyInstaller points into the extracted temp directory. Without the `_MEIPASS`
fix, a packaged build finds **no cameras, silently**.
