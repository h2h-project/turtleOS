# tests/wdt_hang.py — on the board: a hung main loop must reset the turtle.
#   mpremote connect auto run tests/wdt_hang.py
# Starts the firmware's watchdog at its shortest timeout (20 s) and then
# stops feeding it. The board should reset about 20 s later and boot
# normally; /reset_log.txt gains a line for the reset.
import time
from src.app import watchdog

watchdog._enabled = True
assert watchdog.start(watchdog.MIN_TIMEOUT_S), "watchdog did not start"
t0 = time.ticks_ms()
while True:                       # the hang: no feed() in here
    time.sleep_ms(1000)
    print("hung for", time.ticks_diff(time.ticks_ms(), t0) // 1000, "s")
