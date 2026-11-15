import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from common.config_loader import get
from common.gemini_vision import gemini_vision_fallback

LOCATORS_DIR = Path(__file__).resolve().parent.parent / "locators"
CHANGED_LOCATORS_FILE = LOCATORS_DIR / "changed_locators.json"

_locator_cache: Dict[str, Dict[str, List[str]]] = {}
_changed_cache: Optional[List[Dict[str, str]]] = None


def _load_platform_locators(platform: str) -> Dict[str, List[str]]:
    if platform in _locator_cache:
        return _locator_cache[platform]

    locator_file = LOCATORS_DIR / f"{platform}_locators.txt"
    locators: Dict[str, List[str]] = {}

    if locator_file.exists():
        with locator_file.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.rstrip("\n")
                if not line.strip() or line.strip().startswith("#"):
                    continue
                if "=" not in line:
                    continue
                name, expression = line.split("=", 1)
                name = name.strip()
                expression = expression.strip()
                if name and expression:
                    locators.setdefault(name, []).append(expression)

    _locator_cache[platform] = locators
    return locators


def _save_platform_locators(platform: str, locators: Dict[str, List[str]]):
    locator_file = LOCATORS_DIR / f"{platform}_locators.txt"
    lines = []
    for name, expressions in locators.items():
        for expression in expressions:
            lines.append(f"{name} = {expression}")
    with locator_file.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _locator_cache[platform] = locators


def load_changed_locators() -> List[Dict[str, str]]:
    global _changed_cache
    if _changed_cache is not None:
        return _changed_cache
    if not CHANGED_LOCATORS_FILE.exists():
        _changed_cache = []
        return _changed_cache
    with CHANGED_LOCATORS_FILE.open("r", encoding="utf-8") as f:
        _changed_cache = json.load(f)
    return _changed_cache


def save_changed_locators(entries: List[Dict[str, str]]):
    global _changed_cache
    _changed_cache = entries
    with CHANGED_LOCATORS_FILE.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _is_valid_suggested(suggested_locator: str) -> bool:
    if not suggested_locator:
        return False
    stripped = suggested_locator.strip()
    if stripped.startswith("#"):
        return False
    if not stripped.startswith("page.locator("):
        return False
    return True


def record_broken_locator(
    platform: str,
    locator_name: str,
    broke_locator: str,
    suggested_locator: str,
):
    if not _is_valid_suggested(suggested_locator):
        print(
            f"[WARNING] Skipping changed_locators record for '{locator_name}': "
            "suggested locator must be wrapped in page.locator(...) and not start with #",
            flush=True,
        )
        return

    entries = load_changed_locators()

    existing_idx = None
    for i, entry in enumerate(entries):
        if (
            entry.get("platform") == platform
            and entry.get("locator_name") == locator_name
            and entry.get("broke_locator") == broke_locator
        ):
            existing_idx = i
            break

    entry = {
        "platform": platform,
        "locator_name": locator_name,
        "broke_locator": broke_locator,
        "suggested_locator": suggested_locator,
    }

    if existing_idx is not None:
        entries[existing_idx] = entry
    else:
        entries.append(entry)

    save_changed_locators(entries)
    print(
        f"[OK] Recorded broken locator in changed_locators.json: {locator_name}",
        flush=True,
    )


def _expression_in_locators(platform: str, expression: str) -> bool:
    locators = _load_platform_locators(platform)
    for exprs in locators.values():
        if expression in exprs:
            return True
    return False


def _locator_names_for_expression(platform: str, expression: str) -> List[str]:
    locators = _load_platform_locators(platform)
    names = []
    for name, exprs in locators.items():
        if expression in exprs:
            names.append(name)
    return names


def remove_changed_locator_entry(platform: str, locator_name: str):
    entries = load_changed_locators()
    filtered = [
        e
        for e in entries
        if not (e.get("platform") == platform and e.get("locator_name") == locator_name)
    ]
    if len(filtered) != len(entries):
        save_changed_locators(filtered)
        print(
            f"[OK] Removed resolved locator entry from changed_locators.json: {locator_name}",
            flush=True,
        )


