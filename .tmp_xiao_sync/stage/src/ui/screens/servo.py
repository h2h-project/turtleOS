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
#   ~1000 us = one end
#   ~1500 us = centre
#   ~2000 us = other end
#
# For a safer first test, use 1100 -> 1900.
# For a fuller MG996R test, try 1000 -> 2000.
#
# The test does ONE sweep only:
#   SERVO_START_US -> SERVO_END_US over SERVO_SWEEP_MS
#
SERVO_PWM_HZ = 50

SERVO_START_US = 1000
SERVO_END_US = 2000
SERVO_SWEEP_MS = 4000

SERVO_STEP_MS = 50
SERVO_START_SETTLE_MS = 800
SERVO_END_HOLD_MS = 800

SERVO_DEINIT_AFTER_TEST = True

# INA219 screen refresh
SERVO_REFRESH_MS = 500


def _fmt_v(v):
    if v is None:
        return "---"
    return "{:.2f}V".format(float(v))


def _fmt_ma(v):
    if v is None:
        return "---"
    return "{:.0f}mA".format(float(v))


def _fmt_mw(v):
    if v is None:
        return "---"
    return "{:.0f}mW".format(float(v))


class ServoScreen:
    def __init__(self, oled, servo_pin=None, i2c=None, ina=None):
        self.oled = oled
        self._pin = servo_pin

        # I2C / INA219 for servo power measurement.
        # Best wiring for servo current:
        #   Pololu 6V OUT+ -> INA219 VIN+
        #   INA219 VIN-    -> Servo red wire
        #   Pololu GND     -> Servo GND
        self._i2c = i2c
        self._ina = ina

        self._connected = None         # None=unchecked, True=PWM OK, False=failed
        self._servo_configured = None  # config servo_present flag
        self._refresh_ms = SERVO_REFRESH_MS

    # ----------------------------------------------------------
    # INA219 access
    # ----------------------------------------------------------

    def _get_ina(self):
        if self._ina is not None:
            return self._ina

        if self._i2c is None:
            return None

        try:
            from src.drivers.ina219 import INA219
            ina = INA219(self._i2c, auto_init=True)
            if ina.is_present:
                self._ina = ina
        except Exception:
            pass

        return self._ina

    def _read_power(self):
        ina = self._get_ina()

        if ina is None or not ina.is_present:
            return {
                "present": False,
                "bus_v": None,
                "current_ma": None,
                "power_mw": None,
            }

        try:
            return {
                "present": True,
                "bus_v": ina.bus_voltage_v(),
                "current_ma": ina.current_ma(),
                "power_mw": ina.power_mw(),
            }
        except Exception:
            return {
                "present": False,
                "bus_v": None,
                "current_ma": None,
                "power_mw": None,
            }

    def _update_peak(self, peak_ma, power):
        try:
            cur = power.get("current_ma")
            if cur is None:
                return peak_ma

            cur_abs = abs(float(cur))
            if peak_ma is None or cur_abs > peak_ma:
                return cur_abs
        except Exception:
            pass

        return peak_ma

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
        the servo is actually attached.

        With the INA219 in the servo power path, you can infer presence by
        seeing whether current changes during a command, but that is still
        an inference rather than true digital detection.
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

    def _draw(self, status_override=None, gear_rotation_rad=0.0, power=None, peak_ma=None):
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

        if power is None:
            power = self._read_power()

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

        # Power line
        if power.get("present", False):
            bus_v = power.get("bus_v")
            cur_ma = power.get("current_ma")
            o.f_med.write(
                "{} {}".format(_fmt_v(bus_v), _fmt_ma(cur_ma)),
                0,
                body_y + line_h,
            )
        else:
            o.f_med.write("No INA219", 0, body_y + line_h)

        # Third line: peak current during test, otherwise pin label
        if peak_ma is not None:
            o.f_med.write("Pk {}".format(_fmt_ma(peak_ma)), 0, body_y + 2 * line_h)
        elif self._pin is not None:
            o.f_med.write("D8 / GPIO{}".format(self._pin), 0, body_y + 2 * line_h)

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

    # ----------------------------
    # Simple servo test
    # ----------------------------

    def _run_test(self):
        """
        Simple one-way raw PWM test.

        It commands:
            SERVO_START_US -> SERVO_END_US

        over:
            SERVO_SWEEP_MS

        This bypasses src.drivers.servo.Servo.angle() so we can test whether
        the servo responds to plain 50Hz servo pulses.

        During the sweep it displays:
            - servo rail voltage from INA219
            - servo current from INA219
            - peak absolute current seen during the test
        """
        if self._pin is None:
            self._draw("No pin")
            time.sleep_ms(700)
            return

        start_us = self._clamp_pulse_us(SERVO_START_US)
        end_us = self._clamp_pulse_us(SERVO_END_US)

        step_ms = max(20, int(SERVO_STEP_MS))
        sweep_ms = max(step_ms, int(SERVO_SWEEP_MS))
        steps = max(1, int(sweep_ms // step_ms))

        pwm = None
        peak_ma = None
        gear = 0.0

        try:
            from machine import Pin, PWM

            pwm = PWM(Pin(self._pin))
            pwm.freq(SERVO_PWM_HZ)

            # Command start pulse and hold briefly.
            self._write_pulse_us(pwm, start_us)
            power = self._read_power()
            peak_ma = self._update_peak(peak_ma, power)
            self._draw("{}us".format(start_us), gear, power, peak_ma)
            time.sleep_ms(SERVO_START_SETTLE_MS)

            # One smooth sweep from start_us to end_us.
            for i in range(steps + 1):
                pulse = start_us + (end_us - start_us) * i / steps
                pulse = int(pulse)

                self._write_pulse_us(pwm, pulse)

                power = self._read_power()
                peak_ma = self._update_peak(peak_ma, power)

                self._draw("{}us".format(pulse), gear, power, peak_ma)
                gear += 0.05 if end_us >= start_us else -0.05

                time.sleep_ms(step_ms)

            # Hold final pulse briefly.
            power = self._read_power()
            peak_ma = self._update_peak(peak_ma, power)
            self._draw("End {}us".format(end_us), gear, power, peak_ma)
            time.sleep_ms(SERVO_END_HOLD_MS)

        except Exception:
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
        Double click : run one simple raw PWM servo sweep test.
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

        next_refresh = time.ticks_add(time.ticks_ms(), self._refresh_ms)

        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "double" and self._connected:
                self._run_test()

                # Reset refresh timer after test.
                next_refresh = time.ticks_add(time.ticks_ms(), self._refresh_ms)

            elif action in ("single", "quad", "sleep"):
                return action

            now = time.ticks_ms()
            if time.ticks_diff(now, next_refresh) >= 0:
                self._draw()
                next_refresh = time.ticks_add(now, self._refresh_ms)

            time.sleep_ms(25)