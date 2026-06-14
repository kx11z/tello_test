from main import get_drone

tello = get_drone()


tello.takeoff()

print("Flying first half of the circle (60cm radius)...")
tello.curve_xyz_speed(60, 60, 0, 0, 120, 0, 30)
print("Flying second half of the circle...")
tello.curve_xyz_speed(-60, -60, 0, 0, -120, 0, 30)

tello.land()