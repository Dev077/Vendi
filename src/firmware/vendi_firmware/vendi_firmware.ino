#include <Stepper.h>

const int stepsPerRev = 2048;
// Use the sequence for 28BYJ-48
Stepper myStepper(stepsPerRev, 8, 10, 9, 11);

void setup() {
  myStepper.setSpeed(12);
  Serial.begin(9600);
  while (!Serial) { ; }  // 32u4 boards: wait for USB CDC enumeration
  Serial.println("Ready.");
}

void loop() {
  if (Serial.available() > 0) {
    // Read the incoming float
    float degrees = Serial.parseFloat();

    // Drain a trailing CR/LF so it doesn't trigger a phantom "0" command
    // on the next loop iteration.
    while (Serial.available() && (Serial.peek() == '\n' || Serial.peek() == '\r')) {
      Serial.read();
    }

    Serial.print("Moving ");
    Serial.print(degrees);
    Serial.println(" degrees.");
    Serial.flush();  // get the line out before the motor draws current / radiates

    // Logic to ignore trailing zeros or empty enters
    if (degrees != 0) {
      // Calculate steps
      long stepsToMove = (degrees * stepsPerRev) / 360.0;

      myStepper.step(stepsToMove);

      // Release the coils so they don't keep drawing current and radiating
      // EMI into the USB lines (otherwise post-move prints get dropped).
      digitalWrite(8, LOW);
      digitalWrite(9, LOW);
      digitalWrite(10, LOW);
      digitalWrite(11, LOW);
    }

    Serial.println("Done.");
  }
}