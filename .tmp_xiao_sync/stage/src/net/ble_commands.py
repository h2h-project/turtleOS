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


# opcode -> (payload length, handler). Length: int = exact, ("min", n) = at
# least n, None = anything. Phases 5–6 add the rest of the contract's table.
OPCODES = {
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
