# src/ui/glyphs_pico.py — Pico-trimmed pixel glyphs
# Deployed to Pico as src/ui/glyphs.py by airbuddy_pico_sync.sh.
#
# Removed vs full glyphs.py (~422 lines cut, ~46% reduction):
#   - draw_face() + all _f_* vector helpers — used only by frowny.py (excluded from Pico)
#   - draw_compass()                        — compass.py excluded from Pico
#   - draw_gear()                           — servo.py excluded from Pico
#   - draw_battery_v() / battery_status()   — battery.py excluded from Pico (for now)
#
# Kept (all used on Pico):
#   draw_degree, draw_circle, draw_c, draw_sub2
#   _FACE_9PX / draw_face9   (thermobar — eco2/tvoc screens)
#   CLOCK_W / CLOCK_H / draw_clock          (temp screen)
#   draw_wifi / draw_gps / draw_api + state constants  (connection_header)

import time

# ------------------------------------------------------------
# Low-level helpers
# ------------------------------------------------------------

def _pix(fb, x, y, c=1):
    try:
        fb.pixel(int(x), int(y), int(c))
    except Exception:
        pass


def _hline(fb, x, y, w, c=1):
    try:
        fb.hline(int(x), int(y), int(w), int(c))
    except Exception:
        for i in range(int(w)):
            _pix(fb, x + i, y, c)


def _vline(fb, x, y, h, c=1):
    try:
        fb.vline(int(x), int(y), int(h), int(c))
    except Exception:
        for i in range(int(h)):
            _pix(fb, x, y + i, c)


def _fill_rect(fb, x, y, w, h, c=1):
    try:
        fb.fill_rect(int(x), int(y), int(w), int(h), int(c))
    except Exception:
        for yy in range(int(h)):
            _hline(fb, x, y + yy, w, c)


def draw_bitmap_rows(fb, x, y, rows, c=1):
    """rows: list[str] of '0'/'1'. Top-left at (x, y)."""
    x = int(x)
    y = int(y)
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "1":
                _pix(fb, x + rx, y + ry, c)


# ------------------------------------------------------------
# Degree ring (pixel)
# ------------------------------------------------------------

def draw_degree(fb, x, y, r=2, color=1):
    x = int(x); y = int(y); r = int(r)
    cx = x + r; cy = y + r
    pts = [
        (0, r), (1, r), (2, r - 1),
        (r, 0), (r, 1), (r - 1, 2),
    ]
    for dx, dy in pts:
        for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            _pix(fb, cx + sx * dx, cy + sy * dy, color)
            _pix(fb, cx + sx * dy, cy + sy * dx, color)


# ------------------------------------------------------------
# Circle (pixel)
# ------------------------------------------------------------

def draw_circle(fb, cx, cy, r=4, filled=False, color=1):
    cx = int(cx); cy = int(cy); r = int(r)
    pts = [
        (0, r), (1, r), (2, r - 1), (3, r - 2),
        (r, 0), (r, 1), (r - 1, 2), (r - 2, 3),
    ]
    for dx, dy in pts:
        for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            _pix(fb, cx + sx * dx, cy + sy * dy, color)
            _pix(fb, cx + sx * dy, cy + sy * dx, color)
    if filled:
        _fill_rect(fb, cx - 1, cy - 1, 3, 3, color)


# ------------------------------------------------------------
# Pixel "C" glyph (for LARGE temp units)
# ------------------------------------------------------------

def draw_c(fb, x, y, scale=1, color=1):
    rows = [
        "0111110",
        "1100011",
        "1100000",
        "1100000",
        "1100000",
        "1100000",
        "1100011",
        "0111110",
        "0000000",
    ]
    x = int(x); y = int(y); scale = max(1, int(scale))
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "1":
                _fill_rect(fb, x + rx * scale, y + ry * scale, scale, scale, color)


# ------------------------------------------------------------
# Subscript "2" glyph (₂) for CO₂ in MED
# ------------------------------------------------------------

def draw_sub2(fb, x, y, scale=1, color=1):
    rows = [
        "1110",
        "0010",
        "1110",
        "1000",
        "1110",
    ]
    x = int(x); y = int(y); scale = max(1, int(scale))
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "1":
                _fill_rect(fb, x + rx * scale, y + ry * scale, scale, scale, color)


# ------------------------------------------------------------
# 9px-high face glyphs for thermo-bar labels
# ------------------------------------------------------------

_FACE_9PX = {
    "good": [
        "11000000011",
        "11000000011",
        "00000000000",
        "00000000000",
        "00000000000",
        "01000000010",
        "00100000100",
        "00011111000",
        "00000000000",
    ],
    "ok": [
        "11000000011",
        "11000000011",
        "00000000000",
        "00000000000",
        "00000000000",
        "00111111000",
        "00111111000",
        "00000000000",
        "00000000000",
    ],
    "poor": [
        "11000000011",
        "11000000011",
        "00000000000",
        "00000000000",
        "00011111000",
        "00100000100",
        "01000000010",
        "00000000000",
        "00000000000",
    ],
    "bad": [
        "11000000011",
        "11000000011",
        "00000000000",
        "00000000000",
        "01111111110",
        "01000000010",
        "00100000100",
        "00000000000",
        "00000000000",
    ],
    "verybad": [
        "10100000101",
        "01000000010",
        "00000000000",
        "00000000000",
        "01111111110",
        "01000000010",
        "00100000100",
        "00000000000",
        "00000000000",
    ],
}


