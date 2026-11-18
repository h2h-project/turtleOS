# tests/telemetry_queue_host.py — HOST-side (CPython) checks for the offline
# queue in src/net/telemetry_client.py: chunked drain past the server's
# 1000-reading batch limit, partial failure, readings appended mid-drain,
# the block trim at QUEUE_MAX, and the live queue count. Does NOT run on the
# device:
#   cd device && python3 ../tests/telemetry_queue_host.py
import sys, types, os, tempfile, time, json
sys.path.insert(0, os.getcwd())
os.chdir(tempfile.mkdtemp())

clock = [0]
time.ticks_ms = lambda: clock[0]
time.ticks_diff = lambda a, b: a - b
time.sleep_ms = lambda ms: None

MAX_BATCH = 1000            # hopeTurtles.org deviceApiController.js
server = {"got": [], "posts": 0, "fail_at": None, "during": None}


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = json.dumps(body)

    def close(self):
        pass


def post(url, data=None, headers=None, timeout=None):
    server["posts"] += 1
    clock[0] += 1000
    if server["fail_at"] is not None and server["posts"] == server["fail_at"]:
        raise OSError(110)
    readings = json.loads(data)
    if len(readings) > MAX_BATCH:
        return Resp(400, {"ok": False, "error": "batch_too_large"})
    if server["during"]:
        server["during"]()
    server["got"].extend(r["recorded_at"] for r in readings)
    return Resp(200, {"ok": True, "accepted": len(readings)})


sys.modules["urequests"] = types.SimpleNamespace(post=post)

from src.net import telemetry_client as tc   # noqa: E402

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def reset(n):
    for f in (tc.QUEUE_FILE, tc.QUEUE_FILE + ".tmp"):
        try:
            os.remove(f)
        except OSError:
            pass
    tc._qstate.clear()
    tc._qstate.update({"len": None, "drained": 0})
    server.update(got=[], posts=0, fail_at=None, during=None)
    c = tc.TelemetryClient("http://x", "id", "key")
    for i in range(n):
        c.enqueue({"recorded_at": i})
    return c


def file_ts():
    with open(tc.QUEUE_FILE) as f:
        return [json.loads(x)["recorded_at"] for x in f if x.strip()]


# 1. 2500 readings: more than the server takes in one batch.
c = reset(2500)
check("count before drain", tc.queue_count() == 2500)
ok, sent = c.flush_queue()
check("2500 drained ok", ok and sent == 2500)
check("every reading delivered once, in order", server["got"] == list(range(2500)))
check("chunks within the server limit", server["posts"] == 25)
check("queue empty after", tc.queue_count() == 0 and file_ts() == [])

# 2. A chunk fails mid-drain: delivered ones are cut, the rest stay.
c = reset(450)
server["fail_at"] = 3
ok, sent = c.flush_queue()
check("partial: ok because the API answered", ok and sent == 200)
check("partial: undelivered kept in order", file_ts() == list(range(200, 450)))
check("partial: count", tc.queue_count() == 250)
server["fail_at"] = None
ok, sent = c.flush_queue()
check("partial: resumes", ok and sent == 250 and file_ts() == [])

# 3. First chunk fails: nothing lost, reported as failure.
c = reset(150)
server["fail_at"] = 1
ok, sent = c.flush_queue()
check("first chunk fails -> not ok", (ok, sent) == (False, 0))
check("first chunk fails -> all kept", file_ts() == list(range(150)))

# 4. Readings appended during a POST survive and are delivered.
c = reset(300)
extra = iter(range(10000, 10003))
server["during"] = lambda: c.enqueue({"recorded_at": next(extra)}) if server["posts"] <= 3 else None
ok, sent = c.flush_queue()
check("mid-drain appends delivered", ok and sorted(server["got"]) == list(range(300)) + [10000, 10001, 10002])
check("mid-drain appends: queue empty", file_ts() == [])

# 5. Time budget stops the round; the next one finishes.
c = reset(500)
ok, sent = c.flush_queue(budget_ms=2500)
check("budget stops early", ok and sent == 300 and tc.queue_count() == 200)
ok, sent = c.flush_queue()
check("budget: next round finishes", sent == 200 and file_ts() == [])

# 6. Cap: the oldest QUEUE_TRIM go in one block.
c = reset(tc.QUEUE_MAX)
c.enqueue({"recorded_at": "new"})
ts = file_ts()
check("trim drops a block", len(ts) == tc.QUEUE_MAX - tc.QUEUE_TRIM + 1)
check("trim keeps newest", ts[0] == tc.QUEUE_TRIM and ts[-1] == "new")
check("trim count", tc.queue_count() == len(ts))

# 7. Corrupt and unterminated lines.
c = reset(5)
with open(tc.QUEUE_FILE, "a") as f:
    f.write("{garbage\n")
    f.write('{"recorded_at": 99}')            # still being written
tc._qstate["len"] = None
ok, sent = c.flush_queue()
check("corrupt skipped, unterminated kept", server["got"] == [0, 1, 2, 3, 4]
      and open(tc.QUEUE_FILE).read() == '{"recorded_at": 99}')

# 8. Legacy JSON-array queue is migrated, then drained.
c = reset(0)
with open(tc.QUEUE_FILE, "w") as f:
    json.dump([{"recorded_at": i} for i in range(1200)], f)
tc._qstate["len"] = None
ok, sent = c.flush_queue()
check("legacy array drained", ok and sent == 1200 and server["posts"] == 12)

print()
print("ALL PASS" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
