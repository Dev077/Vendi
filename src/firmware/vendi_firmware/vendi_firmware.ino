#include <Stepper.h>

const int stepsPerRev = 2048;
// Use the sequence for 28BYJ-48
Stepper myStepper(stepsPerRev, 8, 10, 9, 11);

void setup() {
  myStepper.setSpeed(12);
  Serial.begin(9600);
  Serial.println("Ready.");
}

void loop() {
  if (Serial.available() > 0) {
    // Read the incoming float
    float degrees = Serial.parseFloat();
    
    // Logic to ignore trailing zeros or empty enters
    if (degrees != 0) {
      // Calculate steps
      long stepsToMove = (degrees * stepsPerRev) / 360.0;
      
      myStepper.step(stepsToMove);
    }

    Serial.print("Moving ");
    Serial.print(degrees);
    Serial.println(" degrees.");
  }
}