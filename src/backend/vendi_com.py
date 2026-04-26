import os

import serial


class VendiCom:
    def __init__(self, arduino_port: str | None = None, baud_rate: int = 9600):
        # VENDI_ARDUINO_PORT overrides the caller's value — set it to e.g.
        # `socket://localhost:9999` to point at an SSH-tunneled serial port.
        port = os.getenv("VENDI_ARDUINO_PORT") or arduino_port
        if port and "://" in port:
            self.ser = serial.serial_for_url(port, baudrate=baud_rate, timeout=1)
        else:
            self.ser = serial.Serial(port, baud_rate, timeout=1)

    def set(self, angle):
        self.ser.write(f"{angle}".encode())

    def wait_done(self, timeout=10.0):
        """Block until the firmware prints 'Done.', or `timeout` seconds elapse.

        The firmware emits 'Done.' on its own line after each `myStepper.step()`
        completes. We rely on this to serialize back-to-back motor commands
        instead of sleeping a magic number.
        """
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.ser.readline()  # respects pyserial's per-read timeout
            if not line:
                continue
            if line.strip() == b"Done.":
                return
        raise TimeoutError("VendiCom: timed out waiting for 'Done.' from firmware")

    def __del__(self):
        try:
            self.ser.close()
        except Exception:
            pass
