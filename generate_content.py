import os
import sys
import json
import time
import random
import requests
from pathlib import Path
from typing import List, Dict, Any

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
MIN_ABSOLUTE_WAIT = get("min_absolute_wait", 15)
MAX_ABSOLUTE_WAIT = get("max_absolute_wait", 30)
MIN_RANDOM_WAIT = get("min_random_wait", 15)
MAX_RANDOM_WAIT = get("max_random_wait", 30)
MIN_RANDOM_WAIT_LONG = get("min_random_wait_long", 30)
MAX_RANDOM_WAIT_LONG = get("max_random_wait_long", 60)
CHATGPT_URL = get("chatgpt_url", "https://chatgpt.com/")

COOKIES_DIR = Path("chatgpt_cookies")
PLATFORM = "chatgpt"


# =========================
# RANDOM WAIT
# =========================
def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


# =========================
# FILE PARSERS & VALIDATORS
# =========================
def can_run_script() -> bool:
    print("[STEP] Checking last post status in json/umang_fb_topics.json...", flush=True)
    topics_file = Path("json/umang_fb_topics.json")
    if not topics_file.exists():
        print("[WARNING] 'json/umang_fb_topics.json' not found. Proceeding by default...", flush=True)
        return True

    try:
        with topics_file.open("r", encoding="utf-8") as f:
            topics = json.load(f)
    except Exception as e:
        print(f"[WARNING] Error reading topics file: {e}. Proceeding...", flush=True)
        return True

    if not topics or not isinstance(topics, list):
        print("[OK] Topics list is empty. Proceeding...", flush=True)
        return True

    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "content_generated" in item:
            last_processed_item = item

    if last_processed_item is None:
        print("[OK] First post detected (No previous history found). Proceeding...", flush=True)
        return True

    cg = last_processed_item.get("content_generated") is True
    ig = last_processed_item.get("image_generated") is True
    p = last_processed_item.get("posted") is True

    if cg and ig and p:
        print(f"[OK] Last post '{last_processed_item.get('topic')}' has all three keys True. Script continuing.", flush=True)
        return True
    else:
        print(f"[INFO] Script halted! Last post '{last_processed_item.get('topic')}' is not complete: content_generated={cg}, image_generated={ig}, posted={p}.", flush=True)
        return False


def get_next_topic_from_json() -> str:
    print("[STEP] Reading json/umang_fb_topics.json...", flush=True)
    topics_file = Path("json/umang_fb_topics.json")
    if not topics_file.exists():
        raise FileNotFoundError("❌ 'json/umang_fb_topics.json' file not found.")

    with topics_file.open("r", encoding="utf-8") as f:
        topics = json.load(f)

    if not topics:
        raise ValueError("❌ 'json/umang_fb_topics.json' is empty.")

    for item in topics:
        if "content_generated" not in item:
            selected_topic = item["topic"]
            print(f"[OK] Selected next topic: '{selected_topic}'", flush=True)
            return selected_topic

    raise ValueError("❌ No new unprocessed topic found in 'json/umang_fb_topics.json'.")


