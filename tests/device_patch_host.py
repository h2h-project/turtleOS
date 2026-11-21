#!/usr/bin/env python3
# tests/device_patch_host.py — review B13: a set_* change must reach the
# dashboard eventually, and never block the main thread
#
#   cd device && python3 ../tests/device_patch_host.py      (<1 s)
#
# Runs the real device_client, actions.save_set_fields and
# TelemetryBackgroundProcess._maybe_push_device in a scratch directory with a
# fake PATCH and a fake WiFi:
#   - save_set_fields() makes no network call, only flags the change;
#   - the flag survives a "reboot" (module reload);
#   - no WiFi: nothing is sent; WiFi up: the current set_* fields go out;
#   - a failed PATCH keeps the flag and waits RETRY_MS before trying again,
#     but a newer change is tried at once;
#   - a change made while a PATCH is in flight is not lost;
#   - push_pending() with nothing pending sends nothing.
# Exit code 0 = all good.

import json
import os
import shutil
import sys
import tempfile
import time

DEVICE = os.getcwd()
sys.path.insert(0, DEVICE)

if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: None

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


work = tempfile.mkdtemp(prefix="patchtest_")
os.chdir(work)
try:
    with open("config.json", "w") as f:
        json.dump({"device_id": "t1", "device_key": "k1",
                   "api_base": "http://example.invalid"}, f)

    from src.net import device_client as dc
    from src.app import actions, monotonic
    from src.net.background_process import TelemetryBackgroundProcess

    calls = []
    result = [True]
    during = [None]          # run inside the fake PATCH (a mid-flight change)

    def fake_patch(cfg, fields):
        calls.append(dict(fields))
        if during[0]:
            fn, during[0] = during[0], None
            fn()
        return result[0]

    dc.patch_set_fields = fake_patch

    class FakeWifi:
        up = False

        def is_connected(self):
            return self.up

    wifi = FakeWifi()
    bp = TelemetryBackgroundProcess(wifi_manager=wifi)
    clock = [10 ** 9]
    monotonic.ms = lambda: clock[0]

    print("save_set_fields only flags the change")
    actions.save_set_fields({"set_destination": [31.35, 34.27], "set_short_name": "SET"})
    check(calls == [], "no PATCH on the main thread")
    check(os.path.exists(dc.PENDING_FILE), "pending flag on flash")

    print("flag survives a reboot")
    dc._state[0] = None          # as after a reload; patch_set_fields stays faked
    check(dc.pending(), "pending read back from flash")

    print("background push")
    bp._maybe_push_device()
    check(calls == [], "no WiFi: nothing sent")
    wifi.up = True
    result[0] = False
    bp._maybe_push_device()
    check(len(calls) == 1 and calls[0].get("set_destination") == [31.35, 34.27]
          and calls[0].get("set_short_name") == "SET", "WiFi up: current set_* fields sent")
    check(dc.pending(), "failed PATCH keeps the flag")
    bp._maybe_push_device()
    check(len(calls) == 1, "no retry before RETRY_MS")
    actions.save_set_fields({"set_arrival": [31.4, 34.3]})
    bp._maybe_push_device()
    check(len(calls) == 2 and calls[1].get("set_arrival") == [31.4, 34.3],
          "a newer change is tried at once")
    clock[0] += dc.RETRY_MS
    result[0] = True
    bp._maybe_push_device()
    check(len(calls) == 3, "retried after RETRY_MS")
    check(not dc.pending() and not os.path.exists(dc.PENDING_FILE),
          "success clears the flag")

    print("change during an in-flight PATCH")
    actions.save_set_fields({"set_full_name": "Pond A"})
    during[0] = lambda: actions.save_set_fields({"set_full_name": "Pond B"})
    bp._maybe_push_device()
    check(dc.pending(), "flag kept: newer values not sent yet")
    bp._maybe_push_device()
    check(calls[-1].get("set_full_name") == "Pond B" and not dc.pending(),
          "next pass sends the newer values and clears")

    print("nothing pending")
    n = len(calls)
    check(dc.push_pending() is None and len(calls) == n, "push_pending sends nothing")
    bp._maybe_push_device()
    check(len(calls) == n, "background sends nothing")
finally:
    os.chdir("/")
    shutil.rmtree(work, ignore_errors=True)

print("\n%s" % ("ALL OK" if not FAILS else "%d FAILED" % len(FAILS)))
sys.exit(1 if FAILS else 0)
