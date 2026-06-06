# src/hal/platform.py
# Tiny platform detection for MicroPython targets.
#
# Detection order (highest priority first):
#   1. config.json "board_type" key  — explicit user override, set once at setup
#   2. uos.uname().machine string    — automatic (works when firmware includes board name)
#   3. sys.platform fallback         — least precise, cannot distinguish ESP32 variants

import sys

_KNOWN_TAGS = ("pico", "esp32", "esp32s3", "xiao_esp32s3")


def platform_tag() -> str:
    # 1. Explicit override in config.json (highest priority).
    #    Reads the file directly — config.py is not imported to avoid circular deps.
    #    Silently ignored if the file is missing or malformed (fresh device).
    try:
        import json
        with open("config.json") as _f:
            _cfg = json.load(_f)
        _bt = str(_cfg.get("board_type", "") or "").strip().lower()
        if _bt in _KNOWN_TAGS:
            return _bt
    except Exception:
        pass

    # 2. uos.uname().machine — works when firmware encodes the board name.
    #    All ESP32 variants return sys.platform == "esp32", so uname is the
    #    only automatic way to tell them apart.
    try:
        import uos
        m = (uos.uname().machine or "").lower()
        if "rp2040" in m or "pico" in m:
            return "pico"
        if "xiao" in m:
            return "xiao_esp32s3"
        if "esp32s3" in m:
            return "esp32s3"
        if "esp32" in m:
            return "esp32"
    except Exception:
        pass

    # 3. sys.platform fallback — cannot distinguish ESP32-S3 from plain ESP32.
    p = getattr(sys, "platform", "") or ""
    p = p.lower()
    if "rp2" in p:
        return "pico"
    if "esp32" in p:
        return "esp32"

    return "unknown"
