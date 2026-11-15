import json
import os
import sys
import time
import random
import shutil
import tempfile
import zipfile
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from common.config_loader import get
from common.locator_loader import resolve_locator
from encrypt import encrypt_file

HEADLESS = False
USER_AGENT = get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
EXTENSION_ZIP = Path("extension/nopecha.zip")
COOKIES_FILE = Path("cookies.json")
MAX_CAPTCHA_WAIT_SECONDS = 180

FACEBOOK_URL = "https://www.facebook.com/"
TWO_STEP_URL_FRAGMENT = "facebook.com/two_step_verification/authentication"

PLATFORM = "facebook"

_EXTRACTED_EXTENSION_DIR = None


def _find_extension_root(extract_dir: str) -> str:
    for root, _dirs, files in os.walk(extract_dir):
        if "manifest.json" in files:
            return root
    return extract_dir


def _prepare_extension_dir():
    global _EXTRACTED_EXTENSION_DIR
    if not os.path.exists(EXTENSION_ZIP):
        raise FileNotFoundError(f"Extension zip not found: {EXTENSION_ZIP}")
    if _EXTRACTED_EXTENSION_DIR is None:
        _EXTRACTED_EXTENSION_DIR = tempfile.mkdtemp(prefix="nopecha_ext_")
        with zipfile.ZipFile(EXTENSION_ZIP, "r") as zf:
            zf.extractall(_EXTRACTED_EXTENSION_DIR)
        ext_root = _find_extension_root(_EXTRACTED_EXTENSION_DIR)
        manifest = os.path.join(ext_root, "manifest.json")
        print(f"[INFO] Extracted extension to: {_EXTRACTED_EXTENSION_DIR}", flush=True)
        print(f"[INFO] Extension root (manifest): {ext_root}", flush=True)
        print(f"[INFO] Manifest exists: {os.path.exists(manifest)}", flush=True)
        _EXTRACTED_EXTENSION_DIR = ext_root
    return _EXTRACTED_EXTENSION_DIR


def _cleanup_extension_dir():
    global _EXTRACTED_EXTENSION_DIR
    if _EXTRACTED_EXTENSION_DIR and os.path.exists(_EXTRACTED_EXTENSION_DIR):
        try:
            shutil.rmtree(_EXTRACTED_EXTENSION_DIR, ignore_errors=True)
        except Exception:
            pass


def load_credentials():
    load_dotenv()

    email = os.getenv("FACEBOOK_EMAIL")
    password = os.getenv("FACEBOOK_PASSWORD")

    if not email or not password:
        missing = []
        if not email:
            missing.append("FACEBOOK_EMAIL")
        if not password:
            missing.append("FACEBOOK_PASSWORD")
        raise EnvironmentError("Missing env vars: " + ", ".join(missing))

    return email, password


def load_cookies():
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except FileNotFoundError:
        return []

    for c in cookies:
        if "sameSite" in c:
            val = str(c["sameSite"]).lower()

            if val in ["no_restriction", "none", "unspecified", "null"]:
                c["sameSite"] = "None"
            elif val == "lax":
                c["sameSite"] = "Lax"
            elif val == "strict":
                c["sameSite"] = "Strict"
            else:
                c["sameSite"] = "Lax"

    return cookies


def save_cookies(context):
    time.sleep(5)
    cookies = context.cookies()

    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)


def perform_login(page, email, password):
    page.goto(FACEBOOK_URL, wait_until="domcontentloaded")
    time.sleep(random.uniform(6, 12))
    resolve_locator(page, PLATFORM, "facebook_email_input", "Email input").fill(email)
    time.sleep(random.uniform(6, 12))
    resolve_locator(page, PLATFORM, "facebook_password_input", "Password input").fill(password)
    time.sleep(random.uniform(6, 12))
    resolve_locator(page, PLATFORM, "facebook_login_button", "Log in button").click()
    time.sleep(random.uniform(6, 12))


def wait_for_captcha_solve(page, context, timeout=MAX_CAPTCHA_WAIT_SECONDS):
    print(f"[STEP] Waiting for captcha solve (max {timeout}s)...", flush=True)
    start = time.time()
    shown = False

    while True:
        url = page.url

        if TWO_STEP_URL_FRAGMENT in url and not shown:
            print("Complete 2FA manually in browser...")
            shown = True

        cookies = context.cookies()
        if any(c.get("name") == "c_user" for c in cookies):
            print("[OK] Captcha solved, login cookies detected.", flush=True)
            return

        if time.time() - start > timeout:
            raise PlaywrightTimeoutError("Captcha solve timeout")

        time.sleep(2)


def main():
    email, password = load_credentials()
    cookies = load_cookies()

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    ext_dir = _prepare_extension_dir()

    user_data_dir = tempfile.mkdtemp(prefix="fb_cookie_profile_")

    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=HEADLESS,
            no_viewport=True,
            user_agent=USER_AGENT,
            args=[
                f"--load-extension={ext_dir}",
                f"--disable-extensions-except={ext_dir}",
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        page = context.pages[0] if context.pages else context.new_page()

        if cookies:
            context.add_cookies(cookies)

        perform_login(page, email, password)

        try:
            wait_for_captcha_solve(page, context)
        except PlaywrightTimeoutError:
            print("Login failed / captcha not completed")
            sys.exit(1)

        save_cookies(context)

        encrypt_file(Path("cookies.json"), Path("fb_cookies/fb_cookies.json.encrypted"), os.getenv("DECRYPT_KEY"))

        print("Login successful + cookies saved")

        page.wait_for_timeout(10000)

    finally:
        _cleanup_extension_dir()
        try:
            if user_data_dir and os.path.exists(user_data_dir):
                shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            pw_cm.__exit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    main()
