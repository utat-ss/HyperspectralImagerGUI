import sys
import time

import numpy as np
from PIL import Image

from core.dll_path import configure_thorlabs_dll_path
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK
from thorlabs_tsi_sdk.tl_camera_enums import OPERATION_MODE

#######################################################
#               THORLABS CAMERA TESTING               #
#######################################################
if __name__ == '__main__':
    configure_thorlabs_dll_path()

    sdk = None
    camera = None

    try:
        # Create SDK object that is used to create TLCamera objects
        sdk = TLCameraSDK()

        # Sanity check - serial number of connected Thorlabs camera should be printed
        available_cameras = sdk.discover_available_cameras()

        if not available_cameras:
            sys.exit('ERR: No available cameras, check connection (i.e. drivers, usb)')

        print(f'Connected Camera Serial #s: {available_cameras}')

        # Open camera and set properties
        camera = sdk.open_camera(available_cameras[0])

        camera.operation_mode = OPERATION_MODE.SOFTWARE_TRIGGERED
        camera.image_poll_timeout_ms = 0                 # No blocking when polling for image, used for get_pending_frame_or_null()
        camera.exposure_time_us = 100000                 # Set exposure time in microseconds
        camera.frames_per_trigger_zero_for_unlimited = 1 # One frame per trigger
        camera.arm(2)                                    # Allocate buffer for 1 frame and prepare camera for acquisition

        # Capture image
        camera.issue_software_trigger()
        frame = None

        # Poll until image is captured or timeout
        start = time.time()

        while frame is None: 
            frame = camera.get_pending_frame_or_null()

            if time.time() - start > 5: # Timeout after 5 seconds
                sys.exit('ERR: Camera did not return a frame')

            time.sleep(0.001)

        # Frame is only valid until next call to get_pending_frame_or_null() or until camera is disarmed
        # So a deep copy is needed for long-term use (not necessary here, but still safe)
        image_data = np.copy(frame.image_buffer)

        img8 = ((image_data.astype(np.float32) - image_data.min()) /
                (image_data.max() - image_data.min()) * 255).astype(np.uint8)

        Image.fromarray(img8).save("thorlabs_stretched.png")
        print('One image saved!')

    finally: # Clean-up
        if camera is not None:
            camera.disarm()  # Stop acquisition
            camera.dispose()

        if sdk is not None:
            sdk.dispose()

    print('Thorlabs camera testing successful!')