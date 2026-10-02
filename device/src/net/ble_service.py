# src/net/ble_service.py — Turtle Wrangler BLE peripheral (Phase 2 skeleton)
#
# Owns the radio lifecycle for the GATT contract v1 in docs/wrangler/README.md:
# activate/deactivate, advertise the Turtle service UUID under the turtle's
# name, track the connection, and enforce the wrangle window. The OLED
# indicators (header "+", waiting-screen rune) read state() / advertising()
# directly — see connection_header.ble_visible().
#
# Serves the Turtle service: Contract info + Turtle name (static) and the
# eight telemetry characteristics packed by src/net/ble_telemetry.py
# (Phase 3), plus the Command service (0201 Command / 0202 Result) run by
# src/net/ble_commands.py (Phase 4).
#
# BONDING
#   LE Secure Connections, MITM, IO capability DisplayOnly: the turtle shows
#   a 6-digit passkey on the OLED and the phone's user types it in, so
#   pairing needs someone standing at the hull. Pairing is only accepted
#   while the Bluetooth screen is showing (set_pairing_allowed); otherwise
#   the link is dropped. Bonds persist in /ble_bonds.json (device-owned;
#   the sync scripts exclude it). Commands require a bonded, encrypted link
#   unless ble_require_bond is false (bench only).
# Telemetry is only packed while a phone is connected — nothing to read
# otherwise — and is refreshed in full the moment one connects.
#
# WRANGLE WINDOW
#   After boot, after waking from sleep, and whenever the Bluetooth screen is
#   opened, the turtle advertises for ble_window_min minutes (0 = always).
#   Closing the window only stops advertising — an open connection is never
#   dropped. After a disconnect, advertising resumes only while the window is
#   still open. ble_enabled = False means the radio is never activated.
#
# THREADING RULE
#   _irq() runs in MicroPython scheduler context. It only records state and
#   appends to event/command lists; tick() (called from run()'s _bg_tick on
#   the main loop) does all printing, advertising and command execution.
#   The one exception is the bond store: NimBLE's GET/SET_SECRET and
#   PASSKEY_ACTION need an answer synchronously, so those handlers read/write
#   the secrets (and /ble_bonds.json) and answer the passkey inside _irq().
#
# Built once in device/main.py step_init_runtime() (turtle mode only) and
# reached through instance() — never construct a second one.

import time

try:
    from micropython import const
except ImportError:          # host tests
    def const(x):
        return x

CONTRACT_VERSION = 1

ADV_INTERVAL_US = 500_000    # 0.5 s: found within a second or two, light on power
PREFERRED_MTU = 185

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_IRQ_MTU_EXCHANGED = const(21)
_IRQ_ENCRYPTION_UPDATE = const(28)
_IRQ_GET_SECRET = const(29)
_IRQ_SET_SECRET = const(30)
_IRQ_PASSKEY_ACTION = const(31)

_PASSKEY_ACTION_DISP = const(3)
_IO_CAPABILITY_DISPLAY_ONLY = const(0)

_F_READ = const(0x0002)
_F_WRITE = const(0x0008)
_F_NOTIFY = const(0x0010)

CMD_BUFFER = 128          # contract: Command attribute sized for WIFI_SET_CREDENTIALS
MAX_INBOX = 4             # queued raw commands; extras get ERR_BUSY from the runner

BONDS_FILE = "/ble_bonds.json"

_UUID_FMT = "26d0{:04x}-5890-45f2-b0be-090d35436a95"


def _uuid(short):
    import bluetooth
    return bluetooth.UUID(_UUID_FMT.format(short))


def _adv_field(t, v):
    import struct
    return struct.pack("BB", len(v) + 1, t) + v


# ---------------------------------------------------------------- bond store
# {(sec_type, key_bytes): value_bytes}, persisted as base64 JSON. Kept at
# module level so it survives stop()/start() of the radio.
_secrets = {}
_secrets_loaded = False


