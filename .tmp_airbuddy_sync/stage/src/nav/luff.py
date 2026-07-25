# src/nav/luff.py — luff-sweep wind angle detection (no windvane)
#
# As the sail crosses the wind direction its load transitions from steady
# fill to flutter, read by the AS5600 as high-frequency jitter. Luffing is
# symmetrical about the wind, so midpoint(onset A, onset B) is the apparent
# wind angle relative to the boat (in AS5600 encoder degrees).
#
# The sweep is stepwise and non-blocking: step() moves the servo a couple
# of degrees at most and samples once. The caller (NavController.tick)
# drives it, so button polling and telemetry never stall.
#
# Phases: idle → position → sweep_a → sweep_b → done | failed
#   position : drive sail to sail_min stop
#   sweep_a  : sweep min→max; the first stretch calibrates the calm-motion
#              jitter baseline (servo/encoder quantization staircases the
#              deltas, so baseline MUST be measured while moving, not at a
#              standstill); threshold = mult x peak calm variance; then the
#              first variance spike = luff onset A
#   sweep_b  : sweep max→min, re-armed after a calm window, first spike =
#              onset B
#
# Known limitation: if the wind zone sits right at sail_min the calibration
# stretch overlaps the flutter and inflates the threshold — see
# docs/pending_nav_dev.md (sweep failure handling).

import time
from array import array


class RollingVariance:
    """Variance over the last n pushed values (preallocated, no per-call alloc)."""

    def __init__(self, n=6):
        self._n = max(2, int(n))
        self._buf = array("f", [0.0] * self._n)
        self._count = 0
        self._idx = 0

    def add(self, v):
        self._buf[self._idx] = float(v)
        self._idx = (self._idx + 1) % self._n
        if self._count < self._n:
            self._count += 1

    def full(self):
        return self._count >= self._n

    def value(self):
        """Variance of the window, or None until full."""
        if self._count < self._n:
            return None
        mean = 0.0
        for i in range(self._n):
            mean += self._buf[i]
        mean /= self._n
        var = 0.0
        for i in range(self._n):
            d = self._buf[i] - mean
            var += d * d
        return var / self._n

    def reset(self):
        self._count = 0
        self._idx = 0


def _delta180(a, b):
    """Shortest signed angular difference a-b in degrees."""
    d = (float(a) - float(b)) % 360.0
    if d > 180.0:
        d -= 360.0
    return d


