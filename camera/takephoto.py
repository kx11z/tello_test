import time
import cv2
from main import get_drone

tello = get_drone()
tello.streamon()

# 1. Get the frame reader object
frame_read = tello.get_frame_read()

# 2. WAIT for the background thread to catch the UDP stream and decode the first frame
print("Warming up camera stream...")
time.sleep(2.0) 

# 3. Grab the active frame and save
frame = frame_read.frame
if frame is not None and frame.size > 0:
    cv2.imwrite("picture.png", frame)
    print("Picture saved successfully.")
else:
    print("Frame was still empty.")

# 4. Shut it down
tello.streamoff()