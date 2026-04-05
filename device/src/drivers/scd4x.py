# src/drivers/scd4x.py
# MicroPython driver for Sensirion SCD4x family (SCD40 / SCD41)
#
# Features:
# - start_periodic_measurement()
# - stop_periodic_measurement()
# - data_ready()
# - read_measurement() -> (co2_ppm, temp_c, rh)
# - get_serial_number()
# - best-effort state tracking for periodic mode
#
# Notes:
# - Default I2C address is 0x62
# - Temperature and humidity are reported alongside CO2
# - For AirBuddy, CO2 is usually the main value from this sensor;
#   temp/RH are useful as diagnostic comparison fields

import time


class SCD4X:
    _DEFAULT_ADDR = 0x62

    _CMD_START_PERIODIC = 0x21B1
    _CMD_STOP_PERIODIC = 0x3F86
    _CMD_GET_DATA_READY = 0xE4B8
    _CMD_READ_MEASUREMENT = 0xEC05
    _CMD_GET_SERIAL = 0x3682

    def __init__(self, i2c, addr=_DEFAULT_ADDR):
        self.i2c = i2c
        self.addr = int(addr)
        self._started = False
        self.serial_number = None

    @staticmethod
    def _crc8(data):
        crc = 0xFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    def _send_cmd(self, cmd):
        self.i2c.writeto(self.addr, bytes([
            (cmd >> 8) & 0xFF,
            cmd & 0xFF
        ]))

    def _read_words(self, num_words):
        raw = self.i2c.readfrom(self.addr, int(num_words) * 3)
        if raw is None or len(raw) != int(num_words) * 3:
            raise OSError("SCD4X short read")

        words = []
        for i in range(int(num_words)):
            b1 = raw[i * 3]
            b2 = raw[i * 3 + 1]
            crc = raw[i * 3 + 2]
            if self._crc8(bytes([b1, b2])) != crc:
                raise ValueError("SCD4X CRC mismatch on word {}".format(i))
            words.append((b1 << 8) | b2)
        return words

    def stop_periodic_measurement(self):
        try:
            self._send_cmd(self._CMD_STOP_PERIODIC)
            time.sleep_ms(500)
        except Exception:
            pass
        self._started = False

    def start_periodic_measurement(self):
        self._send_cmd(self._CMD_START_PERIODIC)
        self._started = True
        time.sleep_ms(5)

    def ensure_started(self):
        if not self._started:
            try:
                self.stop_periodic_measurement()
            except Exception:
                pass
            self.start_periodic_measurement()

    def data_ready(self):
        self._send_cmd(self._CMD_GET_DATA_READY)
        time.sleep_ms(1)
        words = self._read_words(1)
        return (words[0] & 0x07FF) != 0

    def read_measurement(self):
        self._send_cmd(self._CMD_READ_MEASUREMENT)
        time.sleep_ms(1)
        words = self._read_words(3)

        co2_ppm = int(words[0])
        temp_c = -45.0 + 175.0 * (words[1] / 65535.0)
        rh = 100.0 * (words[2] / 65535.0)

        return co2_ppm, temp_c, rh

    def get_serial_number(self):
        was_started = self._started

        if was_started:
            self.stop_periodic_measurement()

        self._send_cmd(self._CMD_GET_SERIAL)
        time.sleep_ms(1)
        words = self._read_words(3)

        serial = (words[0] << 32) | (words[1] << 16) | words[2]
        self.serial_number = serial

        if was_started:
            self.start_periodic_measurement()

        return serial

    def read_if_ready(self):
        if not self.data_ready():
            return None
        return self.read_measurement()