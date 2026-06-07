from djitellopy import Tello

tello = Tello()

try:
    tello.connect()
    print("Battery:", tello.get_battery())

    tello.takeoff()

    print("Flying first half of the circle (60cm radius)...")
    tello.curve_xyz_speed(60, 60, 0, 0, 120, 0, 30)
    print("Flying second half of the circle...")
    tello.curve_xyz_speed(-60, -60, 0, 0, -120, 0, 30)

    tello.land()
except BaseException as e:
    print(f"{e}\n\x1bIf this is a connection issue, make sure you are connected to the drone wifi, your VPN and firewall are off, and there are no other processes running on the port. (use `ss -tulpn` to diagnose) \x1b[0m")
