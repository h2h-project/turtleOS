# src/ui/glyphs.py — tiny pixel glyphs for SSD1306/framebuf
# Pico / MicroPython safe

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
    """
    rows: list[str] of '0'/'1' where each string is a row of pixels.
    Top-left at (x, y).
    """
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
    """
    Small hollow degree ring.
    (x, y) is top-left-ish anchor used in your screens.
    """
    x = int(x)
    y = int(y)
    r = int(r)
    cx = x + r
    cy = y + r

    pts = [
        (0, r), (1, r), (2, r - 1),
        (r, 0), (r, 1), (r - 1, 2),
    ]
    for dx, dy in pts:
        for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            _pix(fb, cx + sx * dx, cy + sy * dy, color)
            _pix(fb, cx + sx * dy, cy + sy * dx, color)


# ------------------------------------------------------------
# Circle (pixel) — used across screens
# ------------------------------------------------------------

def draw_circle(fb, cx, cy, r=4, filled=False, color=1):
    """
    Draws a small circle. If filled=True, draws a simple filled center.
    """
    cx = int(cx)
    cy = int(cy)
    r = int(r)

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
    """
    Draw a pixel 'C' glyph. Default is 7x9 at scale=1.
    """
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

    x = int(x)
    y = int(y)
    scale = max(1, int(scale))
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "1":
                _fill_rect(fb, x + rx * scale, y + ry * scale, scale, scale, color)


# ------------------------------------------------------------
# Subscript "2" glyph (₂) for CO₂ in MED
# ------------------------------------------------------------

def draw_sub2(fb, x, y, scale=1, color=1):
    """
    Draw a small subscript '2' glyph. Default size 4x5 (scale=1).
    """
    rows = [
        "1110",
        "0010",
        "1110",
        "1000",
        "1110",
    ]

    x = int(x)
    y = int(y)
    scale = max(1, int(scale))
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "1":
                _fill_rect(fb, x + rx * scale, y + ry * scale, scale, scale, color)


# ------------------------------------------------------------
# 9px-high face glyphs for thermo-bar labels (eyes + mouth only)
# ------------------------------------------------------------
# Width is 11px; Height is 9px.

