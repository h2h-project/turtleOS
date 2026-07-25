# src/ui/screens/device.py
# MicroPython / Pico-safe
#
# Device info screen
# - Device name (centered)
# - Left aligned data block with prefixes (values on same line)
# - Holds until click in show_live()
#
# Updated:
# - Community fully removed
# - TZ retained
# - Supports new nested API response shape:
#     device.device_name
#     assignment.home.home_name
#     assignment.room.room_name
#     assignment.user.time_zone
#   while still tolerating older flat payloads
#
# Mode-aware layout (Jul 2026):
#   turtle_mode=True  -> Name / Mission (full_name) / ID
#                        The turtle API has no homes/rooms, so they are neither
#                        parsed (see flows._normalize) nor drawn.
#   turtle_mode=False -> Home / Room / Mission-or-Device-ID  (unchanged)

import time
import gc

from config import load_config

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0


class DeviceScreen:

    def __init__(self, oled):
        self.oled = oled

        # Fonts (safe fallbacks)
        self.f_title = getattr(oled, "f_arvo20", None) \
                       or getattr(oled, "f_arvo16", None) \
                       or getattr(oled, "f_med", None)

        self.f_med = getattr(oled, "f_med", None) \
                     or getattr(oled, "f_small", None)

        self.f_small = getattr(oled, "f_small", None) \
                       or self.f_med


    # -------------------------------------------------
    # INTERNAL HELPERS
    # -------------------------------------------------
    def _center_x(self, writer, text, ow):
        try:
            tw, _ = writer.size(text)
            return max(0, (int(ow) - int(tw)) // 2)
        except Exception:
            return 0

    def _nested_get(self, obj, *keys):
        cur = obj
        try:
            for k in keys:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(k)
            return cur
        except Exception:
            return None

    def _pick_device_name(self, api_info):
        if not isinstance(api_info, dict):
            return "AirBuddy"

        # New nested API
        v = self._nested_get(api_info, "device", "device_name")
        if isinstance(v, str) and v:
            return v

        # Back-compat flat
        v = api_info.get("device_name")
        if isinstance(v, str) and v:
            return v

        return "AirBuddy"

    def _pick_home_name(self, api_info):
        if not isinstance(api_info, dict):
            return ""

        v = self._nested_get(api_info, "assignment", "home", "home_name")
        if isinstance(v, str) and v:
            return v

        v = api_info.get("home_name")
        if isinstance(v, str) and v:
            return v

        return ""

    def _pick_room_name(self, api_info):
        if not isinstance(api_info, dict):
            return ""

        v = self._nested_get(api_info, "assignment", "room", "room_name")
        if isinstance(v, str) and v:
            return v

        v = api_info.get("room_name")
        if isinstance(v, str) and v:
            return v

        return ""

    def _pick_mission_full_name(self, api_info):
        # missions_tb.full_name — the long mission label shown in turtle mode.
        if not isinstance(api_info, dict):
            return ""

        v = api_info.get("mission_full_name")
        if isinstance(v, str) and v:
            return v

        v = self._nested_get(api_info, "assignment", "mission", "full_name")
        if isinstance(v, str) and v:
            return v

        v = self._nested_get(api_info, "mission", "full_name")
        if isinstance(v, str) and v:
            return v

        return ""

    def _pick_mission_short_name(self, api_info):
        # missions_tb.short_name — OLED-friendly mission label.
        # Prefer the flat key our normalizer emits; tolerate nested/full_name.
        if not isinstance(api_info, dict):
            return ""

        v = api_info.get("mission_short_name")
        if isinstance(v, str) and v:
            return v

        v = self._nested_get(api_info, "assignment", "mission", "short_name")
        if isinstance(v, str) and v:
            return v

        v = self._nested_get(api_info, "mission", "short_name")
        if isinstance(v, str) and v:
            return v

        # Last resort: the long name if short_name was never set.
        v = api_info.get("mission_full_name")
        if isinstance(v, str) and v:
            return v

        return ""

    # -------------------------------------------------
    # INTERNAL RENDER
    # -------------------------------------------------
    def _render(self, api_info):
        if self.oled is None:
            return

        fb = getattr(self.oled, "oled", None)
        if fb is None:
            return

        fb.fill(0)

        if not isinstance(api_info, dict):
            api_info = {}

        try:
            cfg = load_config() or {}
            device_id = str(cfg.get("device_id", "") or "")
            turtle_mode = bool(cfg.get("turtle_mode", False))
        except Exception:
            device_id = ""
            turtle_mode = False

        device = str(self._pick_device_name(api_info) or "AirBuddy")

        # turtleOS has no homes/rooms on its API — don't parse or draw them.
        if turtle_mode:
            home = ""
            room = ""
            mission = str(self._pick_mission_full_name(api_info) or "")
        else:
            home = str(self._pick_home_name(api_info) or "")
            room = str(self._pick_room_name(api_info) or "")
            mission = str(self._pick_mission_short_name(api_info) or "")

        ow = int(getattr(self.oled, "width", 128))

        # Title top-left in arvo20 at y=0
        if self.f_title:
            try:
                self.f_title.write("Device", 0, 0)
            except Exception:
                pass

        # connectivity icons top-right at y=1 — GPS/WiFi: previous; API: cache
        if _ch:
            try:
                _ch.draw(fb, ow, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        if turtle_mode:
            # turtleOS: device name, mission full_name, and local device ID.
            if self.f_med:
                try:
                    self.f_med.write(("Name: " + (device or "---"))[:20], 0, 24)
                except Exception:
                    pass
                try:
                    self.f_med.write(("Mission: " + (mission or "---"))[:20], 0, 37)
                except Exception:
                    pass
                try:
                    self.f_med.write(("ID: " + (device_id or "---"))[:20], 0, 50)
                except Exception:
                    pass
        else:
            # Home at y=24
            if self.f_med:
                try:
                    self.f_med.write(("Home: " + (home or "---"))[:20], 0, 24)
                except Exception:
                    pass

            # Room at y=37
            if self.f_med:
                try:
                    self.f_med.write(("Room: " + (room or "---"))[:20], 0, 37)
                except Exception:
                    pass

            # Bottom line at y=50: mission short_name when on a mission,
            # otherwise the Device ID (airOS / unassigned).
            if self.f_med:
                try:
                    if mission:
                        self.f_med.write(("Mission: " + mission)[:20], 0, 50)
                    else:
                        self.f_med.write(("Device ID: " + (device_id or "---"))[:20], 0, 50)
                except Exception:
                    pass

        try:
            fb.show()
        except Exception:
            pass

        try:
            gc.collect()
        except Exception:
            pass

    # -------------------------------------------------
    # SHOW LOADING (hollow API — call before the fetch)
    # -------------------------------------------------
    def show_loading(self):
        """Render immediately with API icon hollow, before the device fetch runs."""
        if _ch:
            try:
                _ch.set_api_ok(False)
            except Exception:
                pass
        self._render({})

    # -------------------------------------------------
    # SHOW (brief)
    # -------------------------------------------------
    def show(self, api_info, hold_ms=2000):
        self._render(api_info)
        if hold_ms:
            try:
                time.sleep_ms(int(hold_ms))
            except Exception:
                pass

    # -------------------------------------------------
    # SHOW LIVE (hold until click)
    # -------------------------------------------------
    def show_live(self, *, btn=None, api_info=None, tick_fn=None):
        """
        Blocks until button action.
        Returns:
          btn action string ("single", "double", "triple", etc.)
        tick_fn: optional background callable (e.g. telemetry tick), called every 500ms.
        """
        self._render(api_info)

        if btn is None:
            return None

        try:
            btn.reset()
        except Exception:
            pass

        _tick_next = time.ticks_ms()
        _tick_every = 500

        while True:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now, _tick_every)

            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action is not None:
                return action

            time.sleep_ms(25)