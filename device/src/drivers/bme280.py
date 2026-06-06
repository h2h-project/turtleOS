# src/drivers/bme280.py — Minimal BME280 driver (MicroPython / Pico-safe)
# Measures: temperature (°C), relative humidity (%RH), pressure (hPa)
# I2C address: 0x76 (SDO=GND, default) or 0x77 (SDO=VCC)
# Uses forced mode: one measurement per read() call, sensor sleeps between reads.

import time

BME280_ADDR = 0x76

_REG_CHIP_ID   = 0xD0
_REG_CTRL_HUM  = 0xF2
_REG_STATUS    = 0xF3
_REG_CTRL_MEAS = 0xF4
_REG_CONFIG    = 0xF5
_REG_DATA      = 0xF7  # 8 bytes: press[3], temp[3], hum[2]

_OSRS_x1    = 0b001
_MODE_SLEEP  = 0b00
_MODE_FORCED = 0b01


class BME280:
    def __init__(self, i2c, addr=BME280_ADDR):
        self.i2c = i2c
        self.addr = addr
        self._t_fine = 0
        self._calib_T = None
        self._calib_P = None
        self._calib_H = None
        self._load_calibration()
        self._apply_settings()

    def _read(self, reg, n=1):
        self.i2c.writeto(self.addr, bytes([reg]))
        return self.i2c.readfrom(self.addr, n)

    def _write(self, reg, val):
        self.i2c.writeto(self.addr, bytes([reg, val & 0xFF]))

    def _load_calibration(self):
        def u16(b, i):
            return b[i] | (b[i + 1] << 8)

        def s16(b, i):
            v = u16(b, i)
            return v - 65536 if v > 32767 else v

        raw = self._read(0x88, 6)
        self._calib_T = (u16(raw, 0), s16(raw, 2), s16(raw, 4))

        raw = self._read(0x8E, 18)
        self._calib_P = (
            u16(raw, 0),
            s16(raw, 2), s16(raw, 4), s16(raw, 6), s16(raw, 8),
            s16(raw, 10), s16(raw, 12), s16(raw, 14), s16(raw, 16),
        )

        h1 = self._read(0xA1, 1)[0]
        raw = self._read(0xE1, 7)
        h2 = (raw[1] << 8) | raw[0]
        if h2 > 32767:
            h2 -= 65536
        h3 = raw[2]
        h4 = (raw[3] << 4) | (raw[4] & 0x0F)
        if h4 > 2047:
            h4 -= 4096
        h5 = (raw[5] << 4) | (raw[4] >> 4)
        if h5 > 2047:
            h5 -= 4096
        h6 = raw[6]
        if h6 > 127:
            h6 -= 256
        self._calib_H = (h1, h2, h3, h4, h5, h6)

    def _apply_settings(self):
        # osrs_h = x1
        self._write(_REG_CTRL_HUM, _OSRS_x1)
        # osrs_t = x1, osrs_p = x1, mode = sleep (forced triggered per read)
        self._write(_REG_CTRL_MEAS, (_OSRS_x1 << 5) | (_OSRS_x1 << 2) | _MODE_SLEEP)
        # standby 1000 ms, IIR filter off
        self._write(_REG_CONFIG, 0b10100000)

    def _compensate_temp(self, adc_T):
        T1, T2, T3 = self._calib_T
        v1 = (adc_T / 16384.0 - T1 / 1024.0) * T2
        v2 = (adc_T / 131072.0 - T1 / 8192.0) ** 2 * T3
        self._t_fine = int(v1 + v2)
        return (v1 + v2) / 5120.0

    def _compensate_pressure(self, adc_P):
        P1, P2, P3, P4, P5, P6, P7, P8, P9 = self._calib_P
        v1 = self._t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * P6 / 32768.0
        v2 = v2 + v1 * P5 * 2.0
        v2 = v2 / 4.0 + P4 * 65536.0
        v1 = (P3 * v1 * v1 / 524288.0 + P2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * P1
        if v1 == 0:
            return 0.0
        p = 1048576.0 - adc_P
        p = (p - v2 / 4096.0) * 6250.0 / v1
        v1 = P9 * p * p / 2147483648.0
        v2 = p * P8 / 32768.0
        p = p + (v1 + v2 + P7) / 16.0
        return p / 100.0  # Pa → hPa

    def _compensate_humidity(self, adc_H):
        H1, H2, H3, H4, H5, H6 = self._calib_H
        x = self._t_fine - 76800.0
        x = ((adc_H - (H4 * 64.0 + H5 / 16384.0 * x))
             * (H2 / 65536.0 * (1.0 + H6 / 67108864.0 * x
                                * (1.0 + H3 / 67108864.0 * x))))
        x = x * (1.0 - H1 * x / 524288.0)
        return max(0.0, min(100.0, x))

    def read(self):
        """
        Trigger a forced measurement and return (temp_c, humidity_pct, pressure_hpa).
        Raises OSError/ValueError on hardware or sanity failures.
        """
        # Trigger forced measurement (ctrl_hum must be written before ctrl_meas)
        self._write(_REG_CTRL_HUM, _OSRS_x1)
        self._write(_REG_CTRL_MEAS, (_OSRS_x1 << 5) | (_OSRS_x1 << 2) | _MODE_FORCED)

        # Wait for measurement complete (~9 ms for x1/x1/x1)
        time.sleep_ms(12)
        for _ in range(5):
            if not (self._read(_REG_STATUS, 1)[0] & 0x08):
                break
            time.sleep_ms(5)

        raw = self._read(_REG_DATA, 8)
        adc_P = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        adc_T = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        adc_H = (raw[6] << 8) | raw[7]

        temp_c = self._compensate_temp(adc_T)
        pressure_hpa = self._compensate_pressure(adc_P)
        humidity = self._compensate_humidity(adc_H)

        if not (-40.0 <= temp_c <= 85.0):
            raise ValueError("BME280 temp out of range: {}".format(temp_c))
        if not (300.0 <= pressure_hpa <= 1100.0):
            raise ValueError("BME280 pressure out of range: {}".format(pressure_hpa))

        return temp_c, humidity, pressure_hpa
