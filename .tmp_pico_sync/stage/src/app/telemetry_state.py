# src/app/telemetry_state.py — Telemetry state wrapper for AirBuddy (Pico / MicroPython safe)
#
# PATCH (LOW-RAM):
# - DO NOT import TelemetryScheduler at module import time (can trigger ENOMEM).
# - Lazy-create scheduler only when needed (tick / UI helpers).
# - Make TelemetryState safe to exist even if scheduler can't be created.

def _gc_collect():
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def _time():
    # Lazy import time to reduce boot import pressure
    try:
        import time as _t
        return _t
    except Exception:
        return None


def _load_scheduler_class():
    """
    Lazy import to avoid MemoryError during boot.
    Returns TelemetryScheduler class or None.
    """
    try:
        _gc_collect()
        from src.app.telemetry_scheduler import TelemetryScheduler
        _gc_collect()
        return TelemetryScheduler
    except MemoryError:
        print("[TELEMETRY] scheduler import OOM — telemetry disabled")
        _gc_collect()
        return None
    except Exception as _e:
        print("[TELEMETRY] scheduler import error:", repr(_e))
        _gc_collect()
        return None


class TelemetryState:
    """
    Owns a TelemetryScheduler instance, but creates it lazily to avoid
    MemoryError at import-time.
    """

    def __init__(self, air_sensor, rtc_info_getter, wifi_manager, gps=None, battery_sensor=None):
        self.air_sensor = air_sensor
        self.rtc_info_getter = rtc_info_getter
        self.wifi_manager = wifi_manager
        self.gps = gps
        self.battery_sensor = battery_sensor

        self.scheduler = None  # created lazily

    # ------------------------------------------------------------
    # Internal: create scheduler only when needed
    # ------------------------------------------------------------
    def _ensure_scheduler(self):
        if self.scheduler is not None:
            return True

        TelemetryScheduler = _load_scheduler_class()
        if TelemetryScheduler is None:
            return False

        try:
            self.scheduler = TelemetryScheduler(
                air_sensor=self.air_sensor,
                rtc_info_getter=self.rtc_info_getter,
                wifi_manager=self.wifi_manager,
                gps=self.gps,
                battery_sensor=self.battery_sensor,
            )
            print("[TELEMETRY] scheduler ready")
            return True
        except MemoryError:
            print("[TELEMETRY] scheduler init OOM")
            self.scheduler = None
            _gc_collect()
            return False
        except Exception as _e:
            print("[TELEMETRY] scheduler init error:", repr(_e))
            self.scheduler = None
            _gc_collect()
            return False

    # ------------------------------------------------------------
    # Main loop integration
    # ------------------------------------------------------------
    def tick(self, cfg, rtc_dict=None):
        """
        Background telemetry attempt (only when due + enabled).
        MemoryError-safe: if scheduler can't be created, telemetry just won't run.
        """
        try:
            if not cfg or not cfg.get("telemetry_enabled", True):
                return
        except Exception:
            return

        if not self._ensure_scheduler():
            return

        try:
            return self.scheduler.tick(cfg, rtc_dict=rtc_dict)
        except MemoryError:
            print("[TELEMETRY] tick OOM")
            _gc_collect()
        except Exception as _e:
            print("[TELEMETRY] tick error:", repr(_e))

    # ------------------------------------------------------------
    # Online screen interface — no HTTP, scheduler state only
    # ------------------------------------------------------------
    @property
    def api_state(self):
        if self.scheduler is not None:
            return self.scheduler.api_state
        return {"ok": None, "sending": False, "msg": "", "last_ms": None}

    def request_now(self):
        """Trigger an immediate telemetry send at the next background tick."""
        if self._ensure_scheduler():
            self.scheduler.request_now()
        else:
            print("[ONLINE] request_now: scheduler not ready (will retry on next tick)")

    # ------------------------------------------------------------
    # Helpers for UI
    # ------------------------------------------------------------
    @staticmethod
    def get_queue_size():
        """
        Returns current queue size (int).
        Safe even if TelemetryScheduler can't be imported.
        """
        TelemetryScheduler = _load_scheduler_class()
        if TelemetryScheduler is None:
            return 0
        try:
            return TelemetryScheduler.queue_size()
        except Exception:
            return 0

    @staticmethod
    def _fmt_ts(ts):
        """
        Convert unix seconds -> "MM/DD-HH:MM"
        """
        if ts is None:
            return "---"

        tmod = _time()
        if tmod is None:
            try:
                return str(int(ts))
            except Exception:
                return "---"

        try:
            t = tmod.localtime(int(ts))
            mo = t[1]
            dd = t[2]
            hh = t[3]
            mm = t[4]
            return "{:02d}/{:02d}-{:02d}:{:02d}".format(mo, dd, hh, mm)
        except Exception:
            try:
                return str(int(ts))
            except Exception:
                return "---"

    @staticmethod
    def get_last_sent():
        """
        Returns a dict suitable for UI:
          {"ts": <int|None>, "ok": <bool|None>, "text": "MM/DD-HH:MM" | "---"}
        Safe even if TelemetryScheduler can't be imported.
        """
        TelemetryScheduler = _load_scheduler_class()
        if TelemetryScheduler is None:
            return {"ts": None, "ok": None, "text": "---"}

        last = None
        try:
            last = TelemetryScheduler.read_last_sent()
        except Exception:
            last = None

        ts = None
        ok = None

        if isinstance(last, dict):
            ts = last.get("ts")
            ok = last.get("ok")
        elif last is None:
            ts = None
            ok = None
        else:
            try:
                ts = int(last)
            except Exception:
                ts = None
            ok = None

        return {"ts": ts, "ok": ok, "text": TelemetryState._fmt_ts(ts)}
