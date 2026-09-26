# src/nav/heading.py — heading source abstraction
#
# Wraps the magnetometer behind a stable interface so a fused heading
# (Phase S TODO: complementary filter — heading = 0.98 x (heading +
# gyro_yaw_rate x dt) + 0.02 x magnetometer_heading, using the MPU6050's
# gyro exposed via `imu`) can drop in without touching NavController or
# any screen.
#
# Hardware (turtleOS 2.4+): the GY-87 10DOF board — MPU6050 at 0x69 with a
# QMC5883L/HMC5883L on its auxiliary bus (src/drivers/gy87.py) — is probed
# once at boot and injected here as `mag` + `imu`. With no GY-87, the
# fallback ladder finds a standalone GY-271 compass (0x0D / 0x1E) on the
# main bus. Heading is magnetometer-only (tilt-naive) in both cases.


class HeadingSource:
    """Tilt-naive magnetometer heading (GY-87 on-board mag, else standalone QMC5883L/HMC5883L)."""

    def __init__(self, i2c=None, mag=None, imu=None, offset_deg=0):
        self._i2c = i2c
        self._mag = mag                      # pre-shared magnetometer driver (optional)
        self._imu = imu                      # GY87 / MPU6050-like: pitch_roll(), read_gyro() (optional)
        self._probed = mag is not None
        self._offset_deg = float(offset_deg)

    def _get_mag(self):
        if self._mag is not None:
            return self._mag
        if self._probed or self._i2c is None:
            return None
        self._probed = True
        try:
            from src.drivers.hmc5883l_qmc5883l import QMC5883L, HMC5883L, QMC5883P
            m = QMC5883L(self._i2c)
            if not m.is_present:
                m = HMC5883L(self._i2c)
            if not m.is_present:
                m = QMC5883P(self._i2c)
            if m.is_present:
                self._mag = m
        except Exception:
            pass
        return self._mag

    def heading_deg(self):
        """Heading in degrees [0, 360) with compass_offset_deg applied, or
        None if unavailable."""
        mag = self._get_mag()
        if mag is None or not getattr(mag, "is_present", False):
            return None
        try:
            raw = mag.heading()
            if raw is None:
                return None
            return (raw + self._offset_deg) % 360.0
        except Exception:
            return None

    def pitch_roll(self):
        """(pitch_deg, roll_deg) from the IMU accelerometer, or None when no
        IMU was injected. Axis convention: see src/drivers/mpu6050.py."""
        if self._imu is None:
            return None
        try:
            return self._imu.pitch_roll()
        except Exception:
            return None

    def has_imu(self):
        return self._imu is not None

    def is_stable(self):
        """Heading-stability gate for BOOT→ACQUIRE. Magnetometer-only
        readings have no drift to settle, so present == stable for now;
        the complementary filter replaces this with a real check."""
        return self.heading_deg() is not None
