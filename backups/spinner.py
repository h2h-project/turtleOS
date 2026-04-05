# src/ui/spinner.py  (MicroPython / Pico-safe)
import time
from src.ui.thermobar import ThermoBar


class Spinner:
    """
    Breathing bar spinner using ThermoBar (time-driven, Pico-safe).

    Fixes:
      - Duration is REAL time (ticks_ms), not frame-count (so no "10s" drift)
      - Perfectly symmetric expansion (centered every frame)
      - Max width reduced to ~60% of screen
      - Optional label "Sampling..." in MED font
    """

    BAR_H = 7  # ThermoBar visual height (1px border + 5px inner)
    LABEL = "Sampling..."

    def __init__(self, oled):
        self.oled = oled
        self.bar = ThermoBar(oled)

        # Layout
        self.bar_y = 42
        self.label_gap = 6

        # Target frame pacing (ms)
        # Higher = fewer frames = faster overall on Pico (less CPU)
        self.frame_ms = 45

        # Bar sizing
        self.max_width_ratio = 0.60   # 60% of screen width
        self.min_width_ratio = 0.18   # minimum breathing width

        # Font
        self.f_med = getattr(oled, "f_med", None)

    # ----------------------------
    # Framebuffer helpers
    # ----------------------------
    def _fb(self):
        return getattr(self.oled, "oled", None)

    def _show(self):
        fb = self._fb()
        if fb:
            fb.show()

    def _ticks_ms(self):
        try:
            return time.ticks_ms()
        except Exception:
            return int(time.time() * 1000)

    # ----------------------------
    # Public API
    # ----------------------------
    def spin(self, duration=3.0):
        """
        Runs a single expand->contract cycle over `duration` seconds.
        Uses real elapsed time so it won't drift long on slow hardware.
        """
        fb = self._fb()
        if fb is None:
            return

        screen_w = int(getattr(self.oled, "width", 128))
        screen_h = int(getattr(self.oled, "height", 64))
        cx = screen_w // 2

        # Clear whole screen
        fb.fill(0)

        # --- label placement ---
        label_y = 0
        label_h = 0
        if self.f_med:
            tw, th = self.f_med.size(self.LABEL)
            tx = max(0, (screen_w - tw) // 2)
            label_y = max(0, int(self.bar_y) - th - self.label_gap)
            label_h = th
            self.f_med.write(self.LABEL, tx, label_y)

        self._show()

        # --- bar geometry ---
        max_w = int(screen_w * self.max_width_ratio)
        min_w = int(max_w * self.min_width_ratio)

        # safety bounds (keep rounded corners stable)
        if max_w < 30:
            max_w = 30
        if min_w < 12:
            min_w = 12
        if min_w > max_w:
            min_w = max_w

        bar_y = int(self.bar_y)

        # Clear band dimensions (label + bar)
        band_top = max(0, label_y - 1)
        band_bottom = min(screen_h, bar_y + self.BAR_H + 2)
        band_h = max(0, band_bottom - band_top)

        # --- time-driven loop ---
        start = self._ticks_ms()
        dur_ms = int(float(duration) * 1000)
        end = time.ticks_add(start, dur_ms)

        while time.ticks_diff(end, self._ticks_ms()) > 0:
            now = self._ticks_ms()
            elapsed = time.ticks_diff(now, start)
            if elapsed < 0:
                elapsed = 0
            if elapsed > dur_ms:
                elapsed = dur_ms

            # Normalized 0..1 across the full duration
            u = elapsed / float(dur_ms) if dur_ms > 0 else 1.0

            # Triangle wave: 0→1→0 over one duration
            if u <= 0.5:
                p = u * 2.0
            else:
                p = (1.0 - u) * 2.0

            # Width from min_w..max_w
            current_w = int(min_w + p * (max_w - min_w))
            if current_w < 12:
                current_w = 12

            x = int(cx - (current_w // 2))
            if x < 0:
                x = 0
            if x + current_w > screen_w:
                current_w = screen_w - x

            # Clear only the band
            if band_h > 0:
                fb.fill_rect(0, band_top, screen_w, band_h, 0)

            # Re-draw label (static)
            if self.f_med:
                tw, th = self.f_med.size(self.LABEL)
                tx = max(0, (screen_w - tw) // 2)
                self.f_med.write(self.LABEL, tx, label_y)

            # Draw fully-filled bar within the centered window
            self.bar.draw(
                x=x,
                y=bar_y,
                w=current_w,
                p=1.0,
                outline=True,
                clear_bg=False,
            )

            self._show()

            # Pace frames, but don't "force sleep" if draw is slow
            # (prevents duration drift long on Pico)
            t_after = self._ticks_ms()
            work_ms = time.ticks_diff(t_after, now)
            sleep_ms = self.frame_ms - work_ms
            if sleep_ms > 0:
                time.sleep_ms(int(sleep_ms))
