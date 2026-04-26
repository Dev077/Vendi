from vendi_com import VendiCom
import time

arduino_port = "/dev/ttyACM0" 
baud_rate = 9600

vendicom = VendiCom(arduino_port, baud_rate)
time.sleep(2)
print("Connected to Arduino.")

while True:
    user_input = input("Enter degrees to rotate (or 'q' to quit): ")
    
    if user_input.lower() == 'q':
        break
        
    try:
        vendicom.set(user_input)
        time.sleep(0.1)
            
    except ValueError:
        print("Please enter a valid number.")

del vendicom
print("Connection closed.")
