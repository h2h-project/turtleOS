# src/drivers/mpu9250.py
# MicroPython driver for the MPU-9250 9-DOF IMU (gyro + accel + AK8963
# magnetometer), as sourced and bench-confirmed for turtleOS Phase 0.
#
# MPU-9250:
#   I2C address : 0x69 (AD0 strapped to VCC on the module; 0x68 is DS3231's)
#   WHO_AM_I    : reg 0x75, expect 0x71
#   Powers up in sleep mode — PWR_MGMT_1 (0x6B) must be cleared before any
#   accel/gyro register reads return real data.
#   Bypass mode (INT_PIN_CFG, 0x37, bit1) exposes the internal AK8963
#   magnetometer directly on the shared bus at its own address.
#
# AK8963 (behind bypass):
#   I2C address : 0x0C (fixed)
#   WIA register: 0x00, expect 0x48
#   CNTL1 (0x0A): mode select — 0x16 = continuous mode 2 (100 Hz), 16-bit
#   Data regs   : HXL..HZL at 0x03, little-endian per axis; ST2 (0x09) must
#                 be read after each sample to release the data-ready latch
#
# heading() = atan2(y, x), same convention as QMC5883L/HMC5883L — X pointing
# North = 0°. NOT bench-verified on this specific breakout: some MPU-9250
# modules mount the AK8963 with X/Y swapped or inverted relative to the
# board's marked orientation. Confirm against a known heading before relying
# on this for navigation; see tests/mpu9250_bench.py.

import time
import math

# --- MPU-9250 (gyro + accel) ---
_MPU_ADDR         = const(0x69)
_REG_PWR_MGMT_1   = const(0x6B)
_REG_INT_PIN_CFG  = const(0x37)
_REG_ACCEL_XOUT_H = const(0x3B)
_REG_GYRO_XOUT_H  = const(0x43)
_REG_WHO_AM_I     = const(0x75)
_WHO_AM_I_EXPECTED = const(0x71)
# 0x70 is the MPU-6500's own WHO_AM_I signature. Plenty of boards sold as
# "MPU9250" are actually built on 6500 silicon (no onboard AK8963 die) --
# accept it so accel/gyro still come up; the AK8963 bypass probe below
# will just legitimately report "not found" on those units.
_WHO_AM_I_ACCEPTED = (0x71, 0x70)

# --- AK8963 (magnetometer, only reachable with bypass enabled) ---
_AK_ADDR         = const(0x0C)
_AK_REG_WIA      = const(0x00)
_AK_REG_HXL      = const(0x03)
_AK_REG_ST2      = const(0x09)
_AK_REG_CNTL1    = const(0x0A)
_AK_WIA_EXPECTED = const(0x48)


def _s16(lo, hi):
    v = (hi << 8) | lo
    return v - 65536 if v >= 32768 else v


