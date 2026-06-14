from main import get_drone

drone = get_drone()
drone.takeoff()

drone.move_forward(50)
drone.land()