def _b64(b):
    import binascii
    return binascii.b2a_base64(b).decode().strip()


def _load_secrets():
    global _secrets_loaded
    if _secrets_loaded:
        return
    _secrets_loaded = True
    try:
        import json
        import binascii
        with open(BONDS_FILE) as f:
            for sec_type, key, value in json.load(f):
                _secrets[(sec_type, binascii.a2b_base64(key))] = binascii.a2b_base64(value)
        print("[BLE] %d bond secret(s) loaded" % len(_secrets))
    except OSError:
        pass
    except Exception as e:
        print("[BLE] bond file unreadable, ignoring:", repr(e))


def _save_secrets():
    try:
        import json
        tmp = BONDS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump([(t, _b64(k), _b64(v)) for (t, k), v in _secrets.items()], f)
        import os
        os.rename(tmp, BONDS_FILE)
    except Exception as e:
        print("[BLE] bond save failed:", repr(e))


def advertised_name(cfg):
    """device_name (turtles_tb.name), or Turtle-<last 4 of device_id>
    before the first online boot has saved one."""
    n = str((cfg or {}).get("device_name") or "").strip()
    if n:
        return n
    did = str((cfg or {}).get("device_id") or "").strip()
    return "Turtle-" + (did[-4:] if did else "0000")


