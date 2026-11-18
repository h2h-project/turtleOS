# tests/watchdog_host.py — HOST-side (CPython) checks for src/app/watchdog.py:
# start / feed / relax, the config switch, crash logging, the crash-loop
# back-off and the healthy clear. Does NOT run on the device:
#   cd device && python3 ../tests/watchdog_host.py
import sys, types, os, tempfile, time, json
sys.path.insert(0, os.getcwd())
os.chdir(tempfile.mkdtemp())

clock = [1000]
time.ticks_ms = lambda: clock[0]
time.ticks_diff = lambda a, b: a - b
slept = []
time.sleep_ms = lambda ms: slept.append(ms)

made = []       # timeouts of every WDT created, in order
fed = []
resets = []


class WDT:
    def __init__(self, timeout=None):
        made.append(timeout)

    def feed(self):
        fed.append(made[-1])


class RTC:
    def datetime(self):
        return (2026, 11, 16, 0, 12, 30, 5, 0)


cause = [1]
machine = types.SimpleNamespace(
    WDT=WDT, RTC=RTC, PWRON_RESET=1, HARD_RESET=2, WDT_RESET=3,
    DEEPSLEEP_RESET=4, SOFT_RESET=5,
    reset_cause=lambda: cause[0], reset=lambda: resets.append(1))
sys.modules["machine"] = machine

from src.app import watchdog as wd

# feed() before start() is a no-op.
wd.feed()
assert fed == []

# No config.json: enabled by default. Normal boot starts at the boot timeout.
assert wd.enabled() is True
assert wd.boot(debug=False) is True and made == [120000] and wd.active()
wd.feed()
assert fed == [120000]

# Arming for run(): the timeout comes from config, clamped.
assert wd.timeout_s({}) == 60 and wd.timeout_s({"watchdog_timeout_s": 5}) == 20
assert wd.timeout_s({"watchdog_timeout_s": 9999}) == 600 and wd.timeout_s({"watchdog_timeout_s": "x"}) == 60
assert wd.start(wd.timeout_s({"watchdog_timeout_s": 45})) and made[-1] == 45000

# relax(): 24 h, and no longer "active".
assert wd.relax() and made[-1] == 86400000 and not wd.active()

# Debug mode never starts one; it relaxes only after a soft reset.
n = len(made)
cause[0] = 1
assert wd.boot(debug=True) is False and len(made) == n
cause[0] = 5
assert wd.boot(debug=True) is False and made[-1] == 86400000 and len(made) == n + 1

# watchdog_enabled false in config.json: start() refuses, boot() relaxes a survivor.
json.dump({"watchdog_enabled": False}, open("config.json", "w"))
wd._enabled = None
n = len(made)
assert wd.enabled() is False and wd.start(60) is False and len(made) == n
assert wd.boot(debug=False) is False and made[-1] == 86400000
json.dump({"watchdog_enabled": "true"}, open("config.json", "w"))
wd._enabled = None
assert wd.enabled() is True

# Crash records: traceback appended, count bumped, back-off after 3 in a row.
def crash(msg):
    try:
        raise ValueError(msg)
    except Exception as e:
        return e

assert wd.crash_count() == 0
assert [wd.reset_delay_s(i) for i in (1, 3, 4, 9)] == [3, 3, 600, 600]
for i in range(1, 4):
    slept.clear()
    wd.handle_crash(crash("boom %d" % i))
    assert wd.crash_count() == i and len(resets) == i and len(slept) == 3, (i, slept)
slept.clear()
wd.handle_crash(crash("boom 4"))
assert wd.crash_count() == 4 and len(slept) == 600 and len(resets) == 4
log = open(wd.CRASH_FILE).read()
assert log.count("--- 2026-11-16 12:30:05 ---") == 4 and "boom 1" in log and "boom 4" in log, log

# Ctrl-C during the wait relaxes the watchdog and propagates, with no reset.
def _interrupt(ms):
    raise KeyboardInterrupt
time.sleep_ms = _interrupt
wd.start(60)
try:
    wd.handle_crash(crash("boom 5"))
    raise AssertionError("KeyboardInterrupt swallowed")
except KeyboardInterrupt:
    pass
assert made[-1] == 86400000 and len(resets) == 4
time.sleep_ms = lambda ms: slept.append(ms)

# The crash log is capped: the newest entries survive.
for i in range(300):
    wd.record_crash(crash("filler %03d" % i))
assert os.stat(wd.CRASH_FILE)[6] <= wd.CRASH_FILE_MAX + 400
log = open(wd.CRASH_FILE).read()
assert "filler 299" in log and "boom 1" not in log

# healthy(): nothing before 5 min, then the count is cleared, once.
assert wd.crash_count() > 0
wd.healthy(wd._start_ms + wd.HEALTHY_AFTER_MS - 1)
assert wd.crash_count() > 0
wd.healthy(wd._start_ms + wd.HEALTHY_AFTER_MS)
assert wd.crash_count() == 0 and not os.path.exists(wd.CRASH_COUNT_FILE)

# note_boot(): names the cause and appends to the reset log.
cause[0] = 3
assert wd.note_boot() == "WDT_RESET"
cause[0] = 77
assert wd.note_boot() == "77"
lines = open(wd.RESET_LOG_FILE).read().splitlines()
assert lines[-2] == "2026-11-16 12:30:05 WDT_RESET crashes=0" and lines[-1].endswith("77 crashes=0"), lines

print("watchdog_host: all checks passed")
