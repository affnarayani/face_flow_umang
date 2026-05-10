import json
import os
import sys
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

HEADLESS = False
COOKIES_FILE = "cookies.json"

FACEBOOK_URL = "https://www.facebook.com/"
TWO_STEP_URL_FRAGMENT = "facebook.com/two_step_verification/authentication"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# -------------------- LOAD CREDENTIALS --------------------
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


# -------------------- LOAD COOKIES --------------------
def load_cookies():
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except FileNotFoundError:
        return []

    # normalize SameSite
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


# -------------------- SAVE COOKIES --------------------
def save_cookies(context):
    time.sleep(5)
    cookies = context.cookies()

    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)


# -------------------- LOGIN --------------------
def perform_login(page, email, password):
    page.goto(FACEBOOK_URL, wait_until="domcontentloaded")

    page.get_by_role("textbox", name="Email address or mobile number").fill(email)
    page.get_by_role("textbox", name="Password").fill(password)
    page.get_by_role("button", name="Log in").click()


# -------------------- WAIT LOGIN --------------------
def wait_for_login(page, context, timeout=120):
    start = time.time()
    shown = False

    while True:
        url = page.url

        if TWO_STEP_URL_FRAGMENT in url and not shown:
            print("Complete 2FA manually in browser...")
            shown = True

        cookies = context.cookies()
        if any(c.get("name") == "c_user" for c in cookies):
            return

        if time.time() - start > timeout:
            raise PlaywrightTimeoutError("Login timeout")

        time.sleep(2)


# -------------------- MAIN --------------------
def main():
    email, password = load_credentials()
    cookies = load_cookies()

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
            user_agent=USER_AGENT
        )

        # load cookies if available
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()

        # login flow
        perform_login(page, email, password)

        try:
            wait_for_login(page, context)
        except PlaywrightTimeoutError:
            print("Login failed / 2FA not completed")
            sys.exit(1)

        # save updated cookies
        save_cookies(context)

        print("Login successful + cookies saved")

        page.wait_for_timeout(10000)

    finally:
        pw_cm.__exit__(None, None, None)


if __name__ == "__main__":
    main()