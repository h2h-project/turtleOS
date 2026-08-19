# src/ui/screens/servo.py  (MicroPython / Pico-safe)

import time
import gc

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


# --------------------------------------------------------------------
# Raw servo movement test settings
# --------------------------------------------------------------------
# Normal hobby servo signal:
#   50 Hz PWM
#   ~1000 us = one end   (0 deg)
#   ~1500 us = centre    (90 deg)
#   ~2000 us = other end (180 deg)
#
# The MG996R now gets its own rail from an LTC1871 boost converter
# (3-35V in -> 3.5-35V/9A out) instead of sharing the logic supply. The two
# test movements exist to load that rail two different ways:
#
#   "low"  - a small oscillation around centre. Modest torque, modest
#            current draw - confirms the rail holds up under light load.
#   "high" - full-range bang-bang against the mechanical stops (0 <-> 180),
#            each move a single instantaneous pulse-width jump so the servo
#            slews at its own maximum rate. This is the most aggressive
#            command a servo can be given and draws the most current
#            (worst case: stall current at the stops), so it is the
#            decisive test of whether the rail sags under load.
#
# Step size matters more than step rate for the low-power ramp. An MG996R
# is analogue and has a deadband of roughly 5-10 us (~1-2 deg): command
# increments near that size make the motor hunt back and forth without the
# horn making real progress. MOVE_STEP_DEG keeps every increment decisively
# above the deadband so each step is a real slew.
SERVO_PWM_HZ = 50

SERVO_MIN_US = 1000
SERVO_MAX_US = 2000
SERVO_RANGE_DEG = 180

SERVO_HOME_DEG = 90       # centre for the low-power oscillation

LOW_POWER_HALF_SWEEP_DEG = 15   # low-power arc: HOME +/- this, light load
HIGH_POWER_LO_DEG = 0           # high-power arc: full range, stalls at stops
HIGH_POWER_HI_DEG = 180

MOVE_STEP_DEG = 3.0        # low-power ramp increment (~17 us, past deadband)
MOVE_STEP_PERIOD_MS = 15   # time per ramp step (low-power only)
MOVE_HOLD_MS = 600         # dwell at each end before reversing (high-power)
MOVE_MAX_MS = 4000         # hard cap on a single movement run, either mode
MOVE_POLL_MS = 15          # how often the button is checked for a stop click

SERVO_DEINIT_AFTER_TEST = True


