# tests/mpu9250_bench.py
# Run with: mpremote connect auto run tests/mpu9250_bench.py
#
# Phase 0 bench check for the MPU-9250 IMU + AK8963 magnetometer.
# Plain register pokes (no dependency on src/drivers/mpu9250.py) so this
# is usable to derive the driver in the first place. Settles three open
# questions before the driver's register choices are trusted:
#   1. Does the AK8963 need a settle delay after CNTL1 before its first
#      reading is trustworthy? (compares immediate vs. delayed reads)
#   2. Is atan2(y, x) the right axis convention for heading on this
#      breakout, or does X/Y need swapping/inverting? (rotate the board
#      to a known heading — e.g. a phone compass — while this runs)
#   3. Does CNTL1 = 0x16 (continuous mode 2, 16-bit) behave as expected?

from machine import I2C, Pin
import time, math

try:
    from src.hal.board import i2c_pins as _ip
    _id, SCL, SDA, FREQ = _ip()
except Exception:
    SCL, SDA, FREQ = 6, 5, 400_000

i2c = I2C(0, scl=Pin(SCL), sda=Pin(SDA), freq=FREQ)
print("I2C devices:", [hex(a) for a in i2c.scan()])
print()

MPU_ADDR = 0x69
AK_ADDR = 0x0C


def s16(lo, hi):
    v = (hi << 8) | lo
    return v - 65536 if v >= 32768 else v


print("=== MPU-9250 (0x69) ===")
if MPU_ADDR not in i2c.scan():
    print("  0x69 not found on bus — check wiring / AD0 strap / power before anything else.")
else:
    try:
        who = i2c.readfrom_mem(MPU_ADDR, 0x75, 1)[0]
        print("  WHO_AM_I: 0x{:02X} (expect 0x71)".format(who))

        # Wake the chip out of sleep mode.
        i2c.writeto_mem(MPU_ADDR, 0x6B, bytes([0x00]))
        time.sleep_ms(10)

        d = i2c.readfrom_mem(MPU_ADDR, 0x3B, 6)
        ax, ay, az = s16(d[1], d[0]), s16(d[3], d[2]), s16(d[5], d[4])
        print("  Accel raw: X={:6d} Y={:6d} Z={:6d}  (g: {:.2f} {:.2f} {:.2f})".format(
            ax, ay, az, ax / 16384.0, ay / 16384.0, az / 16384.0))

        d = i2c.readfrom_mem(MPU_ADDR, 0x43, 6)
        gx, gy, gz = s16(d[1], d[0]), s16(d[3], d[2]), s16(d[5], d[4])
        print("  Gyro raw:  X={:6d} Y={:6d} Z={:6d}  (dps: {:.1f} {:.1f} {:.1f})".format(
            gx, gy, gz, gx / 131.0, gy / 131.0, gz / 131.0))

        # Enable bypass so the AK8963 shows up on the shared bus.
        i2c.writeto_mem(MPU_ADDR, 0x37, bytes([0x02]))
        time.sleep_ms(10)
        print("  Bypass enabled (INT_PIN_CFG <- 0x02)")
    except Exception as e:
        print("  FAIL:", repr(e))

print()
print("=== AK8963 (0x0C, behind bypass) ===")
devices_after_bypass = i2c.scan()
print("  I2C devices after bypass:", [hex(a) for a in devices_after_bypass])
if AK_ADDR not in devices_after_bypass:
    print("  0x0C not found — bypass mode did not expose the magnetometer.")
else:
    try:
        wia = i2c.readfrom_mem(AK_ADDR, 0x00, 1)[0]
        print("  WIA: 0x{:02X} (expect 0x48)".format(wia))

        i2c.writeto_mem(AK_ADDR, 0x0A, bytes([0x16]))  # continuous mode 2, 16-bit

        # --- Open question 1: warm-up timing ---
        d_immediate = i2c.readfrom_mem(AK_ADDR, 0x03, 6)
        i2c.readfrom_mem(AK_ADDR, 0x09, 1)  # ST2, release latch
        time.sleep_ms(1500)
        d_delayed = i2c.readfrom_mem(AK_ADDR, 0x03, 6)
        i2c.readfrom_mem(AK_ADDR, 0x09, 1)

        def decode(d):
            x = s16(d[0], d[1]); y = s16(d[2], d[3]); z = s16(d[4], d[5])
            h = math.atan2(y, x) * 180 / math.pi % 360
            return x, y, z, h

        xi, yi, zi, hi_ = decode(d_immediate)
        xd, yd, zd, hd = decode(d_delayed)
        print("  Immediate read: X={:6d} Y={:6d} Z={:6d}  heading={:.1f}deg".format(xi, yi, zi, hi_))
        print("  After 1.5s:     X={:6d} Y={:6d} Z={:6d}  heading={:.1f}deg".format(xd, yd, zd, hd))
        print("  -> if these differ by more than normal jitter, the driver needs a")
        print("     settle delay in AK8963.__init__ after the CNTL1 write.")
        print()

        # --- Open question 2: axis convention / heading sanity ---
        print("  Rotating? Watch 10 live heading samples below against a known")
        print("  reference (phone compass, or a marked North on the bench):")
        for n in range(10):
            time.sleep_ms(150)
            d = i2c.readfrom_mem(AK_ADDR, 0x03, 6)
            i2c.readfrom_mem(AK_ADDR, 0x09, 1)
            x, y, z, h = decode(d)
            print("    #{}: X={:6d} Y={:6d} Z={:6d}  heading={:.1f}deg".format(n, x, y, z, h))
        print("  -> if the printed heading doesn't track the known reference correctly,")
        print("     AK8963.heading() in the driver needs its X/Y swapped or a sign flip.")
    except Exception as e:
        print("  FAIL:", repr(e))
