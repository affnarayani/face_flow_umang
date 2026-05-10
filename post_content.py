import os
import json
import time
import base64
import random
import shutil
import requests
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from huggingface_hub import InferenceClient

from playwright_stealth import Stealth   # ✅ REQUIRED


# =========================
# CONFIG
# =========================
HEADLESS = False

FACEBOOK_COOKIES_FILE = "cookies.json.encrypted"
POSTED_CONTENT_FILE = "posted_content.json"
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

PBKDF2_ITERATIONS = 200_000
MAX_RETRIES = 3


# =========================
# ENV
# =========================
load_dotenv()
DECRYPT_KEY = os.getenv("DECRYPT_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN missing")


# =========================
# CRYPTO
# =========================
def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ciphertext = base64.b64decode(payload["ct"])

    key = _derive_key(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)

    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise RuntimeError("❌ Decryption failed (InvalidTag)")


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    print("[STEP] Loading cookies...", flush=True)

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, DECRYPT_KEY)
    cookies = json.loads(plaintext.decode("utf-8"))

    print("[OK] Cookies loaded", flush=True)
    return cookies


# =========================
# AI
# =========================
client = InferenceClient(
    model="meta-llama/Meta-Llama-3-8B-Instruct",
    token=HF_TOKEN
)


def sanitize_ai_content(text):
    return text.replace("**", "").replace("*", "").strip()


def rewrite_with_hf(text):
    print("[STEP] Rewriting content with HF...", flush=True)

    prompt = (
        f"Rewrite the legal content below into a high-performing LinkedIn post (~120 words).\n"
        f"Rules:\n"
        f"- Exactly 2 paragraphs\n"
        f"- Paragraph 1: Strong hook\n"
        f"- Paragraph 2: Insightful explanation\n"
        f"- End with a thought-provoking question\n"
        f"- Use clear, professional, SEO-friendly language\n"
        f"- Do NOT use symbols like * or **\n"
        f"- IMPORTANT: Add 5–10 relevant hashtags on a new line at the end\n"
        f"- No extra commentary or headings\n"
        f"Content: {text}"
    )

    for _ in range(MAX_RETRIES):
        try:
            res = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=220,
                temperature=0.7,
            )

            result = sanitize_ai_content(res.choices[0].message.content)
            return result

        except Exception as e:
            print("[AI ERROR]", e, flush=True)
            time.sleep(5)

    return sanitize_ai_content(text)


# =========================
# CONTENT
# =========================
def load_json(url):
    print("[STEP] Fetching content...", flush=True)
    return requests.get(url).json()


def get_new_content():
    url = "https://raw.githubusercontent.com/affnarayani/ninetynine_credits_legal_advice_app_content/main/content.json"
    data = load_json(url)

    posted = []
    if Path(POSTED_CONTENT_FILE).exists():
        posted = json.load(open(POSTED_CONTENT_FILE, "r", encoding="utf-8"))

    posted_titles = {p["title"] for p in posted}

    for item in data:
        if item["title"] not in posted_titles:
            return item

    return None


def download_image(url, name):
    path = TEMP_DIR / name
    r = requests.get(url, stream=True)

    with open(path, "wb") as f:
        shutil.copyfileobj(r.raw, f)

    return path


# =========================
# FACEBOOK BOT (STEALTH)
# =========================
def run():
    print("[START] Bot started", flush=True)

    cookies = load_cookies(Path(FACEBOOK_COOKIES_FILE))
    content = get_new_content()

    if not content:
        print("[INFO] No new content")
        return

    rewritten = rewrite_with_hf(content["description"])
    image_path = download_image(content["image"], "post.jpg")

    # =========================
    # STEALTH SETUP (YOUR CODE)
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        context.add_cookies(cookies)
        page = context.new_page()

        print("[STEP] Opening Facebook...", flush=True)
        page.goto("https://www.facebook.com/AdvocateUmangPatna")
        time.sleep(random.randint(15, 30))

        try:
            if page.get_by_role("button", name="Switch Now").is_visible():
                page.get_by_role("button", name="Switch Now").click()
        except:
            pass

        print("[STEP] Opening post box...", flush=True)
        page.get_by_role("button", name="What\'s on your mind?").click()
        time.sleep(random.randint(15, 30))
        page.get_by_role("paragraph").click()
        page.keyboard.type(rewritten + " ")

        print("[STEP] Uploading image...", flush=True)
        with page.expect_file_chooser() as fc:
            page.get_by_role("button", name="Photo/video", exact=True).click()

        fc.value.set_files(str(image_path))
        time.sleep(random.randint(15, 30))
        page.get_by_role("button", name="Next").click()
        print("[STEP] Clicking Next...", flush=True)
        time.sleep(random.randint(15, 30))
        page.get_by_role("button", name="Post", exact=True).click()
        print("[STEP] Clicking Post...", flush=True)
        time.sleep(random.randint(15, 30))

        btn = page.get_by_role("button", name="Not now")
        if btn.count(): 
            btn.first.click()
            print("[STEP] Clicking No WhatsApp...", flush=True)
            time.sleep(random.randint(15, 30))

        print("✅ Posted successfully!", flush=True)

        # save
        posted = []
        if Path(POSTED_CONTENT_FILE).exists():
            posted = json.load(open(POSTED_CONTENT_FILE, "r", encoding="utf-8"))

        posted.insert(0, content)

        with open(POSTED_CONTENT_FILE, "w", encoding="utf-8") as f:
            json.dump(posted, f, indent=2)

        time.sleep(random.randint(15, 30))

    except Exception as e:
        print("[ERROR]", e, flush=True)

    finally:
        try:
            browser.close()
        except:
            pass

        try:
            if TEMP_DIR.exists():
                shutil.rmtree(TEMP_DIR)
            TEMP_DIR.mkdir(exist_ok=True)
            print("[CLEANUP] Temp cleared", flush=True)
        except Exception as e:
            print("[CLEANUP ERROR]", e, flush=True)

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Bot finished", flush=True)


if __name__ == "__main__":
    run()