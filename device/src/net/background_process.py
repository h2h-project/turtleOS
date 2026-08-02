# src/net/background_process.py
#
# TelemetryBackgroundProcess — runs a FreeRTOS task (via _thread) that owns
# all blocking network I/O: WiFi reconnect, HTTP POST, and batch queue flush.
#
# The main thread builds telemetry payloads and calls put_payload() — a
# non-blocking hand-off. The background thread picks up the payload, handles
# WiFi reconnection if needed, POSTs it, and flushes any queued readings.
# The main thread stays free to animate the display and respond to buttons
# regardless of how long the network operations take.
#
# Thread-safety contract:
#   - self._lock guards _pending and _result (shared between threads).
#   - self._client is only ever touched by the background thread (no lock needed).
#   - self._wifi.is_connected() / reconnect() are called only from bg thread;
#     the main thread reads status via result() from _result snapshot only.
#   - I2C is never touched by the background thread; all sensor reads stay
#     on the main thread before put_payload() is called.

import time

try:
    import _thread
    _THREAD_AVAILABLE = True
except ImportError:
    _THREAD_AVAILABLE = False

# Precomputed morse for "BLESS THE AIR" at unit=50 ms.
# Format per byte: (is_on << 7) | duration_in_units.
# Total: 55 bytes, ~4100 ms.
_MORSE_UNIT_MS = 50
_MORSE_BLESSTHEAIR = bytearray([
    0x83, 0x01, 0x81, 0x01, 0x81, 0x01, 0x81, 0x03,  # B: -...
    0x81, 0x01, 0x83, 0x01, 0x81, 0x01, 0x81, 0x03,  # L: .-..
    0x81, 0x03,                                        # E: .
    0x81, 0x01, 0x81, 0x01, 0x81, 0x03,               # S: ...
    0x81, 0x01, 0x81, 0x01, 0x81, 0x07,               # S: ... (word gap)
    0x83, 0x03,                                        # T: -
    0x81, 0x01, 0x81, 0x01, 0x81, 0x01, 0x81, 0x03,  # H: ....
    0x81, 0x07,                                        # E: . (word gap)
    0x81, 0x01, 0x83, 0x03,                            # A: .-
    0x81, 0x01, 0x81, 0x03,                            # I: ..
    0x81, 0x01, 0x83, 0x01, 0x81,                     # R: .-. (no trailing gap)
])


def _gc():
    try:
        import gc
        gc.collect()
    except Exception:
        pass


