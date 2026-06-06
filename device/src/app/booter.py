# src/app/booter.py  (MicroPython / Pico-safe)
import time
import gc
from src.ui.thermobar import ThermoBar


class Booter:
    """
    Boot screen with real-step progress + mpremote logging.

    - boot_pipeline(steps): step-by-step progress bar + footer text + logs
    - show(duration,fps): legacy smooth bar animation (used for sensor warmup)

    LOW-RAM patches:
    - Single-pass footer draw (no shadow / no double-write)
    - gc.collect() before footer writes + between steps
    - Reduced ramp frames to minimize repeated font allocations
    - MemoryError-safe footer fallback

    UI/Timing patches:
    - Faster progression: per-step settle reduced
    - Ramp frames reduced to 1 (less flicker + faster)
    - Final "Locked & loaded!" hold reduced to tiny blink (default 20ms)
    """

    def __init__(self, oled):
        self.oled = oled

        # Med font for footer text and version number; arvo24 for brand label
        self.f_footer = (
                getattr(oled, "f_med", None)
                or getattr(oled, "f_small", None)
        )
        # arvo24 (f_large) is trimmed to digits/punctuation only — no letters.
        # Use arvo20 (full ASCII, Arvo Bold) for the brand label instead.
        self.f_brand = getattr(oled, "f_arvo20", None) or self.f_footer
        self.f_version_num = getattr(oled, "f_small", None) or self.f_footer
        self.f_version = self.f_brand  # height reference for layout

        # Version label content
        self.brand = "airOS"
        self.version_num = "2.2.5"
        self.version = "airOS version 2.2.5"  # used for serial logging

        self.bar = ThermoBar(oled)
        self._layout = None

        # footer truncation
        self._footer_max_chars = 26

    # -------------------------------------------------
    # Framebuffer helpers
    # -------------------------------------------------
    def _fb(self):
        return getattr(self.oled, "oled", None)

    def _clear(self):
        fb = self._fb()
        if fb:
            fb.fill(0)

    def _show_fb(self):
        fb = self._fb()
        if fb:
            fb.show()

    # -------------------------------------------------
    # Text helpers (LOW-RAM safe)
    # -------------------------------------------------
    def _draw_centered_text_shadow(self, writer, text, y):
        """
        LOW-RAM safe centered footer draw.
        - Avoids double-write shadow (saves heap + time)
        - Falls back to rough centering if writer.size() fails
        - Handles MemoryError gracefully
        """
        if not writer or text is None:
            return

        s = str(text)
        w = int(getattr(self.oled, "width", 128))

        # Centering: try exact width, else fallback
        try:
            tw, _ = writer.size(s)
            x = max(0, (w - int(tw)) // 2)
        except Exception:
            x = max(0, (w - (len(s) * 6)) // 2)

        try:
            writer.write(s, x, y)
        except MemoryError:
            try:
                gc.collect()
            except Exception:
                pass
            try:
                short = s[:max(0, self._footer_max_chars - 4)]
                writer.write(short, 0, y)
            except Exception:
                pass
        except Exception:
            pass

    # -------------------------------------------------
    # Layout calc (cached)
    # -------------------------------------------------
    def _calc_layout(self):
        w = int(getattr(self.oled, "width", 128))
        h = int(getattr(self.oled, "height", 64))

        gap_ver_to_bar = 3
        gap_bar_to_footer = 4
        bar_h = 7

        ver_h = 11
        if self.f_version:
            try:
                _, ver_h = self.f_version.size("A")
            except Exception:
                ver_h = 11

        footer_h = 11
        if self.f_footer:
            try:
                _, footer_h = self.f_footer.size("A")
            except Exception:
                footer_h = 11

        bar_w = int(w * 0.70)
        if bar_w < 40:
            bar_w = 40
        if bar_w > w:
            bar_w = w
        bar_x = max(0, (w - bar_w) // 2)

        total_h = ver_h + gap_ver_to_bar + bar_h + gap_bar_to_footer + footer_h
        y0 = max(0, (h - total_h) // 2 - 2)
        if y0 < 0:
            y0 = 0

        ver_y = y0 + 1
        bar_y = y0 + ver_h + gap_ver_to_bar
        footer_y = bar_y + bar_h + gap_bar_to_footer

        return {
            "w": w,
            "h": h,
            "bar_x": bar_x,
            "bar_y": bar_y,
            "bar_w": bar_w,
            "ver_y": ver_y,
            "footer_y": footer_y,
        }

    # -------------------------------------------------
    # Version line: "abOS" (arvo24) + "v2.X.X" (med) centred as a pair
    # -------------------------------------------------
    def _draw_version_line(self, y):
        w = int(getattr(self.oled, "width", 128))
        gap = 4

        bw = bh = 0
        if self.f_brand:
            try:
                bw, bh = self.f_brand.size(self.brand)
            except Exception:
                bw = len(self.brand) * 14
                bh = 20

        vw = vh = 0
        if self.f_version_num:
            try:
                vw, vh = self.f_version_num.size(self.version_num)
            except Exception:
                vw = len(self.version_num) * 6
                vh = 11

        total_w = int(bw) + (gap if bw and vw else 0) + int(vw)
        x = max(0, (w - total_w) // 2)

        # Vertically offset version number: centred against brand + 4px lower
        ver_y_off = max(0, (int(bh) - int(vh)) // 2) + 2

        if self.f_brand:
            try:
                self.f_brand.write(self.brand, x, y)
            except Exception:
                pass

        if self.f_version_num and vw:
            try:
                self.f_version_num.write(
                    self.version_num, x + int(bw) + gap, y + ver_y_off
                )
            except Exception:
                pass

    # -------------------------------------------------
    # Draw frame
    # -------------------------------------------------
    def _draw_frame(self, p=0.0, footer=None):
        if self._layout is None:
            self._layout = self._calc_layout()

        bar_x = self._layout["bar_x"]
        bar_y = self._layout["bar_y"]
        bar_w = self._layout["bar_w"]
        ver_y = self._layout["ver_y"]
        footer_y = self._layout["footer_y"]

        self._clear()

        try:
            gc.collect()
        except Exception:
            pass
        self._draw_version_line(ver_y)

        self.bar.draw(bar_x, bar_y, bar_w, p=max(0.0, min(1.0, float(p))))

        if footer and self.f_footer:
            try:
                gc.collect()
            except Exception:
                pass

            s = str(footer)
            if len(s) > self._footer_max_chars:
                s = s[:self._footer_max_chars]
            self._draw_centered_text_shadow(self.f_footer, s, footer_y)

        self._show_fb()

    # -------------------------------------------------
    # Legacy warmup animation (used by src/app/main.py)
    # -------------------------------------------------
    def show(self, duration=4.0, fps=18, footer=None):
        self._layout = self._calc_layout()

        frames = max(1, int(float(duration) * float(fps)))
        delay_ms = int(1000 / max(1, int(fps)))

        self._draw_frame(p=0.0, footer=footer)

        for i in range(frames + 1):
            p = i / float(frames)
            self._draw_frame(p=p, footer=footer)
            time.sleep_ms(delay_ms)

    # -------------------------------------------------
    # Boot pipeline
    # -------------------------------------------------
    # Detail substrings that indicate a step failed or hardware is missing
    _ERROR_HINTS = (
        "FAIL", "ERROR", "NOT DETECTED", "ENOMEM",
        "HTTP", "BAD", "TIMEOUT", "MISSING", "NO SENSORS",
    )

    @staticmethod
    def _detail_is_error(detail):
        if not detail:
            return False
        d = str(detail).upper()
        for hint in Booter._ERROR_HINTS:
            if hint in d:
                return True
        return False

    def boot_pipeline(
            self,
            steps,
            intro_ms=500,
            fps=18,
            settle_ms=120,
            logger=None,
            *,
            # Explicit final hold so you can eliminate delay after "Locked & loaded!"
            final_hold_ms=20,
            # Fewer ramp frames (1 is fastest/least flicker)
            ramp_frames=1,
            # Per-step pause (override settle_ms behavior cleanly)
            step_pause_ms=None,
            # Extra hold (ms) when a step detail looks like an error
            error_hold_ms=700,
    ):
        if logger is None:
            logger = print

        self._layout = self._calc_layout()

        # Intro
        self._draw_frame(p=0.0, footer=None)
        logger("[BOOT] " + self.version)
        time.sleep_ms(int(intro_ms) if intro_ms is not None else 0)

        try:
            total = len(steps)
        except Exception:
            total = 0

        if total <= 0:
            self._draw_frame(p=1.0, footer="Locked & loaded!")
            logger("[BOOT] Locked & loaded!")
            if final_hold_ms and int(final_hold_ms) > 0:
                time.sleep_ms(int(final_hold_ms))
            return {"ok": True, "results": []}

        # If step_pause_ms not specified, keep using settle_ms
        pause_ms = int(step_pause_ms) if step_pause_ms is not None else int(settle_ms)

        results = []
        p_prev = 0.0

        for idx, item in enumerate(steps):
            try:
                label, fn = item
            except Exception:
                label, fn = ("Step", None)

            label = str(label)

            self._draw_frame(p=p_prev, footer=label)
            logger("[BOOT] " + label)

            # Per-step pause (set to 0 to go full speed)
            if pause_ms > 0:
                time.sleep_ms(pause_ms)

            ok = True
            detail = None

            try:
                if callable(fn):
                    ret = fn()
                    if isinstance(ret, tuple) and len(ret) >= 1:
                        ok = bool(ret[0])
                        if len(ret) >= 2:
                            detail = ret[1]
                    elif isinstance(ret, str):
                        ok = True
                        detail = ret
                    elif isinstance(ret, bool):
                        ok = ret
                else:
                    ok = False
                    detail = "no-fn"
            except Exception as e:
                ok = False
                detail = "err:" + repr(e)
                logger("[BOOT] EXC in " + label + ": " + repr(e))

            # LOW-RAM: collect after running a step (especially WiFi/API)
            try:
                gc.collect()
            except Exception:
                pass

            results.append({"label": label, "ok": ok, "detail": detail})

            p_next = float(idx + 1) / float(total)

            # Ramp animation (default now 1 frame)
            rf = int(ramp_frames) if ramp_frames is not None else 1
            if rf < 1:
                rf = 1

            for j in range(rf):
                pj = p_prev + (p_next - p_prev) * ((j + 1) / float(rf))
                footer = detail if detail else label
                self._draw_frame(p=pj, footer=footer)
                time.sleep_ms(int(1000 / max(1, int(fps))))

            status = "OK" if ok else "FAIL"
            if detail:
                logger("[BOOT] " + label + " -> " + status + " " + str(detail))
            else:
                logger("[BOOT] " + label + " -> " + status)

            # Hold error results long enough to read
            if error_hold_ms and int(error_hold_ms) > 0 and self._detail_is_error(detail):
                time.sleep_ms(int(error_hold_ms))

            p_prev = p_next

        # Final: minimal hold so waiting screen can take over immediately
        self._draw_frame(p=1.0, footer="Locked & loaded!")
        logger("[BOOT] Locked & loaded!")
        if final_hold_ms and int(final_hold_ms) > 0:
            time.sleep_ms(int(final_hold_ms))

        all_ok = True
        for r in results:
            if not r.get("ok", True):
                all_ok = False
                break

        return {"ok": all_ok, "results": results}