class ServoScreen:
    def __init__(self, oled, servo_pin=None):
        self.oled = oled
        self._pin = servo_pin

        self._connected = None         # None=unchecked, True=PWM OK, False=failed
        self._servo_configured = None  # config servo_present flag

    # ----------------------------
    # Probe
    # ----------------------------

    def _probe(self):
        """
        Check config + PWM initialisation.

        Important:
        This does NOT physically detect a servo.

        A normal hobby servo has:
          - power
          - ground
          - signal input

        It has no data return line. So software cannot directly know whether
        the servo is actually attached. cfg["servo_present"] is authoritative.
        """
        if self._pin is None:
            self._connected = False
            self._servo_configured = False
            return

        try:
            from config import load_config
            cfg = load_config() or {}
            self._servo_configured = bool(cfg.get("servo_present", False))
        except Exception:
            self._servo_configured = False

        if not self._servo_configured:
            self._connected = False
            return

        try:
            from machine import Pin, PWM
            pwm = PWM(Pin(self._pin))
            pwm.freq(SERVO_PWM_HZ)
            pwm.deinit()
            self._connected = True
        except Exception:
            self._connected = False

    # ----------------------------
    # Drawing
    # ----------------------------

    def _draw(self, status_override=None, gear_rotation_rad=0.0):
        o = self.oled
        fb = o.oled
        fb.fill(0)

        if _ch:
            try:
                _ch.draw(fb, o.width, icon_y=1)
            except Exception:
                pass

        # Title
        o.f_arvo20.write("Servo", 0, 0)

        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 20

        try:
            _, med_h = o._text_size(o.f_med, "Ag")
        except Exception:
            med_h = 11

        body_y = title_h + 4
        line_h = med_h + 3

        # Status line
        if status_override is not None:
            status = status_override
        elif self._connected is None:
            status = "Checking..."
        elif self._connected:
            status = "PWM OK"
        elif self._pin is None:
            status = "No servo pin"
        elif not self._servo_configured:
            status = "Not wired"
        else:
            status = "PWM failed"

        o.f_med.write(status, 0, body_y)

        if self._pin is not None:
            o.f_med.write("D8 / GPIO{}".format(self._pin), 0, body_y + line_h)

        # Gear icon on the right
        try:
            from src.ui.glyphs import draw_gear
            draw_gear(
                fb,
                cx=107,
                cy=44,
                body_r=12,
                tooth_len=4,
                teeth=6,
                center_r=6,
                filled=bool(self._connected),
                filled_center=False,
                rotation_offset=gear_rotation_rad,
                color=1,
            )
        except Exception:
            pass

        fb.show()

    # ----------------------------
    # Raw PWM helpers
    # ----------------------------

    def _clamp_pulse_us(self, pulse_us):
        # Very wide safety clamp. Normal servo range is about 1000-2000 us.
        if pulse_us < 500:
            return 500
        if pulse_us > 2500:
            return 2500
        return int(pulse_us)

    def _write_pulse_us(self, pwm, pulse_us):
        pulse_us = self._clamp_pulse_us(pulse_us)

        # At 50Hz the PWM period is 20,000 us.
        period_us = int(1000000 // SERVO_PWM_HZ)

        # Prefer duty_ns where available.
        try:
            pwm.duty_ns(int(pulse_us * 1000))
            return
        except Exception:
            pass

        # Pico / many modern MicroPython ports.
        try:
            duty_u16 = int((pulse_us * 65535) // period_us)
            pwm.duty_u16(duty_u16)
            return
        except Exception:
            pass

        # Older ESP32 fallback: 10-bit duty.
        try:
            duty_10 = int((pulse_us * 1023) // period_us)
            pwm.duty(duty_10)
            return
        except Exception:
            pass

        raise RuntimeError("No supported PWM duty method")

    def _make_pwm(self):
        """
        Build the servo PWM with the frequency set from the start.

        A bare PWM(Pin(n)) on ESP32 comes up at the LEDC default (~5 kHz, 50%
        duty) for the moment before .freq() lands. That is garbage to a servo
        and can make it twitch or stall against a stop before the real signal
        arrives, so pass freq in the constructor where the port supports it.
        """
        from machine import Pin, PWM

        p = Pin(self._pin)
        try:
            return PWM(p, freq=SERVO_PWM_HZ, duty_u16=0)
        except Exception:
            pass
        try:
            return PWM(p, freq=SERVO_PWM_HZ)
        except Exception:
            pass

        pwm = PWM(p)
        pwm.freq(SERVO_PWM_HZ)
        return pwm

    def _report_signal(self, pwm):
        """
        Read the timer back and print what the peripheral actually ended up
        with. We only ever verify our own arithmetic otherwise - this catches
        the case where the LEDC timer did not take 50 Hz (at which point a
        1000-2000 us pulse is longer than the period, duty clamps to 100%,
        and the servo sees a DC level with no frame edges at all).
        """
        try:
            f = pwm.freq()
        except Exception:
            f = None
        try:
            d = pwm.duty_ns()
        except Exception:
            d = None

        print("[SERVO] signal check: freq={} Hz (want {}), duty_ns={}".format(
            f, SERVO_PWM_HZ, d))

        if f is not None and abs(int(f) - SERVO_PWM_HZ) > 2:
            print("[SERVO] WARNING: timer is not at {} Hz - pulse widths are "
                  "meaningless at this frequency".format(SERVO_PWM_HZ))
        return f

    def _us_for_angle(self, deg):
        if deg < 0:
            deg = 0
        elif deg > SERVO_RANGE_DEG:
            deg = SERVO_RANGE_DEG
        span_us = SERVO_MAX_US - SERVO_MIN_US
        return self._clamp_pulse_us(SERVO_MIN_US + (deg / SERVO_RANGE_DEG) * span_us)

    def _write_angle(self, pwm, deg):
        us = self._us_for_angle(deg)
        self._write_pulse_us(pwm, us)
        return us

    # ----------------------------
    # Interruptible movement test
    # ----------------------------

    def _poll_stop(self, btn):
        """Return True if a single click (stop) has been seen."""
        try:
            action = btn.poll_action()
        except Exception:
            action = None
        return action == "single"

    def _move_loop(self, btn, pwm, lo, hi, ramped):
        """
        Oscillate between lo and hi until a single click stops it or
        MOVE_MAX_MS total elapses, whichever comes first.

        ramped=True steps through MOVE_STEP_DEG increments (low-power arc,
        gentle on the boost-converter rail). ramped=False jumps instantly to
        each end and holds (high-power arc, max slew, max current draw
        against the stops). Returns "stopped" or "timeout".
        """
        t_start = time.ticks_ms()
        pos = lo
        going_to = hi

        while True:
            if time.ticks_diff(time.ticks_ms(), t_start) >= MOVE_MAX_MS:
                return "timeout"

            if ramped:
                span = going_to - pos
                steps = max(1, int(abs(span) / MOVE_STEP_DEG))
                for i in range(1, steps + 1):
                    if time.ticks_diff(time.ticks_ms(), t_start) >= MOVE_MAX_MS:
                        return "timeout"
                    if self._poll_stop(btn):
                        return "stopped"
                    self._write_angle(pwm, pos + span * i / steps)
                    time.sleep_ms(MOVE_STEP_PERIOD_MS)
            else:
                self._write_angle(pwm, going_to)
                hold_deadline = time.ticks_add(time.ticks_ms(), MOVE_HOLD_MS)
                while time.ticks_diff(hold_deadline, time.ticks_ms()) > 0:
                    if time.ticks_diff(time.ticks_ms(), t_start) >= MOVE_MAX_MS:
                        return "timeout"
                    if self._poll_stop(btn):
                        return "stopped"
                    time.sleep_ms(MOVE_POLL_MS)

            pos = going_to
            going_to = lo if going_to == hi else hi

    def _run_movement(self, btn, mode):
        """
        Run one interruptible test movement.

        mode: "low"  - small oscillation around SERVO_HOME_DEG (light load).
              "high" - full-range bang-bang, 0 <-> 180 (heavy load, stalls
                       at the stops).

        Bypasses src.drivers.servo.Servo.angle() so this tests the raw 50 Hz
        signal path and the servo's own power rail directly. Runs until a
        single click stops it or MOVE_MAX_MS elapses. Each commanded
        position is printed to serial for REPL diagnosis.
        """
        if self._pin is None:
            self._draw("No pin")
            time.sleep_ms(700)
            return

        pwm = None
        label = "Low power" if mode == "low" else "High power"

        try:
            pwm = self._make_pwm()
            print("[SERVO] {} move start: pin={} (max {} ms)".format(
                label, self._pin, MOVE_MAX_MS))

            if mode == "low":
                lo = SERVO_HOME_DEG - LOW_POWER_HALF_SWEEP_DEG
                hi = SERVO_HOME_DEG + LOW_POWER_HALF_SWEEP_DEG
                ramped = True
            else:
                lo = HIGH_POWER_LO_DEG
                hi = HIGH_POWER_HI_DEG
                ramped = False

            self._write_angle(pwm, lo)
            self._report_signal(pwm)
            self._draw("{}...".format(label))

            result = self._move_loop(btn, pwm, lo, hi, ramped)
            print("[SERVO] {} move {}".format(label, result))
            self._draw("{} {}".format(label, result))
            time.sleep_ms(400)

        except Exception as e:
            print("[SERVO] move failed:", e)
            try:
                self._draw("Move failed")
                time.sleep_ms(900)
            except Exception:
                pass

        finally:
            if pwm and SERVO_DEINIT_AFTER_TEST:
                try:
                    pwm.deinit()
                except Exception:
                    pass

        self._draw()

    # ----------------------------
    # Public entry
    # ----------------------------

    def show_live(self, btn, tick_fn=None):
        """
        Single click  : advance carousel (idle) / stop the running movement.
        Double click  : start the low-power movement (small oscillation
                        around centre, light load on the servo rail).
        Triple click  : start the high-power movement (full-range
                        bang-bang against the stops, heaviest load on the
                        servo rail).

        Both movements run until a single click stops them or MOVE_MAX_MS
        elapses, whichever comes first.

        The idle loop does nothing but poll the button. There is no periodic
        redraw: with the INA219 readout gone this screen is entirely static
        once probed, so a refresh tick would only add a 50-100 ms font-render
        plus I2C flush during which the button is not sampled. A click is only
        ~100 ms of debounced level change and the button is sampled *only*
        when polled, so a redraw landing on top of the gap between two clicks
        was enough to break a double into two singles. Poll fast, draw only on
        change.
        """
        try:
            btn.reset()
        except Exception:
            pass

        self._connected = None
        self._servo_configured = None
        gc.collect()

        self._draw()
        self._probe()
        self._draw()

        _tick_next = time.ticks_ms()
        _tick_every = 500

        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "double" and self._connected:
                self._run_movement(btn, "low")
                # The movement drew its own frames; restore the idle view.
                self._draw()
                try:
                    btn.reset()
                except Exception:
                    pass

            elif action == "triple" and self._connected:
                self._run_movement(btn, "high")
                self._draw()
                try:
                    btn.reset()
                except Exception:
                    pass

            elif action in ("single", "quad", "sleep"):
                return action

            if tick_fn is not None:
                now = time.ticks_ms()
                if time.ticks_diff(now, _tick_next) >= 0:
                    try:
                        tick_fn()
                    except Exception:
                        pass
                    _tick_next = time.ticks_add(now, _tick_every)

            time.sleep_ms(2)
