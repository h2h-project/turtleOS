# src/nav/state_machine.py — Olive Turtle 5-state machine (module singleton)
#
# BOOT → ACQUIRE → SAIL_NAV → ARRIVAL   (normal mission)
# any state → SAFE                       (fault detected)
# any reboot → BOOT                      (module default — deterministic recovery)
#
# Module-level state keeps this import-cheap: the telemetry scheduler
# imports it on every send, and screens read it every frame.

BOOT = "BOOT"
ACQUIRE = "ACQUIRE"
SAIL_NAV = "SAIL_NAV"
ARRIVAL = "ARRIVAL"
SAFE = "SAFE"

# Allowed forward transitions. SAFE is reachable from any state (checked
# separately in set_state).
#
# Only NavController enters SAIL_NAV, after the journey resume stages
# (battery, GPS, wind; src/nav/controller.py). A journey start puts the
# turtle in ACQUIRE from any state and the controller does the rest; a
# journey end drops it back to ACQUIRE. SAFE clears to ACQUIRE by itself
# once its cause has gone (NavController._safe_cycle).
_VALID = {
    BOOT: (ACQUIRE,),
    ACQUIRE: (SAIL_NAV,),
    SAIL_NAV: (ARRIVAL, ACQUIRE),
    ARRIVAL: (ACQUIRE,),
    SAFE: (ACQUIRE,),
}

_state = [BOOT]
# Journey resume stage while in ACQUIRE ("BATTERY" / "GPS" / "WIND"), else
# None. Owned by NavController; kept here so telemetry and screens can read
# it without a NavController handle.
_resume = [None]


def resume_stage():
    return _resume[0]


def get_state():
    return _state[0]


def can_set(new):
    """True if set_state(new) would be allowed from the current state."""
    cur = _state[0]
    return new == cur or new == SAFE or new in _VALID.get(cur, ())


def set_state(new, reason=None):
    """Transition to `new` if allowed. Returns True on success."""
    cur = _state[0]
    if new == cur:
        return True
    if new != SAFE and new not in _VALID.get(cur, ()):
        print("[NAV] blocked transition {} -> {} ({})".format(cur, new, reason))
        return False
    _state[0] = new
    if reason:
        print("[NAV] state {} -> {} ({})".format(cur, new, reason))
    else:
        print("[NAV] state {} -> {}".format(cur, new))
    return True


def is_mission_active():
    return _state[0] in (SAIL_NAV, ARRIVAL)


def is_sail_nav():
    return _state[0] == SAIL_NAV


def display_name(state=None):
    """UI form: underscores shown as hyphens (SAIL_NAV → SAIL-NAV)."""
    s = _state[0] if state is None else state
    return s.replace("_", "-")
