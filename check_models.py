import os
from google import genai

api_key = os.getenv("GEMINI_DRIVE_API_KEY") or os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ API Key not found in Environment Variables!")
    exit(1)

client = genai.Client(api_key=api_key)

print("📋 Your API Key supports the following active models:\n")
try:
    for model in client.models.list():
        print(f"🔹 {model.name}")
except Exception as e:
    print(f"❌ Error fetching models: {e}")
