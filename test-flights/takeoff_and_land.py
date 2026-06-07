from djitellopy import Tello

tello = Tello()

try:
    tello.connect()
    print("Battery:", tello.get_battery())
    tello.takeoff()
    tello.land()

except BaseException as e:
    print(f"{e}\n\x1bIf this is a connection issue, make sure you are connected to the drone wifi, your VPN and firewall are off, and there are no other processes running on the port. (use `ss -tulpn` to diagnose) \x1b[0m")
