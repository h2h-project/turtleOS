# tests/wdt_spike.py — hardware check for src/app/watchdog.py (run once per
# MicroPython build):
#   mpremote connect auto run tests/wdt_spike.py
#
# 1. Can the timeout of a running watchdog be changed? (relax() depends on it)
# 2. What does machine.reset_cause() report, and which constants exist?
# 3. Does the watchdog actually reset the board?
#
# Expected output: "PASS: timeout changed", then about 5 s later the board
# resets. Run it a second time to see the reset cause the bite left behind.
import time
import machine

names = ("PWRON_RESET", "HARD_RESET", "WDT_RESET", "DEEPSLEEP_RESET", "SOFT_RESET")
print("reset_cause():", machine.reset_cause())
for n in names:
    print("  machine.{} = {}".format(n, getattr(machine, n, "missing")))

print("WDT 5 s, then re-created at 60 s; sleeping 10 s without feeding...")
machine.WDT(timeout=5000)
w = machine.WDT(timeout=60000)
for i in range(10):
    time.sleep_ms(1000)
    print(" ", i + 1, "s")
print("PASS: timeout changed (still alive after 10 s)")

print("Now 5 s again with no feed: the board should reset in about 5 s.")
machine.WDT(timeout=5000)
t0 = time.ticks_ms()
while True:
    time.sleep_ms(1000)
    print("  waiting", time.ticks_diff(time.ticks_ms(), t0) // 1000, "s")
    if time.ticks_diff(time.ticks_ms(), t0) > 20000:
        print("FAIL: no reset after 20 s")
        break
