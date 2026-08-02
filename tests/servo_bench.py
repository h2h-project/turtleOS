# tests/servo_bench.py — standalone MG996R bench test
#
#   mpremote connect auto run tests/servo_bench.py
#
# Deliberately depends on nothing but machine + the HAL pin constant: no OLED,
# no button, no config, no screen loop. If the servo misbehaves here, the fault
# is in the wiring, the servo, or the PWM peripheral — not in the app.
#
# Everything is printed, so run it with a scope or logic analyser on the signal
# wire and correlate what is commanded against what is on the wire.

import time
from machine import Pin, PWM

try:
    from src.hal.board import servo_pin
    PIN = servo_pin()
except Exception:
    PIN = 7  # XIAO ESP32-S3: D8 / GPIO7

FREQ = 50
PERIOD_US = 1000000 // FREQ


def make_pwm(pin):
    p = Pin(pin)
    try:
        return PWM(p, freq=FREQ, duty_u16=0)
    except Exception:
        pass
    pwm = PWM(p)
    pwm.freq(FREQ)
    return pwm


def write_us(pwm, us):
    """Write a pulse width, preferring duty_ns, and report what stuck."""
    us = int(us)
    try:
        pwm.duty_ns(us * 1000)
        return "duty_ns"
    except Exception:
        pass
    try:
        pwm.duty_u16(int((us * 65535) // PERIOD_US))
        return "duty_u16"
    except Exception:
        pass
    pwm.duty(int((us * 1023) // PERIOD_US))
    return "duty"


def check(pwm):
    try:
        f = pwm.freq()
    except Exception:
        f = None
    try:
        d = pwm.duty_ns()
    except Exception:
        d = None
    print("  freq={} Hz (want {})  duty_ns={}".format(f, FREQ, d))
    if f is not None and abs(int(f) - FREQ) > 2:
        print("  !! timer is not at 50 Hz — pulse widths mean nothing here")
    return f


def hold(pwm, us, ms, label=""):
    method = write_us(pwm, us)
    print("{:>6} us  ({:<8})  {}".format(us, method, label))
    time.sleep_ms(ms)


def main():
    print("=" * 52)
    print("servo bench: pin={} freq={} period={}us".format(PIN, FREQ, PERIOD_US))
    pwm = make_pwm(PIN)
    write_us(pwm, 1500)
    check(pwm)

    # 1. Endpoints, full speed, generous dwell. A healthy MG996R covers the
    #    full range in well under 1 s, so 2 s at each end is unambiguous.
    print("\n-- 1. endpoints (watch the horn, not the motor) --")
    for i in range(3):
        hold(pwm, 1000, 2000, "cycle {} min".format(i + 1))
        hold(pwm, 2000, 2000, "cycle {} max".format(i + 1))

    # 2. Centre, then a coarse staircase. If the horn tracks these but not the
    #    endpoints above, travel is being limited mechanically.
    print("\n-- 2. staircase --")
    for us in (1500, 1250, 1500, 1750, 1500):
        hold(pwm, us, 1200)

    # 3. Hold centre and leave the signal running so the rail and the pulse can
    #    be probed in a steady state.
    print("\n-- 3. holding 1500us for 5 s (probe now) --")
    write_us(pwm, 1500)
    check(pwm)
    time.sleep_ms(5000)

    pwm.deinit()
    print("\ndone — signal off")
    print("=" * 52)


main()
