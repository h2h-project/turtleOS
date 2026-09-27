# tests/ble_spike.py
# Run with: mpremote connect auto run tests/ble_spike.py
#
# Turtle Wrangler Phase 0 spike (docs/wrangler/01_firmware_ble.md), tests A + B.
# Throwaway — proves the BLE plumbing before ble_service.py is written.
#
#   A. Plumbing: advertise the v1 Turtle service UUID under the turtle's name,
#      serve a readable value, a 1 Hz notify counter, and a write that echoes
#      back how many bytes arrived (checks gatts_set_buffer + long writes/MTU).
#   B. Pairing: reading the "Secure" characteristic requires an encrypted,
#      authenticated link, so Android starts pairing. The turtle shows a
#      6-digit passkey on the OLED (and serial). Bonds are saved to
#      /ble_spike_bonds.json via _IRQ_SET_SECRET; re-run after a reset and the
#      phone should reconnect encrypted with no passkey.
#
# The IRQ handler only records events; all printing, OLED drawing and
# notifying happens in the main loop — the same rule ble_service.py will follow.
#
# Ctrl-C to stop. Cleanup: delete /ble_spike_bonds.json and forget the turtle
# in Android's Bluetooth settings.

import sys
for _p in ("/src", "/src/lib"):
    if _p not in sys.path:
        sys.path.append(_p)

import time
import json
import struct
import random
import binascii
import bluetooth
from micropython import const

RUN_S = 15 * 60           # auto-stop after 15 min
BONDS_FILE = "/ble_spike_bonds.json"
ADV_INTERVAL_US = 200_000

# ---- IRQ event codes ----
_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_IRQ_MTU_EXCHANGED = const(21)
_IRQ_ENCRYPTION_UPDATE = const(28)
_IRQ_GET_SECRET = const(29)
_IRQ_SET_SECRET = const(30)
_IRQ_PASSKEY_ACTION = const(31)

_PASSKEY_ACTION_INPUT = const(2)
_PASSKEY_ACTION_DISP = const(3)
_PASSKEY_ACTION_NUMCMP = const(4)

_IO_CAPABILITY_DISPLAY_ONLY = const(0)

# ---- characteristic flags ----
_F_READ = const(0x0002)
_F_WRITE = const(0x0008)
_F_NOTIFY = const(0x0010)
_F_READ_ENC = const(0x0200)
_F_READ_AUTHN = const(0x0400)

# ---- GATT contract v1 UUIDs (docs/wrangler/README.md) ----
def _uuid(short):
    return bluetooth.UUID("26d0{:04x}-5890-45f2-b0be-090d35436a95".format(short))

SVC_TURTLE = _uuid(0x0001)
CH_INFO = (_uuid(0x0101), _F_READ)                          # contract info <BB>
CH_COUNT = (_uuid(0x01F0), _F_READ | _F_NOTIFY)             # spike-only: 1 Hz counter
CH_ECHO = (_uuid(0x01F1), _F_READ | _F_WRITE | _F_NOTIFY)   # spike-only: write echo
CH_SECURE = (_uuid(0x01F2), _F_READ | _F_READ_ENC | _F_READ_AUTHN)  # spike-only: pairing trigger

# ---- event log filled by the IRQ, drained by the main loop ----
_events = []
_state = {"conn": None, "mtu": 23, "passkey": None, "enc": None, "echo_n": None}
_secrets = {}


def _log(*a):
    _events.append(" ".join(str(x) for x in a))


# ---- bond store ----
def _b64(b):
    return binascii.b2a_base64(b).decode().strip()


def _load_secrets():
    try:
        with open(BONDS_FILE) as f:
            for sec_type, key, value in json.load(f):
                _secrets[(sec_type, binascii.a2b_base64(key))] = binascii.a2b_base64(value)
        print("[BOND] loaded", len(_secrets), "secret(s) from", BONDS_FILE)
    except OSError:
        print("[BOND] no bond file yet (first run)")
    except Exception as e:
        print("[BOND] load error:", repr(e))


def _save_secrets():
    try:
        with open(BONDS_FILE, "w") as f:
            json.dump([(t, _b64(k), _b64(v)) for (t, k), v in _secrets.items()], f)
    except Exception as e:
        _log("[BOND] save error:", repr(e))


