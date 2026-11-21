# src/nav/controller.py — autonomy orchestrator, ticked from the main loop
#
# Never owns the loop: tick() is called from _bg_tick() and from the state
# screen, self-rate-limits to cfg["nav_cycle_ms"] (default 300 ms, within
# the 200-500 ms autopilot spec), and returns immediately when not due.
# All servo writes are gated on construction (servo passed only when
# cfg["servo_present"]) and clamped to the physical stop angles.
#
# Resuming a journey (review B3/B4/B9/B10). While a journey is open the
# turtle reaches SAIL_NAV only through three resume stages, run in ACQUIRE
# in priority order:
#   1. BATTERY  wait until battery >= resume_batt_pct (sail feathered once;
#               the servo rail then idles off). No INA219 = pass.
#   2. GPS      wait for a fresh fix. No timeout: a turtle waiting for a fix
#               is never put into SAFE.
#   3. WIND     one luff sweep, skipped if the wind was solved less than
#               luff_resweep_s ago; a sweep that can't run is logged and
#               skipped (steering doesn't use the wind angle yet).
# then SAIL_NAV and steering. The stages start by themselves whenever the
# turtle is in ACQUIRE with a journey open: after a reboot or crash, on a
# journey start, when the battery drops below low_batt_pct while sailing,
# and when SAFE clears. A battery drop during stages 2-3 restarts stage 1.

import time

from src.nav import state_machine as sm
from src.nav import gpsfix
from src.app import monotonic   # "since" stamps that can outlive 6.2 days (B5)

# Resume stages (snapshot()["resume"], BLE 0111 resume_stage 1-3).
RESUME_BATTERY = "BATTERY"
RESUME_GPS = "GPS"
RESUME_WIND = "WIND"

_FRESH_FIX_MS = 5000        # a fix this recent counts as "have GPS"
_SAFE_HOLDOFF_MS = 30000    # SAFE lasts at least this long before it can clear
_STAGE_LOG_MS = 60000       # how often a waiting stage prints its progress


def _journey_open():
    try:
        from src.app import journey
        return bool(journey.active())
    except Exception:
        return False


