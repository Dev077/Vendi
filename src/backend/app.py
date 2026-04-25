from flask import Flask, request, jsonify
from flask_cors import CORS
import cv2
import numpy as np
import base64
import time
from dotenv import load_dotenv
from google.cloud import vision

load_dotenv()

app = Flask(__name__)
# Enable CORS so the React frontend can communicate with this API
CORS(app) 

# initialize Google Cloud Vision Client
client = vision.ImageAnnotatorClient()

# initialize OpenCV Background Subtractor
back_sub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)

# debounce state
last_api_call_time = 0
COOLDOWN_SECONDS = 5.0

@app.route('/process_frame', methods=['POST'])
def process_frame():
    global last_api_call_time
    
    # 1. Receive the Base64 image from React
    data = request.json
    if 'image' not in data:
        return jsonify({"error": "No image provided"}), 400
        
    # Strip the "data:image/jpeg;base64," prefix
    image_b64 = data['image'].split(',')[1]
    
    # 2. Decode the Base64 string into an OpenCV readable format
    nparr = np.frombuffer(base64.b64decode(image_b64), np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    # 3. Apply motion detection
    fg_mask = back_sub.apply(frame)
    motion_score = cv2.countNonZero(fg_mask)
    
    result = {
        "motion_score": motion_score,
        "api_called": False,
        "human_detected": False,
        "message": "Processing..."
    }

    # 4. Logic Gate: Check threshold and cooldown
    if motion_score > 10000:
        current_time = time.time()
        
        if (current_time - last_api_call_time) > COOLDOWN_SECONDS:
            print("Motion detected! Calling Vision API...")
            last_api_call_time = current_time
            
            # Encode frame to JPEG bytes for Google Vision
            success, encoded_image = cv2.imencode('.jpg', frame)
            content = encoded_image.tobytes()
            vision_image = vision.Image(content=content)
            
            # Call Google Cloud Vision API
            response = client.object_localization(image=vision_image)
            objects = response.localized_object_annotations
            
            # Check for humans
            for obj in objects:
                if obj.name.lower() == 'person':
                    result["human_detected"] = True
                    result["confidence"] = round(obj.score * 100, 1)
                    break
            
            result["api_called"] = True
            result["message"] = "API called: Human found!" if result["human_detected"] else "API called: False alarm."
        else:
            result["message"] = "Motion detected, but API is on cooldown."
    else:
         result["message"] = "No significant motion."

    return jsonify(result)

if __name__ == '__main__':
    # Run the Flask server on port 5000
    app.run(port=5000, debug=True)