class TelemetryBackgroundProcess:
    LOOP_SLEEP_MS = 100
    PENDING_MAX = 8

    # Minimum gap between association attempts, however many times a check is
    # requested. The idle/waiting screen is where the device spends most of its
    # life, so an ungated "check on screen entry" would scan almost constantly.
    #
    # Exponential: 15 min after the first failure, doubling per consecutive
    # failure up to a 12 h cap, reset to the base on any success. A turtle that
    # leaves coverage goes quiet within a few hours and stays quiet for the rest
    # of the voyage (~2 attempts/day instead of ~96); one that drops its AP near
    # shore still recovers in 15 min. A deliberate triple-click bypasses all of
    # it — see request_wifi_check(force=True).
    WIFI_RETRY_BASE_MS = 900000      # 15 min
    WIFI_RETRY_CAP_MS = 43200000     # 12 h
    WIFI_FAIL_STREAK_MAX = 6         # 15m << 6 = 16 h, already past the cap

    def __init__(self, wifi_manager):
        self._wifi = wifi_manager
        self._client = None

        if _THREAD_AVAILABLE:
            self._lock = _thread.allocate_lock()
        else:
            self._lock = None

        # Payload FIFO: main thread appends, background thread pops. Bounded at
        # PENDING_MAX; only an overflow beyond that drops anything, and every
        # payload the thread pops is either sent or enqueued to flash.
        self._pending = []         # [(payload_dict, cfg_dict), ...]

        # Result snapshot: background thread writes, main thread reads.
        self._result = {
            "ok": None,
            "sending": False,
            "msg": "",
            "last_ms": None,
            "wifi_ok": None,   # set after each reconnect; main thread reads this
        }

        self._running = False
        self._thread_alive = False

        # WiFi reconnect gating. The send path no longer scans/associates on
        # its own — see _send(). request_wifi_check() arms this, and the
        # exponential backoff keeps repeated screen entries from turning into
        # repeated scans.
        self._wifi_check_requested = False
        self._wifi_force = False
        self._last_wifi_attempt_ms = None
        self._wifi_fail_streak = 0
        self._was_connected = False

        # LED blink state (used during active sends)
        self._blink_timer = None
        self._blink_led = None
        self._blink_active = 1
        self._blink_state = [False]
        self._morse_state = None   # bytearray(4) during morse; None otherwise

    # ------------------------------------------------------------------
    # Main-thread API (non-blocking)
    # ------------------------------------------------------------------

    def start(self):
        """Start the background thread. Safe to call multiple times."""
        if self._running:
            return
        if not _THREAD_AVAILABLE:
            print("[BACKGROUND] _thread not available — running inline fallback")
            return
        self._running = True
        try:
            _thread.start_new_thread(self._run, ())
            print("[BACKGROUND] Process thread started")
        except Exception as e:
            print("[BACKGROUND] Failed to start thread:", repr(e))
            self._running = False

    def request_wifi_check(self, force=False):
        """Ask the background thread to try associating on its next send.

        Non-blocking: sets a flag, never touches the radio on the main thread.
        Call from boot and on entry to the waiting / online / wifi screens.

        force=True is the deliberate-user-action path: it bypasses both the
        backoff and mission_connection_mode, so a triple-click into the
        connectivity carousel always gets a real attempt. Everything else is
        advisory and may be ignored.
        """
        self._wifi_check_requested = True
        if force:
            self._wifi_force = True

    def put_payload(self, payload, cfg):
        """
        Hand off a payload for the background thread to send. Non-blocking.

        Returns True if the payload was accepted. False means the background
        thread is not running (never started, or it crashed) and the caller
        owns the payload — it must queue it to flash itself, or the reading is
        lost. Payloads queue up to PENDING_MAX deep so a slow send (a 6 s WiFi
        reconnect attempt, say) cannot swallow readings taken behind it.
        """
        if not self._running or not self._thread_alive or self._lock is None:
            return False
        self._lock.acquire()
        try:
            self._pending.append((payload, cfg))
            if len(self._pending) > self.PENDING_MAX:
                del self._pending[0]
        finally:
            self._lock.release()
        return True

    def result(self):
        """Non-blocking snapshot of the last send outcome."""
        if self._lock is None:
            return dict(self._result)
        self._lock.acquire()
        try:
            return dict(self._result)
        finally:
            self._lock.release()

    def is_alive(self):
        """True while the background thread is running."""
        return self._thread_alive

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self):
        self._thread_alive = True
        try:
            while self._running:
                pending = self._take_pending()
                if pending is not None:
                    payload, cfg = pending
                    try:
                        self._send(payload, cfg)
                    except Exception as e:
                        # One failed send must never kill the thread — that would
                        # silently strand every later reading.
                        print("[BACKGROUND] send failed:", repr(e))
                        self._safe_enqueue(payload, cfg)
                time.sleep_ms(self.LOOP_SLEEP_MS)
        except Exception as e:
            print("[BACKGROUND] Thread crashed:", repr(e))
        finally:
            self._thread_alive = False
            self._running = False
            # Anything still queued in RAM belongs on flash, not in the bin.
            while True:
                pending = self._take_pending()
                if pending is None:
                    break
                self._safe_enqueue(pending[0], pending[1])
            print("[BACKGROUND] Thread exited")

    def _safe_enqueue(self, payload, cfg):
        """Last-resort persist of a payload that could not be sent."""
        try:
            self._ensure_client(cfg).enqueue(payload)
            print("[BACKGROUND] payload queued to flash")
        except Exception as e:
            print("[BACKGROUND] queue failed — reading lost:", repr(e))

    def _take_pending(self):
        """Atomically pop the oldest pending payload, or None if there is none."""
        if self._lock is None:
            return None
        self._lock.acquire()
        try:
            if not self._pending:
                return None
            return self._pending.pop(0)
        finally:
            self._lock.release()

    def _set_result(self, **kwargs):
        if self._lock is None:
            return
        self._lock.acquire()
        try:
            self._result.update(kwargs)
        finally:
            self._lock.release()

    def _ensure_client(self, cfg):
        if self._client is not None:
            return self._client
        _gc()
        from src.net.telemetry_client import TelemetryClient
        self._client = TelemetryClient(
            api_base=(cfg.get("api_base") or "").strip(),
            device_id=(cfg.get("device_id") or "").strip(),
            device_key=(cfg.get("device_key") or "").strip(),
        )
        _gc()
        return self._client

    def _start_blink(self, hz=2):
        """Start blinking the button LED during an active send (background thread only)."""
        if self._blink_timer is not None:
            return
        try:
            from machine import Timer, Pin
            from src.hal.board import btn_led_pin
            pin_num = btn_led_pin()
            active = 1
            if pin_num is None:
                try:
                    from src.hal.board import user_led_pin, user_led_active_value
                    pin_num = user_led_pin()
                    active = int(user_led_active_value()) if pin_num is not None else 1
                except Exception:
                    pass
            if pin_num is None:
                return
            self._blink_led = Pin(int(pin_num), Pin.OUT)
            self._blink_active = active
            self._blink_state = [False]
            _led = self._blink_led
            _on = active
            _off = 1 - active

            def _cb(t):
                self._blink_state[0] = not self._blink_state[0]
                try:
                    _led.value(_on if self._blink_state[0] else _off)
                except Exception:
                    pass

            self._blink_timer = Timer(-1)
            self._blink_timer.init(mode=Timer.PERIODIC, period=1000 // hz, callback=_cb)
        except Exception:
            self._blink_timer = None

    def _stop_blink(self):
        """Stop the LED blink timer, turn the LED off, and clear all OLED sync flags."""
        try:
            t = self._blink_timer
            if t is not None:
                t.deinit()
                self._blink_timer = None
        except Exception:
            self._blink_timer = None
        try:
            led = self._blink_led
            if led is not None:
                led.value(1 - self._blink_active)
        except Exception:
            pass
        try:
            from src.ui.connection_header import _api_sending_raw, _api_morse_circle, _api_morse_mode
            _api_sending_raw[0] = 0
            _api_morse_circle[0] = 0
            _api_morse_mode[0] = 0
        except Exception:
            pass

    def _start_morse_blink(self):
        """Start morse blink for BLESS THE AIR — drives LED + OLED API circle in sync."""
        self._morse_state = bytearray(4)  # [idx_lo, idx_hi, remaining, done]
        try:
            from machine import Timer, Pin
            from src.hal.board import btn_led_pin
            pin_num = btn_led_pin()
            active = 1
            if pin_num is None:
                try:
                    from src.hal.board import user_led_pin, user_led_active_value
                    pin_num = user_led_pin()
                    active = int(user_led_active_value()) if pin_num is not None else 1
                except Exception:
                    pass
            if pin_num is None:
                return
            self._blink_led = Pin(int(pin_num), Pin.OUT)
            self._blink_active = active
            _led = self._blink_led
            _on = active
            _off = 1 - active
            _seq = _MORSE_BLESSTHEAIR
            _state = self._morse_state

            try:
                from src.ui.connection_header import _api_morse_circle, _api_morse_mode
                _api_morse_mode[0] = 1
            except Exception:
                _api_morse_circle = bytearray(1)
            _circle = _api_morse_circle

            def _morse_tick(t):
                if _state[3]:
                    return
                remaining = _state[2]
                if remaining > 0:
                    _state[2] = remaining - 1
                    return
                idx = _state[0] | (_state[1] << 8)
                if idx >= len(_seq):
                    _state[3] = 1
                    _led.value(_off)
                    _circle[0] = 0
                    return
                byte = _seq[idx]
                is_on = byte >> 7
                ticks = byte & 0x7F
                _led.value(_on if is_on else _off)
                _circle[0] = is_on
                ni = idx + 1
                _state[0] = ni & 0xFF
                _state[1] = ni >> 8
                _state[2] = ticks - 1

            # Apply first step immediately so LED and OLED start together
            b0 = _seq[0]
            io0 = b0 >> 7
            _led.value(_on if io0 else _off)
            _circle[0] = io0
            _state[0] = 1
            _state[2] = (b0 & 0x7F) - 1

            self._blink_timer = Timer(-1)
            self._blink_timer.init(mode=Timer.PERIODIC, period=_MORSE_UNIT_MS,
                                   callback=_morse_tick)
        except Exception:
            self._blink_timer = None
            self._morse_state = None

    def _wait_morse_done(self, timeout_ms=6000):
        """Block the background thread until morse completes or timeout. GIL is released
        every 25 ms so the main thread continues rendering the OLED at 25 ms."""
        state = self._morse_state
        if state is None:
            return
        t0 = time.ticks_ms()
        while not state[3]:
            if time.ticks_diff(time.ticks_ms(), t0) >= timeout_ms:
                break
            time.sleep_ms(25)

    def _wifi_backoff_ms(self):
        """Current retry interval: base doubled per consecutive failure, capped."""
        streak = self._wifi_fail_streak
        if streak > self.WIFI_FAIL_STREAK_MAX:
            streak = self.WIFI_FAIL_STREAK_MAX
        wait = self.WIFI_RETRY_BASE_MS << streak
        return wait if wait < self.WIFI_RETRY_CAP_MS else self.WIFI_RETRY_CAP_MS

    def _may_attempt_wifi(self, cfg):
        """True when an association attempt is allowed right now."""
        if not cfg or not cfg.get("wifi_enabled", False):
            return False
        if not str(cfg.get("wifi_ssid") or ""):
            return False
        if not self._wifi_check_requested:
            return False

        # A deliberate user action always connects, whatever the mode or backoff.
        if self._wifi_force:
            return True

        # wifi_manual / lora: never bring the radio up on our own. A launched
        # turtle has no AP to find, so an automatic scan is pure wasted TX.
        try:
            mode = str(cfg.get("mission_connection_mode", "wifi_auto") or "wifi_auto")
        except Exception:
            mode = "wifi_auto"
        if mode != "wifi_auto":
            return False

        last = self._last_wifi_attempt_ms
        if last is not None:
            if time.ticks_diff(time.ticks_ms(), last) < self._wifi_backoff_ms():
                return False
        return True

    def _send(self, payload, cfg):
        """Execute WiFi reconnect + HTTP send + queue flush on the background thread."""
        self._set_result(sending=True)
        morse_bless = bool(cfg.get("morse_bless", False)) if cfg else False

        # Signal OLED immediately (< 25 ms) before any timer fires
        try:
            from src.ui.connection_header import _api_sending_raw
            _api_sending_raw[0] = 1
        except Exception:
            pass

        if morse_bless:
            self._start_morse_blink()
        else:
            self._start_blink(hz=2)

        ok = False
        msg = ""
        wifi_ok = True
        try:
            # WiFi association.
            #
            # is_connected() is a cheap status read with no radio activity, so
            # it stays on every send — it is the guard that keeps an offline
            # device from ever reaching DNS/connect.
            #
            # reconnect() is NOT. It runs a full-channel wlan.scan() at full TX
            # power plus a multi-second association attempt. Firing that on
            # every send meant a turtle at sea with wifi_enabled left on paid a
            # scan every auto tick and every manual stamp, with no AP in range
            # to find. It now runs only when a check has been requested (boot,
            # or the waiting / online / wifi screens) and not more often than
            # WIFI_RETRY_FLOOR_MS. Offline sends just queue to flash, which is
            # what they did anyway.
            #
            # reconnect() polls on time.sleep_ms(200), which releases the
            # Python mutex, so the main thread keeps running during it.
            if self._wifi:
                try:
                    wifi_ok = self._wifi.is_connected()
                    if not wifi_ok and self._may_attempt_wifi(cfg):
                        ssid = str(cfg.get("wifi_ssid") or "")
                        pw = str(cfg.get("wifi_password") or "")
                        print("[BACKGROUND] WiFi check requested, reconnecting...")
                        self._last_wifi_attempt_ms = time.ticks_ms()
                        self._wifi_check_requested = False
                        self._wifi_force = False
                        self._wifi.reconnect(ssid, pw, timeout_s=6)
                        wifi_ok = self._wifi.is_connected()
                        if wifi_ok:
                            self._wifi_fail_streak = 0
                        elif self._wifi_fail_streak < self.WIFI_FAIL_STREAK_MAX:
                            self._wifi_fail_streak += 1
                        print("[BACKGROUND] WiFi attempt {} — next retry in {} min".format(
                            "ok" if wifi_ok else "failed",
                            self._wifi_backoff_ms() // 60000,
                        ))
                except Exception as _we:
                    print("[BACKGROUND] WiFi err:", repr(_we))
                    wifi_ok = False

            # A fresh association means new DNS servers — drop any resolve
            # cached against the previous network (including negative entries,
            # so a working network is not shut out by a stale failure).
            if wifi_ok and not self._was_connected:
                try:
                    from src.lib.urequests import invalidate_dns
                    invalidate_dns()
                except Exception:
                    pass
            self._was_connected = bool(wifi_ok)

            # Push wifi status to connection_header cache so the main thread never
            # has to call wlan.isconnected() (which blocks on the WiFi driver mutex
            # while WPA2 auth is in progress on this thread).
            self._set_result(wifi_ok=wifi_ok)
            try:
                from src.ui.connection_header import set_wifi_ok
                set_wifi_ok(wifi_ok)
            except Exception:
                pass

            if not wifi_ok:
                _gc()
                try:
                    client = self._ensure_client(cfg)
                    client.enqueue(payload)
                    msg = "wifi offline (queued)"
                except Exception as _qe:
                    msg = "wifi offline (queue err: " + repr(_qe) + ")"
                return

            # Re-assert PM performance mode before TX (ESP32 power-save workaround).
            if self._wifi is not None:
                try:
                    self._wifi._apply_pm_performance(quiet=True)
                except Exception:
                    pass

            _gc()
            client = self._ensure_client(cfg)

            # HTTP POST + batch queue flush (both run on this background thread).
            # socket.recv() in urequests releases the Python mutex on ESP32, so
            # the main thread continues polling buttons during the HTTP wait.
            ok, msg = client.send(payload)

        except Exception as e:
            ok = False
            msg = "EXC " + repr(e)
        finally:
            # Let morse play to completion before clearing LED/flags
            if morse_bless:
                self._wait_morse_done(timeout_ms=6000)
            self._stop_blink()   # clears _api_sending_raw, _api_morse_*, LED off
            self._set_result(
                sending=False,
                ok=ok,
                msg=msg,
                last_ms=time.ticks_ms(),
            )
            _gc()

            # Update connection header API icon from background thread.
            try:
                from src.ui.connection_header import set_api_ok
                set_api_ok(bool(ok))
            except Exception:
                pass

            if ok:
                print("[BACKGROUND] Send ok:", msg)
            else:
                print("[BACKGROUND] Send failed:", msg)
