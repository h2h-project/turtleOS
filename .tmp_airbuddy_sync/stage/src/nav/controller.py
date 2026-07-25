# src/nav/controller.py — autonomy orchestrator, ticked from the main loop
#
# Never owns the loop: tick() is called from _bg_tick() and from the state
# screen, self-rate-limits to cfg["nav_cycle_ms"] (default 300 ms, within
# the 200-500 ms autopilot spec), and returns immediately when not due.
# All servo writes are gated on construction (servo passed only when
# cfg["servo_present"]) and clamped to the physical stop angles.

import time

from src.nav import state_machine as sm
from src.nav import gpsfix


class NavController:
    def __init__(self, cfg, i2c=None, gps=None, servo=None, battery=None):
        self._gps = gps
        self._servo = servo
        self._ina = battery
        self._cycle_ms = int(cfg.get("nav_cycle_ms", 300))
        self._sail_min = int(cfg.get("sail_min_deg", 10))
        self._sail_max = int(cfg.get("sail_max_deg", 170))
        self._next_ms = 0
        self._last_nav_ms = None
        self._sailnav_since_ms = None
        self._next_sweep_ms = None     # periodic re-sweep schedule (SAIL_NAV)
        self._feather_applied = False
        self.fault = None

        from src.nav.heading import HeadingSource
        self._heading = HeadingSource(
            i2c=i2c, offset_deg=cfg.get("compass_offset_deg", 0))

        self._enc = None
        if i2c is not None:
            try:
                from src.drivers.as5600 import AS5600
                enc = AS5600(i2c)
                if enc.is_present:
                    self._enc = enc
            except Exception:
                pass

        from src.nav.waypoints import WaypointSequencer
        self._wps = WaypointSequencer(cfg)

        from src.nav.pid import PID
        self._pid = PID()

        from src.nav.luff import LuffSweep
        self._sweep = LuffSweep(
            self._servo, self._enc,
            speed_dps=cfg.get("luff_sweep_dps", 8),
            threshold_mult=cfg.get("luff_threshold_mult", 5.0),
            sail_min=self._sail_min, sail_max=self._sail_max)
        self._sweep_status = None

        # reused snapshot dict — no per-tick allocation
        self._snap = {"state": sm.get_state(), "heading": None, "sail": None,
                      "wind": None, "wp_dist_m": None, "trim": "CRUISE",
                      "fault": None, "sweeping": False}

    # ------------------------------------------------------------------
    # Public API for screens / main loop
    # ------------------------------------------------------------------

    def begin_luff_sweep(self):
        """Start a sweep (ACQUIRE first-fix, or manual re-sweep in SAIL_NAV)."""
        if sm.get_state() not in (sm.ACQUIRE, sm.SAIL_NAV):
            return False
        return self._sweep.start()

    def sweeping(self):
        return self._sweep.active()

    def sweep_status(self):
        return self._sweep_status

    def wind_angle(self):
        return self._sweep.wind_angle()

    def heading_deg(self):
        """Light accessor for overlays (no snapshot cost)."""
        return self._heading.heading_deg()

    def seconds_to_next_sweep(self):
        """Seconds until the next scheduled luff re-sweep, or None when not
        in SAIL_NAV / nothing scheduled. 0 while a sweep is running."""
        if sm.get_state() != sm.SAIL_NAV:
            return None
        if self._sweep.active():
            return 0
        if self._next_sweep_ms is None:
            return None
        remaining = time.ticks_diff(self._next_sweep_ms, time.ticks_ms())
        return max(0, remaining // 1000)

    def snapshot(self):
        s = self._snap
        s["state"] = sm.get_state()
        s["heading"] = self._heading.heading_deg()
        s["sail"] = None
        if self._enc is not None:
            try:
                s["sail"] = self._enc.angle()
            except Exception:
                pass
        s["wind"] = self._sweep.wind_angle()
        s["fault"] = self.fault
        s["sweeping"] = self._sweep.active()
        lat, lon, _age = gpsfix.get()
        wp = self._wps.current()
        s["wp_dist_m"] = None
        if wp is not None and lat is not None:
            from src.nav.bearing import distance_m
            s["wp_dist_m"] = distance_m(lat, lon, wp[0], wp[1])
        return s

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, cfg, now_ms=None):
        now = time.ticks_ms() if now_ms is None else now_ms
        if time.ticks_diff(now, self._next_ms) < 0:
            return
        # An active sweep needs its own 50 ms cadence (5-10 deg/s with a
        # filled variance window); normal autopilot cycles at nav_cycle_ms.
        if self._sweep.active():
            self._next_ms = time.ticks_add(now, 50)
        else:
            self._next_ms = time.ticks_add(now, self._cycle_ms)

        self._drain_gps()

        state = sm.get_state()
        if state == sm.ACQUIRE:
            if self._sweep.active():
                self._sweep_status = self._sweep.step(now)
                if self._sweep_status["done"]:
                    sm.set_state(sm.SAIL_NAV, "wind angle solved")
                    self._pid.reset()
                    self._sailnav_since_ms = now
                    self._schedule_resweep(cfg, now)
        elif state == sm.SAIL_NAV:
            if self._sweep.active():
                # re-sweep (manual or scheduled) pauses steering while it runs
                self._sweep_status = self._sweep.step(now)
                if self._sweep_status["phase"] in ("done", "failed"):
                    self._schedule_resweep(cfg, now)
            else:
                self._sail_nav_cycle(cfg, now)
        elif state in (sm.ARRIVAL, sm.SAFE):
            self._feather()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _drain_gps(self):
        if self._gps is None:
            return
        try:
            for _ in range(20):
                line = self._gps.read_nmea()
                if line is None:
                    break
                if "RMC" in line:
                    lat, lon, cog = gpsfix.parse_rmc(line)
                    if lat is not None:
                        gpsfix.update(lat, lon, cog)
        except Exception:
            pass

    def _schedule_resweep(self, cfg, now):
        """Arm the periodic wind re-calibration (PDF: ~every 10 minutes)."""
        interval_s = int(cfg.get("luff_resweep_s", 600))
        self._next_sweep_ms = time.ticks_add(now, interval_s * 1000)

    def _sail_nav_cycle(self, cfg, now):
        # Scheduled wind re-calibration — runs before steering; on refusal
        # (e.g. no magnet) just re-arm rather than hammering every cycle.
        if self._next_sweep_ms is not None and \
                time.ticks_diff(now, self._next_sweep_ms) >= 0:
            if not self._sweep.start():
                self._schedule_resweep(cfg, now)
            return

        # Fault: extended GPS loss → SAFE (PDF reliability spec)
        lat, lon, age_ms = gpsfix.get()
        loss_ms = int(cfg.get("gps_loss_safe_s", 120)) * 1000
        if age_ms is None:
            if self._sailnav_since_ms is None:
                self._sailnav_since_ms = now
            if time.ticks_diff(now, self._sailnav_since_ms) >= loss_ms:
                self._enter_safe("gps never acquired")
            return
        if age_ms >= loss_ms:
            self._enter_safe("gps loss")
            return

        # Waypoint sequencing — forward only; final → ARRIVAL
        radius = cfg.get("arrival_radius_m", 300)
        if self._wps.advance_if_arrived(lat, lon, radius_m=radius):
            print("[NAV] waypoint reached, advancing to", self._wps.index())
        if self._wps.is_final_reached():
            sm.set_state(sm.ARRIVAL, "final waypoint reached")
            self._feather()
            return
        wp = self._wps.current()
        if wp is None:
            self._enter_safe("no waypoints configured")
            return

        # Battery failsafe: feather below threshold, resume on recharge
        pct = self._battery_pct()
        if pct is not None and pct < int(cfg.get("low_batt_pct", 20)):
            self._snap["trim"] = "FEATHER"
            self._feather()
            return
        self._snap["trim"] = "CRUISE"
        self._feather_applied = False

        heading = self._heading.heading_deg()
        if heading is None:
            return

        # PID heading control: positive error = boat left of course
        from src.nav.bearing import initial_bearing, norm180
        desired = initial_bearing(lat, lon, wp[0], wp[1])
        error = norm180(desired - heading)
        dt_s = self._cycle_ms / 1000.0
        if self._last_nav_ms is not None:
            dt_s = max(0.001, time.ticks_diff(now, self._last_nav_ms) / 1000.0)
        self._last_nav_ms = now
        out = self._pid.update(error, dt_s)

        # Placeholder actuation around servo neutral; encoder↔servo trim
        # calibration is a pending_nav_dev.md task.
        self._set_sail(90.0 + out)

    def _battery_pct(self):
        if self._ina is None or not getattr(self._ina, "is_present", False):
            return None
        try:
            v = self._ina.bus_voltage_v()
            if v is None:
                return None
            return max(0, min(100, int((float(v) - 3.30) / (4.20 - 3.30) * 100)))
        except Exception:
            return None

    def _set_sail(self, deg):
        if self._servo is None:
            return
        try:
            self._servo.angle(int(max(self._sail_min, min(self._sail_max, deg))))
        except Exception:
            pass

    def _feather(self):
        # MG996R holds position; center approximates zero angle of attack
        # until encoder↔wind trim mapping lands. Apply once per entry.
        if self._feather_applied:
            return
        self._feather_applied = True
        if self._servo is not None:
            try:
                self._servo.center()
            except Exception:
                pass

    def _enter_safe(self, reason):
        self.fault = reason
        sm.set_state(sm.SAFE, reason)
        self._feather_applied = False
        self._feather()
