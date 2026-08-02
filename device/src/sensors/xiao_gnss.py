# src/sensors/xiao_gnss.py — generic GNSS/GPS UART driver
#
# Primary target: Seeed GNSS Add-on for XIAO (Quectel L76K), configured via
# PMTK ASCII commands.
#
# Legacy path: u-blox NEO-6M, configured via proprietary UBX binary protocol.
# Select via config key "gps_module": "l76k" (default) or "neo6m".
# All code inside # LEGACY-NEO6M delimiters is a candidate for deletion once
# NEO-6M devices are fully retired.

from machine import UART, Pin
import time


def _pmtk_sentence(body):
    """Build a $PMTK...* sentence with XOR checksum, CR+LF terminated."""
    ck = 0
    for c in body:
        ck ^= ord(c)
    return "$" + body + "*{:02X}\r\n".format(ck)


# ---- LEGACY-NEO6M: delete this function when NEO-6M support is dropped ----
def _ubx_checksum(body):
    ck_a = ck_b = 0
    for b in body:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return ck_a, ck_b
# ---- END LEGACY-NEO6M ----


class GnssModule:
    def __init__(
            self,
            uart_id=None,
            baud=9600,
            tx_pin=None,
            rx_pin=None,
            timeout=200,
    ):
        """
        Portable GNSS driver.
        If pins not provided, pulls from src.hal.board.gps_pins().
        """
        if uart_id is None or tx_pin is None or rx_pin is None:
            try:
                from src.hal.board import gps_pins
                _uart_id, _baud, _tx, _rx = gps_pins()
                if uart_id is None:
                    uart_id = _uart_id
                if baud == 9600:
                    baud = _baud
                if tx_pin is None:
                    tx_pin = _tx
                if rx_pin is None:
                    rx_pin = _rx
            except Exception:
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

    def send_raw(self, data):
        self.uart.write(data)

    # -------------------------------------------------
    # Module configuration
    # -------------------------------------------------
    def configure_mode(self, turtle_mode=False, module="l76k"):
        """
        Configure NMEA output and update rate: 1 Hz, RMC + GGA only.

        Both modes use the same config. turtle_mode is kept in the signature
        for callers but no longer changes anything — see _configure_l76k.

        module="l76k"  → Seeed GNSS Add-on / Quectel L76K (PMTK ASCII)
        module="neo6m" → u-blox NEO-6M (UBX binary) — LEGACY-NEO6M
        """
        if module == "neo6m":
            # ---- LEGACY-NEO6M: delete this branch when NEO-6M support is dropped ----
            self._configure_neo6m_ubx(turtle_mode)
            # ---- END LEGACY-NEO6M ----
        else:
            self._configure_l76k(turtle_mode)

    def _configure_l76k(self, turtle_mode):
        """Configure Quectel L76K via PMTK ASCII sentences.

        RMC + GGA at 1 Hz, for both turtle and airOS modes.

        Nothing in the tree parses GSA, GSV, VTG or GLL: position and fix
        validity come from RMC, fix quality and satellite count from GGA
        fields 6 and 7. RMC + GGA is ~142 bytes per epoch; the full set with
        GSV was ~700-830 and at 5 Hz needed ~4x the 960 bytes/s a 9600 baud
        link can carry. The module's TX FIFO never drained, so every sentence
        read was the oldest one queued and staleness grew without bound —
        that, not the fix rate, was the multi-second lag on manual stamps.

        1 Hz is also enough on the merits: heading comes from the QMC5883L
        (see nav/heading.py), the luff sweep runs off the AS5600, and at
        turtle speeds (~0.5-1 m/s) a second of travel is well inside the
        receiver's ~2.5 m CEP. Raising the rate only resamples that noise.

        If GPS course-over-ground is ever needed (e.g. damping magnetometer
        drift for the planned ICM-20948), raise the baud with PMTK251 first —
        do not go back to 5 Hz at 9600.
        """
        # PMTK314 field order: GLL, RMC, VTG, GGA, GSA, GSV, ...
        sentences = "PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0"
        rate = "PMTK220,1000"

        self.uart.write(_pmtk_sentence(sentences).encode())
        time.sleep_ms(20)
        self.uart.write(_pmtk_sentence(rate).encode())
        time.sleep_ms(20)

    # ---- LEGACY-NEO6M: delete this method when NEO-6M support is dropped ----
    def _configure_neo6m_ubx(self, turtle_mode):
        """Configure u-blox NEO-6M via proprietary UBX binary protocol.

        GGA + RMC at 1 Hz, matching _configure_l76k — same 9600 baud budget,
        same reasoning.
        """
        # NMEA sentence IDs (class 0xF0):
        #   GGA=0x00, GLL=0x01, GSA=0x02, GSV=0x03, RMC=0x04, VTG=0x05
        rates = {0x00: 1, 0x01: 0, 0x02: 0, 0x03: 0, 0x04: 1, 0x05: 0}
        meas_lo, meas_hi = 0xE8, 0x03  # 1000 ms = 1 Hz

        for msg_id, rate in rates.items():
            pl = bytes([0xF0, msg_id, 0x00, rate, 0x00, 0x00, 0x00, 0x00])
            body = bytes([0x06, 0x01, 0x08, 0x00]) + pl
            ck_a, ck_b = _ubx_checksum(body)
            self.send_raw(bytes([0xB5, 0x62]) + body + bytes([ck_a, ck_b]))
            time.sleep_ms(20)

        pl = bytes([meas_lo, meas_hi, 0x01, 0x00, 0x00, 0x00])
        body = bytes([0x06, 0x08, 0x06, 0x00]) + pl
        ck_a, ck_b = _ubx_checksum(body)
        self.send_raw(bytes([0xB5, 0x62]) + body + bytes([ck_a, ck_b]))
        time.sleep_ms(20)
    # ---- END LEGACY-NEO6M ----

    # -------------------------------------------------
    # NMEA reading
    # -------------------------------------------------
    def read_nmea(self, max_ms=0):
        try:
            n = self.uart.any()
        except Exception:
            n = 0

        if n:
            try:
                chunk = self.uart.read(n)
                if chunk:
                    self._rxbuf += chunk
                    if len(self._rxbuf) > 2048:
                        self._rxbuf = self._rxbuf[-1024:]
            except Exception:
                pass

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
        """Returns (year, month, day, weekday, hour, minute, sec) or None."""
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


# ---- LEGACY-NEO6M: delete this alias when NEO-6M support is dropped ----
# Kept so any code that still imports Ublox6GPS by name does not crash.
Ublox6GPS = GnssModule
# ---- END LEGACY-NEO6M ----
