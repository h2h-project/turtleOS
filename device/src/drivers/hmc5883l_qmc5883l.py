# src/drivers/hmc5883l_qmc5883l.py
# MicroPython drivers for HMC5883L / QMC5883L / QMC5883P 3-axis magnetometers
#
# Two roles in turtleOS 2.4+:
#   * the magnetometer on the GY-87 10DOF board, which sits on the MPU6050's
#     auxiliary I2C bus and appears at 0x0D/0x1E/0x2C once bypass is enabled
#     (src/drivers/gy87.py does that and owns the instance);
#   * a standalone GY-271 compass wired straight to the bus, found by
#     HeadingSource's fallback ladder (src/nav/heading.py) when no GY-87 is
#     present.
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
# QMC5883P (newer QST part on GY-87/GY-271 clones — NOT register-compatible
#   with the QMC5883L despite the name):
#   I2C address : 0x2C (fixed)
#   Chip-ID reg : 0x00 must read 0x80
#   Data regs   : 0x01..0x06 — X LSB, X MSB, Y LSB, Y MSB, Z LSB, Z MSB
#   Must-write  : 0x29 <- 0x06 (axis sign), then CTRL2 (0x0B), CTRL1 (0x0A)
#
# All: heading = atan2(Y, X) — hold sensor flat, X pointing North = 0°

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

# --- QMC5883P constants (distinct chip, distinct register map) ---
_QMCP_ADDR      = const(0x2C)
_QMCP_REG_ID    = const(0x00)   # reads 0x80
_QMCP_REG_DATA  = const(0x01)   # X_LSB, X_MSB, Y_LSB, Y_MSB, Z_LSB, Z_MSB
_QMCP_REG_STAT  = const(0x09)   # bit0 = DRDY
_QMCP_REG_CTRL1 = const(0x0A)
_QMCP_REG_CTRL2 = const(0x0B)
_QMCP_REG_SIGN  = const(0x29)


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


class QMC5883P:
    """QST QMC5883P — the 0x2C part fitted to newer GY-87/GY-271 clones.

    Same public surface as QMC5883L (is_present, read_raw, heading,
    is_data_ready) so HeadingSource / GY87 can use either interchangeably.
    """

    is_present = False

    def __init__(self, i2c, addr=_QMCP_ADDR):
        self._i2c  = i2c
        self._addr = int(addr)

        try:
            chip_id = self._i2c.readfrom_mem(self._addr, _QMCP_REG_ID, 1)[0]
            if chip_id != 0x80:
                print("[QMC5883P] unexpected chip ID: 0x{:02X}".format(chip_id))
                return
        except Exception as e:
            print("[QMC5883P] not found:", repr(e))
            return

        try:
            # 0x29 <- 0x06 : axis-sign register (datasheet-mandated write)
            self._i2c.writeto_mem(self._addr, _QMCP_REG_SIGN, bytes([0x06]))
            # CTRL2 (0x0B): set/reset mode on, ±8 G range
            self._i2c.writeto_mem(self._addr, _QMCP_REG_CTRL2, bytes([0x08]))
            # CTRL1 (0x0A) = 0b1100_0011: continuous mode, ODR 10 Hz,
            # OSR1 x8 (oversample), OSR2 x8 (downsample) — low-noise, plenty
            # fast for a heading.
            self._i2c.writeto_mem(self._addr, _QMCP_REG_CTRL1, bytes([0xC3]))
            time.sleep_ms(10)
        except Exception as e:
            print("[QMC5883P] init write failed:", repr(e))
            return

        self.is_present = True
        print("[QMC5883P] ready at 0x2C, 10Hz, +/-8G")

    def read_raw(self):
        """Return (x, y, z) as signed 16-bit counts, or None on error.
        Register order: X_LSB, X_MSB, Y_LSB, Y_MSB, Z_LSB, Z_MSB."""
        try:
            d = self._i2c.readfrom_mem(self._addr, _QMCP_REG_DATA, 6)
        except Exception:
            return None
        def s16(lo, hi):
            v = (hi << 8) | lo
            return v - 65536 if v >= 32768 else v
        return (s16(d[0], d[1]), s16(d[2], d[3]), s16(d[4], d[5]))

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
            return bool(self._i2c.readfrom_mem(self._addr, _QMCP_REG_STAT, 1)[0] & 0x01)
        except Exception:
            return False
