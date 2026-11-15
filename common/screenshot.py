import os
from pathlib import Path

import requests

from common.config_loader import get

ERROR_DIR = Path(__file__).resolve().parent.parent / "error"
ERROR_DIR.mkdir(parents=True, exist_ok=True)
ERROR_SCREENSHOT_PATH = ERROR_DIR / "error_screenshot.png"


def capture_error_screenshot(page) -> str:
    try:
        page.screenshot(path=str(ERROR_SCREENSHOT_PATH), full_page=True)
        print(f"[OK] Error screenshot captured: {ERROR_SCREENSHOT_PATH}", flush=True)
        return str(ERROR_SCREENSHOT_PATH)
    except Exception as e:
        print(f"[WARNING] Could not capture error screenshot: {e}", flush=True)
        return None


def upload_to_onlyfiles(screenshot_path: str):
    if not screenshot_path:
        return None
    url = get("onlyfiles_upload_url", "https://api.onlyfiles.com/v1/upload")
    expire = get("onlyfiles_expire", 172800)

    try:
        with open(screenshot_path, "rb") as file:
            response = requests.post(url, files={"file": file}, data={"expire": expire})

        if response.status_code == 200:
            res_data = response.json()
            if res_data.get("status"):
                direct_url = res_data["data"]["file"]["url"]["full"]
                print(f"👉 DIRECT LINK (Expires in 48 Hours): {direct_url}")
                return direct_url
            error = res_data.get("error", {})
            print(f"[WARNING] Upload Failed: {error.get('message', 'Unknown error')}")
        else:
            print(f"[WARNING] Upload Failed: {response.status_code}")
    except Exception as e:
        print(f"[WARNING] Could not upload screenshot: {e}", flush=True)
    return None