class MPU9250:
    """Driver for the MPU-9250 6-DOF IMU, with the AK8963 magnetometer
    behind I2C bypass mode exposed as the nested `.mag` object.

    Usage::

        from src.hal.board import init_i2c
        from src.drivers.mpu9250 import MPU9250

        i2c = init_i2c()
        imu = MPU9250(i2c)
        if imu.is_present:
            heading = imu.heading()        # degrees, or None
            ax, ay, az = imu.read_accel()  # g
            gx, gy, gz = imu.read_gyro()   # deg/s
    """

    is_present = False

    def __init__(self, i2c, addr=_MPU_ADDR):
        self._i2c = i2c
        self._addr = int(addr)
        self.mag = None  # AK8963 instance, set only if bypass succeeds

        try:
            who = self._i2c.readfrom_mem(self._addr, _REG_WHO_AM_I, 1)[0]
            if who not in _WHO_AM_I_ACCEPTED:
                print("[MPU9250] WHO_AM_I mismatch: 0x{:02X}".format(who))
                return
            if who != _WHO_AM_I_EXPECTED:
                print("[MPU9250] WHO_AM_I 0x{:02X} (MPU-6500 core, no onboard mag expected)".format(who))
        except Exception as e:
            print("[MPU9250] not found:", repr(e))
            return

        try:
            # Wake the chip — it powers up with the sleep bit set.
            self._i2c.writeto_mem(self._addr, _REG_PWR_MGMT_1, bytes([0x00]))
            time.sleep_ms(10)
        except Exception as e:
            print("[MPU9250] wake write failed:", repr(e))
            return

        self.is_present = True
        print("[MPU9250] ready at 0x{:02X}".format(self._addr))

        try:
            # Enable bypass so the AK8963 answers directly on the shared bus.
            self._i2c.writeto_mem(self._addr, _REG_INT_PIN_CFG, bytes([0x02]))
            time.sleep_ms(10)
            m = AK8963(self._i2c)
            if m.is_present:
                self.mag = m
            else:
                print("[MPU9250] AK8963 bypass enabled but magnetometer not found")
        except Exception as e:
            print("[MPU9250] bypass enable failed:", repr(e))

    def read_accel(self):
        """Return (ax, ay, az) in g (default +-2g range), or None on error."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _REG_ACCEL_XOUT_H, 6)
        except Exception:
            return None
        x = _s16(d[1], d[0])
        y = _s16(d[3], d[2])
        z = _s16(d[5], d[4])
        s = 1.0 / 16384.0
        return (x * s, y * s, z * s)

    def read_gyro(self):
        """Return (gx, gy, gz) in deg/s (default +-250 dps range), or None on error."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _REG_GYRO_XOUT_H, 6)
        except Exception:
            return None
        x = _s16(d[1], d[0])
        y = _s16(d[3], d[2])
        z = _s16(d[5], d[4])
        s = 1.0 / 131.0
        return (x * s, y * s, z * s)

    def heading(self, declination_deg=0.0):
        """Return magnetic heading in degrees [0, 360), or None if the
        AK8963 wasn't found behind bypass, or on read error."""
        if self.mag is None:
            return None
        return self.mag.heading(declination_deg=declination_deg)


class AK8963:
    """Driver for the AK8963 magnetometer inside the MPU-9250, reachable
    only once bypass mode has been enabled on the parent chip."""

    is_present = False

    def __init__(self, i2c, addr=_AK_ADDR):
        self._i2c = i2c
        self._addr = int(addr)

        try:
            wia = self._i2c.readfrom_mem(self._addr, _AK_REG_WIA, 1)[0]
            if wia != _AK_WIA_EXPECTED:
                print("[AK8963] WIA mismatch: 0x{:02X}".format(wia))
                return
        except Exception as e:
            print("[AK8963] not found:", repr(e))
            return

        try:
            # Continuous measurement mode 2 (100 Hz), 16-bit output.
            self._i2c.writeto_mem(self._addr, _AK_REG_CNTL1, bytes([0x16]))
            time.sleep_ms(10)
        except Exception as e:
            print("[AK8963] CNTL1 write failed:", repr(e))
            return

        self.is_present = True
        print("[AK8963] ready at 0x{:02X}".format(self._addr))

    def read_raw(self):
        """Return (x, y, z) as signed 16-bit counts, or None on error.
        Register order: X_LSB, X_MSB, Y_LSB, Y_MSB, Z_LSB, Z_MSB."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _AK_REG_HXL, 6)
            # ST2 must be read to release the data-ready latch for the next sample.
            self._i2c.readfrom_mem(self._addr, _AK_REG_ST2, 1)
        except Exception:
            return None
        x = _s16(d[0], d[1])
        y = _s16(d[2], d[3])
        z = _s16(d[4], d[5])
        return (x, y, z)

    def heading(self, declination_deg=0.0):
        """Return magnetic heading in degrees [0, 360). Returns None on error.
        See module docstring — axis convention not yet bench-verified."""
        raw = self.read_raw()
        if raw is None:
            return None
        x, y, _z = raw
        h = math.atan2(y, x) * (180.0 / math.pi) + float(declination_deg)
        return h % 360.0
