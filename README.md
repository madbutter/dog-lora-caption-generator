# Dog Painting Caption Generator

This project uses GPT-4 Vision to generate detailed captions for oil paintings of dogs, which can be used for training AI art models like LoRA.

## Features
- Processes images of dog paintings from an input folder
- Uses GPT-4 Vision API to analyze images
- Generates detailed captions including:
  - Dog breed
  - Pose
  - Fur details
  - Painting style
  - Background/environment
  - View/perspective

## Setup
1. Clone this repository
2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Set your OpenAI API key as an environment variable:
   ```bash
   # Windows
   set OPENAI_API_KEY=your-key-here
   
   # Unix/Mac
   export OPENAI_API_KEY=your-key-here
   ```
4. Place your dog painting images in the `images` folder
5. Run the script:
   ```bash
   python generate_dog_lora_captions.py
   ```

## Output
The script will create caption files in the `captions` folder, with the same name as the input images but with a `.txt` extension.

## Requirements
- Python 3.6+
- OpenAI API key with GPT-4 Vision access
- Required packages listed in `requirements.txt` 