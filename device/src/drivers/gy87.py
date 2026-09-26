# src/drivers/gy87.py
# Composite driver for the GY-87 10DOF breakout: MPU6050 (accel + gyro),
# HMC5883L or QMC5883L magnetometer on the MPU6050's auxiliary I2C bus, and
# a BMP180 barometer on the main bus.
#
#   MPU6050  : 0x68 (factory, AD0 low) on I2C_EXT                src/drivers/mpu6050.py
#              (0x69 if a board has AD0 strapped high — pass imu_addr)
#   Mag      : 0x0D (QMC5883L), 0x1E (HMC5883L) or 0x2C (QMC5883P),
#              visible only after the MPU6050's I2C bypass is enabled
#                                                                src/drivers/hmc5883l_qmc5883l.py
#   BMP180   : 0x77                                               src/drivers/bmp180.py
#
# The board sits on the I2C_EXT bus (src/hal/board_xiao_esp32_s3.py) because
# 0x68 is the DS3231 RTC on I2C_SYS. One GY87 instance is built during boot
# (device/main.py step_imu) on that bus and shared
# by NavController's HeadingSource, the compass screen and the telemetry
# scheduler. Nothing else should probe these chips.
#
# Partial boards are fine: .imu / .mag / .baro are each None when absent.
# is_present tracks the MPU6050 only — a bare GY-271 compass with no IMU is
# handled by HeadingSource's own fallback ladder, not by this class.

import time


class GY87:
    is_present = False

    def __init__(self, i2c, imu_addr=0x68):
        self._i2c = i2c
        self.imu = None
        self.mag = None
        self.baro = None
        self.mag_name = None

        try:
            from src.drivers.mpu6050 import MPU6050
            m = MPU6050(i2c, addr=imu_addr)
            if m.is_present:
                self.imu = m
                self.is_present = True
        except Exception as e:
            print("[GY87] MPU6050 probe failed:", repr(e))

        if self.imu is not None and self.imu.enable_bypass():
            time.sleep_ms(10)
            try:
                from src.drivers.hmc5883l_qmc5883l import QMC5883L, HMC5883L, QMC5883P
                for cls, name in ((QMC5883L, "QMC5883L"),
                                  (HMC5883L, "HMC5883L"),
                                  (QMC5883P, "QMC5883P")):
                    mg = cls(i2c)
                    if mg.is_present:
                        self.mag, self.mag_name = mg, name
                        break
            except Exception as e:
                print("[GY87] mag probe failed:", repr(e))
            if self.mag is None:
                print("[GY87] bypass on but no magnetometer at 0x0D/0x1E/0x2C")

        try:
            from src.drivers.bmp180 import BMP180
            b = BMP180(i2c)
            if b.is_present:
                self.baro = b
        except Exception as e:
            print("[GY87] BMP180 probe failed:", repr(e))

    # ------------------------------------------------------------------
    # Passthroughs
    # ------------------------------------------------------------------

    def heading(self, declination_deg=0.0):
        """Raw magnetic heading [0, 360) from the on-board mag, or None."""
        if self.mag is None:
            return None
        try:
            return self.mag.heading(declination_deg=declination_deg)
        except Exception:
            return None

    def read_accel(self):
        return self.imu.read_accel() if self.imu is not None else None

    def read_gyro(self):
        return self.imu.read_gyro() if self.imu is not None else None

    def pitch_roll(self):
        return self.imu.pitch_roll() if self.imu is not None else None

    def baro_read(self):
        """Return (temp_c, pressure_hpa, altitude_m) or None."""
        if self.baro is None:
            return None
        r = self.baro.read()
        if r is None:
            return None
        t, p = r
        return (t, p, self.baro.altitude_m(p))

    def summary(self):
        """Short chip inventory for the boot line, e.g. 'MPU6050 + QMC5883L + BMP180'."""
        parts = []
        if self.imu is not None:
            parts.append("MPU6050")
        if self.mag is not None:
            parts.append(self.mag_name)
        if self.baro is not None:
            parts.append("BMP180")
        return " + ".join(parts) if parts else "nothing"
