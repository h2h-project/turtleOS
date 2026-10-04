# src/drivers/servo.py — MG996R sail servo driver (PWM, 50 Hz)

from machine import Pin, PWM

# Never send a pulse outside this, whatever the config says (matches
# SERVO_HARD_MIN_US / SERVO_HARD_MAX_US in ui/screens/servo.py).
HARD_MIN_US = 400
HARD_MAX_US = 2600


class Servo:
    def __init__(self, pin, freq=50, min_us=1000, max_us=2000):
        self.pin = pin
        self.freq = freq
        self.min_us = min_us
        self.max_us = max_us
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(freq)

    def write_us(self, us):
        us = max(self.min_us, min(self.max_us, us))
        period_us = int(1000000 / self.freq)
        duty = int((us / period_us) * 65535)
        self.pwm.duty_u16(duty)

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
        deinits a PWM on this same pin when it finishes, so recreate ours
        if the write fails."""
        us = max(HARD_MIN_US, min(HARD_MAX_US, int(us)))
        duty = int((us / int(1000000 / self.freq)) * 65535)
        try:
            self.pwm.duty_u16(duty)
        except Exception:
            self.pwm = PWM(Pin(self.pin), freq=self.freq)
            self.pwm.duty_u16(duty)

    def center(self):
        self.angle(90)

    def deinit(self):
        self.pwm.deinit()