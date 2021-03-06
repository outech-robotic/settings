#!venv/bin/python
import os
import random
import string
import struct
from math import pi, sin, cos
from threading import Thread, Timer
from time import time, sleep
from typing import Optional

import can
from flask import Flask
from flask_socketio import SocketIO

from views import register_views, InterfaceAdapter, PID, Cap

CAN_BOARD_ID_WIDTH  = 4
CAN_MSG_WIDTH       = 9
CAN_BOARD_ID_MOTOR  = 15
CAN_CHANNEL_MOTOR   = 0b00
CAN_MSG_COD         = 0b00011  # Encoder positions sent from LL (2x32b signed, left and right positions)
CAN_MSG_STOP        = 0b00000  # Stops robot on the spot, resetting all errors of PIDs
CAN_MSG_POS         = 0b00010  # orders a movement : 1 byte for movement type, and 32 bits signed ticks to move
CAN_MSG_SPEED       = 0b10000  # orders the movement of both wheels at constant speed (2x32bits signed, left and right encoder speeds)
CAN_SPEED_ACCEL_LIM = 0b10011
CAN_KP_ID           = 0b10100  # sets proportionnal constant of PID of ID at first byte of data, value is the following 32 bits (unsigned, 65535 * value in floating point of KP)
CAN_KI_ID           = 0b10101  # same for integral constant
CAN_KD_ID           = 0b10110  # and derivative
CAN_MSG_MCS_MODE    = 0b10111  # Sets motion control mode : data: 1 byte: bit 0 = speed mode on/off, bit 1 = position mode on/off

CAN_MSG_DEBUG_DATA  = 0b10001  # 32b 32b debug data
CAN_MSG_HEARTBEAT   = 0b10010


MCS_MODE_SPEED       = [0b001]
MCS_MODE_TRANSLATION = [0b011]
MCS_MODE_ROTATION    = [0b101]
MCS_MODE_ALL         = [0b111]

CAN_MSG_COD_FULL     = CAN_CHANNEL_MOTOR << CAN_MSG_WIDTH | CAN_MSG_COD<<CAN_BOARD_ID_WIDTH | CAN_BOARD_ID_MOTOR

# PID IDs
PID_LEFT_SPEED = 0
PID_LEFT_POS = 2
PID_RIGHT_SPEED = 1
PID_RIGHT_POS = 3

COD_UPDATE_FREQ = 100  # Hz, encoder positions sent from Motion control board each seconds

# Formatters for data packing
fmt_motor_set_speed = struct.Struct('<ll')  # 32b + 32b signe
fmt_motor_cod_pos = struct.Struct('<ll')  # 32b + 32b signe
fmt_motor_move = struct.Struct('<Bi')
fmt_motor_set_pid = struct.Struct('<Bi')  # 8b + 32b non signes
fmt_motor_lim = struct.Struct('<HHHH')

# Physical constants of the robot
WHEEL_DIAMETER = 73.6  # en mm ; 2400 ticks par tour donc par 2*pi*74 mm
DISTANCE_BETWEEN_WHEELS = 363.0  # en mm
TICKS_PER_TURN = 2400.0
MM_TO_TICK = TICKS_PER_TURN / (pi * WHEEL_DIAMETER)
TICK_TO_MM = (pi * WHEEL_DIAMETER) / TICKS_PER_TURN


# CAN
def send_packet(channel, message_id, board, data=[]):
    can_pkt_id = ((channel << CAN_MSG_WIDTH) | (message_id << CAN_BOARD_ID_WIDTH) | board)
    print(can_pkt_id, " MSG:", data)
    with can.interface.Bus(channel='can0', bustype='socketcan', bitrate=1000000) as bus:
        msg = can.Message(
            arbitration_id=can_pkt_id, data=data, is_extended_id=False
        )
        try:
            bus.send(msg)
            print(can_pkt_id, "sent on", bus.channel_info)
        except can.CanError:
            print(can.CanError)
            print("message_id NOT sent")


def avg_list(values):
    return sum(values) / len(values) if len(values) > 0 else 0


