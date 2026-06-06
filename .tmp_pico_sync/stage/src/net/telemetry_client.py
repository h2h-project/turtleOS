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
        j = _json()
        try:
            with open(QUEUE_FILE, "r") as f:
                q = j.load(f)
            if isinstance(q, list):
                return q
            return []
        except Exception:
            return []
        finally:
            _gc_collect()

    def _save_queue(self, q):
        j = _json()
        try:
            with open(QUEUE_FILE, "w") as f:
                j.dump(q, f)
        except Exception:
            pass
        finally:
            _gc_collect()

    def _enqueue(self, payload, max_items=100):
        q = self._load_queue()
        try:
            q.append(payload)
            if len(q) > int(max_items):
                q = q[-int(max_items):]
        except Exception:
            q = [payload]
        self._save_queue(q)

    # ----------------------------
    # HTTP Send (LOW MEM)
    # ----------------------------
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
                # Flush at most 1 queued item per send cycle.
                # Doing 8+ back-to-back HTTP operations fragments the heap badly
                # enough to prevent 1280-byte screen module loads from succeeding.
                # The remaining queue drains on subsequent tick() calls.
                _gc_collect()
                self.flush_queue(max_to_try=1)
                return True, "sent"

            if i < retries - 1:
                try:
                    time.sleep_ms(500)
                except Exception:
                    time.sleep(1)

        self._enqueue(payload)
        return False, "queued: {}".format(last_msg)

    def flush_queue(self, max_to_try=10):
        """
        Flush a limited number of queued items.
        Stops on first failure to avoid long loops + repeated allocations when offline.
        """
        q = self._load_queue()
        if not q:
            return

        try:
            max_to_try = int(max_to_try)
        except Exception:
            max_to_try = 10
        if max_to_try < 1:
            max_to_try = 1

        new_q = []
        total = len(q)

        for idx in range(total):
            item = q[idx]

            if idx >= max_to_try:
                new_q.append(item)
                continue

            ok, msg, server_now, ignored = self._post(item)
            self._last_error = msg

            if not ok:
                # keep this item and the rest, then stop
                new_q.append(item)
                try:
                    for rest in q[idx + 1:]:
                        new_q.append(rest)
                except Exception:
                    pass
                break

            if ignored:
                self._print_send_stamp(
                    item,
                    server_now=server_now,
                    prefix="Ignored (flush)",
                    extra=msg
                )
            else:
                self._print_send_stamp(
                    item,
                    server_now=server_now,
                    prefix="True Sent (flush)",
                    extra=msg if msg and msg != "OK" else None
                )

        self._save_queue(new_q)