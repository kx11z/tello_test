from djitellopy import Tello
import time

tello = Tello()

tello.connect()

print("Battery:", tello.get_battery())

tello.takeoff()

time.sleep(2)

tello.move_forward(50)

time.sleep(2)

tello.land()
