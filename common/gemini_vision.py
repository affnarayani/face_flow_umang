import base64
import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = "gemini-3.5-flash-lite"


def gemini_vision_fallback(page, locator_name: str, purpose: str, failed_expression: str):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[WARNING] GEMINI_API_KEY not set. Skipping Gemini Vision fallback.", flush=True)
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("[WARNING] google-genai not installed. Skipping Gemini Vision fallback.", flush=True)
        return None

    try:
        client = genai.Client(api_key=api_key)

        screenshot_bytes = page.screenshot(full_page=True)
        image_part = types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")

        prompt = (
            f"I am automating a webpage with Playwright (Python). I need to locate the element: '{purpose}' "
            f"(locator name: '{locator_name}'). The locator expression {failed_expression!r} failed to find the element. "
            f"Based on this screenshot, suggest a corrected Playwright Python locator expression using 'page.' "
            f"(for example: page.get_by_role('button', name='Submit') or page.locator('#id')). "
            f"Return ONLY the locator expression string, no markdown, no explanation, no code block."
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image_part],
        )

        text = (response.text or "").strip()
        text = text.strip("`").strip()
        if text.startswith("python"):
            text = text[len("python"):].strip()
        return text if text else None

    except Exception as e:
        print(f"[WARNING] Gemini Vision fallback error: {e}", flush=True)
        return None
