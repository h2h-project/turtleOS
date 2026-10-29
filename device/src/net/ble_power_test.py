# src/net/ble_power_test.py — BLE_POWER_TEST / BLE_POWER_RESULT (turtleOS 2.6.4)
#
# Measures what each kind of advertising costs, with the turtle's own
# INA219, to choose ble_beacon_ms (docs/wrangler/README.md → System, and
# turtle_wrangler/docs/bluetooth_strategy.md for the results table).
#
# run() is reached through run()'s "ble_power_test" source, after the
# command's OK has gone out. It runs nested inside BleService.tick() and
# owns the main loop until it finishes, like the bench sweep: no nav, no
# telemetry ticks, servo rail off, and one OLED screen drawn per block, so
# the display costs the same in every block. A single click stops it.
#
# Each round runs every MODES block in order; blocks of one kind are pooled
# across rounds, so slow drift (battery voltage, temperature) spreads over
# all of them instead of landing on one. The INA219 averages 128
# conversions per reading (68 ms): an advert is a few ms, so single 0.5 ms
# conversions would catch or miss it by luck.

import struct
import time

# (mode, interval_ms): 0 radio off · 1 on, not advertising · 2 advertising
MODES = ((0, 0), (1, 0), (2, 500), (2, 1000), (2, 2000), (2, 5000), (2, 10240))
SETTLE_MS = 3000          # discarded at the start of each block (radio restart, INA219 window)
SAMPLE_MS = 70            # just over one 128-sample INA219 reading (68.1 ms)
VOLT_EVERY_MS = 1000
KICK_EVERY_MS = 1000      # a phone that connects is dropped, at most this often
RESULT_FILE = "/ble_power_test.json"

HEADER = "<IBBBB"         # finished_at, block_s, rounds, n, flags
ROW = "<BHiHHHB"          # mode, interval_ms, mean_ua, sem_ua, samples, mean_mv, flags
ROW_SIZE = 14
NA_I32 = 0x7FFFFFFF
NA_U16 = 0xFFFF


def duration_s(block_s, rounds):
    """What the app is told the test takes: every block, plus radio
    restarts and the start and end screens."""
    return rounds * len(MODES) * block_s + 10


def _label(mode, iv):
    if mode == 0:
        return "Radio off"
    if mode == 1:
        return "On, silent"
    return "Adv %s s" % (("%.1f" % (iv / 1000)) if iv < 10000 else "%.2f" % (iv / 1000))


