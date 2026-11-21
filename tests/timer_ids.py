# tests/timer_ids.py — review B12: which timers this MicroPython build has,
# and does the send LED blink on its registered id?
#
#   mpremote connect auto exec "from src.app import watchdog; watchdog.relax()"
#   mpremote connect auto run tests/timer_ids.py
#
# Prints whether Timer(-1) (virtual) and hardware ids 0-3 can be started, then
# blinks the button LED (GPIO2) at 2 Hz for 3 s on timers.SEND_LED the way a
# telemetry send does. If the Timer(-1) line says FAIL, this build is one where
# the send blink and Morse never ran before turtleOS 2.7.

import time
from machine import Pin, Timer

print("MicroPython timers on this build:")
for tid in (-1, 0, 1, 2, 3):
    try:
        t = Timer(tid)
        t.init(mode=Timer.PERIODIC, period=1000, callback=lambda _t: None)
        t.deinit()
        print("  Timer({:>2})  ok".format(tid))
    except Exception as e:
        print("  Timer({:>2})  FAIL {!r}".format(tid, e))

from src.hal import timers                      # noqa: E402
from src.hal.board import btn_led_pin           # noqa: E402

led = Pin(btn_led_pin(), Pin.OUT)
state = [0]
count = [0]


def _cb(_t):
    state[0] ^= 1
    count[0] += 1
    led.value(state[0])


t = timers.periodic(timers.SEND_LED, 250, _cb)
if t is None:
    print("SEND_LED timer: FAIL (see [TIMER] line above)")
else:
    print("Blinking the button LED on timer id {} for 3 s...".format(timers.SEND_LED))
    time.sleep_ms(3000)
    t.deinit()
    led.value(0)
    print("callbacks fired: {} (expect ~12) -> {}".format(
        count[0], "OK" if count[0] >= 10 else "FAIL"))
