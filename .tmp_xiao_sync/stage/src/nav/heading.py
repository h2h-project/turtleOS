# src/nav/heading.py — heading source abstraction
#
# Wraps the magnetometer behind a stable interface so a fused heading
# (Phase S TODO: complementary filter — heading = 0.98 x (heading +
# gyro_yaw_rate x dt) + 0.02 x magnetometer_heading, using the MPU-9250's
# gyro) can drop in without touching NavController or any screen.
#
# As of Phase 0, the backing chip is the MPU-9250 (0x69) with its AK8963
# magnetometer exposed via I2C bypass at 0x0C — see
# src/drivers/mpu9250.py and docs/navigation_roadmap.md.


class HeadingSource:
    """Tilt-naive magnetometer heading (MPU-9250 / AK8963)."""

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
            from src.drivers.mpu9250 import MPU9250
            m = MPU9250(self._i2c)
            if m.is_present and m.mag is not None:
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
