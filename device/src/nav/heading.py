# src/nav/heading.py — heading source abstraction
#
# Wraps the magnetometer behind a stable interface so the planned
# ICM-20948 9-axis IMU (complementary filter: heading = 0.98 x (heading +
# gyro_yaw_rate x dt) + 0.02 x magnetometer_heading) can drop in without
# touching NavController or any screen.
#
# NOTE for the ICM-20948 integration: its default I2C address 0x68
# collides with the DS3231 RTC on the shared bus — strap AD0 high (0x69)
# or relocate the RTC. See docs/pending_nav_dev.md.


class HeadingSource:
    """Tilt-naive magnetometer heading (QMC5883L primary, HMC5883L fallback)."""

    def __init__(self, i2c=None, mag=None, offset_deg=0):
        self._i2c = i2c
        self._mag = mag                      # pre-shared driver (optional)
        self._probed = mag is not None
        self._offset_deg = float(offset_deg)

    def _get_mag(self):
        if self._mag is not None:
            return self._mag
        if self._probed or self._i2c is None:
            return None
        self._probed = True
        try:
            from src.drivers.hmc5883l_qmc5883l import QMC5883L
            m = QMC5883L(self._i2c)
            if m.is_present:
                self._mag = m
                return self._mag
        except Exception:
            pass
        try:
            from src.drivers.hmc5883l_qmc5883l import HMC5883L
            m = HMC5883L(self._i2c)
            if m.is_present:
                self._mag = m
        except Exception:
            pass
        return self._mag

    def heading_deg(self):
        """Fused heading in degrees [0, 360), or None if unavailable."""
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

    def is_stable(self):
        """Heading-stability gate for BOOT→ACQUIRE. Magnetometer-only
        readings have no drift to settle, so present == stable for now;
        the complementary filter replaces this with a real check."""
        return self.heading_deg() is not None
