# tests/gy87_bench.py
# Run with: mpremote connect auto run tests/gy87_bench.py
#
# Bench check for the GY-87 10DOF board (MPU6050 + HMC/QMC5883 + BMP180):
#   1. WHO_AM_I at 0x68 (or 0x69) on I2C_EXT — the GY-87's own bus, so 0x68
#      here is the IMU, never the DS3231;
#   2. enable I2C bypass and rescan to show the magnetometer appearing at
#      0x0D / 0x1E;
#   3. BMP180 chip ID + one temp / pressure / altitude reading;
#   4. a 20-sample loop of accel, gyro, pitch/roll and raw heading so the
#      axis convention can be eyeballed against how the board is mounted.
# Uses the real drivers so what passes here is what boots.

import time
from machine import I2C, Pin

# The GY-87 lives on I2C_EXT (SDA=GPIO8/D9, SCL=GPIO3/D2), not the system bus.
try:
    from src.hal.board import i2c_ext_pins as _i2c_ext_pins
    _id, SCL_PIN, SDA_PIN, _freq = _i2c_ext_pins()
except Exception:
    _id, SCL_PIN, SDA_PIN, _freq = 1, 3, 8, 400000

i2c = I2C(_id, scl=Pin(SCL_PIN), sda=Pin(SDA_PIN), freq=_freq)


def scan():
    a = i2c.scan()
    print("  bus:", ["0x%02X" % x for x in a])
    return a


print("=" * 48)
print("  GY-87 bench on I2C_EXT  I2C({}) SCL={} SDA={}".format(_id, SCL_PIN, SDA_PIN))
print("=" * 48)
addrs = scan()

imu_addr = 0x68 if 0x68 in addrs else (0x69 if 0x69 in addrs else None)
if imu_addr is None:
    print("!! no MPU6050 at 0x68/0x69 on I2C_EXT — check SDA=GPIO8 SCL=GPIO3 and VCC_IN")
else:
    try:
        who = i2c.readfrom_mem(imu_addr, 0x75, 1)[0]
        print("  WHO_AM_I @0x%02X = 0x%02X  (0x68 expected; 0x70-0x73 = clone, ok)" % (imu_addr, who))
    except Exception as e:
        print("!! WHO_AM_I read failed:", repr(e))

from src.drivers.gy87 import GY87
dev = GY87(i2c, imu_addr=imu_addr or 0x68)
print("  summary:", dev.summary())

if dev.imu is not None:
    print("  bypass:", dev.imu.bypass)
    scan()

if dev.baro is not None:
    r = dev.baro_read()
    if r:
        t, p, alt = r
        print("  BMP180: {:.1f} C  {:.1f} hPa  alt {:.1f} m (ISA 1013.25)".format(t, p, alt))
    else:
        print("!! BMP180 read failed")
else:
    try:
        cid = i2c.readfrom_mem(0x77, 0xD0, 1)[0]
        print("  0x77 chip ID 0x%02X (0x55=BMP180 0x58=BMP280 0x60=BME280)" % cid)
    except Exception:
        print("  no device at 0x77")

if dev.imu is None and dev.mag is None:
    print("nothing more to sample")
else:
    print("-" * 48)
    print("  20 samples — tilt/rotate the board and watch the signs")
    for i in range(20):
        a = dev.read_accel()
        g = dev.read_gyro()
        pr = dev.pitch_roll()
        h = dev.heading()
        line = "  #%02d" % i
        if a:
            line += "  acc[g] %+.2f %+.2f %+.2f" % a
        if g:
            line += "  gyr[dps] %+6.1f %+6.1f %+6.1f" % g
        if pr:
            line += "  pitch %+6.1f roll %+6.1f" % pr
        line += "  hdg %s" % ("%.1f" % h if h is not None else "--")
        print(line)
        time.sleep_ms(250)
print("done")
