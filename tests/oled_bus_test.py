# tests/oled_bus_test.py — per-transaction NACK rate on I2C_SYS.
#
#   mpremote connect auto run tests/oled_bus_test.py
#
# Sends 1000 short writes to each device on the bus and counts failures.
# The OLED driver's init is ~50 writes and aborts on the first error, so even
# a 2-3% failure rate means the OLED almost never comes up. A healthy bus is 0.
# Run with and without a suspect module plugged in to compare.
import time
from machine import I2C, SoftI2C, Pin

SDA, SCL, N = 5, 6, 1000
PAYLOAD = {0x3C: b"\x00\xaf", 0x40: b"\x00", 0x77: b"\xd0", 0x38: b"\x71"}

def rate(i2c, addr):
    f = 0
    for _ in range(N):
        try:
            i2c.writeto(addr, PAYLOAD.get(addr, b"\x00"))
        except OSError:
            f += 1
    return f

# Build each bus only when its turn comes: constructing one takes over the pins.
for label, make in (("hw 400k", lambda: I2C(0, scl=Pin(SCL), sda=Pin(SDA), freq=400_000)),
                    ("hw 100k", lambda: I2C(0, scl=Pin(SCL), sda=Pin(SDA), freq=100_000)),
                    ("soft 50k", lambda: SoftI2C(scl=Pin(SCL), sda=Pin(SDA), freq=50_000))):
    i2c = make()
    found = i2c.scan()
    print("{:9} scan {}  fails/{}: {}".format(label, [hex(a) for a in found], N,
          "  ".join("{}={}".format(hex(a), rate(i2c, a)) for a in found)))

# First write on a freshly constructed I2C(0). The firmware builds a new I2C
# object in many places (init_i2c() is not a singleton); the OLED driver's
# first write is its init, so a failure here is "OLED: not found".
T = 200
f = 0
for _ in range(T):
    i2c = I2C(0, scl=Pin(SCL), sda=Pin(SDA), freq=400_000)
    try:
        i2c.writeto(0x3C, b"\x00\xaf")
    except OSError:
        f += 1
print("first write after new I2C(0): {}/{} failed".format(f, T))

# The failure mode that actually breaks boot: with the AHT21 module attached,
# the FIRST transaction after the bus has been idle ~5-100 ms NACKs (errno 19),
# for every device, and an immediate retry succeeds. Back-to-back writes above
# never see it. A healthy bus scores 0 here.
i2c = I2C(0, scl=Pin(SCL), sda=Pin(SDA), freq=400_000)
for gap in (1, 5, 20, 100, 300):
    first = retry = 0
    for _ in range(20):
        time.sleep_ms(gap)
        try:
            i2c.writeto(0x3C, b"\x00\xaf")
        except OSError:
            first += 1
            try:
                i2c.writeto(0x3C, b"\x00\xaf")
            except OSError:
                retry += 1
    print("after {:3} ms idle: first write failed {}/20, retry failed {}/20".format(gap, first, retry))
