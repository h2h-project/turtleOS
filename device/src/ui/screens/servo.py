# src/ui/screens/servo.py  (MicroPython / Pico-safe)

import time
import gc

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


# --------------------------------------------------------------------
# Simple raw servo test settings
# --------------------------------------------------------------------
# Normal hobby servo signal:
#   50 Hz PWM
#   ~1000 us = one end   (0 deg)
#   ~1500 us = centre    (90 deg)
#   ~2000 us = other end (180 deg)
#
# The test runs TWO legs:
#   A -> B over SERVO_LEG_MS, then B -> A over SERVO_LEG_MS
# where A/B straddle SERVO_HOME_DEG by SERVO_SWEEP_DEG/2.
#
# Step size matters more than step rate. An MG996R is analogue and has a
# deadband of roughly 5-10 us (~1-2 deg): command increments near that size
# make the motor hunt back and forth without the horn making real progress.
# The high-speed pinion is very visibly moving while the ~250:1-reduced
# output creeps. SERVO_STEP_DEG keeps every increment decisively above the
# deadband so each step is a real slew.
SERVO_PWM_HZ = 50

SERVO_MIN_US = 1000
SERVO_MAX_US = 2000
SERVO_RANGE_DEG = 180

SERVO_HOME_DEG = 90       # centre of the sweep
SERVO_SWEEP_DEG = 90      # total travel per leg
SERVO_LEG_MS = 4000       # time to cover one leg (ramp mode)
SERVO_STEP_DEG = 3.0      # command increment (~17 us, well past deadband)

SERVO_START_SETTLE_MS = 800
SERVO_END_HOLD_MS = 800

# Test mode:
#   "endpoints" - bang-bang. Commands the full mechanical range as a single
#                 instantaneous jump and holds, so the servo slews at its own
#                 maximum rate (MG996R: ~0.17 s/60 deg, i.e. 180 deg in ~0.5 s).
#                 This is the most aggressive command a servo can be given, and
#                 therefore the decisive test: if the horn does not reach its
#                 stops under this, no command shape will move it and the fault
#                 is mechanical or electrical, not in this file.
#   "ramp"      - timed SERVO_SWEEP_DEG sweep over SERVO_LEG_MS each way.
#                 Use once the servo is known good and you want to watch
#                 controlled sail-speed motion.
SERVO_TEST_MODE = "endpoints"

SERVO_BANG_LO_DEG = 0
SERVO_BANG_HI_DEG = 180
SERVO_BANG_HOLD_MS = 1500   # dwell at each end; must exceed full-range slew time
SERVO_BANG_CYCLES = 3

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
    # Simple servo test
    # ----------------------------

    def _sweep_leg(self, pwm, from_deg, to_deg, duration_ms):
        """
        Drive from_deg -> to_deg over duration_ms.

        Nothing else happens in here: no OLED writes, no I2C, no button poll.
        The loop is PWM plus a sleep, so the leg takes duration_ms and the
        step period is what it says it is. Steps are sized by SERVO_STEP_DEG
        (not by a fixed period) so each command is a real slew rather than a
        deadband-sized nudge the servo can hunt on.
        """
        span = to_deg - from_deg
        steps = int(abs(span) / SERVO_STEP_DEG)
        if steps < 1:
            steps = 1

        t0 = time.ticks_ms()

        for i in range(1, steps + 1):
            self._write_angle(pwm, from_deg + span * i / steps)

            # Hold this step until its share of the leg has elapsed.
            deadline = time.ticks_add(t0, int(duration_ms * i / steps))
            while True:
                remain = time.ticks_diff(deadline, time.ticks_ms())
                if remain <= 0:
                    break
                time.sleep_ms(remain if remain < 10 else 10)

    def _bang_bang(self, pwm):
        """
        Full-range bang-bang: jump to one end, hold, jump to the other, hold.

        No ramp at all - each move is a single pulse-width change, so the servo
        slews at its own maximum rate against its own stops. Nothing a command
        can do moves a servo harder than this.
        """
        lo = SERVO_BANG_LO_DEG
        hi = SERVO_BANG_HI_DEG
        hold = max(200, int(SERVO_BANG_HOLD_MS))

        for c in range(max(1, int(SERVO_BANG_CYCLES))):
            for deg in (lo, hi):
                us = self._write_angle(pwm, deg)
                print("[SERVO] cycle {} -> {} deg ({} us)".format(c + 1, deg, us))
                self._draw("{:.0f}d {}us".format(deg, us))
                time.sleep_ms(hold)

    def _run_test(self):
        """
        Raw PWM servo test. See SERVO_TEST_MODE for the two shapes.

        This bypasses src.drivers.servo.Servo.angle() so we can test whether
        the servo responds to plain 50Hz servo pulses. The screen is drawn
        between moves only, never during one - the servo is the only thing
        that matters while the test is running. Each commanded position is
        also printed to serial so the test can be followed over the REPL.
        """
        if self._pin is None:
            self._draw("No pin")
            time.sleep_ms(700)
            return

        pwm = None

        try:
            pwm = self._make_pwm()
            print("[SERVO] test start: pin={} mode={}".format(self._pin, SERVO_TEST_MODE))
            self._write_angle(pwm, SERVO_HOME_DEG)
            self._report_signal(pwm)

            if SERVO_TEST_MODE == "endpoints":
                self._bang_bang(pwm)
            else:
                half = SERVO_SWEEP_DEG / 2.0
                a_deg = SERVO_HOME_DEG - half
                b_deg = SERVO_HOME_DEG + half
                leg_ms = max(200, int(SERVO_LEG_MS))

                # Park at A at full servo speed, then let it settle.
                self._write_angle(pwm, a_deg)
                self._draw("Start {:.0f}d".format(a_deg))
                time.sleep_ms(SERVO_START_SETTLE_MS)

                # Leg 1: A -> B.
                self._draw("{:.0f}d > {:.0f}d".format(a_deg, b_deg))
                self._sweep_leg(pwm, a_deg, b_deg, leg_ms)
                time.sleep_ms(SERVO_END_HOLD_MS)

                # Leg 2: B -> A.
                self._draw("{:.0f}d > {:.0f}d".format(b_deg, a_deg))
                self._sweep_leg(pwm, b_deg, a_deg, leg_ms)

            self._draw("Test done")
            print("[SERVO] test done")
            time.sleep_ms(SERVO_END_HOLD_MS)

        except Exception as e:
            print("[SERVO] test failed:", e)
            try:
                self._draw("Test failed")
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

    def show_live(self, btn):
        """
        Single click : advance carousel.
        Double click : run the raw PWM servo test (see SERVO_TEST_MODE).

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

        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "double" and self._connected:
                self._run_test()
                # The test drew its own frames; restore the idle view.
                self._draw()
                try:
                    btn.reset()
                except Exception:
                    pass

            elif action in ("single", "quad", "sleep"):
                return action

            time.sleep_ms(2)
