from djitellopy import Tello


def get_drone():
    tello = Tello()

    try:
        tello.connect()
        battery_level = tello.get_battery() # Percentage?
        if(battery_level < 30):
            print("\x1b[31;1mWARNING\x1b[0;31m 🪫Battery Low,",end="")
        elif(battery_level < 55):
            print("\x1b[33m🔋",end="")
        else:
            print("\x1b[32m🔋",end="")
        print("Battery:", battery_level, "\x1b[0m")

        return tello
    except BaseException as e:
        print(f"\n\n============= FLIGHT FAILED =============\n\x1b[31mError:\x1b[1m {e}\x1b[0m\nIf this is a connection issue, make sure you are \x1b[1mconnected to the drone wifi\x1b[0m, your \x1b[1mVPN\x1b[0m and \x1b[1mfirewall\x1b[0m are off, and there are no other processes running on the port. (use `ss -tulpn` to diagnose, need ports 8889, 8890, 11111) \x1b[0m")
