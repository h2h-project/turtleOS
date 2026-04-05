# src/ui/oled.py  (MicroPython / Pico W / ESP32)
import time
import framebuf
import math
from machine import Pin, I2C

class SSD1306_I2C(framebuf.FrameBuffer):
    """
    Minimal SSD1306/SH1106-compatible I2C framebuffer driver (128x64).

    NOTE:
    - Many 1.3" "128x64" OLEDs are SH1106 (132px wide internal RAM).
      They need a column offset (often 2 or 4) when writing pages.
    - For classic 0.96" SSD1306 modules, col_offset should be 0.
    """
    def __init__(self, width, height, i2c, addr=0x3C, col_offset=0):
        self.width = int(width)
        self.height = int(height)
        self.i2c = i2c
        self.addr = addr
        self.pages = self.height // 8
        self.col_offset = int(col_offset) if col_offset is not None else 0

        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self._init_display()

    def _write_cmd(self, cmd):
        self.i2c.writeto(self.addr, bytes([0x00, cmd]))

    def _set_page_col(self, page, col):
        """
        Set current page + column. Works for SSD1306 and SH1106-style addressing.
        """
        self._write_cmd(0xB0 + (page & 0x0F))
        self._write_cmd(0x00 | (col & 0x0F))          # low nibble
        self._write_cmd(0x10 | ((col >> 4) & 0x0F))   # high nibble

    def _init_display(self):
        # This init sequence is SSD1306-ish, but works on a lot of SH1106 boards too.
        # The critical SH1106 difference for your symptom is the column offset in show().
        for cmd in (
                0xAE,       # display off
                0x20, 0x00, # memory addressing mode (horizontal)
                0x40,       # start line
                0xA1,       # seg remap
                0xC8,       # COM scan dec
                0xDA, 0x12, # COM pins
                0x81, 0x7F, # contrast
                0xA4,       # display follows RAM
                0xA6,       # normal display
                0xD5, 0x80, # display clock divide
                0x8D, 0x14, # charge pump
                0xAF        # display on
        ):
            self._write_cmd(cmd)
        self.fill(0)
        self.show()

    def poweroff(self):
        self._write_cmd(0xAE)

    def poweron(self):
        self._write_cmd(0xAF)

    def show(self):
        """
        Push framebuffer to display.
        col_offset fixes SH1106 132-column RAM mapping issues (removes edge stripe).
        """
        col = self.col_offset
        for page in range(self.pages):
            self._set_page_col(page, col)
            start = self.width * page
            end = start + self.width
            self.i2c.writeto(self.addr, b"\x40" + self.buffer[start:end])


