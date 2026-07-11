import sys

from PIL import Image

from pypylon import pylon

#######################################################
#                BASLER CAMERA TESTING                #
#######################################################
if __name__ == '__main__':
    camera = None

    try:
        # Create pylon transport layer factory object
        # Used to discover + create camera objects
        tl_factory = pylon.TlFactory.GetInstance()

        # Find all connected Basler cameras (should only be one)
        devices = tl_factory.EnumerateDevices()

        if not devices:
            sys.exit('ERR: No available cameras, check connection (i.e. drivers, usb)')
    
        print(f'Connected Camera: {devices[0].GetModelName()} (Serial: {devices[0].GetSerialNumber()})')

        # Open camera and set properties
        camera = pylon.InstantCamera(tl_factory.CreateDevice(devices[0]))
        camera.Open()

        camera.ExposureTime.SetValue(100000)
        camera.TriggerSelector.SetValue('FrameStart')
        camera.TriggerMode.SetValue('On')
        camera.TriggerSource.SetValue('Software')

        # Capture image
        camera.StartGrabbing(maxImages=1, strategy=pylon.GrabStrategy_OneByOne)
        camera.ExecuteSoftwareTrigger()

        timeout_ms = 5000
        grab_result = camera.RetrieveResult(timeout_ms, pylon.TimeoutHandling_ThrowException)

        try:
            if grab_result.GrabSucceeded():
                image_data = grab_result.Array.copy()

                img = Image.fromarray(image_data)
                img.save('basler.png')

                print('One image saved!')
            
            else:
                sys.exit(f'ERR: {grab_result.ErrorCode} {grab_result.ErrorDescription}')

        finally:
            grab_result.Release()

    finally: # Clean-up
        if camera is not None:
            if camera.IsGrabbing():
                camera.StopGrabbing()

            if camera.IsOpen():
                camera.Close()

    print('Basler camera testing successful!')