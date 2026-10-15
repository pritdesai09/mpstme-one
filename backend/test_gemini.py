import os
from pathlib import Path
from dotenv import load_dotenv
from google import genai

# Load .env from the project root
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
chat_model = os.environ["GEMINI_CHAT_MODEL"]
embed_model = os.environ["GEMINI_EMBED_MODEL"]

# 1. Test text generation
resp = client.models.generate_content(
    model=chat_model,
    contents="In one sentence, what is a RAG chatbot?",
)
print(f"[{chat_model}] {resp.text}\n")

# 2. Test embeddings
emb = client.models.embed_content(
    model=embed_model,
    contents="What is the attendance policy at MPSTME?",
)
vector = emb.embeddings[0].values
print(f"[{embed_model}] vector length: {len(vector)}")
print("First 5 values:", vector[:5])
print("\n✅ Gemini is working!")