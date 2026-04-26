"""Hardware dispenser tool exposed to Gemma via native function calling.

The `Dispenser` owns the single serial connection to the Arduino. Each call to
`dispense_can()` returns immediately and runs the two-step rotation
(+250° to release, -250° to home) on a daemon thread, serialized by a lock so
overlapping calls queue cleanly instead of racing on the serial port.
"""

from __future__ import annotations

import threading

from backend.vendi_com import VendiCom


# Hardware-calibrated angles for one full dispense cycle.
DISPENSE_ANGLE = 250.0
RETURN_ANGLE = -250.0


class Dispenser:
    def __init__(self, arduino_port: str = "/dev/cu.usbmodem1051DB2BAE342", baud_rate: int = 9600):
        # Hard-fail on construction if the Arduino isn't connected.
        self._com = VendiCom(arduino_port=arduino_port, baud_rate=baud_rate)
        self._lock = threading.Lock()

    def _run_cycle(self) -> None:
        with self._lock:
            self._com.set(DISPENSE_ANGLE)
            self._com.wait_done()
            self._com.set(RETURN_ANGLE)
            self._com.wait_done()

    def dispense_can(self) -> dict:
        """Fire-and-forget: kick off the dispense cycle on a background thread.

        Returns immediately so Vendi can start celebrating while the motor turns.
        The returned dict is what gets fed back to the model as the tool result.
        """
        threading.Thread(target=self._run_cycle, daemon=True).start()
        return {"status": "dispensing"}


# Tool schema handed to `processor.apply_chat_template(tools=...)`.
# The description is deliberately strict — we want zero false fires of the motor.
DISPENSE_CAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "dispense_can",
        "description": (
            "Physically dispense ONE can of soda from the vending machine by "
            "rotating the dispenser motor. This is a real, irreversible "
            "hardware action — a can will drop and cannot be put back.\n\n"
            "ONLY call this tool when the customer has clearly and explicitly "
            "committed to buying a can RIGHT NOW. Examples that warrant a call: "
            "\"yes, I'll take one\", \"give me a can\", \"I'll buy one\", "
            "\"sure, dispense it\", a clear \"yes\" in direct response to your "
            "offer to dispense.\n\n"
            "DO NOT call this tool for: greetings, small talk, questions about "
            "the cans (price, flavor, what's available), expressions of mild "
            "interest (\"maybe\", \"hmm\", \"I'm thinking about it\"), the "
            "customer asking you to wait, or anything ambiguous. When in doubt, "
            "do NOT call the tool"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def build_dispatch(dispenser: Dispenser) -> dict:
    """Map tool-call names to the bound methods that execute them."""
    return {"dispense_can": dispenser.dispense_can}


TOOL_SCHEMAS = [DISPENSE_CAN_SCHEMA]
