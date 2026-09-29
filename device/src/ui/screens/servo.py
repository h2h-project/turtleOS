# src/ui/screens/servo.py
# Servo + luff-wind-finder screen for TurtleOS.
# MicroPython / XIAO ESP32-S3 safe

import time
import gc

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


# --------------------------------------------------------------------
# Servo hardware configuration
# --------------------------------------------------------------------
#
# Calibrated safe positional range:
#
#     500 us  -> one endpoint
#     2500 us -> opposite endpoint
#
# The luff-test algorithm now lives in wind_finder.py. ServoScreen remains
# the UI + hardware-access layer.
# --------------------------------------------------------------------

SERVO_PWM_HZ = 50

# Wide emergency software clamp. The actual luff sweep range (500-2500 us)
# is owned and tuned in wind_finder.py.
SERVO_HARD_MIN_US = 400
SERVO_HARD_MAX_US = 2600

# Wait this long after the double click before starting the sweep.
WIND_TEST_DELAY_MS = 2000

# Cosmetic gear animation: four rotations over one complete sweep.
GEAR_ROTATION_TURNS = 4.0


def _fmt_ma(value):
    if value is None:
        return "---mA"
    return "{:.0f}mA".format(float(value))


def _fmt_deg(value):
    if value is None:
        return "--- deg"
    return "{:.1f} deg".format(float(value))


