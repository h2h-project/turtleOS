# src/hal/timers.py — the one place hardware timer ids are handed out
#
# The ESP32-S3 has four hardware timers, ids 0-3. Whether Timer(-1) (a
# virtual/software timer) exists depends on the MicroPython build: on some it
# raises, and the send LED blink and Morse used it with the exception
# swallowed, so on those turtles they silently never ran (review B12).
#
# Every periodic timer in turtleOS takes its id from here. Two users must
# never share an id while both can run: Timer(n) returns the same hardware
# timer, and the second init() silently replaces the first one's callback.
#
#   id  user                                      lifetime
#   0   BOOT_LED     boot-pipeline LED blink      boot only; freed before run()
#   1   SEND_LED     send blink + Morse           one telemetry send (bg thread)
#   2   SERVO_GLIDE  Wrangler hand-control glide  while a glide runs
#   3   (spare)

BOOT_LED = 0
SEND_LED = 1
SERVO_GLIDE = 2


def periodic(tid, period_ms, callback):
    """Start a periodic timer on hardware id `tid`, falling back to a virtual
    timer on builds that have one. Returns the Timer, or None (logged) if
    neither works, so a caller can clean up instead of half-starting."""
    try:
        from machine import Timer
    except Exception:
        return None
    for t_id in (tid, -1):
        try:
            t = Timer(t_id)
            t.init(mode=Timer.PERIODIC, period=int(period_ms), callback=callback)
            return t
        except Exception as e:
            err = e
    print("[TIMER] id {} unavailable: {!r}".format(tid, err))
    return None
