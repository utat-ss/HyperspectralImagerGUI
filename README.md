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

The Basler backend is **not** currently selectable: `BaslerCamera` does not implement
the abstract `get_exposure_range_us`, so it cannot be constructed. Once that is fixed it
becomes another hardware-free option, since `PYLON_CAMEMU=1` makes pylon expose an
emulated device through the real grab path.

# Using the GUI

```bash
.venv\Scripts\activate
python src/gui/app.py mock
```

The optional argument auto-connects a device and opens on the Live Data Viewer.
Omit it to start on the Connect tab with nothing connected, which is what a real
user sees. Devices: `mock`, `webcam`, `thorlabs`.

There are two tabs.

**Connect to Device** — one row per device, each with a Connect button, a
Connecting/Ready/Error lamp, and a status line. Several devices can be connected at
once; the **Display** radio picks which one feeds the live view. Everything that
happens is timestamped into the Device Connection Log, including failures —
connecting the Thorlabs camera with none attached turns the lamp red and logs why.

**Live Data Viewer** — the sensor frame on the left, a trace across it in the
middle, camera controls on the right.

- **Start Live** begins streaming. Achieved frame rate is shown bottom-right.
- **Exposure / Gain / Frame rate** apply while live. A control hides itself when the
  connected camera reports that it does not support it.
