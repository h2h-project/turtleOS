# src/ui/screens/frowny.py — Reusable error/frowny-face screen (MicroPython safe)
import time
from src.ui.glyphs import draw_face


class FrownyScreen:
    """
    Reusable error screen: a sad face in the upper portion of the OLED
    with two horizontally-centered med-font lines below it.

    Usage:
        scr = FrownyScreen(oled)
        scr.show(btn, line1="WiFi not enabled", line2="on your device")

    Any button click dismisses the screen and returns to the caller.
    """

    def __init__(self, oled):
        self.oled = oled

    def show(self, btn, line1="", line2=""):
        o = self.oled
        fb = o.oled
        w = int(getattr(o, "width", 128))
        h = int(getattr(o, "height", 64))

        fb.fill(0)

        # Face occupies the upper ~58% of the screen (~37px on a 64px OLED).
        # draw_face centers the face within the given width x face_h zone.
        face_h = int(h * 0.58)
        try:
            draw_face(fb, w, face_h, "bad", right_edge=False)
        except Exception:
            pass

        # Two centered med-font lines in the lower portion
        writer = getattr(o, "f_med", None) or getattr(o, "f_small", None)
        if writer:
            line_h = 12
            y = face_h + 2
            for text in (line1, line2):
                if not text:
                    y += line_h
                    continue
                s = str(text)
                try:
                    tw, _ = writer.size(s)
                    x = max(0, (w - int(tw)) // 2)
                except Exception:
                    x = 0
                try:
                    writer.write(s, x, y)
                except Exception:
                    pass
                y += line_h

        fb.show()

        if btn is not None:
            try:
                btn.reset()
            except Exception:
                pass
            while True:
                a = None
                try:
                    a = btn.poll_action()
                except Exception:
                    pass
                if a in ("single", "double", "triple", "quad", "debug"):
                    break
                time.sleep_ms(25)