_FACE_9PX = {
    # FIXED: "good" now uses a clean symmetric smile.
    # Old one read wrong / "grin" looked broken on SSD1306.
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
    """
    Draw one of the 9px face glyphs at (x, y).
    mood: "good", "ok", "poor", "bad", "verybad"
    """
    rows = _FACE_9PX.get(str(mood).lower(), _FACE_9PX["ok"])

    x = int(x)
    y = int(y)
    scale = max(1, int(scale))
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "1":
                _fill_rect(fb, x + rx * scale, y + ry * scale, scale, scale, color)


# ------------------------------------------------------------
# Clock glyph (9x9)
#
# Fits inside a Mulish-14 (f_med) text line (~11 px cap-height).
# Outer ring is a Bresenham r=4 circle.
# Hour hand  : col 4, rows 1-3  → points to 12
# Minute hand: row 4, cols 4-8  → points to 3 (merges with right wall)
# ------------------------------------------------------------

CLOCK_W = 9
CLOCK_H = 9

_CLOCK_9 = [
    "000111000",  # row 0  top arc
    "011010110",  # row 1  upper arc + hour hand at col 4
    "010010010",  # row 2  sides + hour hand
    "100010001",  # row 3  sides + hour hand
    "100011111",  # row 4  equator: left wall · centre · minute hand · right wall
    "100000001",  # row 5  lower sides
    "010000010",  # row 6  lower arc
    "011000110",  # row 7  lower arc
    "000111000",  # row 8  bottom arc
]


def draw_clock(fb, x, y, color=1):
    """
    Draw a 9×9 clock glyph at (x, y).
    Hour hand points to 12; minute hand points to 3.
    Designed to sit alongside f_med text — same visual weight.
    """
    draw_bitmap_rows(fb, x, y, _CLOCK_9, c=color)


# ------------------------------------------------------------
# Compact status indicators (top row use)
# ------------------------------------------------------------

# ----------------------------
# WiFi indicator (9x6)
#   ON  = solid triangle with WIDE BASE AT BOTTOM
#   OFF = hollow inverted pyramid (points DOWN)
# ----------------------------

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
    """
    Draw compact WiFi indicator at (x, y). Size: 9x6.
    """
    rows = _WIFI_ON_6 if bool(on) else _WIFI_OFF_6
    draw_bitmap_rows(fb, x, y, rows, c=color)


def draw_wifi9(fb, x, y, on=True, color=1):
    draw_wifi(fb, x, y, on=on, color=color)


#
# _GPS_6 = [
#     "1111" "0" "1110" "0" "1111",
#     "1000" "0" "1001" "0" "1000",
#     "1011" "0" "1110" "0" "1111",
#     "1001" "0" "1000" "0" "0001",
#     "1001" "0" "1000" "0" "0001",
#     "1111" "0" "1000" "0" "1111",
# ]

# ----------------------------
# GPS indicator (14x6) — right-angle triangle
#
# Three states:
#   GPS_NONE  (0) : outline only    — no hardware / disabled
#   GPS_INIT  (1) : bottom 2 rows filled, top 4 outlined — hardware present, no fix
#   GPS_FIXED (2) : fully filled   — has satellite fix
# ----------------------------

GPS_NONE  = 0
GPS_INIT  = 1
GPS_FIXED = 2

# Fully filled triangle (right-angle at bottom-right, tip at top-right)
_GPS_TRI_6 = [
    "00000000000001",
    "00000000000011",
    "00000000000111",
    "00000000001111",
    "00000000011111",
    "00000000111111",
]

# Outline only (hypotenuse + right edge + bottom edge)
_GPS_EMPTY_6 = [
    "00000000000001",
    "00000000000011",
    "00000000000101",
    "00000000001001",
    "00000000010001",
    "00000000111111",
]

# Partial fill: bottom 2 rows solid, top 4 outlined (~1/3 visual fill)
_GPS_PART_6 = [
    "00000000000001",
    "00000000000011",
    "00000000000101",
    "00000000001001",
    "00000000011111",
    "00000000111111",
]


def draw_gps(fb, x, y, on=True, color=1, state=None):
    """
    Draw GPS triangle indicator at (x, y). Size: 14x6.

    state (GPS_NONE/GPS_INIT/GPS_FIXED) overrides `on` when provided.
    Legacy callers using on=True/False continue to work (True→GPS_FIXED, False→GPS_NONE).
    """
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


# ----------------------------
# API indicator (7x6)  <-- matches WiFi/GPS height
#
# Visual Logic:
# - Offline          -> hollow ring
# - Connected idle   -> solid fill, 1 Hz blink (0.5 s on / 0.5 s off)
# - Connected send   -> solid fill, 2 Hz blink (0.25 s on / 0.25 s off)
# ----------------------------

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
    "1111111",  # optional: was "0111110"
    "0011100",
]


def _api_heartbeat_on(now_ms=None, sending=False):
    """
    Idle    : solid 7 s, then 3× empty/full flash (0.2 s each).  Cycle = 8.2 s.
              Burst sequence: empty(0.2) full(0.2) empty(0.2) full(0.2) empty(0.2) full(0.2)
    Sending : off/on/off/on over 2 s.  Cycle = 2 s (unchanged).
    Returns True when circle should be FILLED.
    """
    try:
        if now_ms is None:
            now_ms = time.ticks_ms()
    except Exception:
        now_ms = int(time.time() * 1000)

    if sending:
        t = now_ms % 2000
        # off 500 ms | on 500 ms | off 500 ms | on 500 ms
        return 500 <= t < 1000 or t >= 1500
    else:
        t = now_ms % 8200
        if t < 7000:
            return True          # solid hold
        burst = (t - 7000) // 200   # 0..5 frame index within burst
        return burst % 2 == 1       # frames 1,3,5 = ON; 0,2,4 = OFF


def _api_center_dot_xy():
    # 7x6 => x center is +3; y "center" sits nicely at row +2
    return 3, 2


def _api_draw_center_dot(fb, x, y, on=True):
    """
    Draw (on=True) or clear (on=False) the center dot pixel.
    Clearing works because we draw a 0 pixel on top of the filled glyph.
    """
    dx, dy = _api_center_dot_xy()
    try:
        fb.pixel(int(x) + dx, int(y) + dy, 1 if on else 0)
    except Exception:
        # fallback: ignore if framebuffer doesn't support pixel writes (unlikely)
        pass


