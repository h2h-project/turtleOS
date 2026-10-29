# src/drivers/servo.py — MG996R sail servo driver (PWM, 50 Hz)

from machine import Pin, PWM, Timer

# Every write powers the servo rail first (Pololu SSR on GPIO40); a False
# request means the rail is held off, so the write is skipped.
from src.drivers import servo_power

# Never send a pulse outside this, whatever the config says (matches
# SERVO_HARD_MIN_US / SERVO_HARD_MAX_US in ui/screens/servo.py).
HARD_MIN_US = 400
HARD_MAX_US = 2600

# glide_to() steps the pulse this often: one servo frame at 50 Hz.
GLIDE_STEP_MS = 20
# Hardware timer for the glide (nothing else in turtleOS uses 0-3; the LED
# blink uses a virtual one), then a virtual one if the port refuses it.
GLIDE_TIMER_IDS = (2, -1)


def _glide_timer(cb):
    for tid in GLIDE_TIMER_IDS:
        try:
            t = Timer(tid)
            t.init(mode=Timer.PERIODIC, period=GLIDE_STEP_MS, callback=cb)
            return t
        except Exception:
            pass
    return None


# Pins something else has taken over or deinit'd since (the Servo screen's
# bench sweep drives GPIO7 with its own PWM and deinits it when done). On
# ESP32 that detaches the pin from the LEDC channel a Servo still holds, and
# writes to it can then go nowhere without raising, so a Servo rebuilds its
# PWM before the next write once its pin's count has moved on, and a glide
# in progress stops rather than fight the new owner.
_released = {}


def pin_released(pin):
    """Tell every Servo on pin that its PWM was taken over or torn down
    elsewhere."""
    _released[pin] = _released.get(pin, 0) + 1


def _quiet_pwm(pin, freq):
    """A PWM on pin sending no pulses (duty 0) until the first real write.

    A bare PWM(Pin(n)) starts at 50 % duty — at 50 Hz a 10 ms pulse, which
    makes a servo twitch and hunt from boot until the first angle is
    written. MicroPython 1.28 can refuse the keyword constructor on a pin
    that already has a PWM ("PWM is inactive"), so fall back to fetching
    the pin's PWM and re-initialising it."""
    try:
        return PWM(Pin(pin), freq=freq, duty_u16=0)
    except Exception:
        p = PWM(Pin(pin))
        p.init(freq=freq, duty_u16=0)
        return p


class Servo:
    def __init__(self, pin, freq=50, min_us=1000, max_us=2000):
        self.pin = pin
        self.freq = freq
        self.min_us = min_us
        self.max_us = max_us
        self.pwm = _quiet_pwm(pin, freq)
        self._gen = _released.get(pin, 0)
        self._us = None           # pulse last written (None: not yet / unknown)
        self._glide_to = None     # glide target (us) while gliding
        self._glide_step = 0      # us per GLIDE_STEP_MS
        self._timer = None

    def _live_pwm(self):
        """Our PWM, rebuilt if the pin was released since we made it."""
        gen = _released.get(self.pin, 0)
        if gen != self._gen:
            self.pwm = _quiet_pwm(self.pin, self.freq)
            self._gen = gen
        return self.pwm

    def write_us(self, us):
        self._stop_glide()
        if not servo_power.request():
            return
        us = max(self.min_us, min(self.max_us, us))
        period_us = int(1000000 / self.freq)
        duty = int((us / period_us) * 65535)
        self._live_pwm().duty_u16(duty)
        self._us = us

    def pulse_us(self, degrees):
        """The pulse angle() writes for degrees."""
        degrees = max(0, min(180, degrees))
        return self.min_us + int((degrees / 180) * (self.max_us - self.min_us))

    def angle(self, degrees):
        self.write_us(self.pulse_us(degrees))

    def write_pulse(self, us):
        """Any pulse inside the hard limits, ignoring min_us/max_us — for
        hand control across the servo's configured end stops
        (servo_min_us / servo_max_us). The Servo screen's bench sweep
        deinits a PWM on this same pin when it finishes (pin_released), so
        recreate ours then, and if the write fails anyway."""
        self._stop_glide()
        if not servo_power.request():
            return
        self._put(us)

    def _put(self, us):
        """Write one pulse inside the hard limits (write_pulse, glide steps)."""
        us = max(HARD_MIN_US, min(HARD_MAX_US, int(us)))
        duty = int((us / int(1000000 / self.freq)) * 65535)
        try:
            self._live_pwm().duty_u16(duty)
        except Exception:
            self.pwm = _quiet_pwm(self.pin, self.freq)
            self.pwm.duty_u16(duty)
        self._us = us

    def glide_to(self, us, rate_us_s):
        """Move to pulse us at rate_us_s (us per second) instead of in one
        jump: a timer steps the pulse every GLIDE_STEP_MS, so a stream of
        targets (the app's servo dial) becomes one smooth motion. A new
        target just redirects the glide. Jumps when the position is unknown
        or rate_us_s <= 0. Any other write (write_us / write_pulse / angle)
        cancels the glide."""
        us = max(HARD_MIN_US, min(HARD_MAX_US, int(us)))
        if self._us is None or rate_us_s <= 0:
            self.write_pulse(us)
            return
        if not servo_power.request():
            self._stop_glide()
            return
        self._live_pwm()           # ours again after a takeover, before the timer checks
        self._glide_to = us
        self._glide_step = max(1, int(rate_us_s) * GLIDE_STEP_MS // 1000)
        if self._timer is None:
            self._timer = _glide_timer(self._glide_tick)
            if self._timer is None:
                print("[SERVO] no glide timer - jumping")
                self.write_pulse(us)
                return
        self._glide_tick(None)     # first step now, not a frame later

    def _glide_tick(self, _t):
        target = self._glide_to
        if target is None or _released.get(self.pin, 0) != self._gen:
            self._stop_glide()      # done, or the pin has a new owner
            return
        cur = self._us if self._us is not None else target
        d = target - cur
        step = self._glide_step
        nxt = target if -step <= d <= step else cur + (step if d > 0 else -step)
        servo_power.touch()
        try:
            self._put(nxt)
        except Exception as e:
            print("[SERVO] glide step failed:", repr(e))
            self._stop_glide()
            return
        if nxt == target:
            self._stop_glide()

    def _stop_glide(self):
        self._glide_to = None
        t = self._timer
        if t is not None:
            self._timer = None
            try:
                t.deinit()
            except Exception:
                pass

    def center(self):
        self.angle(90)

    def deinit(self):
        self._stop_glide()
        self.pwm.deinit()