# src/drivers/ina219.py
# MicroPython driver for INA219 high-side current/power monitor
# AirBuddy-oriented, lightweight, no external deps

import time


class INA219:
    # Registers
    _REG_CONFIG = 0x00
    _REG_SHUNT_VOLTAGE = 0x01
    _REG_BUS_VOLTAGE = 0x02
    _REG_POWER = 0x03
    _REG_CURRENT = 0x04
    _REG_CALIBRATION = 0x05

    # Config bits
    _RST = 0x8000
    _BRNG_16V = 0x0000
    _BRNG_32V = 0x2000

    _PG_40MV = 0x0000
    _PG_80MV = 0x0800
    _PG_160MV = 0x1000
    _PG_320MV = 0x1800

    _BADCRES_12BIT = 0x0180
    _SADCRES_12BIT_1S = 0x0018

    _MODE_SANDBVOLT_CONTINUOUS = 0x0007

    def __init__(self, i2c, addr=0x40, shunt_ohms=0.1, max_expected_amps=2.0, auto_init=True):
        self.i2c = i2c
        self.addr = int(addr)
        self.shunt_ohms = float(shunt_ohms)
        self.max_expected_amps = float(max_expected_amps)

        self.current_lsb = None
        self.power_lsb = None
        self.calibration_value = None
        self.is_present = False

        if auto_init:
            self.init()

    # ----------------------------
    # Presence / init
    # ----------------------------
    @staticmethod
    def probe(i2c, addr=0x40):
        try:
            devices = i2c.scan()
            return int(addr) in devices
        except Exception:
            return False

    def init(self):
        if not self.probe(self.i2c, self.addr):
            self.is_present = False
            return False

        try:
            self._configure()
            self.is_present = True
            return True
        except Exception:
            self.is_present = False
            return False

    # ----------------------------
    # Low-level helpers
    # ----------------------------
    def _write_register(self, reg, value):
        value = int(value) & 0xFFFF
        data = bytes([reg, (value >> 8) & 0xFF, value & 0xFF])
        self.i2c.writeto(self.addr, data)

    def _read_register(self, reg):
        self.i2c.writeto(self.addr, bytes([reg]))
        data = self.i2c.readfrom(self.addr, 2)
        if data is None or len(data) != 2:
            raise OSError("INA219 short read")
        return (data[0] << 8) | data[1]

    @staticmethod
    def _to_signed(val):
        if val > 32767:
            val -= 65536
        return val

    # ----------------------------
    # Init / calibration
    # ----------------------------
    def _configure(self):
        self._write_register(self._REG_CONFIG, self._RST)
        time.sleep_ms(1)

        current_lsb = self.max_expected_amps / 32767.0
        if current_lsb < 0.00001:
            current_lsb = 0.00001

        current_lsb = round(current_lsb, 8)

        cal = int(0.04096 / (current_lsb * self.shunt_ohms))
        if cal < 1:
            cal = 1
        if cal > 0xFFFF:
            cal = 0xFFFF

        current_lsb = 0.04096 / (cal * self.shunt_ohms)
        power_lsb = current_lsb * 20.0

        self.current_lsb = current_lsb
        self.power_lsb = power_lsb
        self.calibration_value = cal

        self._write_register(self._REG_CALIBRATION, self.calibration_value)

        config = (
                self._BRNG_32V |
                self._PG_320MV |
                self._BADCRES_12BIT |
                self._SADCRES_12BIT_1S |
                self._MODE_SANDBVOLT_CONTINUOUS
        )
        self._write_register(self._REG_CONFIG, config)
        time.sleep_ms(1)

    def wake(self):
        if not self.is_present:
            return False
        try:
            self._configure()
            return True
        except Exception:
            self.is_present = False
            return False

    # ----------------------------
    # Raw readings
    # ----------------------------
    def shunt_voltage_mv(self):
        if not self.is_present:
            return None
        raw = self._to_signed(self._read_register(self._REG_SHUNT_VOLTAGE))
        return raw * 0.01  # 10uV per bit = 0.01mV

    def bus_voltage_v(self):
        if not self.is_present:
            return None
        raw = self._read_register(self._REG_BUS_VOLTAGE)
        return ((raw >> 3) * 0.004)

    def current_ma(self):
        if not self.is_present:
            return None
        self._write_register(self._REG_CALIBRATION, self.calibration_value)
        raw = self._to_signed(self._read_register(self._REG_CURRENT))
        return raw * self.current_lsb * 1000.0

    def power_mw(self):
        if not self.is_present:
            return None
        self._write_register(self._REG_CALIBRATION, self.calibration_value)
        raw = self._read_register(self._REG_POWER)
        return raw * self.power_lsb * 1000.0

    def load_voltage_v(self):
        if not self.is_present:
            return None
        bus_v = self.bus_voltage_v()
        shunt_mv = self.shunt_voltage_mv()
        if bus_v is None or shunt_mv is None:
            return None
        return bus_v + (shunt_mv / 1000.0)

    # ----------------------------
    # Convenience
    # ----------------------------
    def read(self):
        if not self.is_present:
            return {
                "present": False,
                "bus_voltage_v": None,
                "shunt_voltage_mv": None,
                "current_ma": None,
                "power_mw": None,
                "load_voltage_v": None,
            }

        try:
            bus_v = self.bus_voltage_v()
            shunt_mv = self.shunt_voltage_mv()
            current_ma = self.current_ma()
            power_mw = self.power_mw()
            load_v = None if bus_v is None or shunt_mv is None else (bus_v + (shunt_mv / 1000.0))

            return {
                "present": True,
                "bus_voltage_v": None if bus_v is None else round(bus_v, 3),
                "shunt_voltage_mv": None if shunt_mv is None else round(shunt_mv, 3),
                "current_ma": None if current_ma is None else round(current_ma, 2),
                "power_mw": None if power_mw is None else round(power_mw, 2),
                "load_voltage_v": None if load_v is None else round(load_v, 3),
            }
        except Exception:
            self.is_present = False
            return {
                "present": False,
                "bus_voltage_v": None,
                "shunt_voltage_mv": None,
                "current_ma": None,
                "power_mw": None,
                "load_voltage_v": None,
            }