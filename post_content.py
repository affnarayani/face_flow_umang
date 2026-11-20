import os
import sys
import json
import time
import random
import base64
import requests
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.config_loader import get
from common.crypto import load_cookies, get_env_decrypt_key
from common.locator_loader import resolve_locator, apply_auto_correct, get_locator_expressions, eval_locator
from common.screenshot import capture_error_screenshot, upload_to_onlyfiles

# =========================
# ENV
# =========================
load_dotenv()

get_env_decrypt_key()

# =========================
# CONFIG (from json/config.json)
# =========================
HEADLESS = get("headless", True)
USER_AGENT = get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
FACEBOOK_PROFILE_URL = get("facebook_profile_url", "https://www.facebook.com/AdvocateUmangPatna/")

FACEBOOK_COOKIES_FILE = Path("fb_cookies/fb_cookies.json.encrypted")
TOPICS_FILE = Path("json/umang_fb_topics.json")
POST_FILE = Path("json/post.json")
IMAGE_PATH = Path("image/image.png")

PLATFORM = "facebook"


# =========================
# STATE VALIDATION & STATUS UPDATER
# =========================
def can_run_fb_script():
    print(f"[STEP] Checking status in {TOPICS_FILE.name}...", flush=True)

    if not TOPICS_FILE.exists():
        print(f"[INFO] '{TOPICS_FILE.name}' not found. Execution stopped.", flush=True)
        return False, None

    try:
        with TOPICS_FILE.open("r", encoding="utf-8") as f:
            topics = json.load(f)
    except Exception as e:
        print(f"[ERROR] Error reading topics file: {e}.", flush=True)
        return False, None

    if not topics or not isinstance(topics, list):
        print("[INFO] Topics list is empty.", flush=True)
        return False, None

    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "image_generated" in item:
            last_processed_item = item

    if last_processed_item is None:
        print("[INFO] No processed entry found.", flush=True)
        return False, None

    ig = last_processed_item.get("image_generated") is True
    posted = last_processed_item.get("posted") is True

    if ig and not posted:
        current_topic = last_processed_item.get("topic")
        print(f"[OK] Last post '{current_topic}' validation clear! Running Facebook Posting.", flush=True)
        return True, current_topic
    else:
        print(f"[INFO] Script halted! Condition did not match: image_generated={ig}, posted={posted}.", flush=True)
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
        raise FileNotFoundError(f"❌ '{POST_FILE.name}' file not found!")

    with POST_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    title = data.get("title", "").strip()
    p1 = data.get("p1", "").strip()
    p2 = data.get("p2", "").strip()
    p3 = data.get("p3", "").strip()
    conclusion = data.get("conclusion", "").strip()
    keywords = data.get("keywords", [])

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

    if keywords:
        hashtags = " ".join([f"#{kw.strip()}" for kw in keywords])
        full_text += f"\n\n{hashtags}"

    return full_text


# =========================
# MAIN EXECUTION
# =========================
def run():
    print("[START] Facebook Bot started", flush=True)

    apply_auto_correct()

    can_run, active_topic = can_run_fb_script()
    if not can_run:
        print("[INFO] Pre-conditions not met. Exiting script gracefully...", flush=True)
        sys.exit(0)

    if not IMAGE_PATH.exists():
        print(f"[ERROR] Image path '{IMAGE_PATH}' not found. Exiting...", flush=True)
        sys.exit(1)

    cookies = load_cookies(Path(FACEBOOK_COOKIES_FILE))
    post_caption = build_post_text()

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
            user_agent=USER_AGENT
        )

        context.add_cookies(cookies)
        page = context.new_page()

        print("[STEP] Opening Facebook Profile/Page...", flush=True)
        page.goto(FACEBOOK_PROFILE_URL)
        time.sleep(random.randint(4, 8))

        try:
            switch_now = resolve_locator(page, PLATFORM, "facebook_switch_now_button", "Profile switch handle")
            if switch_now.is_visible():
                switch_now.click()
                time.sleep(random.randint(5, 10))
        except:
            pass

        print("[STEP] Opening post creation dialog...", flush=True)
        create_post_btn = None
        start_wait = time.time()
        while time.time() - start_wait < 120:
            expressions = get_locator_expressions(PLATFORM, "facebook_create_post_button")
            if expressions:
                for expression in expressions:
                    try:
                        candidate = eval_locator(page, expression)
                        if candidate.count() > 0:
                            create_post_btn = candidate
                            break
                    except Exception:
                        pass
            if create_post_btn is not None:
                break
            time.sleep(2)

        if create_post_btn is None:
            raise RuntimeError("❌ 'Create post' button did not appear within 120 seconds")

        create_post_btn.click()
        time.sleep(random.randint(6, 12))

        print("[STEP] Locating 'AI label off' button...", flush=True)
        ai_label_off_btn = resolve_locator(
            page, PLATFORM, "facebook_ai_label_off_button", "AI label off button", optional=True
        )
        if ai_label_off_btn is not None:
            ai_label_off_btn.click()
            time.sleep(random.randint(3, 6))
        else:
            print("[INFO] 'AI label off' button not found. Skipping...", flush=True)

        print("[STEP] Toggling 'Add AI label' switch...", flush=True)
        resolve_locator(page, PLATFORM, "facebook_add_ai_label_switch", "Add AI label switch").click()
        time.sleep(random.randint(3, 6))

        print("[STEP] Confirming with 'Got it' button...", flush=True)
        got_it_btn = resolve_locator(
            page, PLATFORM, "facebook_got_it_button", "Got it button", optional=True
        )
        if got_it_btn is not None:
            got_it_btn.click()
            time.sleep(random.randint(3, 6))
        else:
            print("[INFO] 'Got it' button not found. Skipping...", flush=True)

        print("[STEP] Locating textarea and typing post...", flush=True)
        resolve_locator(page, PLATFORM, "facebook_post_paragraph", "Post textarea").click()
        page.keyboard.type(post_caption + " ")
        time.sleep(random.randint(3, 6))

        print("[STEP] Uploading static local image...", flush=True)
        with page.expect_file_chooser() as fc:
            resolve_locator(page, PLATFORM, "facebook_photo_video_button", "Photo/video button").click()

        fc.value.set_files(str(IMAGE_PATH))
        time.sleep(random.randint(6, 12))

        next_btn = resolve_locator(
            page, PLATFORM, "facebook_next_button", "Next button", optional=True
        )
        if next_btn is not None:
            next_btn.click()
            print("[STEP] Clicking Next...", flush=True)
            time.sleep(random.randint(6, 12))

        resolve_locator(page, PLATFORM, "facebook_post_button", "Post button").click()
        print("[STEP] Clicking Post...", flush=True)
        time.sleep(random.randint(8, 15))

        btn = resolve_locator(
            page, PLATFORM, "facebook_not_now_button", "Dismiss Post Post layout prompt", optional=True
        )
        if btn is not None and btn.count():
            btn.first.click()
            print("[STEP] Dismissed Post Post layout prompt...", flush=True)
            time.sleep(random.randint(4, 8))

        print("✅ Posted successfully to Facebook Feed!", flush=True)

        update_posted_status_in_json(active_topic)
        time.sleep(random.randint(5, 10))

    except Exception as e:
        print("[ERROR during execution]", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = capture_error_screenshot(page)
                if screenshot_path:
                    upload_to_onlyfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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
