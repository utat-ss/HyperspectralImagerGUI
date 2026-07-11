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
Run the camera test scripts separately to verify each camera connection.
