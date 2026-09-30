#!/usr/bin/env python3
# tests/ble_decode.py — decode Turtle service values copied from nRF Connect
# (host-side helper; runs with plain python3, not on the device).
#
#   python3 tests/ble_decode.py 0110 A3-5F-4F-1E-36-17-9C-FF-08-01-00-00
#   python3 tests/ble_decode.py 0116 0x...        (dashes, spaces, 0x all fine)
#
# Layouts are GATT contract v1 (docs/wrangler/README.md). Sentinels print "—".

import struct
import sys

NA = {"i": 0x7FFFFFFF, "H": 0xFFFF, "h": 0x7FFF, "I": 0xFFFFFFFF, "B": 0xFF}

LAYOUTS = {
    "0101": ("Contract info", "<BB", ["contract_version", "turtle_mode"]),
    "0110": ("Position", "<iiBBH", ["lat_e7", "lon_e7", "sats", "fix_quality", "fix_age_s"]),
    "0111": ("Nav", "<HHIhBBB", ["heading_x10", "bearing_x10", "dist_m", "xte_m",
                                 "nav_state", "fault", "trim"]),
    "0112": ("Targets", "<iiiiB", ["set_lat_e7", "set_lon_e7", "mission_lat_e7",
                                   "mission_lon_e7", "active_source"]),
    "0113": ("Sail", "<HHBB", ["sail_x10", "wind_x10", "sweep_state", "confidence_pct"]),
    "0114": ("Power", "<HhB", ["voltage_mV", "current_mA", "soc_pct"]),
    "0115": ("IMU / baro", "<hhH", ["pitch_x10", "roll_x10", "pressure_hPa_x10"]),
    "0116": ("Status", "<IBBHHH", ["flags", "telemetry_mode", "connection_mode",
                                   "interval_s", "queue_count", "stamps_session"]),
    "0117": ("Shore sync / journey", "<III", ["last_shore_sync", "journey_id", "device_now"]),
}

ENUMS = {
    "nav_state": ["BOOT", "ACQUIRE", "SAIL_NAV", "ARRIVAL", "SAFE"],
    "fault": ["none", "GPS never acquired", "GPS lost", "no waypoints"],
    "trim": ["CRUISE", "FEATHER"],
    "active_source": ["none", "set_waypoints", "set_destination",
                      "mission_waypoints", "mission_destination"],
    "sweep_state": ["idle", "nav sweep", "bench sweep"],
    "telemetry_mode": ["off", "auto", "manual"],
    "connection_mode": ["wifi_auto", "wifi_manual", "lora"],
}

FLAGS = ["wifi_connected", "api_ok", "gps_fixed", "journey_active", "wifi_enabled",
         "gps_enabled", "gps_hw_present", "imu_present", "mag_present",
         "ina219_present", "baro_present", "as5600_present", "servo_present",
         "rtc_synced", "link_bonded", "ble_require_bond", "command_in_progress",
         "wifi_credentials_set"]


def _fmt(name, code, v):
    if v == NA.get(code) and not name.startswith(("contract", "flags")):
        return "—"
    if name.endswith("_e7"):
        return "%.7f" % (v / 1e7)
    if name.endswith("_x10"):
        return "%.1f" % (v / 10)
    if name in ENUMS and 0 <= v < len(ENUMS[name]):
        return "%d (%s)" % (v, ENUMS[name][v])
    if name in ("last_shore_sync", "device_now", "journey_id") and v:
        import datetime
        t = datetime.datetime.fromtimestamp(v, datetime.timezone.utc)
        return "%d (%s UTC)" % (v, t.strftime("%Y-%m-%d %H:%M:%S"))
    return str(v)


def main():
    if len(sys.argv) < 3:
        print(__doc__ or "usage: ble_decode.py <short id> <hex>")
        sys.exit(1)
    cid = sys.argv[1].lower().replace("0x", "").zfill(4)
    raw = "".join(sys.argv[2:]).replace("0x", "").replace("-", "").replace(":", "").replace(" ", "")
    data = bytes.fromhex(raw)
    if cid == "0102":
        print("Turtle name:", data.decode("utf-8", "replace"))
        return
    title, fmt, names = LAYOUTS[cid]
    need = struct.calcsize(fmt)
    if len(data) < need:
        print("%s: %d bytes, need %d — too short (contract violation)" % (title, len(data), need))
        sys.exit(2)
    vals = struct.unpack(fmt, data[:need])
    print("%s (%s, %d bytes%s)" % (title, cid, len(data),
                                    "" if len(data) == need else ", %d trailing ignored" % (len(data) - need)))
    for name, code, v in zip(names, fmt[1:], vals):
        print("  %-18s %s" % (name, _fmt(name, code, v)))
        if name == "flags":
            on = [f for i, f in enumerate(FLAGS) if v & (1 << i)]
            print("  %-18s %s" % ("", ", ".join(on) or "(none)"))


if __name__ == "__main__":
    main()
