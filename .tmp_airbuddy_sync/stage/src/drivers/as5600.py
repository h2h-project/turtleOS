# src/drivers/as5600.py
# MicroPython driver for AS5600 12-bit magnetic rotary position sensor
#
# I2C address : 0x36 (fixed)
# STATUS reg  : 0x0B — bit3=MD (magnet detected), bit4=ML (too weak), bit5=MH (too strong)
# ANGLE regs  : 0x0E-0x0F — 12-bit filtered output (0..4095 → 0..360°)
# RAW_ANGLE   : 0x0C-0x0D — 12-bit raw output
# MAGNITUDE   : 0x1B-0x1C — field magnitude (AGC output)

_ADDR        = const(0x36)
_REG_STATUS  = const(0x0B)
_REG_RAW_H   = const(0x0C)
_REG_ANGLE_H = const(0x0E)
_REG_MAG_H   = const(0x1B)


class AS5600:
    """Driver for AS5600 12-bit magnetic rotary position sensor.

    Usage::

        from machine import I2C, Pin
        from src.drivers.as5600 import AS5600

        i2c = I2C(0, scl=Pin(6), sda=Pin(5), freq=400000)
        sensor = AS5600(i2c)
        if sensor.is_present:
            deg = sensor.angle()   # 0.0 – 360.0
    """

    is_present = False

    def __init__(self, i2c, addr=_ADDR):
        self._i2c  = i2c
        self._addr = int(addr)
        try:
            self._i2c.readfrom_mem(self._addr, _REG_STATUS, 1)
            self.is_present = True
        except Exception:
            pass

    def status(self):
        """Return dict: md (detected), mh (too strong), ml (too weak)."""
        try:
            st = self._i2c.readfrom_mem(self._addr, _REG_STATUS, 1)[0]
            return {
                "md": bool(st & 0x08),
                "mh": bool(st & 0x20),
                "ml": bool(st & 0x10),
            }
        except Exception:
            return {"md": False, "mh": False, "ml": False}

    def angle(self):
        """Return filtered angle in degrees [0.0, 360.0), or None on error."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _REG_ANGLE_H, 2)
            raw = ((d[0] & 0x0F) << 8) | d[1]
            return raw * (360.0 / 4096.0)
        except Exception:
            return None

    def raw_angle(self):
        """Return raw 12-bit angle (0–4095), or None on error."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _REG_RAW_H, 2)
            return ((d[0] & 0x0F) << 8) | d[1]
        except Exception:
            return None

    def magnitude(self):
        """Return field magnitude (0–4095), or None on error."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _REG_MAG_H, 2)
            return ((d[0] & 0x0F) << 8) | d[1]
        except Exception:
            return None
