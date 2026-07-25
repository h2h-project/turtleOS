# src/nav/gpsfix.py — shared GPS fix cache + RMC parsing
#
# The GPS UART buffer is a single resource: whoever calls read_nmea()
# consumes the sentences. NavController drains it every nav cycle and
# publishes the latest fix here; the telemetry scheduler falls back to
# this cache when its own drain comes up empty.

import time

# lat, lon, cog_deg (course over ground, may be None), ticks_ms of fix
_fix = [None, None, None, 0]


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
    _fix[0] = lat
    _fix[1] = lon
    _fix[2] = cog
    _fix[3] = time.ticks_ms()


def get():
    """Return (lat, lon, age_ms) of the last cached fix, or (None, None, None)."""
    if _fix[0] is None:
        return None, None, None
    return _fix[0], _fix[1], time.ticks_diff(time.ticks_ms(), _fix[3])


def cog_deg():
    return _fix[2]