def draw_face9(fb, x, y, mood="ok", scale=1, color=1):
    rows = _FACE_9PX.get(str(mood).lower(), _FACE_9PX["ok"])
    x = int(x); y = int(y); scale = max(1, int(scale))
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "1":
                _fill_rect(fb, x + rx * scale, y + ry * scale, scale, scale, color)


# ------------------------------------------------------------
# Clock glyph (9x9) — used by temp screen
# ------------------------------------------------------------

CLOCK_W = 9
CLOCK_H = 9

_CLOCK_9 = [
    "000111000",
    "011010110",
    "010010010",
    "100010001",
    "100011111",
    "100000001",
    "010000010",
    "011000110",
    "000111000",
]


def draw_clock(fb, x, y, color=1):
    draw_bitmap_rows(fb, x, y, _CLOCK_9, c=color)


# ------------------------------------------------------------
# WiFi indicator (9x6)
# ------------------------------------------------------------

_WIFI_ON_6 = [
    "111111111",
    "111111111",
    "011111110",
    "001111100",
    "000111000",
    "000010000",
]

_WIFI_OFF_6 = [
    "111111111",
    "100000001",
    "010000010",
    "001000100",
    "000101000",
    "000010000",
]


def draw_wifi(fb, x, y, on=True, color=1):
    rows = _WIFI_ON_6 if bool(on) else _WIFI_OFF_6
    draw_bitmap_rows(fb, x, y, rows, c=color)


def draw_wifi9(fb, x, y, on=True, color=1):
    draw_wifi(fb, x, y, on=on, color=color)


# ------------------------------------------------------------
# GPS indicator (14x6) — three states
# ------------------------------------------------------------

GPS_NONE  = 0
GPS_INIT  = 1
GPS_FIXED = 2

_GPS_TRI_6 = [
    "00000000000001",
    "00000000000011",
    "00000000000111",
    "00000000001111",
    "00000000011111",
    "00000000111111",
]

_GPS_EMPTY_6 = [
    "00000000000001",
    "00000000000011",
    "00000000000101",
    "00000000001001",
    "00000000010001",
    "00000000111111",
]

_GPS_PART_6 = [
    "00000000000001",
    "00000000000011",
    "00000000000101",
    "00000000001001",
    "00000000011111",
    "00000000111111",
]


def draw_gps(fb, x, y, on=True, color=1, state=None):
    if state is None:
        state = GPS_FIXED if bool(on) else GPS_NONE
    state = int(state)
    if state == GPS_NONE:
        draw_bitmap_rows(fb, x, y, _GPS_EMPTY_6, c=color)
    elif state == GPS_INIT:
        draw_bitmap_rows(fb, x, y, _GPS_PART_6, c=color)
    else:
        draw_bitmap_rows(fb, x, y, _GPS_TRI_6, c=color)


def draw_gps9(fb, x, y, on=True, color=1, state=None):
    draw_gps(fb, x, y, on=on, color=color, state=state)


# ------------------------------------------------------------
# API indicator (7x6)
# ------------------------------------------------------------

_API_RING_6 = [
    "0011100",
    "0100010",
    "1000001",
    "1000001",
    "0100010",
    "0011100",
]

_API_FILLED_6 = [
    "0011100",
    "1111111",
    "1111111",
    "1111111",
    "1111111",
    "0011100",
]


def _api_heartbeat_on(now_ms=None, sending=False):
    try:
        if now_ms is None:
            now_ms = time.ticks_ms()
    except Exception:
        now_ms = int(time.time() * 1000)

    if sending:
        t = now_ms % 2000
        return 500 <= t < 1000 or t >= 1500
    else:
        t = now_ms % 8200
        if t < 7000:
            return True
        burst = (t - 7000) // 200
        return burst % 2 == 1


def _api_center_dot_xy():
    return 3, 2


def _api_draw_center_dot(fb, x, y, on=True):
    dx, dy = _api_center_dot_xy()
    try:
        fb.pixel(int(x) + dx, int(y) + dy, 1 if on else 0)
    except Exception:
        pass


def draw_api(fb, x, y, on=True, color=1, *, heartbeat=False, sending=False, now_ms=None):
    x = int(x); y = int(y)

    if not bool(on):
        filled = False
    elif heartbeat:
        filled = _api_heartbeat_on(now_ms=now_ms, sending=sending)
    else:
        filled = True

    if filled:
        draw_bitmap_rows(fb, x, y, _API_FILLED_6, c=color)
    else:
        draw_bitmap_rows(fb, x, y, _API_RING_6, c=color)