class NavController:
    def __init__(self, cfg, i2c=None, gps=None, servo=None, battery=None, imu=None):
        self._gps = gps
        self._servo = servo
        self._ina = battery
        self._cycle_ms = int(cfg.get("nav_cycle_ms", 300))
        self._sail_min = int(cfg.get("sail_min_deg", 10))
        self._sail_max = int(cfg.get("sail_max_deg", 170))
        self._next_ms = 0
        self._next_route_ms = 0
        self._last_nav_ms = None
        # The three "since" stamps below are monotonic.ms(), not ticks_ms():
        # each can be days old (SAFE through a long GPS outage, wind solved
        # before a week waiting for sun) and a ticks_diff past ~6.2 days
        # wraps negative (review B5). Compare them by plain subtraction.
        self._sailnav_since_ms = None
        self._next_sweep_ms = None     # periodic re-sweep schedule (SAIL_NAV)
        self._feather_applied = False
        self._servo_us = None          # last pulse written to the servo (us)
        self._manual_until = None      # ticks_ms: hand control ends (SERVO_SET_ANGLE)
        # autopilot_sail false: the autopilot never moves the servo (no
        # steering, feathering or luff sweeps); only hand control does.
        self._autopilot = bool(cfg.get("autopilot_sail", True))
        # The servo's full travel, just inside its end stops (per servo
        # model; config.py). Hand control spans exactly this range.
        self._min_us = int(cfg.get("servo_min_us", 500))
        self._max_us = int(cfg.get("servo_max_us", 2500))
        # Hand control glides between the dial's angles at this rate
        # (us/s; 0 = jump straight there). config.py.
        self._glide_us_s = int(cfg.get("servo_glide_us_s", 4000))
        self.fault = None
        self._stage = None             # RESUME_* while resuming (sm._resume)
        self._stage_log_ms = None      # next "still waiting" print
        self._safe_since_ms = None     # when SAFE was entered
        self._wind_solved_ms = None    # when a sweep last produced a wind angle
        self._last_state = sm.get_state()

        # imu: the shared GY87 built at boot (src/drivers/gy87.py). Its mag is
        # injected so HeadingSource never re-probes; with no GY-87 it falls
        # back to a standalone compass on the bus.
        from src.nav.heading import HeadingSource
        self._heading = HeadingSource(
            i2c=i2c,
            mag=(getattr(imu, "mag", None) if imu is not None else None),
            imu=imu,
            offset_deg=cfg.get("compass_offset_deg", 0))

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
                      "fault": None, "sweeping": False, "resume": None}

    # The resume stage lives in the state machine module so telemetry can
    # read it without a NavController handle.
    @property
    def _stage(self):
        return sm._resume[0]

    @_stage.setter
    def _stage(self, v):
        sm._resume[0] = v

    # ------------------------------------------------------------------
    # Public API for screens / main loop
    # ------------------------------------------------------------------

    def begin_luff_sweep(self):
        """Start a sweep (ACQUIRE first-fix, or manual re-sweep in SAIL_NAV).
        Refused while a journey resume waits for battery or GPS: those come
        first."""
        if not self._autopilot or sm.get_state() not in (sm.ACQUIRE, sm.SAIL_NAV):
            return False
        if self._stage in (RESUME_BATTERY, RESUME_GPS):
            return False
        return self._sweep.start()

    def resume_stage(self):
        """RESUME_BATTERY / RESUME_GPS / RESUME_WIND while a journey is
        resuming in ACQUIRE, else None."""
        return self._stage

    def sweeping(self):
        return self._sweep.active()

    # Hand control of the sail (Turtle Wrangler's servo dial). 0..180 maps
    # linearly onto servo_min_us..servo_max_us — one end stop to the other.
    # Each move pauses autopilot servo writes for hold_ms; then steering
    # resumes.

    def servo_present(self):
        return self._servo is not None

    def autopilot_sail(self):
        """False when config autopilot_sail is off: hand control only."""
        return self._autopilot

    def manual_sail(self, deg, hold_ms):
        """Move the servo by hand to deg (0..180 of its full travel),
        gliding there at servo_glide_us_s. Returns the target pulse (us), or
        None: no servo, a sweep is
        running (the caller checks sweeping() to report BUSY), or the write
        failed."""
        if self._servo is None or self._sweep.active():
            return None
        d = max(0, min(180, int(deg)))
        us = self._min_us + (self._max_us - self._min_us) * d // 180
        try:
            glide = getattr(self._servo, "glide_to", None)
            if glide is not None:
                glide(us, self._glide_us_s)
            else:
                self._servo.write_pulse(us)
        except Exception as e:
            print("[NAV] manual sail write failed:", repr(e))
            return None
        self._servo_us = us
        self._manual_until = time.ticks_add(time.ticks_ms(), int(hold_ms))
        self._feather_applied = False      # re-feather after the hold (ARRIVAL/SAFE)
        self._pid.reset()                  # no integral wind-up from the paused loop
        return us

    def manual_hold_s(self, now_ms=None):
        """Seconds of hand control left, or 0."""
        if self._manual_until is None:
            return 0
        now = time.ticks_ms() if now_ms is None else now_ms
        left = time.ticks_diff(self._manual_until, now)
        if left <= 0:
            self._manual_until = None
            return 0
        return (left + 999) // 1000

    def servo_position(self):
        """Last commanded servo position on the dial scale (0..180 of the
        full travel, float), whoever wrote it; None if never written."""
        if self._servo_us is None or self._max_us <= self._min_us:
            return None
        f = (self._servo_us - self._min_us) * 180.0 / (self._max_us - self._min_us)
        return max(0.0, min(180.0, f))

    def sweep_status(self):
        return self._sweep_status

    def wind_angle(self):
        return self._sweep.wind_angle()

    def heading_deg(self):
        """Light accessor for overlays (no snapshot cost)."""
        return self._heading.heading_deg()

    def heading_source(self):
        """The shared HeadingSource — screens and telemetry read through it
        so offset handling lives in exactly one place."""
        return self._heading

    def target(self):
        """Active waypoint (lat, lon), or None."""
        return self._wps.current()

    def route_source(self):
        """Which config key the route came from (waypoints.SRC_*)."""
        return self._wps.source()

    def battery_pct(self):
        """Battery percent from the INA219 battery_v(), or None. The
        estimator itself is ina219.pct_from_volts (shared with telemetry and
        Bluetooth)."""
        return self._battery_pct()

    def sail_encoder_present(self):
        return self._enc is not None

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
        s["resume"] = self._stage
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

        # Pick up a target changed at runtime (Destination / Journey / a
        # Bluetooth command). Cheap, but no need to do it every 50 ms.
        if isinstance(cfg, dict) and time.ticks_diff(now, self._next_route_ms) >= 0:
            self._next_route_ms = time.ticks_add(now, 1000)
            try:
                self._wps.sync(cfg)
            except Exception as e:
                print("[NAV] route sync failed:", repr(e))

        # Hand control: no autopilot servo writes (steering, feather, scheduled
        # re-sweep) until the hold runs out. A sweep can't be active here —
        # manual_sail() refuses during one.
        if self.manual_hold_s(now) > 0:
            self._last_nav_ms = None
            return

        # Autopilot sail control switched off (config autopilot_sail): GPS and
        # route keep updating above, but nothing here touches the servo.
        if isinstance(cfg, dict):
            self._autopilot = bool(cfg.get("autopilot_sail", True))
        if not self._autopilot and not self._sweep.active():   # a running sweep finishes
            self._last_nav_ms = None
            return

        state = sm.get_state()
        if state != self._last_state:
            self._state_changed(state, now)
        if state == sm.ACQUIRE:
            if self._stage is None and _journey_open():
                self._abort_sweep("journey resume")   # battery and GPS come first
                self._set_stage(RESUME_BATTERY, now, "journey open")
            if self._stage is not None:
                self._resume_cycle(cfg, now)
            elif self._sweep.active():
                # A sweep started by hand (State screen / Wrangler), no journey.
                self._step_sweep(now)
                if self._sweep_status["done"]:
                    self._enter_sail_nav(cfg, now, "wind angle solved")
        elif state == sm.SAIL_NAV:
            if self._sweep.active():
                # re-sweep (manual or scheduled) pauses steering while it runs
                self._step_sweep(now)
                if self._sweep_status["phase"] in ("done", "failed"):
                    self._schedule_resweep(cfg, now)
            else:
                self._sail_nav_cycle(cfg, now)
        elif state == sm.ARRIVAL:
            self._feather()
        elif state == sm.SAFE:
            self._feather()
            self._safe_cycle(now)

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
                elif "GGA" in line:
                    gpsfix.note_gga(line)
        except Exception:
            pass

    def _state_changed(self, state, now):
        """Bookkeeping when the state moved, by this controller or by
        anyone else (a journey start or end in actions.py)."""
        self._last_state = state
        if state != sm.ACQUIRE and self._stage is not None:
            self._stage = None
        if state == sm.SAFE:
            if self._safe_since_ms is None:
                self._safe_since_ms = monotonic.ms()
        else:
            self._safe_since_ms = None
            if state != sm.SAIL_NAV:
                self.fault = None

    def _set_stage(self, stage, now, why):
        self._stage = stage
        self._stage_log_ms = time.ticks_add(now, _STAGE_LOG_MS)
        print("[NAV] resume: {} ({})".format(stage, why))

    def _stage_log_due(self, now):
        if self._stage_log_ms is None or time.ticks_diff(now, self._stage_log_ms) >= 0:
            self._stage_log_ms = time.ticks_add(now, _STAGE_LOG_MS)
            return True
        return False

    def _step_sweep(self, now):
        self._sweep_status = self._sweep.step(now)
        if self._sweep_status.get("done") and self._sweep.wind_angle() is not None:
            self._wind_solved_ms = monotonic.ms()

    def _abort_sweep(self, why):
        if self._sweep.active():
            self._sweep.abort(why)

    def _enter_sail_nav(self, cfg, now, reason):
        """The one way into SAIL_NAV: fresh PID, GPS-loss timer starting
        now, periodic re-sweep armed."""
        if not sm.set_state(sm.SAIL_NAV, reason):
            return False
        self._last_state = sm.SAIL_NAV
        self._stage = None
        self._pid.reset()
        self._last_nav_ms = None
        self._sailnav_since_ms = monotonic.ms()
        self._feather_applied = False
        self.fault = None
        self._schedule_resweep(cfg, now)
        return True

    def _resume_cycle(self, cfg, now):
        """One step of the journey resume: battery, then GPS, then wind."""
        if not _journey_open():
            print("[NAV] resume cancelled: no journey open")
            self._abort_sweep("journey closed")
            self._stage = None
            return

        pct = self._battery_pct()
        low = int(cfg.get("low_batt_pct", 20))
        need = max(low, int(cfg.get("resume_batt_pct", 40)))

        if self._stage != RESUME_BATTERY and pct is not None and pct < low:
            self._abort_sweep("battery low")
            self._set_stage(RESUME_BATTERY, now, "battery {}% < {}%".format(pct, low))

        if self._stage == RESUME_BATTERY:
            self._snap["trim"] = "FEATHER"
            self._feather()
            if pct is not None and pct < need:
                if self._stage_log_due(now):
                    print("[NAV] resume: waiting for battery {}% (need {}%)".format(pct, need))
                return
            self._snap["trim"] = "CRUISE"
            self._set_stage(RESUME_GPS, now,
                            "battery unknown" if pct is None else "battery {}%".format(pct))

        if self._stage == RESUME_GPS:
            _lat, _lon, age = gpsfix.get()
            if age is None or age > _FRESH_FIX_MS:
                if self._stage_log_due(now):
                    print("[NAV] resume: waiting for a GPS fix")
                return
            if self._wps.current() is None or self._wps.is_final_reached():
                # Nothing to sail to (SAIL_NAV goes to SAFE) or already
                # there (ARRIVAL): no point spending a sweep on it.
                self._enter_sail_nav(cfg, now, "journey resumed, no target to sail")
                return
            interval_ms = int(cfg.get("luff_resweep_s", 600)) * 1000
            if self._wind_solved_ms is not None and \
                    monotonic.ms() - self._wind_solved_ms < interval_ms:
                self._enter_sail_nav(cfg, now, "journey resumed, wind known")
                return
            if not self._sweep.start():
                print("[NAV] resume: wind sweep unavailable ({})".format(self._sweep.error))
                self._enter_sail_nav(cfg, now, "journey resumed, no wind sweep")
                return
            self._set_stage(RESUME_WIND, now, "GPS fix")
            return

        if self._stage == RESUME_WIND:
            if self._sweep.active():
                self._step_sweep(now)
                if self._sweep_status["phase"] not in ("done", "failed"):
                    return
                if self._sweep_status["phase"] == "failed":
                    print("[NAV] resume: wind sweep failed ({})".format(self._sweep.error))
            self._enter_sail_nav(cfg, now, "journey resumed")

    def _safe_cycle(self, now):
        """SAFE clears by itself once its cause has gone: after the hold-off,
        a fresh fix and a target to sail to put the turtle back in ACQUIRE.
        With a journey open the resume stages then take it from there."""
        if self._safe_since_ms is None:
            self._safe_since_ms = monotonic.ms()
        if monotonic.ms() - self._safe_since_ms < _SAFE_HOLDOFF_MS:
            return
        _lat, _lon, age = gpsfix.get()
        if age is None or age > _FRESH_FIX_MS or self._wps.current() is None:
            return
        if sm.set_state(sm.ACQUIRE, "SAFE cleared ({} gone)".format(self.fault)):
            self._state_changed(sm.ACQUIRE, now)

    def _schedule_resweep(self, cfg, now):
        """Arm the periodic wind re-calibration (PDF: ~every 10 minutes)."""
        interval_s = int(cfg.get("luff_resweep_s", 600))
        self._next_sweep_ms = time.ticks_add(now, interval_s * 1000)

    def _sail_nav_cycle(self, cfg, now):
        # Battery first: below low_batt_pct, feather and drop to ACQUIRE.
        # With a journey open the resume stages bring it back once the
        # battery reaches resume_batt_pct (no chatter around one threshold).
        pct = self._battery_pct()
        if pct is not None and pct < int(cfg.get("low_batt_pct", 20)):
            self._snap["trim"] = "FEATHER"
            self._feather()
            sm.set_state(sm.ACQUIRE, "battery {}%".format(pct))
            self._state_changed(sm.ACQUIRE, now)
            return
        self._snap["trim"] = "CRUISE"

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
                self._sailnav_since_ms = monotonic.ms()
            if monotonic.ms() - self._sailnav_since_ms >= loss_ms:
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
        # calibration is a navigation_roadmap.md task.
        self._set_sail(90.0 + out)

    def _battery_pct(self):
        if self._ina is None or not getattr(self._ina, "is_present", False):
            return None
        try:
            from src.drivers.ina219 import pct_from_volts
            return pct_from_volts(self._ina.battery_v())
        except Exception:
            return None

    def _set_sail(self, deg):
        if self._servo is None:
            return
        try:
            d = int(max(self._sail_min, min(self._sail_max, deg)))
            self._servo.angle(d)
            self._servo_us = self._servo.pulse_us(d)
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
                self._servo_us = self._servo.pulse_us(90)
            except Exception:
                pass

    def _enter_safe(self, reason):
        self.fault = reason
        sm.set_state(sm.SAFE, reason)
        self._last_state = sm.SAFE
        self._safe_since_ms = None     # _safe_cycle stamps it on its clock
        self._feather_applied = False
        self._feather()
