import random

from math import pi, sin, cos
from threading import Thread, Timer
from time import time, sleep
from typing import Optional

from views import InterfaceAdapter, PID, Cap, ArmAngles

class RandomAdapter(InterfaceAdapter):
    def on_stop_button(self):
        print("STOP !!!")

    def __init__(self, socketio):
        super().__init__(socketio)

        def f():
            while True:
                sleep(0.1)
                self.push_pos_left(time(), random.randint(0, 100), (cos(time()) + 1) / 2 * 100)
                self.push_pos_right(time(), random.randint(0, 100), (sin(time()) + 1) / 2 * 100)
                self.push_speed_left(time(), random.randint(0, 100), (sin(time()) + 1) / 2 * 100)
                self.push_speed_right(time(), random.randint(0, 100), (cos(time()) + 1) / 2 * 100)

        Thread(target=f).start()

    def on_pid_submission(self, speed_left: PID, speed_right: PID, translation: PID,
                          rotation: PID, cap: Cap) -> None:
        print(cap)

    def on_order_submission(self, speed: Optional[float], position: Optional[float],
                            angle: Optional[float]):
        def send_order():
            print("delayed order submission")

        Timer(2, send_order).start()

    def on_servo_submission(self, left: ArmAngles, centerLeft: ArmAngles, 
                            center: ArmAngles, centerRight: ArmAngles, right: ArmAngles) -> None:
        print("send")
