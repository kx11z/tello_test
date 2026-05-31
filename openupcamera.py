from djitellopy import Tello
import cv2
import time

# Connect to Tello
tello = Tello()
tello.connect()

print(f"Battery: {tello.get_battery()}%")

# Start video stream
tello.streamoff()  # clears any previous stream
tello.streamon()

frame_read = tello.get_frame_read()

# Give the stream a moment to start
time.sleep(2)

while True:
    frame = frame_read.frame

    if frame is not None:
        # Fix red/blue color swap if needed
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        cv2.imshow("Tello Camera", frame)

    key = cv2.waitKey(1) & 0xFF

    # Press q to quit
    if key == ord('q'):
        break

# Cleanup
tello.streamoff()
cv2.destroyAllWindows()
