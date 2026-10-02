# src/net/ble_commands.py — Turtle Wrangler command channel (Phase 4)
#
# GATT contract v1 → Command protocol (docs/wrangler/README.md):
#   Command 0201 (write):  <BB> opcode, seq  + payload
#   Result  0202 (notify): <BBB> opcode, seq, code  + payload
#
# Rules implemented here:
#   - every command gets exactly ONE final result; slow ones first send
#     IN_PROGRESS with the same seq;
#   - one command at a time: anything arriving meanwhile gets ERR_BUSY;
#   - framing / length / bond checks happen before a handler runs, each with
#     its own result code, so the app always learns why;
#   - commands run from BleService.tick() on the main loop — the IRQ handler
#     only queued the raw bytes.
#
# Handlers live in OPCODES and call src.app.actions (the same code the OLED
# gestures use). A handler returns (code, payload_bytes, followup):
#   followup None                  nothing more to do
#   ("after", ms, fn)              run fn() ms after the final result went out
#                                  (reboot, radio off — the phone must get the
#                                  result before the link drops)
#   ("poll", fn, timeout_ms)       code was IN_PROGRESS; fn() returns None while
#                                  still running, else (code, payload_bytes)

import struct
import time

# ---- result codes (mirror src/app/actions.py) ----
OK = 0x00
IN_PROGRESS = 0x01
ERR_UNKNOWN_OPCODE = 0x10
ERR_BAD_LENGTH = 0x11
ERR_BAD_VALUE = 0x12
ERR_BUSY = 0x13
ERR_NOT_BONDED = 0x14
ERR_INTERNAL = 0x7F

AFTER_RESULT_MS = 300     # give the Result notification time to go out


# ---------------------------------------------------------------- handlers
# Signature: fn(ctx, payload) -> (code, payload_bytes, followup)
# ctx: {"svc": BleService, "src": sources dict of getters}

def _op_ble_set_enabled(ctx, payload):
    """0x33 BLE_SET_ENABLED <B> — only 0 is accepted: once Bluetooth is off,
    only the turtle's Bluetooth screen can turn it back on."""
    if payload[0] != 0:
        return ERR_BAD_VALUE, b"", None

    def _off():
        from src.app import actions
        actions.ble_set_enabled(False)

    return OK, b"", ("after", AFTER_RESULT_MS, _off)


def _op_reboot(ctx, payload):
    """0x42 REBOOT — OK, then machine.reset()."""
    def _reset():
        print("[BLE] reboot requested over Bluetooth")
        import machine
        machine.reset()

    return OK, b"", ("after", AFTER_RESULT_MS, _reset)


# ---- Phase 5: journey, destination, GPS & logging ----------------------
# Each calls the same src.app.actions function as its OLED gesture; the
# action's result code is already the contract's, so only payloads differ.

def _get(ctx, key):
    try:
        g = ctx["src"].get(key)
        return g() if g is not None else None
    except Exception:
        return None


def _e7(deg):
    return int(round(float(deg) * 1e7))


def _op_journey_start(ctx, payload):
    """0x01 JOURNEY_START — uses the turtle's own fix. OK <I> journey_id."""
    from src.app import actions
    code, info = actions.journey_start(_get(ctx, "gps"))
    if code == actions.OK:
        return code, struct.pack("<I", int(info.get("journey_id") or 0) & 0xFFFFFFFF), None
    return code, b"", None


def _op_journey_end(ctx, payload):
    """0x02 JOURNEY_END — OK <B> arrival_stamped (0 = no fix; still ended)."""
    from src.app import actions
    code, info = actions.journey_end(_get(ctx, "gps"))
    if code == actions.OK:
        return code, struct.pack("<B", 1 if info.get("arrival_stamped") else 0), None
    return code, b"", None


def _op_dest_set_here(ctx, payload):
    """0x03 DEST_SET_HERE — the turtle's position. OK <ii> stamped lat/lon."""
    from src.app import actions
    code, info = actions.dest_set_here(_get(ctx, "gps"))
    if code == actions.OK:
        return code, struct.pack("<ii", _e7(info["lat"]), _e7(info["lon"])), None
    return code, b"", None


