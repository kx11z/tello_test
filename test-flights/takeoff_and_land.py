from djitellopy import Tello
import time

tello = Tello()

try:
    tello.connect()

    print("Battery:", tello.get_battery())

    tello.takeoff()

    time.sleep(2)

    tello.land()
except BaseException as e:
    print(f"{e}")
    print("\x1b[31;1mCan't connect. Are you connected to the drone's network? Is your VPN on?\x1b[0m")
