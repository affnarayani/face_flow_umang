import os
import sys
import json
import time
import base64
import random
import requests
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = True

FACEBOOK_COOKIES_FILE = "cookies.json.encrypted"
TOPICS_FILE = Path("umang_fb_topics.json")
POST_FILE = Path("post.json")
IMAGE_PATH = Path("image/image.png")

PBKDF2_ITERATIONS = 200_000


# =========================
# ENV
# =========================
load_dotenv()
DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


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


# ==================================
# STATE VALIDATION & STATUS UPDATER
# ==================================
def can_run_fb_script() -> tuple:
    print(f"[STEP] Checking status in {TOPICS_FILE.name}...", flush=True)
    
    if not TOPICS_FILE.exists():
        print(f"[INFO] '{TOPICS_FILE.name}' nahi mili. Execution stopped.", flush=True)
        return False, None
    
    try:
        with TOPICS_FILE.open("r", encoding="utf-8") as f:
            topics = json.load(f)
    except Exception as e:
        print(f"[ERROR] Topics file read karne me dikkat: {e}.", flush=True)
        return False, None

    if not topics or not isinstance(topics, list):
        print("[INFO] Topics list khali hai.", flush=True)
        return False, None

    # Sabse aakhri item nikalna jo process ho raha tha
    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "image_generated" in item:
            last_processed_item = item

    if last_processed_item is None:
        print("[INFO] Koi processed entry nahi mili.", flush=True)
        return False, None

    ig = last_processed_item.get("image_generated") is True
    posted = last_processed_item.get("posted") is True

    # Run strictly when image_generated=True and posted=False
    if ig and not posted:
        current_topic = last_processed_item.get("topic")
        print(f"[OK] Last post '{current_topic}' validation clear! Running Facebook Posting.", flush=True)
        return True, current_topic
    else:
        print(f"[INFO] Script halted! Condition match nahi hui: image_generated={ig}, posted={posted}.", flush=True)
        return False, None


def update_posted_status_in_json(topic_text: str):
    print(f"[STEP] Updating posted status in {TOPICS_FILE.name}...", flush=True)
    if not TOPICS_FILE.exists():
        return
        
    with TOPICS_FILE.open("r", encoding="utf-8") as f:
        topics = json.load(f)
        
    for item in topics:
        if item.get("topic") == topic_text:
            item["posted"] = True
            break
            
    with TOPICS_FILE.open("w", encoding="utf-8") as f:
        json.dump(topics, f, indent=4, ensure_ascii=False)
    print(f"[OK] Status successfully updated (posted=True) in {TOPICS_FILE.name}", flush=True)


# ==================================
# TEXT BUILDER FROM JSON
# ==================================
def build_post_text() -> str:
    print(f"[STEP] Reading post content from {POST_FILE.name}...", flush=True)
    if not POST_FILE.exists():
        raise FileNotFoundError(f"❌ '{POST_FILE.name}' file nahi mili!")
        
    with POST_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
        
    title = data.get("title", "").strip()
    p1 = data.get("p1", "").strip()
    p2 = data.get("p2", "").strip()
    p3 = data.get("p3", "").strip()
    conclusion = data.get("conclusion", "").strip()
    keywords = data.get("keywords", [])
    
    # Text formatting with double newlines for spacing
    content_parts = []
    if title:
        content_parts.append(title)
    if p1:
        content_parts.append(p1)
    if p2:
        content_parts.append(p2)
    if p3:
        content_parts.append(p3)
    if conclusion:
        content_parts.append(conclusion)
        
    full_text = "\n\n".join(content_parts)
    
    # Convert keywords list into hashtags mapping
    if keywords:
        hashtags = " ".join([f"#{kw.strip()}" for kw in keywords])
        full_text += f"\n\n{hashtags}"
        
    return full_text

def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})
        
    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL banane ke liye '/dl/' replace karte hain
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"👉 DIRECT LINK (Expires in 2 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None

# =========================
# MAIN EXECUTION
# =========================
def run():
    print("[START] Facebook Bot started", flush=True)

    # 1. Pre-condition status check
    can_run, active_topic = can_run_fb_script()
    if not can_run:
        print("[INFO] Pre-conditions meet nahi hui. Exiting script gracefully...", flush=True)
        sys.exit(0)

    # 2. Check if image file exists before triggering browser
    if not IMAGE_PATH.exists():
        print(f"[ERROR] Image path '{IMAGE_PATH}' par koi file nahi mili. Exiting...", flush=True)
        sys.exit(1)

    # 3. Load cookies and format post structure
    cookies = load_cookies(Path(FACEBOOK_COOKIES_FILE))
    post_caption = build_post_text()

    # =========================
    # STEALTH PLAYWRIGHT SETUP
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
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

        print("[STEP] Opening Facebook Profile/Page...", flush=True)
        page.goto("https://www.facebook.com/AdvocateUmangPatna/")
        time.sleep(random.randint(4, 8))

        # Profile switch handle if modal appears
        try:
            if page.get_by_role("button", name="Switch Now").is_visible():
                page.get_by_role("button", name="Switch Now").click()
                time.sleep(random.randint(5, 10))
        except:
            pass

        print("[STEP] Opening post creation dialog...", flush=True)
        page.get_by_role("button", name="What's on your mind?").click()
        time.sleep(random.randint(6, 12))
        
        # ========================================================
        # NEW: AI LABELING AUTOMATION FLOW (BEFORE TYPING)
        # ========================================================
        print("[STEP] Locating 'AI label off' button...", flush=True)
        page.get_by_role("button", name="AI label off").click()
        time.sleep(random.randint(3, 6))

        print("[STEP] Toggling 'Add AI label' switch...", flush=True)
        page.get_by_role("switch", name="Add AI label").click()
        time.sleep(random.randint(3, 6))

        print("[STEP] Confirming with 'Got it' button...", flush=True)
        page.get_by_role("button", name="Got it").click()
        time.sleep(random.randint(3, 6))
        # ========================================================

        print("[STEP] Locating textarea and typing post...", flush=True)
        page.get_by_role("paragraph").click()
        page.keyboard.type(post_caption + " ")
        time.sleep(random.randint(3, 6))

        print("[STEP] Uploading static local image...", flush=True)
        with page.expect_file_chooser() as fc:
            page.get_by_role("button", name="Photo/video", exact=True).click()

        fc.value.set_files(str(IMAGE_PATH))
        time.sleep(random.randint(6, 12))
        
        page.get_by_role("button", name="Next").click()
        print("[STEP] Clicking Next...", flush=True)
        time.sleep(random.randint(6, 12))
        
        page.get_by_role("button", name="Post", exact=True).click()
        print("[STEP] Clicking Post...", flush=True)
        time.sleep(random.randint(8, 15))

        # Dynamic dismissal for optional integrated WhatsApp hooks
        btn = page.get_by_role("button", name="Not now").or_(page.get_by_role("button", name="Publish Original Post"))
        if btn.count(): 
            btn.first.click()
            print("[STEP] Dismissed Post Post layout prompt...", flush=True)
            time.sleep(random.randint(4, 8))

        print("✅ Posted successfully to Facebook Feed!", flush=True)

        # 4. Success state management trigger
        update_posted_status_in_json(active_topic)
        time.sleep(random.randint(5, 10))

    except Exception as e:
        print("[ERROR during execution]", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        if browser:
            try:
                browser.close()
            except:
                pass
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
            except:
                pass
        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Bot session concluded safely.", flush=True)


if __name__ == "__main__":
    run()