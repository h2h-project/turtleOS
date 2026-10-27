# src/drivers/servo_power.py — sail-servo power rail (Pololu #5421 isolated SSR)
#
# The SSR's EN pin (XIAO underside pad MTDO / GPIO40, HAL servo_power_pin())
# switches the 2S servo pack's + lead into the LM2596 buck. Cutting it saves
# the buck's ~5-10 mA idle draw. Wiring: adding_servo_switch.md.
#
# servo_power_mode (config):
#   "auto"  the rail comes on when something writes the servo and goes off
#           after servo_power_idle_s with no servo write
#   "on"    the rail stays on (the behaviour before the switch existed)
#   "off"   the rail stays off and servo writes are skipped
#
# Every servo write calls request() first: the Servo driver (autopilot,
# luff sweeps, Wrangler hand control) and the Servo screen's wind-finder.
# tick() runs from run()'s _bg_tick and does the idle cut-off.
#
# Turning the rail off stops the servo PWM and drives the signal pin low
# FIRST, so an unpowered servo isn't fed through its signal line.
#
# On boards with no power pin every call is a no-op and request() returns
# True, so the servo behaves as always-powered.

import time

# Wait after switching the rail on before the first pulse, so the LM2596
# output is up. Paid once per power-up, never on later writes.
SETTLE_MS = 100

MODES = ("auto", "on", "off")
DEFAULT_IDLE_S = 30

_pin = None          # machine.Pin, or False: no power pin on this board
_on = False
_last_ms = 0         # last servo write (ticks_ms)
_mode = "auto"
_idle_s = DEFAULT_IDLE_S
_present = True      # cfg servo_present


def _get_pin():
    global _pin
    if _pin is None:
        n = None
        try:
            from src.hal.board import servo_power_pin
            n = servo_power_pin()
        except Exception:
            pass
        if n is None:
            _pin = False
        else:
            try:
                from machine import Pin
                _pin = Pin(int(n), Pin.OUT, value=0)
            except Exception as e:
                print("[SERVO] power pin init failed:", repr(e))
                _pin = False
    return _pin


def configure(cfg):
    """Pick up servo_power_mode / servo_power_idle_s / servo_present."""
    global _mode, _idle_s, _present
    if not isinstance(cfg, dict):
        return
    m = cfg.get("servo_power_mode", "auto")
    _mode = m if m in MODES else "auto"
    try:
        _idle_s = int(cfg.get("servo_power_idle_s", DEFAULT_IDLE_S))
    except Exception:
        _idle_s = DEFAULT_IDLE_S
    _present = bool(cfg.get("servo_present", True))


def init(cfg=None):
    """Drive the EN pin low (rail off). Call as early in boot as possible:
    the pin floats until then."""
    configure(cfg)
    _get_pin()


def available():
    """True when this board has a servo power switch."""
    return _get_pin() is not False


def is_on():
    """Rail state: True / False, or None on a board with no switch."""
    if _get_pin() is False:
        return None
    return _on


def mode():
    return _mode


def touch():
    """Count as servo activity without touching the pin (glide steps run
    from a timer callback and must not sleep)."""
    global _last_ms
    _last_ms = time.ticks_ms()


def request():
    """Power the rail for a servo write. Returns False when the rail is held
    off (mode "off" or servo_present false): skip the write."""
    global _on, _last_ms
    p = _get_pin()
    if p is False:
        return True
    if _mode == "off" or not _present:
        return False
    _last_ms = time.ticks_ms()
    if not _on:
        p.value(1)
        _on = True
        print("[SERVO] power on")
        time.sleep_ms(SETTLE_MS)
    return True


def _quiet_signal():
    """Stop servo PWM and hold the signal pin low."""
    try:
        from src.hal.board import servo_pin
        n = servo_pin()
    except Exception:
        n = None
    if n is None:
        return
    try:
        from src.drivers.servo import pin_released, _quiet_pwm
        pin_released(n)            # every Servo on n rebuilds its PWM; glides stop
        _quiet_pwm(n, 50).deinit()
    except Exception:
        pass
    try:
        from machine import Pin
        Pin(n, Pin.OUT, value=0)
    except Exception:
        pass


def off(reason=""):
    """Cut the rail now (signal pin quieted first)."""
    global _on
    p = _get_pin()
    if p is False or not _on:
        return
    _quiet_signal()
    p.value(0)
    _on = False
    print("[SERVO] power off", reason)


def tick(cfg=None):
    """Apply the mode and the idle cut-off. Called from run()'s _bg_tick."""
    configure(cfg)
    if _get_pin() is False:
        return
    if _mode == "on" and _present:
        if not _on:
            request()
        return
    if not _on:
        return
    if _mode == "off" or not _present:
        off("(" + ("mode off" if _mode == "off" else "no servo") + ")")
    elif time.ticks_diff(time.ticks_ms(), _last_ms) >= _idle_s * 1000:
        off("(idle {} s)".format(_idle_s))