def apply_auto_correct():
    auto_correct = get("auto_correct_locator", False)
    if not auto_correct:
        print("[INFO] auto_correct_locator is OFF. Checking resolved entries...", flush=True)
        entries = load_changed_locators()
        still_broken = []
        for entry in entries:
            platform = entry.get("platform")
            locator_name = entry.get("locator_name")
            suggested = entry.get("suggested_locator", "")
            if _expression_in_locators(platform, suggested):
                print(
                    f"[INFO] Suggested locator already present in txt for '{locator_name}'. Removing entry.",
                    flush=True,
                )
            else:
                still_broken.append(entry)
        save_changed_locators(still_broken)
        return

    print("[INFO] auto_correct_locator is ON. Applying changed locators...", flush=True)
    entries = load_changed_locators()
    remaining = []

    for entry in entries:
        platform = entry.get("platform")
        locator_name = entry.get("locator_name")
        broke_locator = entry.get("broke_locator", "")
        suggested_locator = entry.get("suggested_locator", "")

        locators = _load_platform_locators(platform)
        if locator_name not in locators:
            remaining.append(entry)
            continue

        expressions = locators[locator_name]
        if broke_locator not in expressions:
            remaining.append(entry)
            continue

        # Append suggested as a fallback under the same name (duplicate name)
        updated_expressions = list(expressions)
        updated_expressions.append(suggested_locator)
        locators[locator_name] = updated_expressions
        _save_platform_locators(platform, locators)
        print(
            f"[OK] Applied auto-correct for '{locator_name}' in {platform}_locators.txt (appended fallback with same name)",
            flush=True,
        )

    save_changed_locators(remaining)
    print("[INFO] Auto-correct applied. changed_locators.json updated.", flush=True)


def eval_locator(page, expression: str):
    namespace = {"page": page, "re": re}
    return eval(expression, {"__builtins__": {}}, namespace)


def get_locator_expressions(platform: str, name: str) -> Optional[List[str]]:
    locators = _load_platform_locators(platform)
    return locators.get(name)


def resolve_locator(page, platform: str, name: str, purpose: str = "", optional: bool = False):
    expressions = get_locator_expressions(platform, name)
    if not expressions:
        if optional:
            return None
        raise RuntimeError(f"Locator '{name}' not found for platform '{platform}'")

    # Try each expression in order: primary first, then fallbacks (same name duplicates)
    for expression in expressions:
        try:
            locator = eval_locator(page, expression)
            if locator.count() > 0:
                remove_changed_locator_entry(platform, name)
                return locator
        except Exception as eval_err:
            if not optional:
                print(f"[WARNING] Locator '{name}' expression failed: {eval_err}", flush=True)

    if optional:
        return None

    print(f"[INFO] All expressions for '{name}' failed. Trying Gemini Vision fallback...", flush=True)
    # Use the primary (first) expression as the reference for vision fallback
    primary_expression = expressions[0]
    fallback_expression = gemini_vision_fallback(page, name, purpose, primary_expression)
    if fallback_expression and _is_valid_suggested(fallback_expression):
        try:
            locator = eval_locator(page, fallback_expression)
            if locator.count() > 0:
                print(f"[OK] Gemini Vision fallback locator succeeded for '{name}'", flush=True)
                record_broken_locator(platform, name, primary_expression, fallback_expression)
                remove_changed_locator_entry(platform, name)
                return locator
        except Exception as fb_err:
            print(f"[WARNING] Gemini Vision fallback eval failed for '{name}': {fb_err}", flush=True)

    if fallback_expression and _is_valid_suggested(fallback_expression):
        record_broken_locator(platform, name, primary_expression, fallback_expression)

    raise RuntimeError(f"Locator '{name}' could not be resolved (all expressions + Gemini Vision fallback failed)")


def get_locator_expression(platform: str, name: str) -> Optional[List[str]]:
    return get_locator_expressions(platform, name)
