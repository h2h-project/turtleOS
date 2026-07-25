# src/net/wifi_manager_null.py  (MicroPython / Pico-safe)
class NullWiFiManager:
    """
    Drop-in replacement for WiFiManager on non-WiFi boards.
    Provides the methods your UI / telemetry expects.
    """
    supported = False

    def __init__(self, *args, **kwargs):
        self._connected = False
        self._last_err = "No WiFi hardware"

    def connect(self, *args, **kwargs):
        self._connected = False
        return False

    def tick(self, *args, **kwargs):
        return False

    def is_connected(self):
        return False

    def rssi(self):
        return None

    def last_error(self):
        return self._last_err

    def status_dict(self):
        return {
            "supported": False,
            "connected": False,
            "rssi": None,
            "error": self._last_err,
        }
