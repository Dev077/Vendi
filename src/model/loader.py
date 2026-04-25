from dotenv import load_dotenv
import os
from transformers import AutoProcessor, AutoModelForMultimodalLM

def main():
    load_dotenv()
    MODEL_ID = os.getenv("MODEL_ID")
    
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForMultimodalLM.from_pretrained(
        MODEL_ID, 
        dtype="auto", 
        device_map="auto"
    )
    
    messages = [
        {
            'role': 'user',
            'content': [
                {"type": "video", "video": "https://github.com/bebechien/gemma/raw/refs/heads/main/videos/ForBiggerBlazes.mp4"},
                {'type': 'text', 'text': 'Describe this video.'}
            ]
        }
    ]
    
    # Process input
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
    ).to(model.device)
    input_len = inputs["input_ids"].shape[-1]
    
    # Generate output
    outputs = model.generate(**inputs, max_new_tokens=512)
    response = processor.decode(outputs[0][input_len:], skip_special_tokens=False)
    
    # Parse output
    processor.parse_response(response)
    print(response)

if __name__ == "__main__":
    main()