# ---- IRQ ----
def _irq(event, data):
    if event == _IRQ_CENTRAL_CONNECT:
        conn, addr_type, addr = data
        _state["conn"] = conn
        _state["mtu"] = 23
        _state["enc"] = None
        _log("[CONN] connected handle=%d addr=%s" % (conn, binascii.hexlify(bytes(addr), ":").decode()))
    elif event == _IRQ_CENTRAL_DISCONNECT:
        conn, _t, _a = data
        _state["conn"] = None
        _state["passkey"] = None
        _log("[CONN] disconnected handle=%d" % conn)
    elif event == _IRQ_GATTS_WRITE:
        conn, attr = data
        if attr == h_echo:
            _state["echo_n"] = len(ble.gatts_read(h_echo))
    elif event == _IRQ_MTU_EXCHANGED:
        conn, mtu = data
        _state["mtu"] = mtu
        _log("[MTU] exchanged:", mtu)
    elif event == _IRQ_ENCRYPTION_UPDATE:
        conn, encrypted, authenticated, bonded, key_size = data
        _state["enc"] = (encrypted, authenticated, bonded)
        _state["passkey"] = None
        _log("[ENC] encrypted=%d authenticated=%d bonded=%d key_size=%d"
             % (encrypted, authenticated, bonded, key_size))
    elif event == _IRQ_PASSKEY_ACTION:
        conn, action, passkey = data
        if action == _PASSKEY_ACTION_DISP:
            pk = random.randint(0, 999999)
            _state["passkey"] = pk
            ble.gap_passkey(conn, action, pk)
            _log("[PAIR] passkey to enter on the phone: %06d" % pk)
        else:
            _log("[PAIR] unexpected passkey action", action, "- declining")
            ble.gap_passkey(conn, action, 0)
    elif event == _IRQ_SET_SECRET:
        sec_type, key, value = data
        key = (sec_type, bytes(key))
        if value is None:
            if key in _secrets:
                del _secrets[key]
                _save_secrets()
                _log("[BOND] secret deleted (type %d)" % sec_type)
                return True
            return False
        _secrets[key] = bytes(value)
        _save_secrets()
        _log("[BOND] secret stored (type %d) total=%d" % (sec_type, len(_secrets)))
        return True
    elif event == _IRQ_GET_SECRET:
        sec_type, index, key = data
        if key is None:
            i = 0
            for (t, _k), v in _secrets.items():
                if t == sec_type:
                    if i == index:
                        return v
                    i += 1
            return None
        v = _secrets.get((sec_type, bytes(key)))
        _log("[BOND] secret lookup type %d: %s" % (sec_type, "HIT" if v else "miss"))
        return v


# ---- helpers ----
def _read_cfg():
    try:
        with open("/config.json") as f:
            return json.load(f)
    except Exception:
        return {}


def _name(cfg):
    n = str(cfg.get("device_name") or "").strip()
    if n:
        return n
    did = str(cfg.get("device_id") or "")
    return "Turtle-" + (did[-4:] if did else "0000")


def _adv_field(t, v):
    return struct.pack("BB", len(v) + 1, t) + v


def _advertise(name):
    adv = _adv_field(0x01, b"\x06") + _adv_field(0x07, bytes(SVC_TURTLE))
    nb = name.encode()[:29]
    resp = _adv_field(0x09, nb)
    ble.gap_advertise(ADV_INTERVAL_US, adv_data=adv, resp_data=resp, connectable=True)
    print("[ADV] advertising as %r (adv %d B, resp %d B)" % (nb, len(adv), len(resp)))


def _oled():
    try:
        from src.ui.oled import OLED
        return OLED(col_offset=int(_read_cfg().get("oled_col_offset", 0)))
    except Exception as e:
        print("[OLED] unavailable:", repr(e))
        return None