class LuffSweep:
    _VAR_FLOOR = 0.05      # deg^2 — minimum usable threshold
    _ARM_CALM_MS = 600     # sweep_b must see calm this long before re-arming
    _CAL_MS = 1200         # moving-baseline calibration stretch in sweep_a

    def __init__(self, servo, encoder, speed_dps=8.0, threshold_mult=5.0,
                 sail_min=10, sail_max=170, step_ms=50, window_n=6):
        self._servo = servo            # may be None (servo_present=false)
        self._enc = encoder            # AS5600 or None
        self._speed_dps = max(2.0, float(speed_dps))
        self._mult = float(threshold_mult)
        self._min = int(sail_min)
        self._max = int(sail_max)
        self._step_ms = max(20, int(step_ms))
        self._var = RollingVariance(window_n)

        self._phase = "idle"
        self._cmd = None               # current commanded servo angle
        self._next_ms = 0
        self._phase_start_ms = 0
        self._calm_since_ms = None
        self._armed = False
        self._threshold = None
        self._cal_peak = 0.0
        self._cal_n = 0
        self._prev_enc = None
        self.angle_a = None            # encoder deg at luff onset A
        self.angle_b = None
        self._wind = None
        self.error = None

    # ------------------------------------------------------------------

    _MIN_ZONE_DEG = 4.0    # |A-B| below this is a double-trigger, not a luff zone

    def start(self, now_ms=None):
        if self._enc is None or not getattr(self._enc, "is_present", False):
            self._phase = "failed"
            self.error = "no encoder"
            return False
        # A missing/weak magnet leaves the AS5600 free-floating: mostly 0.0
        # with sporadic noise jumps that false-trigger both luff onsets
        # (observed on hardware). Refuse to sweep without a detected magnet.
        try:
            if not self._enc.status().get("md", False):
                self._phase = "failed"
                self.error = "no magnet"
                return False
        except Exception:
            pass
        self._phase = "position"
        self._cmd = float(self._max + self._min) / 2.0 if self._cmd is None else self._cmd
        # Timing stamps come from the first step() call so the sweep always
        # runs on the caller's clock (start carries no clock of its own).
        self._next_ms = None
        self._phase_start_ms = None
        self._var.reset()
        self._prev_enc = None
        self._calm_since_ms = None
        self._armed = False
        self._threshold = None
        self._cal_peak = 0.0
        self._cal_n = 0
        self.angle_a = None
        self.angle_b = None
        self._wind = None
        self.error = None
        return True

    def active(self):
        return self._phase in ("position", "baseline", "sweep_a", "sweep_b")

    def wind_angle(self):
        return self._wind

    # ------------------------------------------------------------------

    def _drive(self, deg):
        self._cmd = max(self._min, min(self._max, float(deg)))
        if self._servo is not None:
            try:
                self._servo.angle(int(self._cmd))
            except Exception:
                pass

    def _sample(self):
        """Push the latest encoder jitter delta; return variance (or None)."""
        try:
            a = self._enc.angle()
        except Exception:
            a = None
        if a is None:
            return None
        if self._prev_enc is not None:
            self._var.add(_delta180(a, self._prev_enc))
        self._prev_enc = a
        return self._var.value()

    def _fail(self, msg):
        self._phase = "failed"
        self.error = msg
        if self._servo is not None:
            try:
                self._servo.center()
            except Exception:
                pass

    def step(self, now_ms=None):
        """Advance the sweep by at most one servo step. Returns status dict."""
        now = time.ticks_ms() if now_ms is None else now_ms
        if self._next_ms is None:
            self._next_ms = now
        if self._phase_start_ms is None:
            self._phase_start_ms = now
        if self.active() and time.ticks_diff(now, self._next_ms) >= 0:
            self._next_ms = time.ticks_add(now, self._step_ms)
            step_deg = self._speed_dps * self._step_ms / 1000.0
            var = self._sample()
            elapsed = time.ticks_diff(now, self._phase_start_ms)

            if self._phase == "position":
                # 2x speed to the start stop; no detection yet
                if self._cmd <= self._min:
                    self._phase = "sweep_a"
                    self._phase_start_ms = now
                    self._var.reset()
                    self._prev_enc = None
                else:
                    self._drive(self._cmd - 2.0 * step_deg)

            elif self._phase == "sweep_a":
                # Calibration stretch: sweep while tracking the calm-motion
                # jitter peak; only then derive the threshold and arm.
                # Gated on sample count, not just time — the caller may tick
                # us slower than step_ms and the window must actually fill.
                if self._threshold is None:
                    if var is not None:
                        self._cal_n += 1
                        if var > self._cal_peak:
                            self._cal_peak = var
                    if elapsed >= self._CAL_MS and self._cal_n >= self._var._n:
                        self._threshold = max(
                            self._cal_peak * self._mult, self._VAR_FLOOR)
                    if self._cmd >= self._max:
                        self._fail("no luff (A)")
                    else:
                        self._drive(self._cmd + step_deg)
                elif var is not None and var > self._threshold:
                    self.angle_a = self._prev_enc
                    self._phase = "sweep_b"
                    self._phase_start_ms = now
                    self._calm_since_ms = None
                    self._armed = False
                    self._drive(self._max)   # jump past the flutter zone
                    self._var.reset()
                    self._prev_enc = None
                elif self._cmd >= self._max:
                    self._fail("no luff (A)")
                else:
                    self._drive(self._cmd + step_deg)

            elif self._phase == "sweep_b":
                # Re-arm only after the sail has filled (calm window) so we
                # don't trigger on the same flutter zone we jumped across.
                if not self._armed:
                    if var is not None and var <= self._threshold:
                        if self._calm_since_ms is None:
                            self._calm_since_ms = now
                        elif time.ticks_diff(now, self._calm_since_ms) >= self._ARM_CALM_MS:
                            self._armed = True
                    else:
                        self._calm_since_ms = None
                if self._armed and var is not None and var > self._threshold:
                    self.angle_b = self._prev_enc
                    # A real luff zone has angular width; A ~= B means both
                    # onsets fired on the same noise event, not on wind.
                    if abs(_delta180(self.angle_a, self.angle_b)) < self._MIN_ZONE_DEG:
                        self._fail("luff zone too narrow")
                    else:
                        from src.nav.bearing import midpoint_angle
                        self._wind = midpoint_angle(self.angle_a, self.angle_b)
                        self._phase = "done"
                        if self._servo is not None:
                            try:
                                self._servo.center()
                            except Exception:
                                pass
                elif self._cmd <= self._min:
                    self._fail("no luff (B)")
                else:
                    self._drive(self._cmd - step_deg)

        return {
            "phase": self._phase,
            "progress": self._progress(),
            "sail_deg": self._cmd,
            "angle_a": self.angle_a,
            "angle_b": self.angle_b,
            "wind_angle": self._wind,
            "done": self._phase == "done",
            "error": self.error,
        }

    def _progress(self):
        """Coarse 0-100 estimate across the four active phases."""
        if self._phase == "done":
            return 100
        if self._phase in ("idle", "failed"):
            return 0
        span = max(1.0, float(self._max - self._min))
        if self._cmd is None:
            return 0
        if self._phase == "position":
            return int(10.0 * (1.0 - (self._cmd - self._min) / span))
        frac = (self._cmd - self._min) / span
        if self._phase == "sweep_a":
            return 10 + int(45.0 * frac)
        return 60 + int(38.0 * (1.0 - frac))   # sweep_b
