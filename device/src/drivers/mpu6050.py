# src/drivers/mpu6050.py
# MicroPython driver for the MPU6050 6-DOF IMU (3-axis gyro + 3-axis accel),
# as mounted on the GY-87 10DOF breakout used by turtleOS 2.4+.
#
# MPU6050:
#   I2C address : 0x68 factory default (AD0 low), 0x69 with AD0 high.
#                 0x68 is ALSO the DS3231 RTC's address on I2C_SYS, which is
#                 why the GY-87 lives on I2C_EXT (src/hal/board_xiao_esp32_s3.py)
#                 and why device/main.py only ever probes 0x68 for an IMU on
#                 that bus. The constructor reads WHO_AM_I before writing
#                 anything, so a wrong-bus probe is a harmless read.
#   WHO_AM_I    : reg 0x75, expect 0x68 (the WHO_AM_I value does NOT change
#                 with AD0). Clone silicon sometimes reports 0x70..0x73; those
#                 are accepted with a printed note.
#   Powers up in sleep mode — PWR_MGMT_1 (0x6B) must be cleared before any
#   accel/gyro register reads return real data.
#
# Auxiliary I2C bus / bypass:
#   On the GY-87 the HMC5883L/QMC5883L magnetometer hangs off the MPU6050's
#   auxiliary I2C pins, not the main bus. enable_bypass() clears I2C_MST_EN
#   (USER_CTRL 0x6A) and sets I2C_BYPASS_EN (INT_PIN_CFG 0x37) so the mag
#   answers on the main bus at its own address (0x0D / 0x1E). Both writes are
#   needed — bypass is ignored while the master is enabled.
#
# Axis convention for pitch_roll() is NOT bench-verified against the board's
# mounting in the hull. Confirm sign/axis with tests/gy87_bench.py before
# relying on it for control (Phase S).

import time
import math

_MPU_ADDR          = const(0x68)
_REG_CONFIG        = const(0x1A)
_REG_GYRO_CONFIG   = const(0x1B)
_REG_ACCEL_CONFIG  = const(0x1C)
_REG_INT_PIN_CFG   = const(0x37)
_REG_ACCEL_XOUT_H  = const(0x3B)
_REG_TEMP_OUT_H    = const(0x41)
_REG_GYRO_XOUT_H   = const(0x43)
_REG_USER_CTRL     = const(0x6A)
_REG_PWR_MGMT_1    = const(0x6B)
_REG_WHO_AM_I      = const(0x75)

_WHO_AM_I_EXPECTED = const(0x68)
# 0x70..0x73: MPU-6500 / 9250 / 6050-clone signatures seen on cheap GY-87s.
# Register map for accel/gyro/bypass is identical, so accept them.
_WHO_AM_I_ACCEPTED = (0x68, 0x70, 0x71, 0x72, 0x73)

_ACCEL_LSB_PER_G   = 16384.0   # ±2 g full scale
_GYRO_LSB_PER_DPS  = 131.0     # ±250 dps full scale


def _s16(hi, lo):
    v = (hi << 8) | lo
    return v - 65536 if v >= 32768 else v