class ServoScreen:

    def __init__(self, oled, servo_pin=None, i2c=None, ina=None):
        self.oled = oled
        self._pin = servo_pin

        self._i2c = i2c
        self._ina = ina
        self._as5600 = None

        self._connected = None
        self._servo_configured = None

        self._last_current_ma = None
        self._last_angle_deg = None

        # Persistent PWM while this screen is active.
        self._pwm = None

        # Completed wind result remains visible until another action.
        self._wind_result = None


    # ----------------------------------------------------------------
    # INA219 access
    # ----------------------------------------------------------------

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


    def _read_current_ma(self):
        ina = self._get_ina()

        if ina is None or not getattr(ina, "is_present", False):
            return None

        try:
            value = ina.current_ma()
            self._last_current_ma = value
            return value

        except Exception:
            return None


    # ----------------------------------------------------------------
    # AS5600 access
    # ----------------------------------------------------------------

    def _get_as5600(self):
        if self._as5600 is not None:
            return self._as5600

        if self._i2c is None:
            return None

        try:
            from src.drivers.as5600 import AS5600
            sensor = AS5600(self._i2c)

            if sensor.is_present:
                self._as5600 = sensor

        except Exception:
            pass

        return self._as5600


    def _read_angle_deg(self):
        """
        Read the filtered AS5600 angle.

        A valid angle() result is accepted directly. STATUS.MD is diagnostic
        only; it does not gate the angle reading.
        """

        sensor = self._get_as5600()

        if sensor is None or not getattr(sensor, "is_present", False):
            return None

        try:
            value = sensor.angle()

            if value is not None:
                self._last_angle_deg = value

            return value

        except Exception:
            return None


    # ----------------------------------------------------------------
    # Servo probe
    # ----------------------------------------------------------------

    def _probe(self):
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

        pwm = None

        try:
            pwm = self._make_pwm()
            self._connected = True

        except Exception:
            self._connected = False

        finally:
            if pwm is not None:
                try:
                    pwm.deinit()
                except Exception:
                    pass


    # ----------------------------------------------------------------
    # Drawing
    # ----------------------------------------------------------------

    def _draw(
        self,
        status_override=None,
        current_ma=None,
        angle_deg=None,
        gear_rotation_rad=0.0,
    ):
        """
        Normal Servo screen.

        Left:
            status
            servo current
            AS5600 angle

        Right:
            servo gear
        """

        o = self.oled
        fb = o.oled
        fb.fill(0)

        if _ch:
            try:
                _ch.draw(fb, o.width, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

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

        if current_ma is None:
            current_ma = self._last_current_ma

        if angle_deg is None:
            angle_deg = self._last_angle_deg

        current_text = (
            _fmt_ma(current_ma)
            if self._get_ina() is not None
            else "No INA219"
        )

        angle_text = (
            _fmt_deg(angle_deg)
            if self._get_as5600() is not None
            else "No AS5600"
        )

        o.f_med.write(current_text, 0, body_y + line_h)
        o.f_med.write(angle_text, 0, body_y + 2 * line_h)

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


    def _draw_wind_result(self, result):
        """
        Leave the completed luff result on the Servo screen.

        Luff alignment by itself can be 180 degrees ambiguous, so both the
        primary AS5600 angle and its opposite are shown.
        """

        if not result or not result.get("ok", False):
            self._draw(
                "Wind failed",
                current_ma=self._last_current_ma,
                angle_deg=self._last_angle_deg,
            )
            return

        o = self.oled
        fb = o.oled
        fb.fill(0)

        if _ch:
            try:
                _ch.draw(fb, o.width, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

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

        wind_deg = result.get("wind_angle_deg")
        opposite_deg = result.get("opposite_angle_deg")
        jitter_deg = result.get("jitter_deg")
        confidence = result.get("confidence")

        o.f_med.write(
            "Wind? {:.1f}".format(float(wind_deg)),
            0,
            body_y,
        )

        o.f_med.write(
            "Alt {:.1f}".format(float(opposite_deg)),
            0,
            body_y + line_h,
        )

        o.f_med.write(
            "Jit {:.2f} x{:.1f}".format(
                float(jitter_deg),
                float(confidence),
            ),
            0,
            body_y + 2 * line_h,
        )

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
                filled=True,
                filled_center=False,
                rotation_offset=0.0,
                color=1,
            )
        except Exception:
            pass

        fb.show()


    # ----------------------------------------------------------------
    # Raw PWM
    # ----------------------------------------------------------------

    def _clamp_pulse_us(self, pulse_us):
        if pulse_us < SERVO_HARD_MIN_US:
            return SERVO_HARD_MIN_US
        if pulse_us > SERVO_HARD_MAX_US:
            return SERVO_HARD_MAX_US
        return int(pulse_us)


    def _write_pulse_to_pwm(self, pwm, pulse_us):
        pulse_us = self._clamp_pulse_us(pulse_us)
        period_us = int(1000000 // SERVO_PWM_HZ)

        try:
            pwm.duty_ns(int(pulse_us * 1000))
            return
        except Exception:
            pass

        try:
            duty_u16 = int((pulse_us * 65535) // period_us)
            pwm.duty_u16(duty_u16)
            return
        except Exception:
            pass

        try:
            duty_10 = int((pulse_us * 1023) // period_us)
            pwm.duty(duty_10)
            return
        except Exception:
            pass

        raise RuntimeError("No supported PWM duty method")


    def _make_pwm(self):
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


    def _ensure_pwm(self):
        if self._pwm is None:
            self._pwm = self._make_pwm()
        return self._pwm


    def _write_pulse_us(self, pulse_us):
        """Hardware callback passed into WindFinder."""
        pwm = self._ensure_pwm()
        self._write_pulse_to_pwm(pwm, pulse_us)


    # ----------------------------------------------------------------
    # Wind finder integration
    # ----------------------------------------------------------------

    def _wind_progress(
        self,
        progress,
        pulse_us,
        angle_deg,
        current_ma,
        jitter_deg,
    ):
        """
        OLED update callback from wind_finder.py.

        It runs once per tested servo position, not once per sensor sample, so
        OLED I2C traffic does not contaminate the vibration measurement timing.
        """

        if progress < 0.0:
            progress = 0.0
        if progress > 1.0:
            progress = 1.0

        percent = int(progress * 100.0)

        gear_rotation = (
            progress
            * GEAR_ROTATION_TURNS
            * 2.0
            * 3.14159265
        )

        self._last_current_ma = current_ma
        self._last_angle_deg = angle_deg

        self._draw(
            "Sweep {}%".format(percent),
            current_ma=current_ma,
            angle_deg=angle_deg,
            gear_rotation_rad=gear_rotation,
        )


    def _run_wind_finder(self):
        if not self._connected:
            self._draw("Servo unavailable")
            return

        if self._get_as5600() is None:
            self._draw("No AS5600")
            return

        self._wind_result = None

        self._draw(
            "Wind test 2s",
            current_ma=self._read_current_ma(),
            angle_deg=self._read_angle_deg(),
        )

        time.sleep_ms(WIND_TEST_DELAY_MS)

        try:
            from src.ui.screens.wind_finder import WindFinder

            finder = WindFinder(
                write_pulse_us=self._write_pulse_us,
                read_angle_deg=self._read_angle_deg,
                read_current_ma=self._read_current_ma,
                progress_cb=self._wind_progress,
            )

            result = finder.run()
            self._wind_result = result
            self._draw_wind_result(result)
            self._view = "wind"

        except Exception as e:
            print("[WIND] failed:", repr(e))
            self._view = "failed"   # keep the failure on screen; no blink redraw

            self._draw(
                "Wind failed",
                current_ma=self._last_current_ma,
                angle_deg=self._last_angle_deg,
            )


    # ----------------------------------------------------------------
    # Cleanup
    # ----------------------------------------------------------------

    def _release_pwm(self):
        if self._pwm is not None:
            try:
                self._pwm.deinit()
            except Exception:
                pass

            self._pwm = None


    # ----------------------------------------------------------------
    # Public entry
    # ----------------------------------------------------------------

    def show_live(self, btn, tick_fn=None):
        """
        Servo screen controls:

        SINGLE CLICK
            Advance to the next carousel screen.

        DOUBLE CLICK
            Wait 2 seconds, then run one complete luff wind-finder sweep.

        TRIPLE CLICK
            No Servo-screen action.

        QUAD / SLEEP
            Return the action to the main UI.

        The completed wind result remains on screen until another action.
        """

        try:
            btn.reset()
        except Exception:
            pass

        self._connected = None
        self._servo_configured = None
        gc.collect()

        self._view = "normal"
        self._draw()
        self._probe()

        self._read_current_ma()
        self._read_angle_deg()

        self._draw(
            current_ma=self._last_current_ma,
            angle_deg=self._last_angle_deg,
        )

        _tick_next = time.ticks_ms()
        _tick_every = 500
        _ble = _ch.BleWatch() if _ch else None

        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            # Static screen: redraw only for the header's Bluetooth "+",
            # keeping whichever view is up (a wind-finder result stays on
            # screen until the next click).
            if (action is None and _ble is not None and _ble.changed()
                    and self._view != "failed"):
                if self._view == "wind" and self._wind_result is not None:
                    self._draw_wind_result(self._wind_result)
                else:
                    self._draw(current_ma=self._last_current_ma,
                               angle_deg=self._last_angle_deg)

            # Single click: next carousel screen.
            if action == "single":
                self._release_pwm()

                # Existing carousel uses "single" as next-screen.
                return "single"

            # Double click: run luff sweep.
            elif action == "double" and self._connected:
                self._run_wind_finder()

                try:
                    btn.reset()
                except Exception:
                    pass

            # Triple click: deliberately ignored.
            elif action == "triple":
                try:
                    btn.reset()
                except Exception:
                    pass

            # Other actions.
            elif action in ("quad", "sleep"):
                self._release_pwm()
                return action

            # Background tick only while idle. We intentionally do NOT call
            # tick_fn during the luff sweep; the nav controller must not issue
            # competing servo commands while WindFinder owns the servo.
            if tick_fn is not None:
                now = time.ticks_ms()

                if time.ticks_diff(now, _tick_next) >= 0:
                    try:
                        tick_fn()
                    except Exception:
                        pass

                    _tick_next = time.ticks_add(now, _tick_every)

            time.sleep_ms(2)