#!/usr/bin/env python3
# tests/config_save_host.py — review B6: a cut-off config save must never
# blank the WiFi and device credentials
#
#   cd device && python3 ../tests/config_save_host.py      (<1 s)
#
# Runs the real device/config.py in a scratch directory and checks:
#   - save_config() never removes config.json before the rename (no window
#     with no file);
#   - a reset after the .tmp is written but before the rename: load_config()
#     still returns the old, complete config.json;
#   - config.json missing with a good .tmp left (older firmware's window):
#     the .tmp is recovered, credentials intact, and written back;
#   - config.json present but corrupt, no .tmp: credentials are not
#     overwritten with defaults, and update_config() refuses to write;
#   - no config at all (fresh board): defaults are written as before.
# Exit code 0 = all good.

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.getcwd())

import config  # noqa: E402

CREDS = {"wifi_ssid": "pond", "wifi_password": "pw", "device_id": "t1", "device_key": "k1"}
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def reset_dir():
    for f in os.listdir("."):
        os.remove(f)


def write(name, text):
    with open(name, "w") as f:
        f.write(text)


def creds_ok(cfg):
    return all(cfg.get(k) == v for k, v in CREDS.items())


work = tempfile.mkdtemp(prefix="cfgtest_")
os.chdir(work)
try:
    print("save_config never removes config.json first")
    reset_dir()
    write("config.json", json.dumps(CREDS))
    real_remove = os.remove
    removed = []
    os.remove = lambda p: (removed.append(p), real_remove(p))
    try:
        config.save_config(dict(CREDS, turtle_mode=True))
    finally:
        os.remove = real_remove
    check("config.json" not in removed, "no os.remove('config.json')")
    check(not os.path.exists("config.json.tmp"), "tmp renamed into place")

    print("reset after .tmp written, before rename")
    reset_dir()
    config.save_config(dict(CREDS))
    write("config.json.tmp", '{"wifi_ssid": "half-writ')      # truncated
    cfg = config.load_config()
    check(creds_ok(cfg), "old config.json kept")

    print("config.json missing, good .tmp left behind")
    reset_dir()
    write("config.json.tmp", json.dumps(CREDS))
    cfg = config.load_config()
    check(creds_ok(cfg), "credentials recovered from .tmp")
    with open("config.json") as f:
        check(creds_ok(json.load(f)), "recovered config written back to config.json")

    print("config.json corrupt, no .tmp")
    reset_dir()
    corrupt = '{"wifi_ssid": "pond", "wifi_password": "pw", "device_id"'
    write("config.json", corrupt)
    cfg = config.load_config()
    with open("config.json") as f:
        check(f.read() == corrupt, "corrupt file left untouched by load_config")
    try:
        config.update_config({"ble_enabled": False})
        refused = False
    except OSError:
        refused = True
    check(refused, "update_config refuses to overwrite it")
    with open("config.json") as f:
        check(f.read() == corrupt, "still untouched after update_config")

    print("fresh board, no config at all")
    reset_dir()
    cfg = config.load_config()
    check(os.path.exists("config.json"), "defaults written")
    check(cfg.get("turtle_mode") is True, "defaults applied")
    cfg = config.update_config({"wifi_ssid": "x"})
    check(cfg.get("wifi_ssid") == "x", "writes work again once the file is good")
finally:
    os.chdir("/")
    shutil.rmtree(work, ignore_errors=True)

print("\n%s" % ("ALL OK" if not FAILS else "%d FAILED" % len(FAILS)))
sys.exit(1 if FAILS else 0)
