import serial

class VendiCom:
    def __init__(self, arduino_port="/dev/ttyACM0", baud_rate=9600):
        self.ser = serial.Serial(arduino_port, baud_rate, timeout=1)

    def set(self, angle):
        self.ser.write(f"{angle}".encode())

    def __del__(self):
        self.ser.close()
