# tests/mag_raw.py
# Run with: mpremote connect auto run tests/mag_raw.py
#
# Raw magnetometer dump for diagnosing a "compass stuck in one sector"
# fault. Prints X/Y/Z counts + derived heading ~4x/s for 30 s while you
# slowly rotate the whole board through a full turn on a flat surface,
# well away from laptops / phones / speakers / the AS5600 magnet.
#
# What the numbers mean:
#   * X and Y should each swing through both a clear negative and a clear
#     positive extreme over one rotation, roughly equal in magnitude, and
#     the (X,Y) pairs should trace a circle centred near 0,0. Heading then
#     sweeps the full 0-359.
#   * If X/Y barely move -> chip isn't sampling (mode/register bug) OR a
#     strong DC field has railed it.
#   * If X/Y move but the circle is far off-centre (e.g. X stays 1800..2600,
#     never near 0) -> hard-iron offset: a magnet or ferrous mass is stuck
#     to the field. Heading will only cover a wedge -> your N/NE symptom.
#   * If the circle is centred and full but heading is mirrored/rotated ->
#     axis-sign / axis-order bug in the driver.

import time
from machine import I2C, Pin

try:
    from src.hal.board import i2c_ext_pins as _p
    _id, SCL, SDA, FRQ = _p()
except Exception:
    _id, SCL, SDA, FRQ = 1, 3, 8, 400000

i2c = I2C(_id, scl=Pin(SCL), sda=Pin(SDA), freq=FRQ)
print("I2C_EXT I2C(%d) SCL=%d SDA=%d" % (_id, SCL, SDA))
print("scan:", ["0x%02X" % a for a in i2c.scan()])

# Bring up the GY-87 exactly as the firmware does (MPU6050 bypass -> mag).
from src.drivers.gy87 import GY87
dev = GY87(i2c)
print("summary:", dev.summary())
mag = dev.mag
if mag is None:
    print("no magnetometer - stop")
    raise SystemExit

print("scan after bypass:", ["0x%02X" % a for a in i2c.scan()])
print("-" * 60)
print("rotate the board slowly through one full turn now")
print("-" * 60)

xmin = ymin = zmin =  99999
xmax = ymax = zmax = -99999
n = int(30 / 0.25)
for _ in range(n):
    raw = mag.read_raw()
    hdg = mag.heading()
    if raw is None:
        print("read_raw -> None")
    else:
        x, y, z = raw
        xmin = min(xmin, x); xmax = max(xmax, x)
        ymin = min(ymin, y); ymax = max(ymax, y)
        zmin = min(zmin, z); zmax = max(zmax, z)
        print("X %+7d  Y %+7d  Z %+7d   hdg %s" %
              (x, y, z, "%.1f" % hdg if hdg is not None else "--"))
    time.sleep_ms(250)

print("-" * 60)
print("X range %+7d .. %+7d   span %6d   mid %+7d" % (xmin, xmax, xmax - xmin, (xmin + xmax) // 2))
print("Y range %+7d .. %+7d   span %6d   mid %+7d" % (ymin, ymax, ymax - ymin, (ymin + ymax) // 2))
print("Z range %+7d .. %+7d   span %6d   mid %+7d" % (zmin, zmax, zmax - zmin, (zmin + zmax) // 2))
print("ideal: X/Y spans similar, X/Y mids near 0. A mid far from 0 = hard-iron offset.")