class _Acc:
    """Running mean and variance (Welford) of one kind of block."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.v_sum = 0.0
        self.v_n = 0
        self.disturbed = False

    def add(self, x):
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)

    def row(self, mode, iv):
        mean_ua = NA_I32 if self.n == 0 else int(round(self.mean * 1000))
        if self.n < 2:
            sem_ua = NA_U16
        else:
            sem_ua = min(0xFFFE, int(round((self.m2 / (self.n - 1) / self.n) ** 0.5 * 1000)))
        mv = NA_U16 if self.v_n == 0 else min(0xFFFE, int(round(self.v_sum / self.v_n * 1000)))
        return {"mode": mode, "interval_ms": iv, "mean_ua": mean_ua, "sem_ua": sem_ua,
                "samples": min(self.n, 0xFFFF), "mean_mv": mv, "disturbed": self.disturbed}


def _draw(oled, lines):
    if oled is None:
        return
    try:
        fb = oled.oled
        fb.fill(0)
        oled.f_arvo16.write("BT power test", 0, 0)
        y = 22
        for t in lines:
            oled.f_small.write(t, 0, y)
            y += 13
        fb.show()
    except Exception:
        pass


def _unix_now():
    try:
        from src.app.journey import _now_unix_seconds
        return int(_now_unix_seconds())
    except Exception:
        return 0


def run(svc, ina, btn, oled, block_s, rounds):
    """Run the whole test (blocking) and save the results. Leaves the radio
    on with a fresh wrangle window, whatever happens."""
    print("[BLE] power test: %d round(s) of %d x %d s" % (rounds, len(MODES), block_s))
    accs = [_Acc() for _ in MODES]
    aborted = False
    try:
        # The phone that asked: let the Result notification go, then drop it.
        svc.test_kick()
        t = time.ticks_ms()
        while svc.connected() and time.ticks_diff(time.ticks_ms(), t) < 2000:
            time.sleep_ms(20)
        try:
            from src.drivers import servo_power
            servo_power.off("(BT power test)")
        except Exception:
            pass
        ina.set_averaging(128)
        try:
            btn.reset()
        except Exception:
            pass

        total = rounds * len(MODES)
        k = 0
        for r in range(rounds):
            for i, (mode, iv) in enumerate(MODES):
                k += 1
                acc = accs[i]
                want = -1 if mode == 0 else (None if mode == 1 else iv * 1000)
                svc.test_radio(want)
                _draw(oled, ("%d/%d  %s" % (k, total, _label(mode, iv)),
                             "Round %d of %d" % (r + 1, rounds), "Click to stop"))
                t0 = time.ticks_ms()
                settle = time.ticks_add(t0, SETTLE_MS)
                end = time.ticks_add(t0, block_s * 1000)
                next_sample = t0
                next_volt = settle
                kicked = None
                while True:
                    now = time.ticks_ms()
                    if time.ticks_diff(end, now) <= 0:
                        break
                    if svc.connected():
                        if kicked is None or time.ticks_diff(now, kicked) >= KICK_EVERY_MS:
                            svc.test_kick()
                            acc.disturbed = True
                            kicked = now
                    elif mode == 2 and not svc.advertising():
                        svc.test_radio(want)      # a connect stopped the advertising
                    if time.ticks_diff(now, next_sample) >= 0:
                        next_sample = time.ticks_add(next_sample, SAMPLE_MS)
                        if time.ticks_diff(now, settle) >= 0:
                            c = ina.current_ma()
                            if c is not None:
                                acc.add(c)
                    if time.ticks_diff(now, next_volt) >= 0:
                        next_volt = time.ticks_add(next_volt, VOLT_EVERY_MS)
                        v = ina.load_voltage_v()
                        if v is not None:
                            acc.v_sum += v
                            acc.v_n += 1
                    try:
                        if btn.poll_action() == "single":
                            aborted = True
                            break
                    except Exception:
                        pass
                    time.sleep_ms(5)
                if aborted:
                    break
            if aborted:
                break
    except Exception as e:
        print("[BLE] power test err:", repr(e))
        aborted = True
    finally:
        try:
            ina.wake()                    # back to single conversions
        except Exception:
            pass

    rows = [a.row(m, iv) for a, (m, iv) in zip(accs, MODES)]
    res = {"finished_at": _unix_now(), "block_s": block_s, "rounds": rounds,
           "aborted": aborted, "rows": rows}
    try:
        import json
        with open(RESULT_FILE, "w") as f:
            json.dump(res, f)
    except Exception as e:
        print("[BLE] power test save err:", repr(e))
    for row in rows:
        print("[BLE] power test %-10s %s uA +/- %s (%d samples)%s" % (
            _label(row["mode"], row["interval_ms"]), row["mean_ua"], row["sem_ua"],
            row["samples"], " DISTURBED" if row["disturbed"] else ""))
    _draw(oled, ("Stopped early" if aborted else "Done", "Results are in the app", ""))
    try:
        svc.test_end()
    except Exception as e:
        print("[BLE] power test radio restore err:", repr(e))
    time.sleep_ms(1500)
    return res


def load():
    """The last test's results, or None."""
    try:
        import json
        with open(RESULT_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def result_payload(res):
    """BLE_POWER_RESULT payload (contract: header + 14-byte rows)."""
    rows = res.get("rows") or []
    out = struct.pack(HEADER, int(res.get("finished_at") or 0) & 0xFFFFFFFF,
                      int(res.get("block_s") or 0) & 0xFF, int(res.get("rounds") or 0) & 0xFF,
                      len(rows), 1 if res.get("aborted") else 0)
    for r in rows:
        out += struct.pack(ROW, int(r["mode"]) & 0xFF, int(r["interval_ms"]) & 0xFFFF,
                           int(r["mean_ua"]), int(r["sem_ua"]) & 0xFFFF,
                           int(r["samples"]) & 0xFFFF, int(r["mean_mv"]) & 0xFFFF,
                           1 if r.get("disturbed") else 0)
    return out
