#!venv/bin/python
import os
import random
import string

from flask import Flask
from flask_socketio import SocketIO
from adapter.pycan import CANAdapter
from adapter.random import RandomAdapter

from views import register_views


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