def draw_api(fb, x, y, on=True, color=1, *, heartbeat=False, sending=False, now_ms=None):
    """
    Draw API indicator at (x, y).

    Modes:
    - on=False                    -> hollow ring (offline)
    - on=True, heartbeat=False    -> solid fill (steady)
    - on=True, heartbeat=True,
        sending=False  -> solid 4 s, then off/on/off/on burst 0.5 s  (4.5 s cycle)
        sending=True   -> off/on/off/on over 2 s  (2 s cycle)
    """
    x = int(x)
    y = int(y)

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


# ============================================================
# Full-face drawing engine (merged from faces.py)
# ============================================================
# Private helpers use _f_ prefix to avoid shadowing the simpler
# glyphs-module helpers above (which have different signatures).

def _f_in_bounds(w, h, x, y):
    return 0 <= x < w and 0 <= y < h


def _f_pix(fb, w, h, x, y, c=1):
    if _f_in_bounds(w, h, x, y):
        fb.pixel(x, y, c)


def _f_hline(fb, w, h, x, y, length, c=1):
    if y < 0 or y >= h:
        return
    x0 = max(0, x)
    x1 = min(w - 1, x + length - 1)
    for xx in range(x0, x1 + 1):
        fb.pixel(xx, y, c)


def _f_vline(fb, w, h, x, y, length, c=1):
    if x < 0 or x >= w:
        return
    y0 = max(0, y)
    y1 = min(h - 1, y + length - 1)
    for yy in range(y0, y1 + 1):
        fb.pixel(x, yy, c)


def _f_line(fb, w, h, x0, y0, x1, y1, c=1):
    # Bresenham
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        _f_pix(fb, w, h, x0, y0, c)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _f_thick_line(fb, w, h, x0, y0, x1, y1, thickness=2, c=1):
    _f_line(fb, w, h, x0, y0, x1, y1, c)
    if thickness <= 1:
        return
    _f_line(fb, w, h, x0 + 1, y0, x1 + 1, y1, c)
    _f_line(fb, w, h, x0 - 1, y0, x1 - 1, y1, c)
    _f_line(fb, w, h, x0, y0 + 1, x1, y1 + 1, c)
    _f_line(fb, w, h, x0, y0 - 1, x1, y1 - 1, c)


def _f_circle_outline(fb, w, h, cx, cy, r, c=1):
    x = r
    y = 0
    err = 0
    while x >= y:
        for dx, dy in (
                ( x,  y), ( y,  x), (-y,  x), (-x,  y),
                (-x, -y), (-y, -x), ( y, -x), ( x, -y)
        ):
            _f_pix(fb, w, h, cx + dx, cy + dy, c)
        y += 1
        err += 1 + 2 * y
        if 2 * (err - x) + 1 > 0:
            x -= 1
            err += 1 - 2 * x


def draw_thick_circle(fb, w, h, cx, cy, r, thickness=3, c=1):
    for i in range(thickness):
        rr = r - i
        if rr > 0:
            _f_circle_outline(fb, w, h, cx, cy, rr, c)


