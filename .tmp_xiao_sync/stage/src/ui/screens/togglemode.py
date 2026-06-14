# src/ui/screens/togglemode.py
import time
import machine


def show_toggle_mode(oled, current_turtle_mode):
    """
    Toggle turtle_mode in config, show confirmation for 2 s, then reboot.
    Call with the CURRENT value of turtle_mode; the screen flips it.
    """
    new_turtle_mode = not current_turtle_mode
    line1 = "turtle mode" if new_turtle_mode else "airBuddy mode"
    line2 = "activated"

    if oled is not None:
        fb = oled.oled
        fb.fill(0)

        try:
            w1, h1 = oled._text_size(oled.f_arvo16, line1)
            w2, h2 = oled._text_size(oled.f_arvo16, line2)
            total_h = h1 + 6 + h2
            y1 = max(0, (64 - total_h) // 2)
            y2 = y1 + h1 + 6
            x1 = max(0, (oled.width - w1) // 2)
            x2 = max(0, (oled.width - w2) // 2)
            oled.f_arvo16.write(line1, x1, y1)
            oled.f_arvo16.write(line2, x2, y2)
        except Exception:
            try:
                oled.f_med.write(line1, 0, 22)
                oled.f_med.write(line2, 0, 38)
            except Exception:
                pass

        try:
            fb.show()
        except Exception:
            pass

    try:
        from config import load_config, save_config
        cfg = load_config() or {}
        cfg["turtle_mode"] = new_turtle_mode
        save_config(cfg)
    except Exception as e:
        print("[TOGGLEMODE] save failed:", repr(e))

    time.sleep_ms(2000)
    machine.reset()