def _draw(o, name):
    if o is None:
        return
    fb = o.oled
    fb.fill(0)
    try:
        if _state["passkey"] is not None:
            o.draw_centered(o.f_small, "Pair code", 2)
            o.draw_centered(o.f_large, "%06d" % _state["passkey"], 22)
        else:
            o.draw_centered(o.f_small, "BLE spike", 0)
            o.draw_centered(o.f_small, name[:24], 12)
            if _state["conn"] is None:
                line = "advertising"
            else:
                enc = _state["enc"]
                line = "connected" if not enc else ("bonded" if enc[2] else "encrypted")
            o.draw_centered(o.f_med, line, 26)
            o.draw_centered(o.f_small, "MTU %d" % _state["mtu"], 50)
    except Exception as e:
        print("[OLED] draw error:", repr(e))
    fb.show()


# ---- main ----
print("=" * 52)
print("  Turtle Wrangler BLE spike (Phase 0, tests A + B)")
print("=" * 52)
print("[SYS]", sys.implementation, sys.platform)

cfg = _read_cfg()
name = _name(cfg)
oled = _oled()

_load_secrets()
ble = bluetooth.BLE()
ble.irq(_irq)
_retry = []
for k, v in (("bond", True), ("le_secure", True), ("mitm", True), ("io", _IO_CAPABILITY_DISPLAY_ONLY)):
    try:
        ble.config(**{k: v})
        print("[CFG] %s=%r ok (before active)" % (k, v))
    except Exception as e:
        print("[CFG] %s=%r failed before active: %r - retrying after" % (k, v, e))
        _retry.append((k, v))
ble.active(True)
print("[CFG] BLE active:", ble.active())
for k, v in _retry + [("gap_name", name), ("mtu", 185)]:
    try:
        ble.config(**{k: v})
        print("[CFG] %s=%r ok" % (k, v))
    except Exception as e:
        print("[CFG] %s=%r FAILED: %r" % (k, v, e))
try:
    print("[CFG] own address:", binascii.hexlify(ble.config("mac")[1], ":").decode())
except Exception as e:
    print("[CFG] mac read failed:", repr(e))

((h_info, h_count, h_echo, h_secure),) = ble.gatts_register_services(
    ((SVC_TURTLE, (CH_INFO, CH_COUNT, CH_ECHO, CH_SECURE)),)
)
ble.gatts_write(h_info, struct.pack("<BB", 1, 1))
ble.gatts_write(h_secure, b"secret ok")
ble.gatts_set_buffer(h_echo, 200, False)
print("[GATT] registered: info=%d count=%d echo=%d secure=%d" % (h_info, h_count, h_echo, h_secure))

_advertise(name)
_draw(oled, name)

count = 0
last_conn = None
last_passkey = None
t_end = time.ticks_add(time.ticks_ms(), RUN_S * 1000)
t_next = time.ticks_ms()
try:
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        while _events:
            print(_events.pop(0))

        conn = _state["conn"]
        if conn is None and last_conn is not None:
            _advertise(name)            # advertising stops on connect; resume after
        if conn != last_conn or _state["passkey"] != last_passkey:
            last_conn, last_passkey = conn, _state["passkey"]
            _draw(oled, name)

        n = _state["echo_n"]
        if n is not None:
            _state["echo_n"] = None
            msg = ("rx %d B" % n).encode()
            print("[ECHO] received %d bytes (MTU %d)" % (n, _state["mtu"]))
            ble.gatts_write(h_echo, msg)
            if conn is not None:
                try:
                    ble.gatts_notify(conn, h_echo, msg)
                except Exception as e:
                    print("[ECHO] notify failed:", repr(e))

        if time.ticks_diff(time.ticks_ms(), t_next) >= 0:
            t_next = time.ticks_add(t_next, 1000)
            count += 1
            v = struct.pack("<I", count)
            ble.gatts_write(h_count, v)
            if conn is not None:
                try:
                    ble.gatts_notify(conn, h_count, v)
                except Exception as e:
                    print("[COUNT] notify failed:", repr(e))
            if count % 30 == 0:
                print("[ALIVE] t=%ds conn=%s enc=%s" % (count, conn, _state["enc"]))

        time.sleep_ms(20)
    print("[DONE] run time elapsed")
finally:
    try:
        ble.active(False)
    except Exception:
        pass
    if oled is not None:
        oled.clear()
    print("[DONE] BLE off. Bond file:", BONDS_FILE, "(%d secrets)" % len(_secrets))
