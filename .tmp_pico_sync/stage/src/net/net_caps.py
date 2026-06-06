# src/net/net_caps.py  (MicroPython / Pico-safe)

def wifi_supported() -> bool:
    """
    True only when WiFi hardware is available.
    On non-WiFi RP2040 builds, `import network` usually fails.
    On Pico W builds it should succeed.
    On ESP32, WiFi silicon is always present — skip the WLAN() probe to
    avoid stranding RX DMA buffers in the heap when the driver fails to
    fully initialise and deinit during the capability check.
    """
    try:
        import network  # type: ignore
    except Exception:
        return False

    try:
        import sys
        if sys.platform == "esp32":
            return True  # WiFi always present; skip WLAN() to protect heap
    except Exception:
        pass

    try:
        _ = network.WLAN(network.STA_IF)
        return True
    except Exception:
        return False
