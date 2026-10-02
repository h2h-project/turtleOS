#!/usr/bin/env python3
# tests/ble_decode.py — decode Turtle service values copied from nRF Connect
# (host-side helper; runs with plain python3, not on the device).
#
#   python3 tests/ble_decode.py 0110 A3-5F-4F-1E-36-17-9C-FF-08-01-00-00
#   python3 tests/ble_decode.py 0116 0x...        (dashes, spaces, 0x all fine)
#   python3 tests/ble_decode.py 0202 42-01-14      (a command Result)
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

RESULTS = {0x00: "OK", 0x01: "IN_PROGRESS", 0x10: "ERR_UNKNOWN_OPCODE",
           0x11: "ERR_BAD_LENGTH", 0x12: "ERR_BAD_VALUE", 0x13: "ERR_BUSY",
           0x14: "ERR_NOT_BONDED", 0x15: "ERR_WRONG_STATE", 0x20: "ERR_NO_GPS_FIX",
           0x21: "ERR_RTC_NOT_SYNCED", 0x22: "ERR_NO_HARDWARE", 0x23: "ERR_NO_TARGET",
           0x30: "ERR_WIFI_FAILED", 0x31: "ERR_API_FAILED", 0x32: "ERR_NOT_STAMPED",
           0x7F: "ERR_INTERNAL"}

OPCODES = {0x01: "JOURNEY_START", 0x02: "JOURNEY_END", 0x03: "DEST_SET_HERE",
           0x04: "DEST_SET_MISSION", 0x05: "DEST_SET_COORDS", 0x06: "DEST_CLEAR",
           0x10: "NAV_LUFF_SWEEP", 0x11: "SERVO_BENCH_SWEEP", 0x20: "GPS_STAMP",
           0x21: "GPS_SET_ENABLED", 0x22: "TELEMETRY_SET_MODE",
           0x23: "TELEMETRY_SET_INTERVAL", 0x24: "API_HANDSHAKE",
           0x30: "WIFI_SET_ENABLED", 0x31: "WIFI_SET_CREDENTIALS",
           0x32: "CONNECTION_MODE_SET", 0x33: "BLE_SET_ENABLED", 0x40: "SLEEP",
           0x41: "SET_TURTLE_MODE", 0x42: "REBOOT", 0x43: "COMPASS_SET_OFFSET"}

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
    if cid == "0202":
        if len(data) < 3:
            print("Result: %d bytes, need 3" % len(data))
            sys.exit(2)
        op, seq, code = data[0], data[1], data[2]
        print("Result (0202): opcode 0x%02X (%s)  seq %d  ->  0x%02X %s" % (
            op, OPCODES.get(op, "?"), seq, code, RESULTS.get(code, "(unknown code)")))
        if len(data) > 3:
            print("  payload:", data[3:].hex("-").upper())
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
