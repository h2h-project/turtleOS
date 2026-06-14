# src/drivers/servo.py — MG996R sail servo driver (PWM, 50 Hz)

from machine import Pin, PWM

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

    def angle(self, degrees):
        degrees = max(0, min(180, degrees))
        us = self.min_us + int((degrees / 180) * (self.max_us - self.min_us))
        self.write_us(us)

    def center(self):
        self.angle(90)

    def deinit(self):
        self.pwm.deinit()