class BleService:
    def __init__(self, cfg):
        self._name = advertised_name(cfg)
        self._window_min = int((cfg or {}).get("ble_window_min", 10) or 0)
        self._ble = None
        self._active = False
        self._advertising = False
        self._conn = None
        self._mtu = 23
        self._window_until = None     # ticks_ms deadline; None = closed
        self._events = []
        self._suspended = False       # radio stopped for sleep, not by the user
        self._handles = {}
        self._data = None             # ble_telemetry.TurtleData (attach_sources)
        self._due = {}                # short id -> ticks_ms next refresh
        self._last_val = {}           # short id -> bytes last written
        self._last_key = {}           # short id -> notify_key() last notified
        self._last_notify = {}        # short id -> ticks_ms of last notify
        self._enc = None              # (encrypted, authenticated, bonded) of this link
        self._passkey = None          # 6-digit code while pairing (drawn on the OLED)
        self._pairing_allowed = False # True only while the Bluetooth screen shows
        self._cmd_inbox = []          # raw Command writes, queued by _irq()
        self._sources = None
        from src.net.ble_commands import CommandRunner
        self._runner = CommandRunner(self, lambda: self._sources)

    # ------------------------------------------------------------ status

    @property
    def name(self):
        return self._name

    def active(self):
        return self._active

    def connected(self):
        return self._conn is not None

    def advertising(self):
        return self._advertising

    def mtu(self):
        return self._mtu

    def link_bonded(self):
        """True when the current link is encrypted, authenticated (MITM,
        passkey) and bonded."""
        e = self._enc
        return bool(self._conn is not None and e and e[0] and e[1] and e[2])

    def passkey(self):
        return self._passkey

    def command_busy(self):
        return self._runner.busy()

    def set_pairing_allowed(self, allowed):
        """The Bluetooth screen opens the pairing gate while it's showing."""
        self._pairing_allowed = bool(allowed)
        if not allowed:
            self._passkey = None

    def bond_count(self):
        """Stored peer bonds (LTKs), not counting our own identity key."""
        _load_secrets()
        return sum(1 for (t, _k) in _secrets if t == 2)

    def forget_bonds(self):
        """Delete every stored bond and restart the radio with a clean
        store. Phones must then forget the turtle and pair again."""
        _secrets.clear()
        try:
            import os
            os.remove(BONDS_FILE)
        except Exception:
            pass
        print("[BLE] all bonds forgotten")
        if self._active:
            self.stop()
            self.start()

    def window_open(self):
        if self._window_min == 0:
            return True
        if self._window_until is None:
            return False
        return time.ticks_diff(self._window_until, time.ticks_ms()) > 0

    def window_remaining_s(self):
        """Seconds left in the window; None when always-open or closed."""
        if self._window_min == 0 or self._window_until is None:
            return None
        return max(0, time.ticks_diff(self._window_until, time.ticks_ms()) // 1000)

    def state(self):
        """"off" | "connected" | "advertising" | "closed" (on, window shut)."""
        if not self._active:
            return "off"
        if self._conn is not None:
            return "connected"
        if self._advertising:
            return "advertising"
        return "closed"

    # ------------------------------------------------------------ lifecycle

    def start(self):
        """Activate the radio, register the GATT table and open a window."""
        if self._active:
            self.open_window()
            return True
        try:
            import bluetooth
            import struct
            _load_secrets()               # our identity key is read at activation
            self._ble = bluetooth.BLE()
            self._ble.irq(self._irq)
            retry = []
            for k, v in (("bond", True), ("le_secure", True), ("mitm", True),
                         ("io", _IO_CAPABILITY_DISPLAY_ONLY)):
                try:
                    self._ble.config(**{k: v})
                except Exception:
                    retry.append((k, v))
            self._ble.active(True)
            for k, v in retry:
                try:
                    self._ble.config(**{k: v})
                except Exception as e:
                    print("[BLE] security config %s failed: %r" % (k, e))
            try:
                self._ble.config(gap_name=self._name)
            except Exception as e:
                print("[BLE] gap_name not set:", repr(e))
            try:
                self._ble.config(mtu=PREFERRED_MTU)
            except Exception as e:
                print("[BLE] mtu not set:", repr(e))

            from src.net import ble_telemetry as T
            chars = [(_uuid(0x0101), _F_READ),   # Contract info <BB>
                     (_uuid(0x0102), _F_READ)]   # Turtle name, UTF-8
            for cid, _base, _force in T.SCHEDULE:
                chars.append((_uuid(cid), _F_READ | _F_NOTIFY))
            svc = (_uuid(0x0001), tuple(chars))
            cmd_svc = (_uuid(0x0002), (
                (_uuid(0x0201), _F_WRITE),              # Command
                (_uuid(0x0202), _F_READ | _F_NOTIFY),   # Result
            ))
            (handles, (h_cmd, h_res)) = self._ble.gatts_register_services((svc, cmd_svc))
            self._ble.gatts_set_buffer(h_cmd, CMD_BUFFER, False)
            self._ble.gatts_set_buffer(h_res, 64)
            h_info, h_name = handles[0], handles[1]
            self._handles = {"info": h_info, "name": h_name, "cmd": h_cmd, "res": h_res}
            for i, (cid, _base, _force) in enumerate(T.SCHEDULE):
                self._handles[cid] = handles[2 + i]
                self._ble.gatts_set_buffer(handles[2 + i], 20)
            self._due = {}
            self._last_val = {}
            self._last_key = {}
            self._ble.gatts_write(h_info, struct.pack("<BB", CONTRACT_VERSION, 1))
            name_b = self._name.encode()[:64]
            self._ble.gatts_set_buffer(h_name, 64)
            self._ble.gatts_write(h_name, name_b)

            self._active = True
            self._conn = None
            self._enc = None
            self._passkey = None
            self._cmd_inbox = []
            self._advertising = False
            print("[BLE] active as %r (contract v%d)" % (self._name, CONTRACT_VERSION))
            self.open_window()
            return True
        except Exception as e:
            print("[BLE] start failed:", repr(e))
            self._active = False
            return False

    def stop(self):
        """Deactivate the radio. Drops any connection."""
        if self._ble is not None:
            try:
                if self._conn is not None:
                    self._ble.gap_disconnect(self._conn)
            except Exception:
                pass
            try:
                self._ble.active(False)
            except Exception as e:
                print("[BLE] deactivate err:", repr(e))
        self._active = False
        self._advertising = False
        self._conn = None
        self._enc = None
        self._passkey = None
        self._cmd_inbox = []
        self._runner.cancel()
        self._window_until = None
        print("[BLE] off")

    def suspend(self):
        """Sleep: stop the radio, remembering it was on."""
        if self._active:
            self._suspended = True
            self.stop()

    def resume(self):
        """Wake: restart the radio (with a fresh window) if sleep stopped it."""
        if self._suspended:
            self._suspended = False
            self.start()

    def open_window(self):
        """(Re)open the wrangle window; advertising starts on the next tick."""
        if self._window_min == 0:
            self._window_until = None
        else:
            self._window_until = time.ticks_add(time.ticks_ms(), self._window_min * 60_000)
        if self._active:
            print("[BLE] window open: %s" % (
                "always" if self._window_min == 0 else "%d min" % self._window_min))

    # ------------------------------------------------------------ IRQ

    def _irq(self, event, data):
        # Scheduler context: record only (see THREADING RULE above).
        if event == _IRQ_CENTRAL_CONNECT:
            self._conn = data[0]
            self._advertising = False      # the stack stops advertising on connect
            self._mtu = 23
            self._events.append("connect")
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self._conn = None
            self._enc = None
            self._passkey = None
            self._events.append("disconnect")
        elif event == _IRQ_MTU_EXCHANGED:
            self._mtu = data[1]
            self._events.append("mtu")
        elif event == _IRQ_GATTS_WRITE:
            conn, attr = data
            if attr == self._handles.get("cmd"):
                if len(self._cmd_inbox) < MAX_INBOX:
                    self._cmd_inbox.append(bytes(self._ble.gatts_read(attr)))
                else:
                    self._events.append("inbox_full")
        elif event == _IRQ_ENCRYPTION_UPDATE:
            conn, encrypted, authenticated, bonded, key_size = data
            self._enc = (bool(encrypted), bool(authenticated), bool(bonded))
            self._passkey = None
            self._events.append("enc")
        elif event == _IRQ_PASSKEY_ACTION:
            conn, action, _pk = data
            if action == _PASSKEY_ACTION_DISP and self._pairing_allowed:
                import os
                pk = int.from_bytes(os.urandom(4), "little") % 1_000_000
                self._passkey = pk
                self._ble.gap_passkey(conn, action, pk)
                self._events.append("passkey")
            else:
                # Not on the Bluetooth screen (or an unexpected method):
                # refuse — tick() drops the link.
                self._events.append("pair_refused")
        elif event == _IRQ_SET_SECRET:
            sec_type, key, value = data
            key = (sec_type, bytes(key))
            if value is None:
                if key in _secrets:
                    del _secrets[key]
                    _save_secrets()
                    return True
                return False
            _secrets[key] = bytes(value)
            _save_secrets()
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
            return _secrets.get((sec_type, bytes(key)))

    # ------------------------------------------------------------ tick

    def tick(self):
        """Main-loop housekeeping. Cheap; safe to call every _bg_tick."""
        if not self._active:
            return
        try:
            while self._events:
                ev = self._events.pop(0)
                if ev == "connect":
                    print("[BLE] phone connected")
                    # Fresh values for the phone's first reads, and a first
                    # notify for every characteristic once it subscribes.
                    self._due = {}
                    self._last_key = {}
                elif ev == "disconnect":
                    print("[BLE] phone disconnected")
                    self._runner.cancel()
                    self._cmd_inbox = []
                elif ev == "mtu":
                    print("[BLE] MTU", self._mtu)
                elif ev == "enc":
                    e = self._enc or (False, False, False)
                    print("[BLE] link encrypted=%d authenticated=%d bonded=%d" % e)
                elif ev == "passkey":
                    print("[BLE] pairing: passkey shown on the Bluetooth screen")
                elif ev == "pair_refused":
                    print("[BLE] pairing refused - open the Bluetooth screen to pair")
                    try:
                        if self._conn is not None:
                            self._ble.gap_disconnect(self._conn)
                    except Exception:
                        pass
                elif ev == "inbox_full":
                    print("[BLE] command inbox full - write dropped")

            while self._conn is not None and self._cmd_inbox:
                self._runner.handle(self._cmd_inbox.pop(0))
            self._runner.tick()          # polls, and a pending reboot / radio-off

            if self._conn is not None:
                self._pump()

            if self._conn is None:
                want = self.window_open()
                if want and not self._advertising:
                    self._advertise(True)
                elif not want and self._advertising:
                    self._advertise(False)
                    print("[BLE] window closed - advertising stopped")
        except Exception as e:
            print("[BLE] tick err:", repr(e))

    # ------------------------------------------------------------ telemetry

    def attach_sources(self, sources):
        """Hand in run()'s getters (see ble_telemetry.TurtleData)."""
        sources = dict(sources or {})
        sources.setdefault("bonded", self.link_bonded)
        sources.setdefault("cmd_busy", self.command_busy)
        self._sources = sources
        try:
            from src.net.ble_telemetry import TurtleData
            self._data = TurtleData(sources)
        except Exception as e:
            print("[BLE] telemetry attach failed:", repr(e))
            self._data = None

    def send_result(self, value):
        """Write + notify the Result characteristic (readable afterwards, so
        an app that reconnects can learn a command's outcome)."""
        h = self._handles.get("res")
        if h is None or self._ble is None:
            return
        try:
            self._ble.gatts_write(h, value)
            if self._conn is not None:
                self._ble.gatts_notify(self._conn, h, value)
        except Exception as e:
            print("[BLE] result send err:", repr(e))

    def _pump(self):
        """Refresh due characteristics; notify the ones whose value changed
        (or whose forced interval is up). At most one pass per tick."""
        if self._data is None or self._ble is None:
            return
        from src.net import ble_telemetry as T
        now = time.ticks_ms()
        for cid, base, force in T.SCHEDULE:
            due = self._due.get(cid)
            if due is not None and time.ticks_diff(now, due) < 0:
                continue
            self._due[cid] = time.ticks_add(now, self._data.period(cid, base))
            h = self._handles.get(cid)
            if h is None:
                continue
            try:
                val = self._data.pack(cid, now)
            except Exception as e:
                print("[BLE] pack %04x err: %r" % (cid, e))
                continue
            if val is None:
                continue
            if val != self._last_val.get(cid):
                self._ble.gatts_write(h, val)
                self._last_val[cid] = val
            key = T.notify_key(cid, val)
            forced = bool(force) and time.ticks_diff(
                now, self._last_notify.get(cid, time.ticks_add(now, -force))) >= force
            if key != self._last_key.get(cid) or forced:
                conn = self._conn
                if conn is not None:
                    try:
                        self._ble.gatts_notify(conn, h, val)
                        self._last_notify[cid] = now
                    except Exception as e:
                        print("[BLE] notify %04x err: %r" % (cid, e))
                self._last_key[cid] = key

    def _advertise(self, on):
        try:
            if on:
                adv = _adv_field(0x01, b"\x06") + _adv_field(0x07, bytes(_uuid(0x0001)))
                resp = _adv_field(0x09, self._name.encode()[:29])
                self._ble.gap_advertise(ADV_INTERVAL_US, adv_data=adv,
                                        resp_data=resp, connectable=True)
                self._advertising = True
                print("[BLE] advertising")
            else:
                self._ble.gap_advertise(None)
                self._advertising = False
        except Exception as e:
            print("[BLE] advertise err:", repr(e))
            self._advertising = False


# ---------------------------------------------------------------- singleton

_instance = None


def init(cfg):
    """Build the one BleService (boot step 8). Starts the radio only when
    ble_enabled; otherwise it stays off until the Bluetooth screen or an
    action turns it on."""
    global _instance
    if _instance is None:
        _instance = BleService(cfg)
        if (cfg or {}).get("ble_enabled", True):
            _instance.start()
        else:
            print("[BLE] disabled in config (ble_enabled=false)")
    return _instance


def instance():
    return _instance