def _op_dest_set_mission(ctx, payload):
    """0x04 DEST_SET_MISSION."""
    from src.app import actions
    code, _info = actions.dest_set_mission()
    return code, b"", None


def _op_dest_set_coords(ctx, payload):
    """0x05 DEST_SET_COORDS <ii> lat_e7, lon_e7 (app map picker)."""
    from src.app import actions
    lat_e7, lon_e7 = struct.unpack("<ii", payload)
    code, _info = actions.dest_set_coords(lat_e7 / 1e7, lon_e7 / 1e7)
    return code, b"", None


def _op_dest_clear(ctx, payload):
    """0x06 DEST_CLEAR."""
    from src.app import actions
    code, _info = actions.dest_clear()
    return code, b"", None


def _op_gps_stamp(ctx, payload):
    """0x20 GPS_STAMP — manual mode only.
    OK <BHB> committed_to, stamps_session, has_position
    ERR_NOT_STAMPED <B> reason"""
    from src.app import actions
    code, info = actions.gps_stamp(_get(ctx, "telemetry"), _get(ctx, "cfg"))
    if code == actions.OK:
        return code, struct.pack("<BHB", int(info.get("committed_to", 1)),
                                 min(int(info.get("stamps_session", 0)), 0xFFFE),
                                 1 if info.get("has_position") else 0), None
    if code == actions.ERR_NOT_STAMPED:
        return code, struct.pack("<B", int(info.get("reason", 0xFF)) & 0xFF), None
    return code, b"", None


def _op_gps_set_enabled(ctx, payload):
    """0x21 GPS_SET_ENABLED <B> 0/1."""
    if payload[0] > 1:
        return ERR_BAD_VALUE, b"", None
    from src.app import actions
    code, _info = actions.gps_set_enabled(_get(ctx, "gps"), payload[0] == 1)
    return code, b"", None


_MODES = ("off", "auto", "manual")


def _op_telemetry_set_mode(ctx, payload):
    """0x22 TELEMETRY_SET_MODE <B> 0 off · 1 auto · 2 manual."""
    if payload[0] >= len(_MODES):
        return ERR_BAD_VALUE, b"", None
    from src.app import actions
    code, _info = actions.telemetry_set_mode(_MODES[payload[0]])
    return code, b"", None


def _op_telemetry_set_interval(ctx, payload):
    """0x23 TELEMETRY_SET_INTERVAL <H> seconds (>= 10)."""
    from src.app import actions
    (secs,) = struct.unpack("<H", payload)
    code, _info = actions.telemetry_set_interval(secs)
    return code, b"", None


API_HANDSHAKE_TIMEOUT_MS = 30_000


def _op_api_handshake(ctx, payload):
    """0x24 API_HANDSHAKE — IN_PROGRESS, then OK / ERR_API_FAILED once the
    background sender records a new attempt (ERR_API_FAILED after 30 s)."""
    from src.app import actions
    tel = _get(ctx, "telemetry")
    code, info = actions.api_handshake(tel, _get(ctx, "cfg"))
    if code != actions.IN_PROGRESS:
        return code, b"", None
    before = info.get("before_ms")
    deadline = time.ticks_add(time.ticks_ms(), API_HANDSHAKE_TIMEOUT_MS)

    def _poll():
        r = actions.api_outcome(tel, before)
        if r is not None:
            return r, b""
        if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
            return actions.ERR_API_FAILED, b""
        return None

    return IN_PROGRESS, b"", ("poll", _poll, API_HANDSHAKE_TIMEOUT_MS + 5_000)


