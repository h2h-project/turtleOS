# src/net/telemetry_client.py  (LOW-MEM, Pico-friendly)
#
# Updated:
# - Reads tiny JSON response on success / 202 to extract server_now (unix seconds)
# - Treats 202 ignored as success (do not re-queue bogus boot readings)
# - Prints:
#     True Sent
#     Device Time : dd/mm/yy hh:mm:ss
#     Server Time : dd/mm/yy hh:mm:ss
#     Clock Drift : N seconds
#     #########################################
# - Normalizes api_base so both of these work:
#     http://air2.earthen.io
#     http://air2.earthen.io/api

import time

try:
    import gc
except Exception:
    gc = None

try:
    import urequests as requests
except Exception:
    requests = None

# Reported to the server as X-Firmware-Version so the dashboard can flag
# devices that need an OS update. booter.py is the single source of truth.
try:
    from src.app.booter import VERSION_NUM as _FW_VERSION
except Exception:
    _FW_VERSION = None

QUEUE_FILE = "telemetry_queue.json"
QUEUE_MAX = 5000
# At the cap, drop this many of the oldest readings in one rewrite, so a full
# queue costs one file rewrite per QUEUE_TRIM new readings instead of one per
# reading (the file is ~2 MB at the cap).
QUEUE_TRIM = 500
# Readings per batch POST. The server rejects batches over 1000
# (hopeTurtles.org deviceApiController.js MAX_BATCH); small chunks also keep
# each request short enough to finish over weak WiFi.
FLUSH_CHUNK = 100
# One flush_queue() call stops starting new chunks after this long, so the
# background thread gets back to its loop; the next send carries on.
FLUSH_BUDGET_MS = 90000
_COPY_BLOCK = 4096

# Queue state shared by every TelemetryClient instance (the scheduler and the
# background thread each build their own) and by both threads.
#   len     — lines in QUEUE_FILE, None until first counted
#   drained — lines at the head of QUEUE_FILE already accepted by the server
#             but not yet cut from the file (only during a flush)
_qstate = {"len": None, "drained": 0}

try:
    import _thread
    _qlock = _thread.allocate_lock()
except Exception:
    _qlock = None


def _lock():
    if _qlock is not None:
        _qlock.acquire()


def _unlock():
    if _qlock is not None:
        _qlock.release()


def _count_lines():
    """Non-blank lines in QUEUE_FILE (0 if absent). Caller holds the lock."""
    n = 0
    try:
        with open(QUEUE_FILE, "r") as f:
            for line in f:
                if line.strip():
                    n += 1
    except OSError:
        pass
    return n


def queue_count():
    """Readings still waiting to be sent. Used by every on-screen and
    Bluetooth queue count, so a drain in progress counts down live."""
    _lock()
    try:
        if _qstate["len"] is None:
            _qstate["len"] = _count_lines()
        return max(0, _qstate["len"] - _qstate["drained"])
    finally:
        _unlock()
        _gc_collect()


def _rewrite_tail(skip_bytes=0, skip_lines=0):
    """Cut the head off QUEUE_FILE: drop the first skip_bytes bytes, then the
    next skip_lines lines, and keep the rest byte-for-byte. Streams in blocks
    (no JSON parsing) and replaces the file with tmp+rename, which is atomic
    on LittleFS. Caller holds the lock. Returns the lines kept."""
    import os as _os
    tmp = QUEUE_FILE + ".tmp"
    kept = 0
    try:
        with open(QUEUE_FILE, "rb") as src, open(tmp, "wb") as dst:
            if skip_bytes:
                src.seek(skip_bytes)
            while skip_lines > 0:
                if not src.readline():
                    break
                skip_lines -= 1
            while True:
                block = src.read(_COPY_BLOCK)
                if not block:
                    break
                kept += block.count(b"\n")
                dst.write(block)
        _os.rename(tmp, QUEUE_FILE)
        return kept
    except OSError:
        try:
            _os.remove(tmp)
        except Exception:
            pass
        return None


def _gc_collect():
    if gc:
        try:
            gc.collect()
        except Exception:
            pass


def _json():
    """Prefer ujson to reduce overhead. Fallback to json if needed."""
    try:
        import ujson as j
        return j
    except Exception:
        import json as j
        return j


