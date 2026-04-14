import os

import google.generativeai as genai
from pydantic import BaseModel


class Message(BaseModel):
    message: str


def _ensure_genai():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=key)


def extract_all_fields(user_query: str) -> str:
    try:
        _ensure_genai()
        prompt = f"""
The user will provide any query, for example: "virat kohli funny meme in hindi".

Your job is to understand the intent and generate a funny meme-style caption in Hindi (or a mix of Hindi-English), consisting of exactly 7 to 8 words. The response should be humorous, catchy, and suitable as text for a meme.

Return only the final caption text — no explanation, no formatting, no extra content.
Query: {user_query}
"""
        model = genai.GenerativeModel("gemini-2.0-flash")
        ai_response = model.generate_content(prompt)
        ai_response_text = (ai_response.text or "").strip() if ai_response else ""
        return ai_response_text.replace("```json", "").replace("```", "").strip() or "Caption generation failed"
    except Exception as e:
        print(f"extract_all_fields error: {e}")
        return "Caption generation failed"


def intent(user_query: str) -> str:
    try:
        _ensure_genai()
        prompt = f"""
Understand the user's intent clearly and identify exactly what kind of image they want. Only return a short, single-line message specifically describing the image to be generated — do not use the word 'meme' or any similar terms.

Example:
User: virat kohli meme generate funny
Response: virat kohli hd image generate -
Avoid any extra words. Just return a clear, concise line describing the image that the user wants to generate, ending with this note:
(don't add any caption in img, only HD image generate, clearly only image).

Query: {user_query}
"""
        model = genai.GenerativeModel("gemini-2.0-flash")
        ai_response = model.generate_content(prompt)
        ai_response_text = (ai_response.text or "").strip() if ai_response else ""
        return ai_response_text.replace("```json", "").replace("```", "").strip() or "Caption generation failed"
    except Exception as e:
        print(f"intent error: {e}")
        return "Caption generation failed"
