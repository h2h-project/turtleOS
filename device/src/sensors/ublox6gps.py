# src/sensors/ublox6gps.py
from machine import UART, Pin
import time


def _ubx_checksum(body):
    ck_a = ck_b = 0
    for b in body:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return ck_a, ck_b


class Ublox6GPS:
    def __init__(
            self,
            uart_id=None,
            baud=9600,
            tx_pin=None,
            rx_pin=None,
            timeout=200,
    ):
        """
        Portable GPS driver.
        If pins not provided, pulls from src.hal.board.gps_pins()
        """

        # Pull from HAL if not explicitly provided
        if uart_id is None or tx_pin is None or rx_pin is None:
            try:
                from src.hal.board import gps_pins
                _uart_id, _tx, _rx = gps_pins()

                if uart_id is None:
                    uart_id = _uart_id
                if tx_pin is None:
                    tx_pin = _tx
                if rx_pin is None:
                    rx_pin = _rx
            except Exception:
                # Pico fallback defaults
                if uart_id is None:
                    uart_id = 1
                if tx_pin is None:
                    tx_pin = 8
                if rx_pin is None:
                    rx_pin = 9

        try:
            import gc
            gc.collect()
        except Exception:
            pass

        self.uart = UART(
            int(uart_id),
            baudrate=int(baud),
            tx=Pin(int(tx_pin)),
            rx=Pin(int(rx_pin)),
            timeout=timeout,
            rxbuf=2048,
        )

        self._rxbuf = b""

    # -------------------------------------------------
    # Non-blocking line read
    # -------------------------------------------------
    def readline(self):
        if self.uart.any():
            return self.uart.readline()
        return None

    def send_ubx(self, data):
        self.uart.write(data)

    def configure_mode(self, turtle_mode=False):
        # NMEA sentence IDs (class 0xF0):
        #   GGA=0x00, GLL=0x01, GSA=0x02, GSV=0x03, RMC=0x04, VTG=0x05
        #
        # static:     keep GGA + RMC only (disable GLL, GSA, GSV, VTG), 1 Hz
        # navigation: keep GGA, GSA, GSV, RMC, VTG (disable GLL only), 5 Hz
        if turtle_mode:
            rates = {0x00: 1, 0x01: 0, 0x02: 1, 0x03: 1, 0x04: 1, 0x05: 1}
            meas_lo, meas_hi = 0xC8, 0x00  # 200 ms = 5 Hz
        else:
            rates = {0x00: 1, 0x01: 0, 0x02: 0, 0x03: 0, 0x04: 1, 0x05: 0}
            meas_lo, meas_hi = 0xE8, 0x03  # 1000 ms = 1 Hz

        for msg_id, rate in rates.items():
            pl = bytes([0xF0, msg_id, 0x00, rate, 0x00, 0x00, 0x00, 0x00])
            body = bytes([0x06, 0x01, 0x08, 0x00]) + pl
            ck_a, ck_b = _ubx_checksum(body)
            self.send_ubx(bytes([0xB5, 0x62]) + body + bytes([ck_a, ck_b]))
            time.sleep_ms(20)

        pl = bytes([meas_lo, meas_hi, 0x01, 0x00, 0x00, 0x00])
        body = bytes([0x06, 0x08, 0x06, 0x00]) + pl
        ck_a, ck_b = _ubx_checksum(body)
        self.send_ubx(bytes([0xB5, 0x62]) + body + bytes([ck_a, ck_b]))
        time.sleep_ms(20)

    def read_nmea(self, max_ms=0):
        # Pull whatever is available
        try:
            n = self.uart.any()
        except Exception:
            n = 0

        if n:
            try:
                chunk = self.uart.read(n)
                if chunk:
                    self._rxbuf += chunk
                    # prevent runaway buffer
                    if len(self._rxbuf) > 2048:
                        self._rxbuf = self._rxbuf[-1024:]
            except Exception:
                pass

        # Extract full lines
        for _ in range(32):
            i = self._rxbuf.find(b"\n")
            if i < 0:
                return None

            line = self._rxbuf[:i + 1]
            self._rxbuf = self._rxbuf[i + 1:]

            try:
                txt = line.decode("ascii", "ignore").strip()
            except Exception:
                txt = ""

            if txt.startswith("$GP") or txt.startswith("$GN"):
                return txt
        return None

    # -------------------------------------------------
    # RMC helpers
    # -------------------------------------------------
    def get_rmc(self, max_ms=2000):
        t = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t) < max_ms:
            line = self.read_nmea()
            if line and ("RMC" in line):
                return line
        return None

    def has_fix(self, max_ms=2000):
        rmc = self.get_rmc(max_ms=max_ms)
        if not rmc:
            return False
        parts = rmc.split(",")
        return len(parts) > 2 and parts[2] == "A"

    def get_utc_datetime(self, max_ms=4000):
        """
        Returns (year,month,day,weekday,hour,minute,sec)
        """
        rmc = self.get_rmc(max_ms=max_ms)
        if not rmc:
            return None

        p = rmc.split(",")
        if len(p) < 10:
            return None
        if p[2] != "A":
            return None

        hhmmss = p[1]
        ddmmyy = p[9]
        if len(hhmmss) < 6 or len(ddmmyy) != 6:
            return None

        hour = int(hhmmss[0:2])
        minute = int(hhmmss[2:4])
        sec = int(float(hhmmss[4:]))

        day = int(ddmmyy[0:2])
        month = int(ddmmyy[2:4])
        yy = int(ddmmyy[4:6])
        year = 2000 + yy if yy < 80 else 1900 + yy

        weekday = 1  # placeholder
        return (year, month, day, weekday, hour, minute, sec)
