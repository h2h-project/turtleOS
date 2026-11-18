# tests/wdt_crash.py — on the board: a crash must be logged and reset.
#   mpremote connect auto run tests/wdt_crash.py
# Runs the same handler device/main.py calls when run() raises. Expect
# "[WDT] crash #N in a row, resetting in 3 s", then a reset. Afterwards
# /crash.txt ends with this traceback and /crash_count holds N.
from src.app import watchdog


def _boom():
    raise ValueError("wdt_crash.py test crash")


try:
    _boom()
except Exception as e:
    watchdog.handle_crash(e)