class TelemetryClient:
    def __init__(self, api_base, device_id, device_key):
        self.api_base = (api_base or "").strip().rstrip("/")

        # Accept either:
        #   http://host
        #   http://host/api
        # and normalize to the actual telemetry endpoint.
        if self.api_base.endswith("/api"):
            self.endpoint = self.api_base + "/v1/telemetry"
        else:
            self.endpoint = self.api_base + "/api/v1/telemetry"
        self.batch_endpoint = self.endpoint + "/batch"

        self.device_id = (device_id or "").strip()
        self.device_key = (device_key or "").strip()
        self._last_error = ""

    def last_error(self):
        return self._last_error or ""

    # --------------------------------------------------
    # Timestamp Formatter (Human Readable)
    # --------------------------------------------------
    def _fmt_epoch(self, epoch_s):
        # epoch_s is a Unix (1970-based) timestamp; time.localtime() on ESP32
        # MicroPython uses the 2000-01-01 epoch, so subtract the offset first.
        _MP_EPOCH_OFFSET = 946_684_800
        try:
            t = time.localtime(int(epoch_s) - _MP_EPOCH_OFFSET)
            return "%02d/%02d/%02d %02d:%02d:%02d" % (
                t[2], t[1], t[0] % 100,
                t[3], t[4], t[5]
            )
        except Exception:
            return str(epoch_s)

    def _payload_ts(self, payload):
        """Extract device timestamp (unix seconds) from payload."""
        try:
            if isinstance(payload, dict):
                ts = payload.get("recorded_at", None)
                if ts is None:
                    ts = payload.get("ts", None)
                return ts
        except Exception:
            pass
        return None

    def _print_send_stamp(self, payload, server_now=None, prefix="True Sent", extra=None):
        """Print human readable device/server time + drift + separator."""
        try:
            ts = self._payload_ts(payload)

            print(prefix)

            if ts is not None:
                print("Device Time :", self._fmt_epoch(ts))

            if server_now is not None:
                print("Server Time :", self._fmt_epoch(server_now))
                if ts is not None:
                    try:
                        drift = int(server_now) - int(ts)
                        print("Clock Drift :", drift, "seconds")
                    except Exception:
                        pass

            if extra:
                print(extra)

            print("#########################################")
        except Exception:
            pass

    # ----------------------------
    # Queue Handling
    # ----------------------------
    def _load_queue(self):
        """Load queue from JSONL file. Each line is one JSON object.
        Corrupt lines are skipped. Legacy JSON-array format is auto-migrated."""
        j = _json()
        items = []
        try:
            with open(QUEUE_FILE, "r") as f:
                legacy = False
                first_line = True
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if first_line:
                        first_line = False
                        if stripped.startswith("["):
                            legacy = True
                            break
                    try:
                        obj = j.loads(stripped)
                        if isinstance(obj, dict):
                            items.append(obj)
                    except Exception:
                        pass  # corrupt line — skip, keep the rest

            if legacy:
                # Old format: one big JSON array. Parse and rewrite as JSONL.
                try:
                    with open(QUEUE_FILE, "r") as f:
                        q = j.load(f)
                    if isinstance(q, list):
                        items = [x for x in q if isinstance(x, dict)]
                        self._save_queue(items)  # rewrite in JSONL immediately
                except Exception:
                    items = []

        except OSError:
            pass
        finally:
            _gc_collect()

        _qstate["len"] = len(items)
        return items

    def _save_queue(self, q):
        """Write queue as JSONL via tmp+rename — atomic on LittleFS."""
        j = _json()
        tmp = QUEUE_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                for item in q:
                    try:
                        f.write(j.dumps(item) + "\n")
                    except Exception:
                        pass
            import os as _os
            _os.rename(tmp, QUEUE_FILE)
            _qstate["len"] = len(q)
        except Exception:
            try:
                import os as _os
                _os.remove(tmp)
            except Exception:
                pass
        finally:
            _gc_collect()

    def enqueue(self, payload):
        """Append one reading to the queue.
        Direct append (safe: only the new line risks corruption on power loss).
        At QUEUE_MAX the oldest QUEUE_TRIM readings are cut in one rewrite."""
        j = _json()
        try:
            line = j.dumps(payload) + "\n"
        except Exception:
            return
        _lock()
        try:
            if _qstate["len"] is None:
                _qstate["len"] = _count_lines()
            if _qstate["len"] >= QUEUE_MAX:
                # Readings a flush has already delivered go first; they are
                # the oldest anyway. A flush in progress sees drained reset
                # and keeps its cursor valid by re-reading from the top.
                drop = max(QUEUE_TRIM, _qstate["drained"])
                kept = _rewrite_tail(skip_lines=drop)
                if kept is not None:
                    print("telemetry queue: full, dropped", _qstate["len"] - kept, "oldest readings")
                    _qstate["len"] = kept
                    _qstate["drained"] = 0
                    _qstate["trimmed"] = _qstate.get("trimmed", 0) + 1
            with open(QUEUE_FILE, "a") as f:
                f.write(line)
            _qstate["len"] += 1
        except Exception:
            pass
        finally:
            _unlock()
            _gc_collect()

    # ----------------------------
    # HTTP Send (LOW MEM)
    # ----------------------------
    def _post_batch(self, payloads, timeout_s=8):
        """POST a list of payloads to /api/v1/telemetry/batch.
        Returns: (ok: bool, accepted: int, msg: str)"""
        if not requests:
            return False, 0, "no_urequests"
        if not self.device_id or not self.device_key:
            return False, 0, "missing_device_auth"
        if not payloads:
            return True, 0, "empty"

        headers = {
            "Content-Type": "application/json",
            "X-Device-Id": self.device_id,
            "X-Device-Key": self.device_key,
        }
        if _FW_VERSION:
            headers["X-Firmware-Version"] = str(_FW_VERSION)

        _gc_collect()
        j = _json()
        r = None
        try:
            body = j.dumps(payloads)
            r = requests.post(self.batch_endpoint, data=body, headers=headers, timeout=timeout_s)
            try:
                del body
            except Exception:
                pass

            status = getattr(r, "status_code", None)
            if status is None:
                return False, 0, "no_status"
            status = int(status)

            accepted = len(payloads)
            msg = "OK"
            try:
                resp_text = r.text
                if resp_text:
                    resp_obj = j.loads(resp_text)
                    if isinstance(resp_obj, dict):
                        if resp_obj.get("accepted") is not None:
                            accepted = int(resp_obj["accepted"])
                        if resp_obj.get("message"):
                            msg = str(resp_obj["message"])
            except Exception:
                pass

            if 200 <= status < 300:
                return True, accepted, msg
            return False, 0, "HTTP {}".format(status)

        except MemoryError:
            _gc_collect()
            return False, 0, "ENOMEM"
        except OSError as e:
            _gc_collect()
            try:
                code = e.args[0]
            except Exception:
                code = "?"
            return False, 0, "EXC OSError({})".format(code)
        except Exception as e:
            _gc_collect()
            return False, 0, "EXC {}".format(repr(e))
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
                try:
                    del r
                except Exception:
                    pass
            _gc_collect()

    def _post(self, payload, timeout_s=5):
        """
        Returns:
          (ok: bool, msg: str, server_now: int|None, ignored: bool)
        """
        if not requests:
            return False, "no_urequests", None, False

        if not self.device_id or not self.device_key:
            return False, "missing_device_auth", None, False

        headers = {
            "Content-Type": "application/json",
            "X-Device-Id": self.device_id,
            "X-Device-Key": self.device_key,
        }
        if _FW_VERSION:
            headers["X-Firmware-Version"] = str(_FW_VERSION)

        _gc_collect()
        j = _json()

        r = None
        try:
            body = j.dumps(payload)

            r = requests.post(self.endpoint, data=body, headers=headers, timeout=timeout_s)

            try:
                del body
            except Exception:
                pass

            status = getattr(r, "status_code", None)
            server_now = None
            ignored = False
            msg = "OK"

            # Read only small JSON response body if available
            resp_obj = None
            try:
                resp_text = r.text
                if resp_text:
                    try:
                        resp_obj = j.loads(resp_text)
                    except Exception:
                        resp_obj = None
            except Exception:
                resp_obj = None

            if isinstance(resp_obj, dict):
                try:
                    if resp_obj.get("server_now", None) is not None:
                        server_now = int(resp_obj.get("server_now"))
                except Exception:
                    server_now = None

                try:
                    if resp_obj.get("ignored"):
                        ignored = True
                        reason = resp_obj.get("reason", None)
                        if reason:
                            msg = "ignored: {}".format(reason)
                        else:
                            msg = "ignored"
                except Exception:
                    pass

                # If API returns a message, keep it when not already set by ignored
                if msg == "OK":
                    try:
                        api_msg = resp_obj.get("message", None)
                        if api_msg:
                            msg = str(api_msg)
                    except Exception:
                        pass

            if status is None:
                return False, "no_status", None, False

            status = int(status)

            # 2xx all count as success from device perspective.
            # This is important because 202 means "accepted but ignored"
            # and should NOT be re-queued.
            if 200 <= status < 300:
                return True, msg, server_now, ignored

            # Try to include API response detail for non-2xx failures
            if isinstance(resp_obj, dict):
                try:
                    detail = resp_obj.get("message") or resp_obj.get("error")
                    if detail:
                        return False, "HTTP {} {}".format(status, detail), server_now, False
                except Exception:
                    pass

            return False, "HTTP {}".format(status), server_now, False

        except MemoryError:
            _gc_collect()
            return False, "ENOMEM", None, False

        except OSError as e:
            _gc_collect()
            try:
                code = e.args[0]
            except Exception:
                code = "?"
            return False, "EXC OSError({})".format(code), None, False

        except Exception as e:
            _gc_collect()
            return False, "EXC {}".format(repr(e)), None, False

        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
                try:
                    del r
                except Exception:
                    pass
            _gc_collect()

    # ----------------------------
    # Public Send
    # ----------------------------
    def send(self, payload, retries=1):
        last_msg = ""

        for i in range(retries):
            ok, msg, server_now, ignored = self._post(payload, timeout_s=3)
            last_msg = msg
            self._last_error = msg

            if ok:
                if ignored:
                    self._print_send_stamp(
                        payload,
                        server_now=server_now,
                        prefix="Ignored",
                        extra=msg
                    )
                    return True, msg

                self._print_send_stamp(
                    payload,
                    server_now=server_now,
                    prefix="True Sent",
                    extra=msg if msg and msg != "OK" else None
                )
                _gc_collect()
                self.flush_queue()
                return True, "sent"

            if i < retries - 1:
                try:
                    time.sleep_ms(500)
                except Exception:
                    time.sleep(1)

        self.enqueue(payload)
        return False, "queued: {}".format(last_msg)

    def _read_chunk(self, offset):
        """Read up to FLUSH_CHUNK readings starting at byte offset.
        Returns (items, lines, new_offset). Corrupt lines are skipped but
        still consumed; a last line with no newline is still being written
        and is left for next time."""
        j = _json()
        items = []
        lines = 0
        try:
            with open(QUEUE_FILE, "rb") as f:
                if offset:
                    f.seek(offset)
                while len(items) < FLUSH_CHUNK:
                    raw = f.readline()
                    if not raw or not raw.endswith(b"\n"):
                        break
                    offset += len(raw)
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    lines += 1
                    try:
                        obj = j.loads(stripped)
                        if isinstance(obj, dict):
                            items.append(obj)
                    except Exception:
                        pass
        except OSError:
            pass
        return items, lines, offset

    def _is_legacy_queue(self):
        try:
            with open(QUEUE_FILE, "rb") as f:
                return f.read(1) == b"["
        except OSError:
            return False

    def flush_queue(self, budget_ms=FLUSH_BUDGET_MS):
        """Drain the queue in batch POSTs of FLUSH_CHUNK readings.

        Walks the file with a byte cursor; each chunk counts as delivered once
        the server answers 2xx. The delivered head is cut from the file in one
        rewrite at the end (also after a failed chunk), so a 5000-reading
        backlog costs one rewrite, not one per chunk. Readings appended
        meanwhile are kept. A reset mid-drain resends the chunks since the
        last rewrite; the server ignores the duplicates (INSERT IGNORE on
        turtle + recorded_at).

        Stops at the first failed chunk or once budget_ms has passed; the next
        send or flush resumes from what is left.

        Returns (ok: bool, sent: int). ok is True when the queue was empty or
        at least one chunk was delivered (the API answered); sent counts the
        queued lines delivered."""
        if self._is_legacy_queue():
            self._load_queue()  # migrates the old JSON-array file to JSONL

        if queue_count() == 0:
            return True, 0

        _gc_collect()
        total = queue_count()
        print("telemetry queue: flushing", total, "buffered readings in chunks of", FLUSH_CHUNK)

        t0 = time.ticks_ms()
        offset = 0
        sent = 0
        failed_msg = None
        trims = _qstate.get("trimmed", 0)
        while True:
            items, lines, new_offset = self._read_chunk(offset)
            if not lines:
                break
            if items:
                ok, accepted, msg = self._post_batch(items)
                self._last_error = msg
                if not ok:
                    failed_msg = msg
                    break
            # A cap trim during the POST rewrote the file under the cursor.
            # Nothing is lost (the trimmed lines were the oldest, including
            # this chunk); restart from the top of the new file.
            _lock()
            try:
                if _qstate.get("trimmed", 0) != trims:
                    trims = _qstate.get("trimmed", 0)
                    offset = 0
                else:
                    offset = new_offset
                    _qstate["drained"] += lines
                sent += lines
            finally:
                _unlock()
            if items:
                print("telemetry queue: chunk sent ({}/{} accepted), {} left".format(
                    accepted, len(items), queue_count()))
            if time.ticks_diff(time.ticks_ms(), t0) > budget_ms:
                print("telemetry queue: flush budget used, resuming on next send")
                break
            _gc_collect()

        # Cut the delivered head off the file, keeping anything appended
        # while the POSTs ran.
        _lock()
        try:
            if _qstate.get("trimmed", 0) != trims:
                offset = 0  # a cap trim already cut the head; cursor is stale
            if offset:
                kept = _rewrite_tail(skip_bytes=offset)
                if kept is not None:
                    _qstate["len"] = kept
            _qstate["drained"] = 0
        finally:
            _unlock()
            _gc_collect()

        if failed_msg is not None:
            print("telemetry queue: batch flush failed:", failed_msg,
                  "- sent", sent, "this round, will retry next send")
            return sent > 0, sent
        print("telemetry queue: flushed", sent, "readings,", queue_count(), "left")
        return True, sent