class MPU6050:
    """Driver for the MPU6050 accel/gyro.

    Usage::

        from src.hal.board import init_i2c_ext
        from src.drivers.mpu6050 import MPU6050

        imu = MPU6050(init_i2c_ext())
        if imu.is_present:
            ax, ay, az = imu.read_accel()      # g
            gx, gy, gz = imu.read_gyro()       # deg/s
            pitch, roll = imu.pitch_roll()     # deg
            imu.enable_bypass()                # expose the aux-bus magnetometer
    """

    is_present = False

    def __init__(self, i2c, addr=_MPU_ADDR):
        self._i2c = i2c
        self._addr = int(addr)
        self.who_am_i = None
        self.bypass = False

        # Read-only identity check first: nothing is written until the chip
        # has answered WHO_AM_I, so probing the wrong bus can't disturb an RTC.
        try:
            who = self._i2c.readfrom_mem(self._addr, _REG_WHO_AM_I, 1)[0]
        except Exception as e:
            print("[MPU6050] not found at 0x{:02X}: {}".format(self._addr, repr(e)))
            return
        self.who_am_i = who
        if who not in _WHO_AM_I_ACCEPTED:
            print("[MPU6050] WHO_AM_I mismatch: 0x{:02X}".format(who))
            return
        if who != _WHO_AM_I_EXPECTED:
            print("[MPU6050] WHO_AM_I 0x{:02X} (clone/6500-class silicon, accepted)".format(who))

        try:
            # Wake, clock from gyro X PLL (steadier than the internal oscillator).
            self._i2c.writeto_mem(self._addr, _REG_PWR_MGMT_1, bytes([0x01]))
            time.sleep_ms(10)
            # DLPF ~44 Hz accel / 42 Hz gyro — kills servo/wave chatter.
            self._i2c.writeto_mem(self._addr, _REG_CONFIG, bytes([0x03]))
            self._i2c.writeto_mem(self._addr, _REG_GYRO_CONFIG, bytes([0x00]))   # ±250 dps
            self._i2c.writeto_mem(self._addr, _REG_ACCEL_CONFIG, bytes([0x00]))  # ±2 g
            time.sleep_ms(10)
        except Exception as e:
            print("[MPU6050] init write failed:", repr(e))
            return

        self.is_present = True
        print("[MPU6050] ready at 0x{:02X}".format(self._addr))

    # ------------------------------------------------------------------
    # Aux bus
    # ------------------------------------------------------------------

    def enable_bypass(self):
        """Expose the auxiliary-bus magnetometer on the main I2C bus.
        Returns True on success. Safe to call repeatedly."""
        if not self.is_present:
            return False
        try:
            uc = self._i2c.readfrom_mem(self._addr, _REG_USER_CTRL, 1)[0]
            self._i2c.writeto_mem(self._addr, _REG_USER_CTRL, bytes([uc & ~0x20 & 0xFF]))
            self._i2c.writeto_mem(self._addr, _REG_INT_PIN_CFG, bytes([0x02]))
            time.sleep_ms(10)
            self.bypass = True
            return True
        except Exception as e:
            print("[MPU6050] bypass enable failed:", repr(e))
            return False

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------

    def _read3(self, reg):
        d = self._i2c.readfrom_mem(self._addr, reg, 6)
        return (_s16(d[0], d[1]), _s16(d[2], d[3]), _s16(d[4], d[5]))

    def read_accel(self):
        """Return (ax, ay, az) in g (±2 g range), or None on error."""
        try:
            x, y, z = self._read3(_REG_ACCEL_XOUT_H)
        except Exception:
            return None
        s = 1.0 / _ACCEL_LSB_PER_G
        return (x * s, y * s, z * s)

    def read_gyro(self):
        """Return (gx, gy, gz) in deg/s (±250 dps range), or None on error."""
        try:
            x, y, z = self._read3(_REG_GYRO_XOUT_H)
        except Exception:
            return None
        s = 1.0 / _GYRO_LSB_PER_DPS
        return (x * s, y * s, z * s)

    def read_temp_c(self):
        """Die temperature in °C — diagnostics only, runs warm. None on error."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _REG_TEMP_OUT_H, 2)
        except Exception:
            return None
        return _s16(d[0], d[1]) / 340.0 + 36.53

    def pitch_roll(self):
        """Return (pitch_deg, roll_deg) from the accelerometer's gravity
        vector, or None on error. Pitch: nose up positive. Roll: starboard
        down positive. Only meaningful when the board is not accelerating;
        see the module header on axis verification."""
        a = self.read_accel()
        if a is None:
            return None
        ax, ay, az = a
        try:
            pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
            roll = math.atan2(ay, az)
        except Exception:
            return None
        return (math.degrees(pitch), math.degrees(roll))
