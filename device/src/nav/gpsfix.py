# src/nav/gpsfix.py — shared GPS fix cache + RMC parsing
#
# The GPS UART buffer is a single resource: whoever calls read_nmea()
# consumes the sentences. NavController drains it every nav cycle and
# publishes the latest fix here; the telemetry scheduler falls back to
# this cache when its own drain comes up empty.

# Ages are measured on src/app/monotonic.ms(), not ticks_ms(): a ticks_ms
# stamp more than ~6.2 days old reads as negative (fresh) once ticks_diff
# wraps, which put a week-old position into telemetry, the GPS icon and
# journey stamps as if it were live (review B5). monotonic.ms() never wraps.

# Every fix is checked twice before it lands here (review B8):
#   - read_nmea() (src/sensors/xiao_gnss.py) drops any sentence whose *hh
#     checksum is missing or wrong (checksum_ok below), so a damaged line from
#     the UART (shared pins with the console, gotcha 16; buffer truncation)
#     never reaches a parser;
#   - update() drops a fix that jumps more than MAX_JUMP_DEG from the last one
#     it accepted, until MAX_JUMP_DEG-consistent fixes at the new place have
#     arrived RELOCATE_AFTER times in a row. This used to live only in the
#     telemetry scheduler's own drain, which turtle mode never uses.

from src.app import monotonic

# lat, lon, cog_deg (course over ground, may be None), monotonic ms of fix
_fix = [None, None, None, 0]

MAX_JUMP_DEG = 1.0      # ~111 km: a turtle can't move this far between fixes
RELOCATE_AFTER = 5      # consecutive agreeing far fixes (~5 s at 1 Hz) = really moved

# Jump filter state: candidate new place [lat, lon], how many agreeing
# rejected fixes in a row, and the running total of rejections (diagnostics).
_jump = [None, None, 0]
rejected = 0


def checksum_ok(line):
    """True when an NMEA sentence ends in a correct *hh XOR checksum."""
    try:
        star = line.rfind("*")
        if star < 1 or len(line) < star + 3:
            return False
        x = 0
        for ch in line[1:star]:
            x ^= ord(ch)
        return x == int(line[star + 1:star + 3], 16)
    except Exception:
        return False


def _far(lat1, lon1, lat2, lon2):
    dlon = abs(lon1 - lon2)
    if dlon > 180.0:
        dlon = 360.0 - dlon
    return abs(lat1 - lat2) > MAX_JUMP_DEG or dlon > MAX_JUMP_DEG


def parse_rmc(line):
    """Parse a $GPRMC/$GNRMC sentence → (lat, lon, cog_deg) or (None, None, None)."""
    try:
        parts = line.split(",")
        if len(parts) < 9 or parts[2] != "A":
            return None, None, None
        lat_raw, lat_dir = parts[3], parts[4]
        lon_raw, lon_dir = parts[5], parts[6]
        if not lat_raw or not lon_raw:
            return None, None, None
        lat_d = int(float(lat_raw) // 100)
        lat = lat_d + (float(lat_raw) - lat_d * 100) / 60.0
        if lat_dir == "S":
            lat = -lat
        lon_d = int(float(lon_raw) // 100)
        lon = lon_d + (float(lon_raw) - lon_d * 100) / 60.0
        if lon_dir == "W":
            lon = -lon
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            return None, None, None
        if lat == 0.0 and lon == 0.0:
            return None, None, None
        cog = None
        if parts[8]:
            try:
                cog = float(parts[8])
            except Exception:
                cog = None
        return round(lat, 6), round(lon, 6), cog
    except Exception:
        return None, None, None


def update(lat, lon, cog=None):
    """Publish a fix. Returns True if accepted, False if dropped as a jump."""
    global rejected
    if _fix[0] is not None and _far(lat, lon, _fix[0], _fix[1]):
        rejected += 1
        if _jump[0] is not None and not _far(lat, lon, _jump[0], _jump[1]):
            _jump[2] += 1
        else:
            _jump[0], _jump[1], _jump[2] = lat, lon, 1
        if _jump[2] < RELOCATE_AFTER:
            if rejected <= 3 or rejected % 100 == 0:
                print("[GPS] fix {:.5f},{:.5f} jumps >{} deg from {:.5f},{:.5f}: dropped ({} so far)".format(
                    lat, lon, MAX_JUMP_DEG, _fix[0], _fix[1], rejected))
            return False
        print("[GPS] {} agreeing fixes at the new place: relocated".format(_jump[2]))
    _jump[0], _jump[1], _jump[2] = None, None, 0
    _fix[0] = lat
    _fix[1] = lon
    _fix[2] = cog
    _fix[3] = monotonic.ms()
    return True


def reset():
    """Forget the cached fix and jump-filter state (host tests)."""
    global rejected
    _fix[0], _fix[1], _fix[2], _fix[3] = None, None, None, 0
    _jump[0], _jump[1], _jump[2] = None, None, 0
    _gga[0], _gga[1], _gga[2] = None, None, 0
    rejected = 0


def get():
    """Return (lat, lon, age_ms) of the last cached fix, or (None, None, None)."""
    if _fix[0] is None:
        return None, None, None
    return _fix[0], _fix[1], monotonic.ms() - _fix[3]


def cog_deg():
    return _fix[2]


# GGA detail: fix quality (field 6: 0 none, 1 GPS, 2 DGPS, ...), satellites
# in use (field 7), monotonic ms of the sentence. Kept apart from the RMC fix so
# a GGA-only reader never disturbs lat/lon/COG.
_gga = [None, None, 0]


def note_gga(line):
    """Cache fix quality + satellite count from a $GPGGA/$GNGGA sentence.
    Returns (quality, sats) or (None, None) if the sentence is unusable."""
    try:
        p = line.split(",")
        if len(p) < 8:
            return None, None
        q = int(p[6]) if p[6] else None
        s = int(p[7]) if p[7] else None
        if q is None and s is None:
            return None, None
        _gga[0] = q
        _gga[1] = s
        _gga[2] = monotonic.ms()
        return q, s
    except Exception:
        return None, None


def gga():
    """Return (quality, sats, age_ms) of the last GGA, or (None, None, None)."""
    if _gga[0] is None and _gga[1] is None:
        return None, None, None
    return _gga[0], _gga[1], monotonic.ms() - _gga[2]