class OLED:
    """
    OLED helper.

    HAL behavior:
      - If caller passes i2c=..., we use it.
      - Else if caller explicitly passes pin_sda/pin_scl (not None), we create I2C with those pins.
      - Else we ask src.hal.board.init_i2c() for the correct bus for this board (Pico vs ESP32).

    New:
      - col_offset: fixes SH1106-style displays (common on 1.3" 128x64).
        Typical values: 0 (SSD1306), 2 or 4 (SH1106 modules).
    """

    def __init__(
            self,
            width=128,
            height=64,
            addr=0x3C,
            i2c=None,
            i2c_id=0,
            pin_sda=None,
            pin_scl=None,
            freq=100_000,
            col_offset=2,
    ):
        self.width = int(width)
        self.height = int(height)

        # 1) Prefer injected bus
        if i2c is not None:
            self.i2c = i2c

        # 2) If explicit pins provided, honor them (backwards compatible)
        elif pin_sda is not None and pin_scl is not None:
            self.i2c = I2C(i2c_id, sda=Pin(pin_sda), scl=Pin(pin_scl), freq=freq)

        # 3) Otherwise, use HAL-selected pins for current board
        else:
            from src.hal.board import init_i2c
            self.i2c = init_i2c()

        # Hardware probe — raises OSError(19) if no OLED on the bus.
        # Imports below are intentionally placed AFTER this line so that 84 KB of
        # font bitmap data is never loaded when running headless (no OLED hardware).
        self.oled = SSD1306_I2C(width, height, self.i2c, addr=addr, col_offset=col_offset)

        from src import fonts
        from src.drivers.ezFBfont import ezFBfont
        from src.ui.waiting import WaitingScreen

        # --- ezFBfont writers bound to the framebuffer device ---
        self.f_vsmall = ezFBfont(self.oled, fonts.VSMALL, fg=1, bg=0, tkey=-1)
        self.f_small  = ezFBfont(self.oled, fonts.SMALL,  fg=1, bg=0, tkey=-1)
        self.f_med    = ezFBfont(self.oled, fonts.MED,    fg=1, bg=0, tkey=-1)
        self.f_large  = ezFBfont(self.oled, fonts.LARGE,  fg=1, bg=0, tkey=-1)
        self.f_arvo = ezFBfont(self.oled, fonts.get("arvo"), fg=1, bg=0, tkey=-1)
        self.f_arvo16 = ezFBfont(self.oled, fonts.get("arvo16"), fg=1, bg=0, tkey=-1)
        self.f_arvo20 = ezFBfont(self.oled, fonts.get("arvo20"), fg=1, bg=0, tkey=-1)

        # Screens
        self.waiting_screen = WaitingScreen(flip_x=False, flip_y=True, gap=6)

        self.clear()

    # ----------------------------
    # Helpers
    # ----------------------------
    def clear(self):
        self.oled.fill(0)
        self.oled.show()

    def _text_size(self, writer, text):
        return writer.size(text)

    def _center_x(self, writer, text):
        w, _ = self._text_size(writer, text)
        return max(0, (self.width - w) // 2)

    def draw_centered(self, writer, text, y):
        x = self._center_x(writer, text)
        writer.write(text, x, y)

    def _draw_tag_bottom_right(self, tag):
        if not tag:
            return
        w, h = self._text_size(self.f_vsmall, tag)
        x = max(0, self.width - w - 2)
        y = max(0, self.height - h - 1)
        self.f_vsmall.write(tag, x, y)

    # ----------------------------
    # Screens
    # ----------------------------
    def show_waiting(self, line="Know thy air"):
        self.waiting_screen.render(self, line=line, animate=True, period_ms=1000)

    def show_spinner_frame(self, frame):
        self.oled.fill(0)

        if isinstance(frame, dict):
            text = str(frame.get("text", ""))
        else:
            text = str(frame)

        _, h = self._text_size(self.f_med, text)
        y = max(0, (self.height - h) // 2)
        self.draw_centered(self.f_med, text, y)

        self.oled.show()

    def show_cached(self, reading, log_count):
        self.oled.fill(0)

        def draw_left(writer, text, y):
            writer.write(text, 2, y)

        def draw_right(writer, text, y):
            w, _ = self._text_size(writer, text)
            x = max(0, self.width - w - 2)
            writer.write(text, x, y)

        time_part = "--:--"

        y = 0
        draw_left(self.f_vsmall, "Cached " + time_part, y)
        draw_right(self.f_vsmall, "Log:" + str(log_count), y)
        y += 12

        draw_left(self.f_small, "Temp:{:.1f}C".format(reading.temp_c), y)
        draw_right(self.f_small, "AQI:{}".format(reading.aqi), y)
        y += 12

        draw_left(self.f_small, "eCO2:{}".format(reading.eco2_ppm), y)
        draw_right(self.f_small, "TVOC:{}".format(reading.tvoc_ppb), y)
        y += 12

        draw_left(self.f_small, "RH:{:.0f}%".format(reading.humidity), y)

        self.oled.show()

    def show_metric(self, heading, value, tag="just now"):
        self.oled.fill(0)

        self.draw_centered(self.f_med, heading, 2)

        _, hv = self._text_size(self.f_large, value)
        y_val = max(14, (self.height - hv) // 2)
        self.draw_centered(self.f_large, value, y_val)

        self._draw_tag_bottom_right(tag)
        self.oled.show()

    def show_face(self, air_rating):
        rating_raw = (air_rating or "Ok").strip() or "Ok"
        rating = rating_raw.lower().replace("-", " ").replace("_", " ")
        rating = " ".join(rating.split())

        self.oled.fill(0)

        label = "Air: " + rating_raw
        label_y = self.height - 12
        self.draw_centered(self.f_small, label, label_y)

        cx = self.width // 2
        cy = (label_y // 2) + 2
        r = min(22, (label_y // 2) - 2)

        for a in range(0, 360, 10):
            x = int(cx + r * math.cos(math.radians(a)))
            y = int(cy + r * math.sin(math.radians(a)))
            if 0 <= x < self.width and 0 <= y < self.height:
                self.oled.pixel(x, y, 1)

        eye_dx = r // 2
        eye_y = cy - (r // 3)
        self.oled.fill_rect(cx - eye_dx - 2, eye_y - 2, 4, 4, 1)
        self.oled.fill_rect(cx + eye_dx - 2, eye_y - 2, 4, 4, 1)

        mouth_y = cy + (r // 3)
        if rating in ("very good", "verygood", "good"):
            for dx in range(-r // 2, r // 2 + 1):
                y = mouth_y + (dx * dx) // (r) // 2
                self.oled.pixel(cx + dx, y, 1)
        elif rating == "ok":
            self.oled.hline(cx - r // 2, mouth_y, r, 1)
        else:
            for dx in range(-r // 2, r // 2 + 1):
                y = mouth_y - (dx * dx) // (r) // 2
                self.oled.pixel(cx + dx, y, 1)

        self.oled.show()

    def poweroff(self):
        try:
            self.oled.poweroff()
        except Exception:
            pass

    def poweron(self):
        try:
            self.oled.poweron()
        except Exception:
            pass

    def show_settings(self, time_str, ip, power_tag):
        self.oled.fill(0)

        self.draw_centered(self.f_large, time_str, 0)
        ip_text = ip if ip else "No connection"
        self.draw_centered(self.f_small, ip_text, 40)

        self._draw_tag_bottom_right(power_tag)
        self.oled.show()
