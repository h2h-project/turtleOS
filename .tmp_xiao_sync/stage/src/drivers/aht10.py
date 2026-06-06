# src/drivers/aht10.py — Minimal AHT10 I2C driver (MicroPython / Pico-safe)
#
# Reads temperature (°C) + humidity (%RH)
# Address usually 0x38

import time

AHT10_ADDR = 0x38

# Commands (per common AHT10 datasheets / known implementations)
CMD_INIT = b"\xE1\x08\x00"
CMD_TRIGGER = b"\xAC\x33\x00"
CMD_SOFTRESET = b"\xBA"

class AHT10:
    def __init__(self, i2c, addr=AHT10_ADDR):
        self.i2c = i2c
        self.addr = addr
        self._init_ok = False
        self._init()

    def _init(self):
        # Soft reset then init
        try:
            self.i2c.writeto(self.addr, CMD_SOFTRESET)
            time.sleep_ms(20)
        except Exception:
            pass

        try:
            self.i2c.writeto(self.addr, CMD_INIT)
            time.sleep_ms(20)
            self._init_ok = True
        except Exception:
            self._init_ok = False

    def _read_raw(self):
        """
        Returns tuple (humidity_raw_20bit, temp_raw_20bit) or raises.
        """
        # Trigger measurement
        self.i2c.writeto(self.addr, CMD_TRIGGER)

        # Typical measurement time 75ms..100ms; allow a bit more for safety
        time.sleep_ms(90)

        data = self.i2c.readfrom(self.addr, 6)
        if not data or len(data) != 6:
            raise OSError("AHT10 read failed")

        status = data[0]
        # Bit 7 busy flag on many AHT sensors; if still busy, wait a touch and re-read once
        if status & 0x80:
            time.sleep_ms(40)
            data = self.i2c.readfrom(self.addr, 6)

        b1, b2, b3, b4, b5 = data[1], data[2], data[3], data[4], data[5]

        # 20-bit humidity: b1<<12 | b2<<4 | (b3>>4)
        hum_raw = (b1 << 12) | (b2 << 4) | (b3 >> 4)

        # 20-bit temp: (b3&0x0F)<<16 | b4<<8 | b5
        tmp_raw = ((b3 & 0x0F) << 16) | (b4 << 8) | b5

        return hum_raw, tmp_raw

    def read(self):
        """
        Returns (temp_c, rh_percent)
        """
        if not self._init_ok:
            self._init()

        hum_raw, tmp_raw = self._read_raw()

        # Convert
        rh = (hum_raw / 1048576.0) * 100.0
        temp_c = (tmp_raw / 1048576.0) * 200.0 - 50.0

        # Clamp to sane limits
        if rh < 0:
            rh = 0.0
        if rh > 100:
            rh = 100.0

        return temp_c, rh