def _f_dot_eye(fb, w, h, cx, cy, size=3, c=1):
    s = max(1, size // 2)
    fb.fill_rect(cx - s, cy - s, 2 * s + 1, 2 * s + 1, c)


def _f_x_eye(fb, w, h, cx, cy, size=3, thick=2, c=1):
    s = size
    _f_thick_line(fb, w, h, cx - s, cy - s, cx + s, cy + s, thick, c)
    _f_thick_line(fb, w, h, cx - s, cy + s, cx + s, cy - s, thick, c)


def _f_star_eye(fb, w, h, cx, cy, size=3, thick=2, c=1):
    s = size
    _f_thick_line(fb, w, h, cx - s, cy, cx + s, cy, thick, c)
    _f_thick_line(fb, w, h, cx, cy - s, cx, cy + s, thick, c)
    _f_thick_line(fb, w, h, cx - s, cy - s, cx + s, cy + s, thick, c)
    _f_thick_line(fb, w, h, cx - s, cy + s, cx + s, cy - s, thick, c)


def _f_mouth_arc(fb, w, h, cx, cy, radius, angle_span_deg=40, facing="up", thick=2, c=1):
    radius = max(6, int(radius))
    half_span = max(8, int(radius * 0.75))
    sag = max(3, int(radius * 0.22))

    for dx in range(-half_span, half_span + 1):
        y_off = int((dx * dx) * sag / max(1, (half_span * half_span)))
        if facing == "up":
            yy = cy + sag - y_off
        else:
            yy = cy - sag + y_off
        for t in range(thick):
            _f_pix(fb, w, h, cx + dx, yy + t - (thick // 2), c)


def _f_mouth_flat(fb, w, h, cx, cy, w_half, thick=2, c=1):
    for t in range(thick):
        _f_hline(fb, w, h, cx - w_half, cy + t, w_half * 2 + 1, c)


def _f_mouth_worried(fb, w, h, cx, cy, w_half, thick=2, c=1):
    _f_thick_line(fb, w, h, cx - w_half, cy + 2, cx - 2, cy, thick, c)
    _f_thick_line(fb, w, h, cx - 2, cy, cx + w_half, cy + 1, thick, c)


def _f_mouth_frown_legacy(fb, w, h, cx, cy, w_half, curve, thick=2, c=1):
    for dx in range(-w_half, w_half + 1):
        y = int((dx * dx) / max(1, curve))
        yy = cy + y
        _f_pix(fb, w, h, cx + dx, yy, c)
        if thick >= 2:
            _f_pix(fb, w, h, cx + dx, yy - 1, c)
        if thick >= 3:
            _f_pix(fb, w, h, cx + dx, yy + 1, c)


def _f_mouth_grin(fb, w, h, cx, cy, radius, w_half, thick=2, c=1):
    radius = max(6, int(radius))
    w_half = max(8, int(w_half))
    _f_mouth_arc(fb, w, h, cx, cy, radius=radius, facing="up", thick=max(2, thick), c=c)
    teeth_y = cy + max(1, int(radius * 0.10))
    teeth_w = max(8, int(w_half * 1.10))
    _f_hline(fb, w, h, cx - (teeth_w // 2), teeth_y, teeth_w, c)
    _f_pix(fb, w, h, cx - w_half, teeth_y - 1, c)
    _f_pix(fb, w, h, cx + w_half, teeth_y - 1, c)


def draw_face(fb, width, height, mood, *, right_edge=True, fill_height_ratio=0.90):
    r = int((height * float(fill_height_ratio)) / 2)
    r = max(10, min(r, (height // 2) - 2))

    cx = (width - 1) - r if right_edge else (width // 2)
    cy = height // 2

    draw_thick_circle(fb, width, height, cx, cy, r, thickness=3, c=1)

    eye_y = cy - int(r * 0.30)
    eye_dx = int(r * 0.35)
    mouth_y = cy + int(r * 0.32)

    lx = cx - eye_dx
    rx = cx + eye_dx

    eye_thick = 2
    mouth_thick = 2

    if mood == "star":
        _f_star_eye(fb, width, height, lx, eye_y, size=3, thick=eye_thick, c=1)
        _f_star_eye(fb, width, height, rx, eye_y, size=3, thick=eye_thick, c=1)
        _f_mouth_arc(fb, width, height, cx, mouth_y, radius=int(r * 0.55), facing="up", thick=3, c=1)

    elif mood == "grin":
        _f_dot_eye(fb, width, height, lx, eye_y, size=3, c=1)
        _f_dot_eye(fb, width, height, rx, eye_y, size=3, c=1)
        _f_mouth_grin(
            fb, width, height,
            cx, mouth_y,
            radius=int(r * 0.52),
            w_half=int(r * 0.42),
            thick=mouth_thick,
            c=1
        )

    elif mood == "good":
        _f_dot_eye(fb, width, height, lx, eye_y, size=3, c=1)
        _f_dot_eye(fb, width, height, rx, eye_y, size=3, c=1)
        _f_mouth_arc(fb, width, height, cx, mouth_y, radius=int(r * 0.50), facing="up", thick=mouth_thick, c=1)

    elif mood == "ok":
        _f_dot_eye(fb, width, height, lx, eye_y, size=3, c=1)
        _f_dot_eye(fb, width, height, rx, eye_y, size=3, c=1)
        _f_mouth_flat(fb, width, height, cx, mouth_y, w_half=int(r * 0.40), thick=mouth_thick, c=1)

    elif mood == "poor":
        _f_dot_eye(fb, width, height, lx, eye_y + 1, size=3, c=1)
        _f_dot_eye(fb, width, height, rx, eye_y + 1, size=3, c=1)
        _f_thick_line(fb, width, height, lx - 6, eye_y - 6, lx + 6, eye_y - 7, thickness=2, c=1)
        _f_thick_line(fb, width, height, rx - 6, eye_y - 7, rx + 6, eye_y - 6, thickness=2, c=1)
        _f_mouth_worried(fb, width, height, cx, mouth_y, w_half=int(r * 0.40), thick=mouth_thick, c=1)

    elif mood == "bad":
        _f_dot_eye(fb, width, height, lx, eye_y + 1, size=3, c=1)
        _f_dot_eye(fb, width, height, rx, eye_y + 1, size=3, c=1)
        _f_mouth_arc(fb, width, height, cx, mouth_y + 2, radius=int(r * 0.50), facing="down", thick=mouth_thick, c=1)

    else:  # "verybad"
        _f_x_eye(fb, width, height, lx, eye_y, size=3, thick=eye_thick, c=1)
        _f_x_eye(fb, width, height, rx, eye_y, size=3, thick=eye_thick, c=1)
        _f_mouth_frown_legacy(
            fb, width, height, cx, mouth_y + 2,
            w_half=int(r * 0.40),
            curve=max(5, int(r * 0.25)),
            thick=mouth_thick,
            c=1
        )
        _f_thick_line(
            fb, width, height,
            cx - int(r * 0.55), cy + 2,
            cx - int(r * 0.35), cy + 8,
            thickness=2, c=1
        )
        _f_thick_line(
            fb, width, height,
            cx + int(r * 0.55), cy + 2,
            cx + int(r * 0.35), cy + 8,
            thickness=2, c=1
        )


# ------------------------------------------------------------
# Compass ring with North indicator dot
# ------------------------------------------------------------

def draw_compass(fb, w, h, cx, cy, r, heading_deg=0.0, ring_thick=2, dot_r=3, color=1):
    """
    Compass ring with a filled North-indicator dot orbiting the inner perimeter.

    heading_deg=0   → dot at top    (facing North: North is ahead)
    heading_deg=90  → dot at left   (facing East:  North is to the left)
    heading_deg=180 → dot at bottom (facing South: North is behind)
    heading_deg=270 → dot at right  (facing West:  North is to the right)

    w, h        : screen dimensions for bounds-safe pixel writes
    r           : outer radius of the ring
    ring_thick  : pixel thickness of the ring
    dot_r       : radius of the filled North dot
    """
    import math as _m
    cx = int(cx); cy = int(cy); r = int(r)
    ring_thick = max(1, int(ring_thick))
    dot_r = max(1, int(dot_r))

    # Concentric ring (ring_thick circles, outermost first)
    for i in range(ring_thick):
        rr = r - i
        if rr > 0:
            _f_circle_outline(fb, w, h, cx, cy, rr, color)

    # North dot orbits just inside the ring wall
    r_orbit = max(dot_r + 1, r - ring_thick - dot_r - 1)

    # heading=0 → top; heading increases clockwise → dot counter-clockwise on screen
    _ang = -_m.radians(float(heading_deg))
    dot_x = cx + int(r_orbit * _m.sin(_ang))
    dot_y = cy - int(r_orbit * _m.cos(_ang))

    # Filled circular dot using squared-distance test (no sqrt required)
    _r2 = dot_r * dot_r
    for _dy in range(-dot_r, dot_r + 1):
        for _dx in range(-dot_r, dot_r + 1):
            if _dx * _dx + _dy * _dy <= _r2:
                _f_pix(fb, w, h, dot_x + _dx, dot_y + _dy, color)


# ------------------------------------------------------------
# Diameter line in a circle — sail position indicator
# ------------------------------------------------------------

def draw_diameter_line(fb, w, h, cx, cy, r, angle_deg, ring_thick=1, color=1):
    """
    Circle outline with a line slicing through the center, rotating with
    angle_deg. Same screen orientation as draw_compass: 0° is vertical
    (top-bottom), increasing angle rotates counter-clockwise on screen.
    """
    import math as _m
    cx = int(cx); cy = int(cy); r = int(r)

    for i in range(max(1, int(ring_thick))):
        rr = r - i
        if rr > 0:
            _f_circle_outline(fb, w, h, cx, cy, rr, color)

    _ang = -_m.radians(float(angle_deg))
    rr = r - max(1, int(ring_thick)) - 1
    dx = int(rr * _m.sin(_ang))
    dy = int(rr * _m.cos(_ang))
    _f_line(fb, w, h, cx - dx, cy + dy, cx + dx, cy - dy, color)


# ------------------------------------------------------------
# Rotating arrow in a circle — wind direction indicator
# ------------------------------------------------------------

def draw_arrow_in_circle(fb, w, h, cx, cy, r, angle_deg, ring_thick=1, color=1):
    """
    Circle outline with an arrow from center-symmetric tail to head plus a
    two-line chevron at the head. angle_deg points the arrow HEAD:
    0° = up, same orientation convention as draw_compass.
    """
    import math as _m
    cx = int(cx); cy = int(cy); r = int(r)

    for i in range(max(1, int(ring_thick))):
        rr = r - i
        if rr > 0:
            _f_circle_outline(fb, w, h, cx, cy, rr, color)

    _ang = -_m.radians(float(angle_deg))
    rr = r - max(1, int(ring_thick)) - 2
    hx = cx + int(rr * _m.sin(_ang))          # head
    hy = cy - int(rr * _m.cos(_ang))
    tx = cx - int(rr * _m.sin(_ang))          # tail
    ty = cy + int(rr * _m.cos(_ang))
    _f_line(fb, w, h, tx, ty, hx, hy, color)

    # Chevron: two short lines swept ±150° back from the arrow direction
    barb = max(3, rr // 3)
    for sweep in (2.618, -2.618):             # ±150° in radians
        ba = _ang + sweep
        bx = hx + int(barb * _m.sin(ba))
        by = hy - int(barb * _m.cos(ba))
        _f_line(fb, w, h, hx, hy, bx, by, color)


# ------------------------------------------------------------
# Gear icon — 6-tooth gear with center circle
# ------------------------------------------------------------

def draw_gear(fb, cx, cy, body_r=6, tooth_len=2, teeth=6, center_r=3,
              filled=True, filled_center=False, rotation_offset=0.0, color=1):
    """
    Draw a gear icon at (cx, cy).
    filled=True  → solid body ring + teeth (servo present)
    filled=False → hollow body ring (inner/outer 1-px boundary) + teeth outlines
    Center circle is always hollow (outline only); filled_center is ignored.
    """
    import math as _m
    cx = int(cx); cy = int(cy)
    body_r = int(body_r); tooth_len = int(tooth_len)
    outer_r = body_r + tooth_len
    center_r = int(center_r)

    body_r2 = body_r * body_r
    outer_r2 = outer_r * outer_r
    center_r2 = center_r * center_r

    # Boundary thresholds for hollow-body mode (1-px inner and outer rims)
    body_outer_r2 = (body_r - 1) * (body_r - 1)
    body_inner_r2 = (center_r + 1) * (center_r + 1)

    tooth_spacing = 2.0 * _m.pi / teeth       # 60° for 6 teeth
    tooth_half = tooth_spacing * 0.325         # each tooth spans ~65% of its slot
    rotation = _m.pi * 1.5 + float(rotation_offset)   # first tooth at 12 o'clock + animation offset

    for dy in range(-outer_r, outer_r + 1):
        for dx in range(-outer_r, outer_r + 1):
            d2 = dx * dx + dy * dy
            if d2 > outer_r2 or d2 <= center_r2:
                continue
            if d2 <= body_r2:
                # Body ring: always draw when filled; hollow → only inner/outer 1-px rims
                if filled or d2 > body_outer_r2 or d2 <= body_inner_r2:
                    _pix(fb, cx + dx, cy + dy, color)
            else:
                angle = _m.atan2(float(dy), float(dx))
                if angle < 0.0:
                    angle += 2.0 * _m.pi
                a_mod = (angle - rotation) % tooth_spacing
                if a_mod < 0.0:
                    a_mod += tooth_spacing
                if a_mod <= tooth_half or a_mod >= tooth_spacing - tooth_half:
                    _pix(fb, cx + dx, cy + dy, color)

    # Clear center hole then draw hollow circle (center is always hollow)
    for dy in range(-center_r, center_r + 1):
        for dx in range(-center_r, center_r + 1):
            if dx * dx + dy * dy <= center_r2:
                _pix(fb, cx + dx, cy + dy, 0)

    _f_circle_outline(fb, 128, 64, cx, cy, center_r, color)


# ------------------------------------------------------------
# Vertical battery glyph with horizontal fill bands
# ------------------------------------------------------------

def battery_status(volts):
    """Map bus voltage (V) to a charge-level string."""
    if volts is None:
        return "critical"
    if volts >= 4.05:
        return "full"
    elif volts >= 3.90:
        return "high"
    elif volts >= 3.75:
        return "medium"
    elif volts >= 3.55:
        return "low"
    else:
        return "critical"


_BATTERY_LEVEL_BANDS = {
    "full":     5,
    "high":     4,
    "medium":   3,
    "low":      2,
    "critical": 1,
}


def draw_battery_v(fb, x, y, bands_filled=5, total_bands=5,
                   w=22, h=34, nub_w=8, nub_h=3, color=1):
    """
    Vertical battery glyph with `total_bands` horizontal bands.
    (x, y)       = top-left of the nub (positive terminal at the top).
    w, h         = body width and height (not counting the nub).
    bands_filled = how many bands to fill solid, counted from the bottom.
    Empty bands are drawn as hollow outlines.
    Bands are anchored to the bottom of the body with no bottom margin;
    any leftover pixels from integer division appear at the top.
    """
    x = int(x); y = int(y)
    w = int(w); h = int(h)
    nub_w = int(nub_w); nub_h = int(nub_h)
    total_bands = max(1, int(total_bands))
    bands_filled = max(0, min(int(bands_filled), total_bands))

    # Nub — positive terminal, centred on body width
    nub_x = x + (w - nub_w) // 2
    _fill_rect(fb, nub_x, y, nub_w, nub_h, color)

    # Body outline — extended 1 px beyond the inner band area on top and
    # bottom so there is a visible margin between the bars and the border.
    # The top extension merges with the nub's bottom row (body_y - 1 =
    # y + nub_h - 1), which is the standard battery-icon look.
    body_y = y + nub_h
    _hline(fb, x, body_y - 1,     w, color)   # top    (1 px above inner area)
    _hline(fb, x, body_y + h,     w, color)   # bottom (1 px below inner area)
    _vline(fb, x,         body_y - 1, h + 2, color)   # left
    _vline(fb, x + w - 1, body_y - 1, h + 2, color)   # right

    # Band area (1 px inside the border on left/right sides)
    inner_x = x + 2
    inner_w = w - 4
    inner_y = body_y + 1
    inner_h = h - 2          # pixel rows available inside body walls (unchanged)

    gap    = 1               # 1-px gap between bands; no top/bottom padding
    avail  = inner_h - (total_bands - 1) * gap
    band_h = max(2, avail // total_bands)
    # Any remainder pixels land as extra space at the top (near positive terminal)

    for band_idx in range(total_bands):
        # band_idx 0 = bottommost band, anchored to the inner bottom wall
        bottom = inner_y + inner_h - 1 - band_idx * (band_h + gap)
        band_y = max(inner_y, bottom - band_h + 1)
        if band_idx < bands_filled:
            _fill_rect(fb, inner_x, band_y, inner_w, band_h, color)
        else:
            # hollow outline for unfilled bands
            _hline(fb, inner_x, band_y,              inner_w, color)
            _hline(fb, inner_x, band_y + band_h - 1, inner_w, color)
            _vline(fb, inner_x,               band_y, band_h, color)
            _vline(fb, inner_x + inner_w - 1, band_y, band_h, color)