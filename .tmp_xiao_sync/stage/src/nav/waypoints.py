# src/nav/waypoints.py — forward-only waypoint sequencer with flash persistence
#
# Target resolution order (first non-empty wins):
#   1. cfg["set_waypoints"]     — operator test-project route
#   2. cfg["set_destination"]    — operator test-project single target
#   3. cfg["mission_waypoints"]  — grand-mission route
#   4. cfg["mission_destination"]— grand-mission single target
# The set_* values are set by hand on the Destination screen (pond/field
# tests); when none are set the sequencer falls back to the grand mission.
#
# The active index persists to /nav_state.json so a watchdog reboot resumes
# mid-mission. A short signature of the resolved waypoint list is stored
# alongside it: if the resolved target changed since the last run (e.g. the
# operator set a test project), the index resets to 0 instead of resuming
# against a stale list.

import json

_STATE_PATH = "/nav_state.json"


def _clean_pairs(seq):
    """Filter an iterable of [lat, lon] to well-formed, in-range tuples."""
    out = []
    if not isinstance(seq, (list, tuple)):
        return out
    for wp in seq:
        try:
            lat, lon = float(wp[0]), float(wp[1])
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                out.append((lat, lon))
        except Exception:
            pass
    return out


class WaypointSequencer:
    def __init__(self, cfg):
        self._wps = self._resolve(cfg)
        self._idx = 0
        self._restore()

    @staticmethod
    def _resolve(cfg):
        wps = _clean_pairs(cfg.get("set_waypoints") or [])
        if not wps:
            wps = _clean_pairs([cfg.get("set_destination")] if cfg.get("set_destination") else [])
        if not wps:
            wps = _clean_pairs(cfg.get("mission_waypoints") or [])
        if not wps:
            wps = _clean_pairs([cfg.get("mission_destination")] if cfg.get("mission_destination") else [])
        return wps

    def _signature(self):
        # Rounded so tiny float noise doesn't count as a route change.
        return ";".join("{:.5f},{:.5f}".format(la, lo) for la, lo in self._wps)

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
                st = json.load(f)
            # Only resume the saved index if it belongs to the same route.
            if st.get("wp_sig") != self._signature():
                return
            idx = int(st.get("wp_index", 0))
            if 0 <= idx <= len(self._wps):
                self._idx = idx
        except Exception:
            pass

    def _persist(self):
        try:
            tmp = _STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"wp_index": self._idx, "wp_sig": self._signature()}, f)
            import os
            os.rename(tmp, _STATE_PATH)
        except Exception:
            pass
