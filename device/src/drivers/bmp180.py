# src/drivers/bmp180.py
# MicroPython driver for the Bosch BMP180 barometric pressure + temperature
# sensor, as mounted on the GY-87 10DOF breakout.
#
#   I2C address : 0x77 (fixed). NOTE: 0x77 is also the BME280's SDO=HIGH
#                 alternate address — always check the chip-ID register
#                 (0xD0) before deciding which driver owns 0x77:
#                 0x55 = BMP180, 0x58 = BMP280, 0x60 = BME280.
#   Calibration : 11 signed/unsigned 16-bit words at 0xAA..0xBF, read once.
#   Measurement : write CTRL_MEAS (0xF4) = 0x2E for temperature (4.5 ms),
#                 0x34 | (oss << 6) for pressure (4.5 / 7.5 / 13.5 / 25.5 ms),
#                 then read OUT_MSB.. (0xF6).
#
# Compensation follows the Bosch datasheet integer algorithm verbatim so it
# stays exact on MicroPython's 64-bit ints.

import time

_BMP_ADDR       = const(0x77)
_REG_CHIP_ID    = const(0xD0)
_REG_CAL_START  = const(0xAA)
_REG_CTRL_MEAS  = const(0xF4)
_REG_OUT_MSB    = const(0xF6)
_CHIP_ID        = const(0x55)
_CMD_TEMP       = const(0x2E)
_CMD_PRESS      = const(0x34)

SEA_LEVEL_HPA = 1013.25


class BMP180:
    """Driver for the BMP180 barometer.

    Usage::

        from src.drivers.bmp180 import BMP180
        baro = BMP180(i2c)
        if baro.is_present:
            t_c, p_hpa = baro.read()
            alt_m = baro.altitude_m(p_hpa)
    """

    is_present = False

    def __init__(self, i2c, addr=_BMP_ADDR, oss=1):
        self._i2c = i2c
        self._addr = int(addr)
        self._oss = max(0, min(3, int(oss)))
        self._b5 = 0

        try:
            cid = self._i2c.readfrom_mem(self._addr, _REG_CHIP_ID, 1)[0]
        except Exception as e:
            print("[BMP180] not found at 0x{:02X}: {}".format(self._addr, repr(e)))
            return
        if cid != _CHIP_ID:
            print("[BMP180] chip ID 0x{:02X} is not a BMP180 (0x55)".format(cid))
            return

        try:
            c = self._i2c.readfrom_mem(self._addr, _REG_CAL_START, 22)
        except Exception as e:
            print("[BMP180] calibration read failed:", repr(e))
            return

        def s16(i):
            v = (c[i] << 8) | c[i + 1]
            return v - 65536 if v >= 32768 else v

        def u16(i):
            return (c[i] << 8) | c[i + 1]

        self._ac1 = s16(0);  self._ac2 = s16(2);  self._ac3 = s16(4)
        self._ac4 = u16(6);  self._ac5 = u16(8);  self._ac6 = u16(10)
        self._b1  = s16(12); self._b2  = s16(14)
        self._mb  = s16(16); self._mc  = s16(18); self._md  = s16(20)
        if self._ac5 == 0 or self._ac4 == 0 or self._md == 0:
            print("[BMP180] calibration looks blank")
            return

        self.is_present = True
        print("[BMP180] ready at 0x{:02X}, oss={}".format(self._addr, self._oss))

    # ------------------------------------------------------------------

    def _cmd(self, cmd, wait_ms):
        self._i2c.writeto_mem(self._addr, _REG_CTRL_MEAS, bytes([cmd]))
        time.sleep_ms(wait_ms)

    def _read_ut(self):
        self._cmd(_CMD_TEMP, 5)
        d = self._i2c.readfrom_mem(self._addr, _REG_OUT_MSB, 2)
        return (d[0] << 8) | d[1]

    def _read_up(self):
        self._cmd(_CMD_PRESS | (self._oss << 6), (5, 8, 14, 26)[self._oss])
        d = self._i2c.readfrom_mem(self._addr, _REG_OUT_MSB, 3)
        return ((d[0] << 16) | (d[1] << 8) | d[2]) >> (8 - self._oss)

    def _temp_from_ut(self, ut):
        x1 = ((ut - self._ac6) * self._ac5) >> 15
        x2 = (self._mc << 11) // (x1 + self._md)
        self._b5 = x1 + x2
        return ((self._b5 + 8) >> 4) / 10.0

    def _press_from_up(self, up):
        oss = self._oss
        b6 = self._b5 - 4000
        x1 = (self._b2 * ((b6 * b6) >> 12)) >> 11
        x2 = (self._ac2 * b6) >> 11
        x3 = x1 + x2
        b3 = (((self._ac1 * 4 + x3) << oss) + 2) >> 2
        x1 = (self._ac3 * b6) >> 13
        x2 = (self._b1 * ((b6 * b6) >> 12)) >> 16
        x3 = ((x1 + x2) + 2) >> 2
        b4 = (self._ac4 * (x3 + 32768)) >> 15
        b7 = (up - b3) * (50000 >> oss)
        if b7 < 0x80000000:
            p = (b7 * 2) // b4
        else:
            p = (b7 // b4) * 2
        x1 = (p >> 8) * (p >> 8)
        x1 = (x1 * 3038) >> 16
        x2 = (-7357 * p) >> 16
        p = p + ((x1 + x2 + 3791) >> 4)
        return p / 100.0

    # ------------------------------------------------------------------

    def read_temp_c(self):
        """Temperature in °C (0.1 °C resolution), or None on error."""
        if not self.is_present:
            return None
        try:
            return self._temp_from_ut(self._read_ut())
        except Exception:
            return None

    def read_pressure_hpa(self):
        """Pressure in hPa. Runs a temperature cycle first (the compensation
        needs it). None on error."""
        if not self.is_present:
            return None
        try:
            self._temp_from_ut(self._read_ut())
            return self._press_from_up(self._read_up())
        except Exception:
            return None

    def read(self):
        """Return (temp_c, pressure_hpa) from one measurement cycle, or None."""
        if not self.is_present:
            return None
        try:
            t = self._temp_from_ut(self._read_ut())
            p = self._press_from_up(self._read_up())
            return (t, p)
        except Exception:
            return None

    @staticmethod
    def altitude_m(pressure_hpa, sea_level_hpa=SEA_LEVEL_HPA):
        """Barometric altitude in metres from the international standard
        atmosphere. Absolute value drifts with the weather (tens of metres);
        relative changes over minutes are good to ~1 m."""
        try:
            return 44330.0 * (1.0 - (float(pressure_hpa) / float(sea_level_hpa)) ** 0.1903)
        except Exception:
            return None