# opcode -> (payload length, handler). Length: int = exact, ("min", n) = at
# least n, None = anything. Phase 6 adds the rest of the contract's table.
OPCODES = {
    0x01: (0, _op_journey_start),
    0x02: (0, _op_journey_end),
    0x03: (0, _op_dest_set_here),
    0x04: (0, _op_dest_set_mission),
    0x05: (8, _op_dest_set_coords),
    0x06: (0, _op_dest_clear),
    0x20: (0, _op_gps_stamp),
    0x21: (1, _op_gps_set_enabled),
    0x22: (1, _op_telemetry_set_mode),
    0x23: (2, _op_telemetry_set_interval),
    0x24: (0, _op_api_handshake),
    0x33: (1, _op_ble_set_enabled),
    0x42: (0, _op_reboot),
}


def _length_ok(rule, n):
    if rule is None:
        return True
    if isinstance(rule, tuple):
        return n >= rule[1]
    return n == rule


# ---------------------------------------------------------------- runner

class CommandRunner:
    def __init__(self, svc, sources_getter):
        self._svc = svc
        self._sources = sources_getter     # () -> sources dict (may be None)
        self._pending = None               # dict while a command is running

    def busy(self):
        return self._pending is not None

    def cancel(self):
        """Link dropped: forget any in-flight command (its result can't be
        delivered; the app reads Result after reconnecting)."""
        p = self._pending
        self._pending = None
        # A queued reboot / radio-off still happens — the phone asked for it.
        if p is not None and p.get("kind") == "after":
            try:
                p["fn"]()
            except Exception as e:
                print("[BLE] after-cancel action err:", repr(e))

    def _cfg(self):
        src = self._sources() or {}
        try:
            return src.get("cfg")() or {}
        except Exception:
            return {}

    def _send(self, op, seq, code, payload=b""):
        self._svc.send_result(struct.pack("<BBB", op & 0xFF, seq & 0xFF, code) + payload)
        print("[BLE] cmd 0x%02x seq %d -> 0x%02x" % (op, seq, code))

    def handle(self, raw):
        """One raw write from the Command characteristic."""
        if len(raw) < 2:
            self._send(raw[0] if raw else 0, 0, ERR_BAD_LENGTH)
            return
        op, seq, payload = raw[0], raw[1], bytes(raw[2:])

        if self._pending is not None:
            self._send(op, seq, ERR_BUSY)
            return
        entry = OPCODES.get(op)
        if entry is None:
            self._send(op, seq, ERR_UNKNOWN_OPCODE)
            return
        rule, fn = entry
        if not _length_ok(rule, len(payload)):
            self._send(op, seq, ERR_BAD_LENGTH)
            return
        if self._cfg().get("ble_require_bond", True) and not self._svc.link_bonded():
            self._send(op, seq, ERR_NOT_BONDED)
            return

        try:
            code, out, follow = fn({"svc": self._svc, "src": self._sources() or {}}, payload)
        except Exception as e:
            print("[BLE] cmd 0x%02x handler err: %r" % (op, e))
            self._send(op, seq, ERR_INTERNAL)
            return

        self._send(op, seq, code, out or b"")
        now = time.ticks_ms()
        if follow is None:
            return
        kind = follow[0]
        if kind == "after":
            self._pending = {"kind": "after", "op": op, "seq": seq,
                             "due": time.ticks_add(now, follow[1]), "fn": follow[2]}
        elif kind == "poll" and code == IN_PROGRESS:
            self._pending = {"kind": "poll", "op": op, "seq": seq, "fn": follow[1],
                             "deadline": time.ticks_add(now, follow[2])}

    def tick(self):
        p = self._pending
        if p is None:
            return
        now = time.ticks_ms()
        if p["kind"] == "after":
            if time.ticks_diff(now, p["due"]) >= 0:
                self._pending = None
                try:
                    p["fn"]()
                except Exception as e:
                    print("[BLE] after-action err:", repr(e))
            return
        # poll
        try:
            r = p["fn"]()
        except Exception as e:
            print("[BLE] poll err:", repr(e))
            r = (ERR_INTERNAL, b"")
        if r is None and time.ticks_diff(now, p["deadline"]) >= 0:
            r = (ERR_INTERNAL, b"")
            print("[BLE] cmd 0x%02x timed out" % p["op"])
        if r is not None:
            self._pending = None
            self._send(p["op"], p["seq"], r[0], r[1] or b"")
