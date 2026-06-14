# src/nav/waypoints.py — forward-only waypoint sequencer with flash persistence
#
# The operator loads cfg["waypoints"] (list of [lat, lon]) before
# deployment; falls back to the single cfg["dest_coord"]. The active index
# persists to /nav_state.json so a watchdog reboot resumes mid-mission.

import json

_STATE_PATH = "/nav_state.json"


class WaypointSequencer:
    def __init__(self, cfg):
        wps = cfg.get("waypoints") or []
        if not wps:
            dest = cfg.get("dest_coord")
            if isinstance(dest, (list, tuple)) and len(dest) == 2:
                wps = [dest]
        self._wps = []
        for wp in wps:
            try:
                lat, lon = float(wp[0]), float(wp[1])
                if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                    self._wps.append((lat, lon))
            except Exception:
                pass
        self._idx = 0
        self._restore()

    def count(self):
        return len(self._wps)

    def index(self):
        return self._idx

    def current(self):
        """Active waypoint (lat, lon), or None when none remain."""
        if self._idx < len(self._wps):
            return self._wps[self._idx]
        return None

    def advance_if_arrived(self, lat, lon, radius_m=300):
        """Advance (forward only) when inside the arrival radius.
        Returns True if the sequencer advanced."""
        wp = self.current()
        if wp is None or lat is None or lon is None:
            return False
        from src.nav.bearing import distance_m
        if distance_m(lat, lon, wp[0], wp[1]) > float(radius_m):
            return False
        self._idx += 1
        self._persist()
        return True

    def is_final_reached(self):
        return len(self._wps) > 0 and self._idx >= len(self._wps)

    # ------------------------------------------------------------------

    def _restore(self):
        try:
            with open(_STATE_PATH) as f:
                idx = int(json.load(f).get("wp_index", 0))
            if 0 <= idx <= len(self._wps):
                self._idx = idx
        except Exception:
            pass

    def _persist(self):
        try:
            tmp = _STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"wp_index": self._idx}, f)
            import os
            os.rename(tmp, _STATE_PATH)
        except Exception:
            pass
