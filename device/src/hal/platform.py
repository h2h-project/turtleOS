# src/hal/platform.py
# Tiny platform detection for MicroPython targets.

import sys

def platform_tag() -> str:
    # Common sys.platform values:
    #  - 'rp2'   for Raspberry Pi Pico / Pico W (RP2040)
    #  - 'esp32'  for ESP32 AND ESP32-S3 (MicroPython does not distinguish here)
    #
    # To tell ESP32 from ESP32-S3 we must check uos.uname().machine,
    # which contains e.g. "ESP32S3 module" on S3 firmware.
    # Always check uname first so the S3 is never misidentified as plain ESP32.
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

    # Fallback: sys.platform (less precise — cannot distinguish S3 from ESP32)
    p = getattr(sys, "platform", "") or ""
    p = p.lower()
    if "rp2" in p:
        return "pico"
    if "esp32" in p:
        return "esp32"

    return "unknown"
