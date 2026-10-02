# Setup

## Requirements

- Windows
- Python 3.9 or newer
- Python virtual environment

## Set Up Virtual Environment
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Install Drivers
To install the required camera drivers, install the following software (drivers are bundled with the vendor software).

### Thorlabs
Install ThorImageCAM: https://www.thorlabs.com/software-pages/thorcam

### Basler
Install pylon: https://www.baslerweb.com/en/downloads/software/1844667035/

## API Documentation
### Thorlabs
Download and unzip the Windows SDK and Doc. for Scientific Cameras. The documentation can be found at: `Scientific Camera Interfaces\Thorlabs_Camera_Python_API_Reference.pdf`

### Basler
The Python wrapper documentation for pylon is limited; however, the C/C++ API documentation can be found at: https://docs.baslerweb.com/pylonapi/

## Testing

The camera smoke-test scripts verify a connection end to end. Each needs the
matching device physically attached:

```bash
python src/test_thorlabs.py   # saves thorlabs_stretched.png on success
python src/test_basler.py     # saves basler.png on success
```

Both are standalone and print/exit with `ERR: ...` on failure, so they double as
the canonical low-level example of how to drive each SDK.

## Running without hardware

Run `python src/gui/app.py webcam` to launch against any connected webcam via
OpenCV, or `python src/gui/app.py mock` for synthetic frames — both let you
develop and test the GUI with no scientific camera attached.

`python src/gui/app.py spectrograph` is the demo path: synthetic spectrograph
frames built from a known pixel-to-wavelength mapping, with realistic smile,
keystone and sensor noise. Use this to show the spectrometer features working
without an instrument.

The Basler backend is **not** currently selectable: `BaslerCamera` does not implement
the abstract `get_exposure_range_us`, so it cannot be constructed. When its owner lands
that fix it becomes another hardware-free option, since `PYLON_CAMEMU=1` makes pylon
expose an emulated device through the real grab path.

# Using the GUI

```bash
.venv\Scripts\activate
python src/gui/app.py spectrograph
```

The optional argument auto-connects a device and opens on the Live Data Viewer.
Omit it to start on the Connect tab with nothing connected, which is what a real
user sees. Devices: `spectrograph`, `mock`, `webcam`, `thorlabs`.

There are four tabs.

**Connect to Device** — one row per device, each with a Connect button, a
Connecting/Ready/Error lamp, and a status line. Several devices can be connected at
once; the **Display** radio picks which one feeds the live view. Everything that
happens is timestamped into the Device Connection Log, including failures — connecting
`VNIR ThorCAM` with no camera attached turns the lamp red and logs the reason. The
SWIR row is listed but disabled, because its file-based interface is not specified yet.

**Live Data Viewer** — the sensor frame on the left, the extracted spectrum in the
middle, controls on the right.

- **Start Live** begins streaming. Achieved frame rate is shown bottom-right.
- **Exposure / Gain / Frame rate** apply while live. A control is hidden when the
  connected camera does not support it.
- **Spectrum Calculation Method** chooses how the frame becomes a trace.
  *Slide-Adjust Line Cross Section* puts a draggable red line on the frame and reads
  that one row; *Horizontal Binning* averages all rows. Smile, dark and QE corrections
  stay greyed out until the data they need exists — hover for the reason.
- **Adjust Axes** sets explicit X/Y ranges; clear a field or tick Autoscale to go back.
- **Markers 1 and 2** are draggable verticals that read out X and Y.
- **Save Measurement** writes a folder per measurement. See below.

**Spec. Calibration** — turns pixel columns into wavelengths.

- *Method 2 (Spectral Peaks)* is the easier one: **Load reference spectrum**, then
  **Capture calibration lamp frame**, then **Update Spectral Calibration (Method 2)**.
- *Method 1 (Bandpass Filter)* wants a frame captured through each filter of known
  cut-on/cut-off. With only one filter you must also state the dispersion direction —
  two edges fit a straight line equally well in either direction, and a mirrored
  wavelength axis looks entirely plausible.
- Results show the polynomial coefficients, smile coefficients, fit residual and the
  per-column wavelengths, and can be saved to JSON.

Once a calibration is selected the spectrum's x-axis switches to nanometres and the
smile and QE corrections become available.

## Saved measurements

Each save creates its own timestamped folder (default `measurements/`, git-ignored)
holding `spectrum.csv`, the raw frame as `.npy` plus a `.png` preview,
`metadata.json` and `notes.txt`. CSV is always written and loads with
`np.loadtxt(path, delimiter=",", comments="#")`.

**A spectrum saved without a calibration is written against `pixel_index`, not
`wavelength_nm`**, and carries a `# NOT WAVELENGTH CALIBRATED` banner. A file that
claims nanometres it does not have would outlive the session and travel to other tools.

## Known rough edge

Ticking **Sensor QE Correction** divides the trace by a response given in percent,
which shrinks it roughly 60x and drops it to the bottom of a bit-depth-scaled plot.
Tick **Autoscale** to see it. Whether the QE curve should be normalised to a peak of
1.0 instead is still open.
