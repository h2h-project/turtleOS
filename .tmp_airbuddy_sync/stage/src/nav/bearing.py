# src/nav/bearing.py — great-circle navigation math (pure, no hardware)

import math

_EARTH_R_M = 6371000.0


def norm360(deg):
    """Normalize to [0, 360)."""
    return float(deg) % 360.0


def norm180(deg):
    """Normalize to (-180, 180]. Positive = target is to the left of course."""
    d = float(deg) % 360.0
    if d > 180.0:
        d -= 360.0
    return d


def initial_bearing(lat1, lon1, lat2, lon2):
    """Great-circle bearing in degrees true from point 1 to point 2."""
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dl = math.radians(float(lon2) - float(lon1))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return norm360(math.degrees(math.atan2(y, x)))


def distance_m(lat1, lon1, lat2, lon2):
    """Haversine distance in metres."""
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lon2) - float(lon1))
    a = (math.sin(dp / 2.0) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * _EARTH_R_M * math.asin(math.sqrt(a))


def midpoint_angle(a_deg, b_deg):
    """Circular midpoint of two angles along the shortest arc.

    Used to solve wind angle from the two luff angles:
    midpoint_angle(350, 10) == 0.
    """
    return norm360(float(a_deg) + norm180(float(b_deg) - float(a_deg)) / 2.0)
