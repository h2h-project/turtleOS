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

QUEUE_FILE = "telemetry_queue.json"
QUEUE_MAX = 777


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
        self._queue_len = None

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

        self._queue_len = len(items)
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
            self._queue_len = len(q)
        except Exception:
            try:
                import os as _os
                _os.remove(tmp)
            except Exception:
                pass
        finally:
            _gc_collect()

    def _enqueue(self, payload):
        """Append one reading to the queue.
        Direct append (safe: only the new line risks corruption on power loss).
        Compacts via tmp+rename only when the cap is reached."""
        j = _json()
        current = self._queue_len
        if current is None:
            self._load_queue()
            current = self._queue_len or 0

        if current >= QUEUE_MAX:
            # Load, trim to (QUEUE_MAX - 1) most recent, append new item, save atomically.
            q = self._load_queue()
            q = q[-(QUEUE_MAX - 1):]
            q.append(payload)
            self._save_queue(q)
        else:
            try:
                with open(QUEUE_FILE, "a") as f:
                    f.write(j.dumps(payload) + "\n")
                self._queue_len = current + 1
            except Exception:
                pass
            finally:
                _gc_collect()

    # ----------------------------
    # HTTP Send (LOW MEM)
    # ----------------------------
    def _post_batch(self, payloads, timeout_s=15):
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
            ok, msg, server_now, ignored = self._post(payload)
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

        self._enqueue(payload)
        return False, "queued: {}".format(last_msg)

    def flush_queue(self):
        """Drain all queued readings in one batch POST.
        Keeps queue intact on failure so the next successful send retries."""
        q = self._load_queue()
        if not q:
            return

        _gc_collect()
        print("telemetry queue: flushing", len(q), "buffered readings via batch")

        ok, accepted, msg = self._post_batch(q)
        self._last_error = msg

        if ok:
            self._print_send_stamp(
                q[-1],
                prefix="Batch Sent ({}/{} readings)".format(accepted, len(q)),
                extra=msg if msg and msg != "OK" else None
            )
            self._save_queue([])
        else:
            print("telemetry queue: batch flush failed:", msg, "- will retry next send")