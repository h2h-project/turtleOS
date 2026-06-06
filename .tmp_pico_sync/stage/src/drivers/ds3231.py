# src/drivers/ds3231.py
# DS3231 RTC driver (MicroPython, Pico-safe)
#
# Wiring example (Pico):
#   SDA -> GPIO0
#   SCL -> GPIO1
#   3V3 -> VCC
#   GND -> GND
#
# I2C address: 0x68

class DS3231:
    ADDR = 0x68

    # Register map
    REG_TIME = 0x00     # 0x00..0x06 (sec, min, hour, day-of-week, day, month, year)
    REG_STATUS = 0x0F   # Status register (OSF bit)
    REG_TEMP_MSB = 0x11 # Temperature MSB
    REG_TEMP_LSB = 0x12 # Temperature LSB (upper two bits are fractional .25C)

    def __init__(self, i2c, addr=ADDR, probe=True):
        self.i2c = i2c
        self.addr = addr

        if probe:
            devices = self.i2c.scan()
            if self.addr not in devices:
                raise OSError("DS3231 not found on I2C (addr 0x%02X). scan=%s" % (self.addr, devices))

    # -------------------------
    # BCD helpers
    # -------------------------
    @staticmethod
    def _bcd2dec(b):
        return (b >> 4) * 10 + (b & 0x0F)

    @staticmethod
    def _dec2bcd(d):
        return ((d // 10) << 4) | (d % 10)

    # -------------------------
    # Low-level I2C helpers
    # -------------------------
    def _read(self, reg, nbytes):
        return self.i2c.readfrom_mem(self.addr, reg, nbytes)

    def _write(self, reg, data):
        # data can be int or bytes-like
        if isinstance(data, int):
            data = bytes([data])
        self.i2c.writeto_mem(self.addr, reg, data)

    # -------------------------
    # Public API
    # -------------------------
    def datetime(self, dt=None):
        """
        Get or set DS3231 time.

        If dt is None: returns tuple:
            (year, month, day, weekday, hour, minute, second)

        If dt is provided: expects same tuple:
            (year, month, day, weekday, hour, minute, second)

        weekday: 1=Mon ... 7=Sun (DS3231 convention)
        year: full year e.g. 2026
        """
        if dt is None:
            data = self._read(self.REG_TIME, 7)

            sec = self._bcd2dec(data[0] & 0x7F)
            minute = self._bcd2dec(data[1] & 0x7F)

            # Hour register: handle 24h mode (bit 6 = 0) vs 12h mode (bit 6 = 1)
            hr_raw = data[2]
            if hr_raw & 0x40:
                # 12h mode
                hour = self._bcd2dec(hr_raw & 0x1F)
                is_pm = True if (hr_raw & 0x20) else False
                if hour == 12:
                    hour = 12 if is_pm else 0
                else:
                    hour = hour + 12 if is_pm else hour
            else:
                # 24h mode
                hour = self._bcd2dec(hr_raw & 0x3F)

            weekday = self._bcd2dec(data[3] & 0x07)
            day = self._bcd2dec(data[4] & 0x3F)

            month = self._bcd2dec(data[5] & 0x1F)
            year = self._bcd2dec(data[6]) + 2000

            return (year, month, day, weekday, hour, minute, sec)

        # Set time
        year, month, day, weekday, hour, minute, sec = dt

        if year < 2000 or year > 2099:
            raise ValueError("DS3231 driver supports years 2000..2099 (got %s)" % year)
        if not (1 <= month <= 12):
            raise ValueError("month must be 1..12")
        if not (1 <= day <= 31):
            raise ValueError("day must be 1..31")
        if not (1 <= weekday <= 7):
            raise ValueError("weekday must be 1..7 (Mon..Sun)")
        if not (0 <= hour <= 23):
            raise ValueError("hour must be 0..23")
        if not (0 <= minute <= 59):
            raise ValueError("minute must be 0..59")
        if not (0 <= sec <= 59):
            raise ValueError("second must be 0..59")

        data = bytearray(7)
        data[0] = self._dec2bcd(sec)
        data[1] = self._dec2bcd(minute)

        # Force 24-hour mode (bit6=0)
        data[2] = self._dec2bcd(hour) & 0x3F

        data[3] = self._dec2bcd(weekday)
        data[4] = self._dec2bcd(day)
        data[5] = self._dec2bcd(month)  # century bit not used here
        data[6] = self._dec2bcd(year - 2000)

        self._write(self.REG_TIME, data)
        return dt

    def temperature(self):
        """
        Returns DS3231 internal temperature in Celsius (float).
        Resolution is 0.25°C.
        """
        msb = self._read(self.REG_TEMP_MSB, 1)[0]
        lsb = self._read(self.REG_TEMP_LSB, 1)[0]

        # msb is signed int8
        if msb & 0x80:
            msb = msb - 256

        frac = (lsb >> 6) * 0.25
        return msb + frac

    def lost_power(self):
        """
        Returns True if Oscillator Stop Flag (OSF) is set.
        This indicates the RTC oscillator stopped (e.g., power loss / dead battery).
        """
        status = self._read(self.REG_STATUS, 1)[0]
        return True if (status & 0x80) else False

    def clear_lost_power(self):
        """
        Clears OSF (Oscillator Stop Flag).
        Call this after setting the time to acknowledge/reset the flag.
        """
        status = self._read(self.REG_STATUS, 1)[0]
        status &= 0x7F
        self._write(self.REG_STATUS, status)