def update_topic_status_in_json(topic_text: str):
    print("[STEP] Updating topic status in json/umang_fb_topics.json...", flush=True)
    topics_file = Path("json/umang_fb_topics.json")
    if not topics_file.exists():
        return

    with topics_file.open("r", encoding="utf-8") as f:
        topics = json.load(f)

    for item in topics:
        if item.get("topic") == topic_text:
            item["content_generated"] = True
            item["image_generated"] = False
            item["posted"] = False
            break

    with topics_file.open("w", encoding="utf-8") as f:
        json.dump(topics, f, indent=4, ensure_ascii=False)
    print("[OK] Topic status successfully updated (content_generated=True) in json/umang_fb_topics.json", flush=True)


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    apply_auto_correct()

    if not can_run_script():
        print("[INFO] Conditions not met. Exiting script gracefully...", flush=True)
        sys.exit(0)

    post_file = Path("json/post.json")
    with post_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'json/post.json' cleared/initialized at the start of the run.", flush=True)

    try:
        topic = get_next_topic_from_json()
    except Exception as e:
        print(f"[ERROR] Error reading config files: {e}", flush=True)
        sys.exit(1)

    encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError("❌ No .encrypted cookie files found in 'chatgpt_cookies/' folder")

    CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto(CHATGPT_URL, wait_until="load")
        print("[OK] URL opened successfully (Logged In)", flush=True)

        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = resolve_locator(page, PLATFORM, "chatgpt_profile_button", "Check login success via profile button")

        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        print("[STEP] Locating chat textbox...", flush=True)
        textbox = resolve_locator(page, PLATFORM, "chatgpt_textbox", "Locating chat textbox")

        if textbox.count() > 0:
            textbox.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator could not be loaded (All strategies failed).")

        custom_random_wait(MIN_ABSOLUTE_WAIT, MAX_ABSOLUTE_WAIT)

        prompt = (
            f"IMPORTANT: Your entire response must be wrapped inside a single ```json code block. "
            f"STRICTLY: Do not output any text, explanation, markdown, commentary, or notes before or after the JSON code block.\n\n"

            f"You are an elite Facebook ghostwriter who specializes in creating high-performing educational posts that generate engagement, saves, shares, and comments.\n\n"

            f"Write a Facebook post on the topic: '{topic}'.\n\n"

            f"PRIMARY OBJECTIVE:\n"
            f"Create a post that teaches one valuable concept while keeping readers engaged until the end.\n"
            f"The post should feel naturally written by a knowledgeable professional, not by an AI, textbook, lawyer, journalist, or academic writer.\n\n"

            f"TARGET AUDIENCE:\n"
            f"Professionals, founders, students, working adults, and general readers with little or no prior knowledge of the topic.\n\n"

            f"FACEBOOK WRITING RULES:\n"
            f"- Target length: 150-220 words.\n"
            f"- Use short paragraphs.\n"
            f"- Optimize for mobile reading.\n"
            f"- Use natural line breaks frequently.\n"
            f"- Write in a conversational yet authoritative tone.\n"
            f"- Avoid jargon whenever possible.\n"
            f"- If technical terms are necessary, explain them simply.\n"
            f"- Avoid sounding like an article, textbook, legal document, or lecture.\n"
            f"- Avoid generic filler.\n"
            f"- Avoid repeating the same point.\n"
            f"- Avoid phrases such as:\n"
            f"  'It is important to understand'\n"
            f"  'Understanding this helps'\n"
            f"  'In conclusion'\n"
            f"  'Let's discuss'\n"
            f"  'Here's why'\n\n"

            f"CONTENT REQUIREMENTS:\n"
            f"- Start with a strong curiosity-driven hook.\n"
            f"- The first 2 lines must make readers want to continue.\n"
            f"- Introduce a common misconception, surprising fact, or misunderstood aspect of the topic.\n"
            f"- Explain the concept in a simple and practical way.\n"
            f"- Include at least one insight that makes readers think:\n"
            f"  'I didn't know that.'\n"
            f"- Focus on clarity over completeness.\n"
            f"- Readers should learn one useful thing in under 30 seconds.\n\n"

            f"POST STRUCTURE:\n"
            f"1. Hook\n"
            f"2. Misconception or surprising insight\n"
            f"3. Clear explanation\n"
            f"4. Practical takeaway\n"
            f"5. Comment-driving question directly related to the topic\n\n"

            f"OUTPUT FORMAT:\n"
            f"Return ONLY this JSON structure:\n\n"

            f"{{\n"
            f'  "title": "{topic}",\n'
            f'  "p1": "Hook and curiosity-driven opening...",\n'
            f'  "p2": "Misconception or surprising insight...",\n'
            f'  "p3": "Explanation and practical takeaway...",\n'
            f'  "conclusion": "Topic-related question designed to encourage comments...",\n'
            f'  "keywords": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5"]\n'
            f"}}\n\n"

            f"STRICT RULES:\n"
            f"- 'title' must exactly match '{topic}'.\n"
            f"- 'keywords' must contain exactly 5 relevant hashtags without the '#' symbol.\n"
            f"- Do not use markdown headings.\n"
            f"- Do not use numbered lists.\n"
            f"- Do not use bullet points.\n"
            f"- Do not include emojis unless they genuinely improve readability.\n"
            f"- Ensure the conclusion question is directly related to the main insight of the post.\n"
            f"- Return valid JSON only inside a single code block.\n"
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(MIN_ABSOLUTE_WAIT, MAX_ABSOLUTE_WAIT)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = resolve_locator(page, PLATFORM, "chatgpt_send_button", "Locating and clicking send button")
        send_button.click()

        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        print("[STEP] Waiting for generated JSON code block to complete writing (15s checks)...", flush=True)
        code_block_locator = resolve_locator(page, PLATFORM, "chatgpt_code_block", "Waiting for generated JSON code block")

        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)

            if code_block_locator.count() > 0:
                print("[OK] Code block visible, parsing live text size variations...", flush=True)

                last_length = 0
                max_check_cycles = 15

                for cycle in range(max_check_cycles):
                    time.sleep(15)

                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)

                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)

                    if current_length > 0 and current_length == last_length:
                        if current_text.endswith("}"):
                            json_content = current_text
                            print("[OK] Content generation is fully finished and finalized.", flush=True)
                            break
                        else:
                            print("[WARNING] Text generation paused but JSON bracket '}' is missing. Waiting further...", flush=True)

                    last_length = current_length

                if json_content:
                    break

            if attempt < 5:
                print(f"[WARNING] Code block not completely written or block not found. Next retry window...", flush=True)
                custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
            else:
                print("❌ Max retries reached. Streaming incomplete. Exiting script...", flush=True)
                if 'page' in locals() and page:
                    try:
                        screenshot_path = capture_error_screenshot(page)
                        if screenshot_path:
                            upload_to_onlyfiles(screenshot_path)
                    except Exception as screenshot_err:
                        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)

        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]

                parsed_json = json.loads(json_content.strip())
                parsed_json["title"] = topic

                print("[STEP] Saving to json/post.json...", flush=True)
                with post_file.open("w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                print("[OK] Facebook post successfully saved to json/post.json", flush=True)

                update_topic_status_in_json(topic)

            except json.JSONDecodeError as je:
                print(f"[ERROR] Failed to parse content JSON: {je}. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)
        else:
            print("[ERROR] Save skipped because no data was fetched. Exiting script...", flush=True)
            try:
                browser.close()
            except:
                pass
            sys.exit(1)

        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        custom_random_wait(MIN_ABSOLUTE_WAIT, MAX_ABSOLUTE_WAIT)

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
                print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
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

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()