class CANAdapter(InterfaceAdapter):
    """ La classe qu'il faut implem pour s'interfacer avec la page web. """

    def __init__(self, socketio: SocketIO):
        self.setpoint_speed = 0.0
        self.setpoint_pos = None
        self.setpoint_angle = None
        self.avg_left = [0.0 for i in range(10)]
        self.avg_right = [0.0 for i in range(10)]
        self.cod_start_left = 0
        self.cod_start_right = 0
        self.cod_last_left = 0
        self.cod_last_right = 0

        super(CANAdapter, self).__init__(socketio)  # Il faut garder cette ligne.

        # A la place de cette fonction et du thread, on met le code qui recoit les msg CAN et on
        # appelle les fonctions .push_*_*
        def f():
            with can.interface.Bus(channel='can0', bustype='socketcan', bitrate=1000000) as bus:
                for message in bus:
                    # Encoder position
                    if (message.arbitration_id) == CAN_MSG_COD_FULL:
                        posl, posr = fmt_motor_cod_pos.unpack(message.data)
                        posl, posr = posl * TICK_TO_MM, posr * TICK_TO_MM
                        speedl, speedr = ((posl - self.cod_last_left)  * COD_UPDATE_FREQ,
                                          (posr - self.cod_last_right) * COD_UPDATE_FREQ)
                        self.cod_last_left, self.cod_last_right = posl, posr
                        self.avg_left = self.avg_left[1:]
                        self.avg_left.append(speedl)
                        self.avg_right = self.avg_right[1:]
                        self.avg_right.append(speedr)
                        speedl = avg_list(self.avg_left)
                        speedr = avg_list(self.avg_right)
                        t = int(time() * 1000)
                        self.push_speed_left(t, speedl, self.setpoint_speed)
                        self.push_speed_right(t, speedr, self.setpoint_speed)
                        setpoint_left, setpoint_right = 0.0, 0.0
                        if self.setpoint_pos is not None:
                            setpoint_left = self.setpoint_pos
                            setpoint_right = self.setpoint_pos
                        elif self.setpoint_angle is not None:
                            setpoint_left = -self.setpoint_angle
                            setpoint_right = self.setpoint_angle
                        self.push_pos_left(t, posl-self.cod_start_left, setpoint_left)
                        self.push_pos_right(t, posr-self.cod_start_right, setpoint_right)
                        # 1er argument le temps (time.time()), 2eme la valeur, 3eme la consigne.

                    # Stop message received
                    elif (message.arbitration_id>>CAN_BOARD_ID_WIDTH) == 0b0000001:
                        print("#############\n\n\n MESSAGE :", message.arbitration_id, " ", message.data, "\n\n\n#############") 


        Thread(target=f).start()

    def on_pid_submission(self, speed_left: PID, speed_right: PID, translation: PID,
                          rotation: PID, cap: Cap) -> None:
        # Ici tu implem l'envoi des paquets sur le CAN.
        print(f"Got PID {translation} {rotation} {speed_left} {speed_right}")

        translation.p = round(translation.p * 65535)
        translation.i = round(translation.i * 65535)
        translation.d = round(translation.d * 65535)

        rotation.p = round(rotation.p * 65535)
        rotation.i = round(rotation.i * 65535)
        rotation.d = round(rotation.d * 65535)

        speed_left.p = round(speed_left.p * 65535)
        speed_left.i = round(speed_left.i * 65535)
        speed_left.d = round(speed_left.d * 65535)

        speed_right.p = round(speed_right.p * 65535)
        speed_right.i = round(speed_right.i * 65535)
        speed_right.d = round(speed_right.d * 65535)

        send_packet(CAN_CHANNEL_MOTOR, CAN_KP_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_LEFT_POS, int(translation.p)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KI_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_LEFT_POS, int(translation.i)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KD_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_LEFT_POS, int(translation.d)))

        send_packet(CAN_CHANNEL_MOTOR, CAN_KP_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_RIGHT_POS, int(rotation.p)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KI_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_RIGHT_POS, int(rotation.i)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KD_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_RIGHT_POS, int(rotation.d)))

        send_packet(CAN_CHANNEL_MOTOR, CAN_KP_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_LEFT_SPEED, int(speed_left.p)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KI_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_LEFT_SPEED, int(speed_left.i)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KD_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_LEFT_SPEED, int(speed_left.d)))

        send_packet(CAN_CHANNEL_MOTOR, CAN_KP_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_RIGHT_SPEED, int(speed_right.p)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KI_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_RIGHT_SPEED, int(speed_right.i)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_KD_ID, CAN_BOARD_ID_MOTOR,
                    fmt_motor_set_pid.pack(PID_RIGHT_SPEED, int(speed_right.d)))
        send_packet(CAN_CHANNEL_MOTOR, CAN_SPEED_ACCEL_LIM, CAN_BOARD_ID_MOTOR, fmt_motor_lim.pack(int(cap.SpeedTranslation*MM_TO_TICK), int(cap.SpeedRotation*MM_TO_TICK), int(cap.SpeedWheel*MM_TO_TICK), int(cap.AccelWheel*MM_TO_TICK)))

    def on_order_submission(self, speed: Optional[float], position: Optional[float],
                            angle: Optional[float]):
        # Ici tu implem l'envoi des paquets sur le CAN.
        print(
            f"Got order {speed} {position} {angle}")  # position en mm, speed en mm/s, angle en degré
        def send_order():
            self.cod_start_left = self.cod_last_left
            self.cod_start_right = self.cod_last_right
            self.setpoint_speed = 0.0
            self.setpoint_pos = None
            self.setpoint_angle = None
            # Cas 1 : translation avec asserv en vitesse uniquement (2 roues allant à la meme vitesse)
            if speed is not None:
                send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_MCS_MODE, CAN_BOARD_ID_MOTOR, MCS_MODE_SPEED)
                print("SPEED")
                self.setpoint_speed = speed
                speed_ticks = speed * MM_TO_TICK
                print(speed_ticks)
                send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_SPEED, CAN_BOARD_ID_MOTOR,
                            fmt_motor_set_speed.pack(int(speed_ticks), int(speed_ticks)))

            # Cas 2 : juste translation, les vitesses des roues sont gérées par le LL
            elif position is not None:
                send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_MCS_MODE, CAN_BOARD_ID_MOTOR, MCS_MODE_ALL)
                print("POS")
                self.setpoint_pos = position  # position in mm that each wheel have to travel
                position_ticks = position * MM_TO_TICK  # in ticks for Motion control board
                send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_POS, CAN_BOARD_ID_MOTOR,
                            fmt_motor_move.pack(0, int(position_ticks)))

            # Cas 3 : juste rotation
            elif angle is not None:
                send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_MCS_MODE, CAN_BOARD_ID_MOTOR, MCS_MODE_ALL)
                print("ANGLE")
                # distance for each wheel(in opposite direcitons), to reach angle, in mm for graph
                self.setpoint_angle = angle * DISTANCE_BETWEEN_WHEELS / 2
                angle_ticks = self.setpoint_angle * MM_TO_TICK  # in ticks for Motion control board
                send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_POS, CAN_BOARD_ID_MOTOR, fmt_motor_move.pack(1, int(angle_ticks)))
                print(angle_ticks)

        if speed is not None or position is not None or angle is not None:
            Timer(0.8, send_order).start() # Delayed movement order
        else:
            send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_STOP, CAN_BOARD_ID_MOTOR, []) # instant stop order if not parameters

    def on_stop_button(self):
        self.setpoint_pos = None
        self.setpoint_angle = None
        self.setpoint_speed = 0.0
        send_packet(CAN_CHANNEL_MOTOR, CAN_MSG_STOP, CAN_BOARD_ID_MOTOR, [])


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

        Timer(0.8, send_order).start()


if __name__ == '__main__':
    
    # Flask
    app = Flask(__name__)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SECRET_KEY'] = ''.join(random.choice(string.printable) for i in range(64))
    socketio = SocketIO(app, cors_allowed_origins="*")

    # Enable CAN
    os.system("./can_enable.sh")

    if os.environ.get('RANDOM_GRAPH'):
        register_views(app, socketio, RandomAdapter(socketio))
    else:
        register_views(app, socketio, CANAdapter(socketio))

    socketio.run(app, host='0.0.0.0', port=5000)
