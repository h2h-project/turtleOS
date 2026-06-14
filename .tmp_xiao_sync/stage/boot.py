import sys
if "/src" not in sys.path:
    sys.path.append("/src")
if "/src/lib" not in sys.path:
    sys.path.append("/src/lib")

try:
    import esp
    esp.osdebug(None)   # suppress all ESP-IDF C-level log output
except Exception:
    pass
