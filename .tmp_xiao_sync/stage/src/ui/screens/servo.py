# src/ui/screens/servo.py  (MicroPython / Pico-safe)

import time
import gc

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


class ServoScreen:
    def __init__(self, oled, servo_pin=None):
        self.oled = oled
        self._pin = servo_pin
        self._connected = None       # None=unchecked, True=present+PWM OK, False=absent/failed
        self._servo_configured = None  # reflects config servo_present flag after probe

    # ----------------------------
    # Probe
    # ----------------------------

    def _probe(self):
        """
        Set self._connected based on the servo_present config flag + PWM init test.

        PWM(Pin(n)).init() always succeeds on ESP32-S3 regardless of whether a
        servo is physically wired, so software cannot detect physical connection.
        The servo_present config key is the authoritative source of truth.
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

        # Config says present — verify PWM driver initialises without error
        try:
            from src.drivers.servo import Servo
            s = Servo(self._pin)
            s.deinit()
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

        # Title flush with top edge, matching GPS screen convention (title_y=0)
        o.f_arvo20.write("Servo", 0, 0)

        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 20

        data_y = title_h + 4
        line_h = 13

        if status_override is not None:
            status = status_override
        elif self._connected is None:
            status = "Checking..."
        elif self._connected:
            status = "Connected"
        elif self._pin is None:
            status = "No servo pin"
        elif not self._servo_configured:
            status = "Not wired"
        else:
            status = "Init failed"

        o.f_med.write(status, 0, data_y)

        if self._pin is not None:
            o.f_med.write("D8 / GPIO{}".format(self._pin), 0, data_y + line_h)
            # Suppress spec line during testing to keep the screen clean
            if self._connected and status_override is None:
                o.f_med.write("50Hz  1-2ms", 0, data_y + line_h * 2)

        # Gear icon — right side, vertically centred in the screen below the title
        try:
            from src.ui.glyphs import draw_gear
            draw_gear(
                fb,
                cx=107,
                cy=44,
                body_r=12, tooth_len=4, teeth=6, center_r=6,
                filled=bool(self._connected),
                filled_center=False,
                rotation_offset=gear_rotation_rad,
                color=1,
            )
        except Exception:
            pass

        fb.show()

    # ----------------------------
    # Servo test
    # ----------------------------

    def _run_test(self):
        """
        Sweep 60°→120° (CW, 2 s) then 120°→60° (CCW, 2 s).
        Servo PWM stays active only for the sweep duration; deinit at the end
        to release holding current.  Gear icon rotates in sync with servo direction.
        """
        frame_ms   = 25          # display refresh interval
        phase_ms   = 2000        # 2 s per direction
        frames     = phase_ms // frame_ms  # 80 frames per phase

        cw_start, cw_end   = 60, 120      # clockwise phase
        ccw_start, ccw_end = 120, 60      # counter-clockwise phase

        # Radians added to gear each frame; 0.13 rad ≈ 7.4° → ~1 visual cycle / 2 s
        cw_step  =  0.13
        ccw_step = -0.13

        gear_rad = 0.0
        srv = None

        try:
            from src.drivers.servo import Servo
            srv = Servo(self._pin)

            # Phase 1 — clockwise
            for i in range(frames + 1):
                deg = cw_start + (cw_end - cw_start) * i / frames
                srv.angle(deg)
                self._draw("Testing...", gear_rad)
                gear_rad += cw_step
                time.sleep_ms(frame_ms)

            # Phase 2 — counter-clockwise
            for i in range(frames + 1):
                deg = ccw_start + (ccw_end - ccw_start) * i / frames
                srv.angle(deg)
                self._draw("Testing...", gear_rad)
                gear_rad += ccw_step
                time.sleep_ms(frame_ms)

        except Exception:
            pass

        finally:
            if srv:
                try:
                    srv.deinit()
                except Exception:
                    pass

        self._draw()   # restore normal status

    # ----------------------------
    # Public entry
    # ----------------------------

    def show_live(self, btn):
        """
        Single click  : advance carousel.
        Double click  : run servo test (only when connected).
        """
        btn.reset()
        self._connected = None
        self._servo_configured = None
        gc.collect()

        self._draw()         # show "Checking..." immediately
        self._probe()        # check config + optional PWM test
        self._draw()         # update with result

        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "double" and self._connected:
                self._run_test()
            elif action in ("single", "quad", "sleep"):
                return action

            time.sleep_ms(25)
