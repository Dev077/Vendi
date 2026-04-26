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

    # Motor: 12 RPM × 2048 steps/rev → ~2.44ms/step. 250° ≈ 1422 steps ≈ 3.47s.
    # Per-degree cost in seconds, plus a fixed buffer for parseFloat + prints.
    _SECONDS_PER_DEGREE = 60.0 / (12 * 360)
    _OVERHEAD_SECONDS = 0.5

    def set(self, angle):
        # Drop any stale firmware chatter before issuing a new command so it
        # can't be mistaken for output from this move.
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        # Trailing \n terminates Serial.parseFloat() on the firmware immediately
        # instead of making it wait for its 1s stream timeout.
        self.ser.write(f"{angle}\n".encode())
        self.ser.flush()

    def wait_move(self, angle):
        """Block long enough for `angle` degrees of motor travel to finish.

        The firmware was supposed to emit 'Done.' after each step(), but on this
        32u4 board that line is unreliable (USB CDC drops post-motor prints).
        Speed is deterministic, so we time it instead.
        """
        import time
        time.sleep(abs(angle) * self._SECONDS_PER_DEGREE + self._OVERHEAD_SECONDS)

    def __del__(self):
        try:
            self.ser.close()
        except Exception:
            pass
