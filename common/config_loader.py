import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "json" / "config.json"

_config_cache = None


def load_config(config_path=CONFIG_PATH):
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    with open(config_path, "r", encoding="utf-8") as f:
        _config_cache = json.load(f)
    return _config_cache


def get(section_key, default=None, config_path=CONFIG_PATH):
    config = load_config(config_path)
    return config.get(section_key, default)
