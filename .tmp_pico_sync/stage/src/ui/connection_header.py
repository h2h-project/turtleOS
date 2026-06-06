# src/ui/connection_header.py
#
# Consolidated connectivity status header: GPS  API  WiFi
#
# Draws a right-aligned cluster of three icons at the top of any screen.
# Import and call draw() from any screen that needs the connectivity row.
#
# GPS states (re-exported for callers):
#   GPS_NONE  (0) — no hardware / disabled        → outline triangle
#   GPS_INIT  (1) — hardware present, no fix       → partially filled triangle
#   GPS_FIXED (2) — has satellite fix              → fully filled triangle
#
# Live probing
# ------------
# WiFi:  draw() always probes network.WLAN live — a fast C-level flag read,
#        no socket or I/O.  The wifi_ok parameter is accepted for backward
#        compatibility but is no longer used; the live result always wins.
#
# API:   HTTP cannot be performed inside a draw call.  Instead a module-level
#        boolean _api_ok is maintained.  Call set_api_ok(True/False) from the
#        telemetry scheduler after each POST attempt.  draw() uses that cached
#        value when the caller passes api_connected=None (the default).
#        Callers that pass an explicit True/False still override the cache for
#        that call (and update the cache so later draw() calls stay in sync).

from src.ui.glyphs import draw_wifi, draw_gps, draw_api
from src.ui.glyphs import GPS_NONE, GPS_INIT, GPS_FIXED  # noqa: F401 — re-exported

# Icon pixel dimensions (callers may import for layout math)
WIFI_W = 9
WIFI_H = 6
API_W  = 7
API_H  = 6
GPS_W  = 14
GPS_H  = 6
HEIGHT = 6   # height of the header strip

# ---------------------------------------------------------------------------
# Module-level state cache — mirrors set_api_ok() pattern.
# ---------------------------------------------------------------------------
_api_ok = False
_gps_state = 0  # 0 == GPS_NONE

# _wifi_hw_enabled: True  = WiFi driver was initialised at boot (safe to probe live)
#                   False = WiFi was disabled/skipped — do NOT touch network.WLAN()
#                           because on ESP32 that triggers a driver init attempt
#                           which OOMs on a tight post-boot heap.
# Defaults to True so Pico W (always-on driver) works without explicit setup.
_wifi_hw_enabled = True


def set_api_ok(ok):
    """
    Update the cached API reachability flag.
    Call this from the telemetry scheduler after each POST attempt so that
    every screen's connection header reflects the actual server state.
    """
    global _api_ok
    _api_ok = bool(ok)


def set_gps_state(state):
    """
    Update the cached GPS state (GPS_NONE=0, GPS_INIT=1, GPS_FIXED=2).
    Call from the main loop whenever status["gps_on"] changes, and from
    waiting.py's _apply_idle_ret so all carousel screens see the current value.
    """
    global _gps_state
    _gps_state = int(state)


def get_gps_state():
    """Return the cached GPS state integer."""
    return _gps_state


def set_wifi_enabled(ok):
    """
    Tell the header whether the WiFi driver was actually started this boot.
    Call set_wifi_enabled(False) when wifi_enabled=False in config so that
    _probe_wifi() never touches network.WLAN() (which on ESP32 attempts a
    driver init and spams OOM errors on every draw when heap is tight).
    """
    global _wifi_hw_enabled
    _wifi_hw_enabled = bool(ok)


def _probe_wifi():
    """
    Live WiFi check via MicroPython network module.
    Short-circuits to False when the driver was never started (ESP32 safety).
    On Pico W the driver is always alive so the live probe always runs.
    """
    if not _wifi_hw_enabled:
        return False
    try:
        import network
        wlan = network.WLAN(network.STA_IF)
        return bool(wlan.active() and wlan.isconnected())
    except Exception:
        return False


def draw(
        fb,
        oled_width,
        gps_state=GPS_NONE,
        api_connected=None,
        wifi_ok=None,          # accepted for compat; live probe is used unless wifi_override set
        wifi_override=None,    # when not None, forces WiFi icon state (bypasses live probe)
        api_sending=False,
        now_ms=None,
        icon_y=1,
        right_inset=1,
        gap=4,
):
    """
    Draw the right-aligned GPS / API / WiFi status cluster.

    Cluster layout (right-to-left): WiFi — gap — API — gap — GPS

    Parameters
    ----------
    fb            : framebuf / SSD1306 framebuffer
    oled_width    : screen width in pixels
    gps_state     : GPS_NONE (0), GPS_INIT (1), or GPS_FIXED (2)
    api_connected : True/False to override the module cache, or None to use it
    wifi_ok       : deprecated — live probe is always used unless wifi_override is set
    wifi_override : when not None, forces WiFi icon True/False (used for flash animation)
    api_sending   : True during an active telemetry send pulse
    now_ms        : current time.ticks_ms() value, or None to sample internally
    icon_y        : top-y pixel of the icon row
    right_inset   : pixels inset from right edge before the first icon
    gap           : pixels between icons
    """
    # WiFi: use override when provided (animation), else live probe.
    if wifi_override is not None:
        wifi_actual = bool(wifi_override)
    else:
        wifi_actual = _probe_wifi()

    # API: use the caller-supplied value if explicit; fall back to cache.
    # The cache is only updated via set_api_ok() — passing an explicit value
    # here is a local display decision and must NOT overwrite the cache.
    if api_connected is None:
        api_actual = _api_ok
    else:
        api_actual = bool(api_connected)

    w = int(oled_width)
    y = int(icon_y)
    g = int(gap)
    x = w - int(right_inset)

    # WiFi (rightmost)
    x -= WIFI_W
    fb.fill_rect(x, y, WIFI_W, WIFI_H, 0)
    draw_wifi(fb, x, y, on=wifi_actual, color=1)
    x -= g

    # API
    x -= API_W
    fb.fill_rect(x, y, API_W, API_H, 0)
    draw_api(
        fb, x, y,
        on=bool(api_actual),
        heartbeat=bool(api_actual),
        sending=bool(api_sending),
        color=1,
        now_ms=now_ms,
    )
    x -= g

    # GPS (leftmost in cluster)
    x -= GPS_W
    fb.fill_rect(x, y, GPS_W, GPS_H, 0)
    draw_gps(fb, x, y, state=int(gps_state), color=1)
