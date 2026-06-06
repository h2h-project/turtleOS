# src/drivers/hmc5883l_qmc5883l.py
# MicroPython drivers for HMC5883L and QMC5883L 3-axis magnetometers
#
# HMC5883L (genuine):
#   I2C address : 0x1E (fixed)
#   ID registers: 0x0A–0x0C must read b'H43'
#   Data order  : X MSB, X LSB, Z MSB, Z LSB, Y MSB, Y LSB  (Z before Y!)
#
# QMC5883L (GY-271 clone):
#   I2C address : 0x0D (fixed)
#   Data order  : X LSB, X MSB, Y LSB, Y MSB, Z LSB, Z MSB  (little-endian, XYZ)
#
# Both: heading = atan2(Y, X) — hold sensor flat, X pointing North = 0°

import time
import math

# --- HMC5883L constants ---
_HMC_ADDR    = const(0x1E)
_HMC_REG_CRA    = const(0x00)
_HMC_REG_CRB    = const(0x01)
_HMC_REG_MODE   = const(0x02)
_HMC_REG_DATA   = const(0x03)
_HMC_REG_STATUS = const(0x09)
_HMC_REG_ID_A   = const(0x0A)

# GN[2:0] → (crb_bits, range_gauss, lsb_per_gauss)
_HMC_GAIN = [
    (0b000, 0.88, 1370),
    (0b001, 1.3,  1090),   # default (index 1)
    (0b010, 1.9,   820),
    (0b011, 2.5,   660),
    (0b100, 4.0,   440),
    (0b101, 4.7,   390),
    (0b110, 5.6,   330),
    (0b111, 8.1,   230),
]
_HMC_RATE_HZ = [0.75, 1.5, 3.0, 7.5, 15.0, 30.0, 75.0]

# --- QMC5883L constants ---
_QMC_ADDR     = const(0x0D)
_QMC_REG_DATA  = const(0x00)
_QMC_REG_STAT  = const(0x06)
_QMC_REG_CTRL1 = const(0x09)
_QMC_REG_CTRL2 = const(0x0A)
_QMC_REG_RST   = const(0x0B)
_QMC_REG_ID    = const(0x0D)


class HMC5883L:
    is_present = False

    def __init__(self, i2c, addr=_HMC_ADDR, gain=1, data_rate=4):
        self._i2c  = i2c
        self._addr = int(addr)
        self._gain = max(0, min(7, int(gain)))
        self._lsb  = float(_HMC_GAIN[self._gain][2])

        try:
            ids = self._i2c.readfrom_mem(self._addr, _HMC_REG_ID_A, 3)
            if ids != b'H43':
                print("[HMC5883L] ID mismatch:", ids)
                return
        except Exception as e:
            print("[HMC5883L] not found:", repr(e))
            return

        rate = max(0, min(6, int(data_rate)))
        cra = (0b11 << 5) | (rate << 2) | 0b00
        crb = _HMC_GAIN[self._gain][0] << 5
        try:
            self._i2c.writeto_mem(self._addr, _HMC_REG_CRA,  bytes([cra]))
            self._i2c.writeto_mem(self._addr, _HMC_REG_CRB,  bytes([crb]))
            self._i2c.writeto_mem(self._addr, _HMC_REG_MODE, bytes([0x00]))
            time.sleep_ms(10)
        except Exception as e:
            print("[HMC5883L] init write failed:", repr(e))
            return

        self.is_present = True
        print("[HMC5883L] ready, gain=±{:.1f}Ga, rate={}Hz".format(
            _HMC_GAIN[self._gain][1], _HMC_RATE_HZ[rate]))

    def read_raw(self):
        """Return (x, y, z) as signed ADC counts, or None on error.
        Register order from chip: X, Z, Y (Z before Y)."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _HMC_REG_DATA, 6)
        except Exception:
            return None
        def s16(hi, lo):
            v = (hi << 8) | lo
            return v - 65536 if v >= 32768 else v
        x = s16(d[0], d[1])
        z = s16(d[2], d[3])
        y = s16(d[4], d[5])
        if x in (-4096, 4096) or y in (-4096, 4096) or z in (-4096, 4096):
            return None
        return (x, y, z)

    def read_gauss(self):
        """Return (x, y, z) in Gauss, or None on error."""
        raw = self.read_raw()
        if raw is None:
            return None
        s = 1.0 / self._lsb
        return (raw[0] * s, raw[1] * s, raw[2] * s)

    def heading(self, declination_deg=0.0):
        """Return magnetic heading in degrees [0, 360). Returns None on error."""
        g = self.read_gauss()
        if g is None:
            return None
        x, y, _ = g
        h = math.atan2(y, x) * (180.0 / math.pi) + float(declination_deg)
        return h % 360.0

    def is_data_ready(self):
        try:
            status = self._i2c.readfrom_mem(self._addr, _HMC_REG_STATUS, 1)[0]
            return bool(status & 0x01)
        except Exception:
            return False


class QMC5883L:
    is_present = False

    def __init__(self, i2c, addr=_QMC_ADDR):
        self._i2c  = i2c
        self._addr = int(addr)

        try:
            chip_id = self._i2c.readfrom_mem(self._addr, _QMC_REG_ID, 1)[0]
            if chip_id != 0xFF:
                print("[QMC5883L] unexpected chip ID: 0x{:02X}".format(chip_id))
        except Exception as e:
            print("[QMC5883L] not found:", repr(e))
            return

        try:
            self._i2c.writeto_mem(self._addr, _QMC_REG_RST, bytes([0x01]))
        except Exception as e:
            print("[QMC5883L] RST write failed:", repr(e))
            return

        # OSR=512, RNG=2G, ODR=50Hz, continuous mode
        try:
            self._i2c.writeto_mem(self._addr, _QMC_REG_CTRL1, bytes([0x05]))
            time.sleep_ms(10)
        except Exception as e:
            print("[QMC5883L] CTRL1 write failed:", repr(e))
            return

        self.is_present = True
        print("[QMC5883L] ready at 0x0D, 50Hz, ±2G")

    def read_raw(self):
        """Return (x, y, z) as signed 16-bit counts, or None on error.
        Register order: X_LSB, X_MSB, Y_LSB, Y_MSB, Z_LSB, Z_MSB."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _QMC_REG_DATA, 6)
        except Exception:
            return None
        def s16(lo, hi):
            v = (hi << 8) | lo
            return v - 65536 if v >= 32768 else v
        x = s16(d[0], d[1])
        y = s16(d[2], d[3])
        z = s16(d[4], d[5])
        return (x, y, z)

    def heading(self, declination_deg=0.0):
        """Return magnetic heading in degrees [0, 360). Returns None on error."""
        raw = self.read_raw()
        if raw is None:
            return None
        x, y, _ = raw
        h = math.atan2(y, x) * (180.0 / math.pi) + float(declination_deg)
        return h % 360.0

    def is_data_ready(self):
        try:
            return bool(self._i2c.readfrom_mem(self._addr, _QMC_REG_STAT, 1)[0] & 0x01)
        except Exception:
            return False
