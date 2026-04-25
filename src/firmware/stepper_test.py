import serial
import time

# Replace with your actual port (e.g., '/dev/ttyACM0' on Linux/Mac)
arduino_port = "/dev/ttyACM0" 
baud_rate = 9600

try:
    ser = serial.Serial(arduino_port, baud_rate, timeout=1)
    time.sleep(2) # Wait for Arduino to reset
    print("Connected to Arduino.")

    while True:
        user_input = input("Enter degrees to rotate (or 'q' to quit): ")
        
        if user_input.lower() == 'q':
            break
            
        try:
            ser.write(f"{user_input}\n".encode())
            
            time.sleep(0.1)
            while ser.in_waiting > 0:
                print(f"Arduino says: {ser.readline().decode().strip()}")
                
        except ValueError:
            print("Please enter a valid number.")

    ser.close()
    print("Connection closed.")

except Exception as e:
    print(f"Error: {e}")
