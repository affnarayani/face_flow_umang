import os
import sys
import json
import time
import random
from pathlib import Path

from dotenv import load_dotenv

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.config_loader import get
from common.crypto import load_cookies, get_env_decrypt_key
from common.locator_loader import resolve_locator, apply_auto_correct
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
MIN_RANDOM_WAIT = get("min_random_wait", 15)
MAX_RANDOM_WAIT = get("max_random_wait", 30)
MIN_RANDOM_WAIT_LONG = get("min_random_wait_long", 30)
MAX_RANDOM_WAIT_LONG = get("max_random_wait_long", 60)
MIN_RANDOM_WAIT_SHORT = get("min_random_wait_short", 3)
MAX_RANDOM_WAIT_SHORT = get("max_random_wait_short", 5)
CHATGPT_URL = get("chatgpt_url", "https://chatgpt.com/")
MAX_RETRIES = get("max_retries", 10)

COOKIES_DIR = Path("chatgpt_cookies")
IMAGE_DIR = Path("image")
IMAGE_DIR.mkdir(exist_ok=True)

PLATFORM = "chatgpt"


# =========================
# RANDOM WAIT
# =========================
def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


# =========================
# VALIDATION & STATUS UPDATERS
# =========================
def can_run_image_script():
    print("[STEP] Checking status in json/umang_fb_topics.json...", flush=True)
    topics_file = Path("json/umang_fb_topics.json")
    if not topics_file.exists():
        print("[INFO] 'json/umang_fb_topics.json' not found. Execution stopped.", flush=True)
        return False, None

    try:
        with topics_file.open("r", encoding="utf-8") as f:
            topics = json.load(f)
    except Exception as e:
        print(f"[ERROR] Error reading topics file: {e}.", flush=True)
        return False, None

    if not topics or not isinstance(topics, list):
        print("[INFO] Topics list is empty.", flush=True)
        return False, None

    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "content_generated" in item:
            last_processed_item = item

    if last_processed_item is None:
        print("[INFO] No processed entry found.", flush=True)
        return False, None

    cg = last_processed_item.get("content_generated") is True
    ig = last_processed_item.get("image_generated") is True

    if cg and not ig:
        print(f"[OK] Last post '{last_processed_item.get('topic')}' validation clear! Running Image Generation.", flush=True)
        return True, last_processed_item.get("topic")
    else:
        print(f"[INFO] Script halted! Condition did not match: content_generated={cg}, image_generated={ig}.", flush=True)
        return False, None


def update_image_status_in_json(topic_text: str):
    print("[STEP] Updating image status in json/umang_fb_topics.json...", flush=True)
    topics_file = Path("json/umang_fb_topics.json")
    if not topics_file.exists():
        return

    with topics_file.open("r", encoding="utf-8") as f:
        topics = json.load(f)

    for item in topics:
        if item.get("topic") == topic_text:
            item["image_generated"] = True
            break

    with topics_file.open("w", encoding="utf-8") as f:
        json.dump(topics, f, indent=4, ensure_ascii=False)
    print("[OK] Image status successfully updated (image_generated=True) in json/umang_fb_topics.json", flush=True)


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    apply_auto_correct()

    can_run, topic = can_run_image_script()
    if not can_run:
        print("[INFO] Pre-conditions not met. Exiting script gracefully...", flush=True)
        sys.exit(0)

    encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError("❌ No .encrypted cookie files found in 'chatgpt_cookies/' folder")

    CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    post_file = Path("json/post.json")
    print("[STEP] Loading post JSON...", flush=True)
    if not post_file.exists():
        print("[ERROR] 'json/post.json' file not found.", flush=True)
        sys.exit(1)

    with post_file.open("r", encoding="utf-8") as json_file:
        post_data = json.load(json_file)

    post_title = post_data.get("title", topic)
    print(f"[OK] Post Title extracted: {post_title}", flush=True)

    post_keywords = post_data.get("keywords", [])
    print(f"[OK] Post Keywords extracted: {post_keywords}", flush=True)

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

        context.grant_permissions(["clipboard-read", "clipboard-write"])
        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        prompt = ("""
        Create image for a Facebook post with title: "{post_title}". Image must in the ratio 1:1. This will be a hero image to this Facebook post. Image must be engaging. The core idea of the image must revolve around these keywords: {post_keywords}. Output only the final image directly.
        """)

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto(CHATGPT_URL, wait_until="load")
        print("[OK] URL opened", flush=True)

        print("[STEP] Performing initial random wait (30-60 seconds)...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = resolve_locator(page, PLATFORM, "chatgpt_textbox", "Locating chat textbox")

        if chat_box.count() > 0:
            chat_box.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator could not be loaded (All strategies failed).")

        formatted_base = prompt.format(post_title=post_title, post_keywords=post_keywords)
        clean_base_prompt = " ".join(formatted_base.split())
        prompt_text = clean_base_prompt
        print(f"[STEP] Filling prompt: '{prompt_text}'", flush=True)
        chat_box.first.fill(prompt_text)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        page.keyboard.press("Enter")
        print("[OK] Prompt sent successfully", flush=True)

        # Wait for share button to appear (image generated + detected)
        share_button = None
        found_share = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

            try:
                locator = resolve_locator(page, PLATFORM, "chatgpt_share_image_button", "Share this image button")
                if locator.is_visible():
                    share_button = locator
                    found_share = True
                    print("✅ 'Share this image' button located successfully!", flush=True)
                    break
            except Exception as loc_err:
                print(f"[INFO] Share locator exception: {loc_err}", flush=True)

        if not found_share or not share_button:
            print("❌ Error: 'Share this image' button not found after retries. Exiting program.", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = capture_error_screenshot(page)
                    if screenshot_path:
                        upload_to_onlyfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
            sys.exit(1)

        print("[STEP] Clicking 'Share this image' button...", flush=True)
        share_button.click()
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        print("[STEP] Clicking 'Download' button...", flush=True)
        download_btn = resolve_locator(page, PLATFORM, "chatgpt_download_button", "Download button")

        with page.expect_download(timeout=60000) as download_info:
            download_btn.click()

        download = download_info.value
        local_filename = IMAGE_DIR / "image.png"
        download.save_as(local_filename)
        print(f"✅ Image downloaded successfully: {local_filename}", flush=True)

        update_image_status_in_json(topic)

        print("[STEP] Performing final random wait before exit (30-60 seconds)...", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
        print("[DONE] Image download successfully processed.", flush=True)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
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

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()
