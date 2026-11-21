# src/net/device_client.py — push operator-set nav fields to the server.
#
# PATCH <api_base>/v1/device with X-Device-Id / X-Device-Key headers and a
# JSON body of the set_* fields. Best-effort: the local config.json is
# authoritative for navigation; this is only so the hopeturtles.org
# dashboard can show what the operator set.
#
# Review B13: the PATCH used to run once, on the main thread (freezing the
# screen and Bluetooth for up to 6 s), and a failure was never retried, so a
# target set offline never reached the dashboard. Now:
#   - actions.save_set_fields() only calls mark_pending(), which writes the
#     device-owned flag file PENDING_FILE (it survives a reboot);
#   - the background thread (background_process._maybe_push_device) calls
#     push_pending() whenever WiFi is up, retrying every RETRY_MS on failure;
#   - step_api() calls it once at boot, after a successful device lookup.
# push_pending() always sends every set_* field from config.json as it is
# now. The server treats each as an absolute value, so re-sending is
# harmless and the latest target always wins.

try:
    import urequests as requests
except Exception:
    requests = None

try:
    import gc
except Exception:
    gc = None

try:
    import _thread
    _lock = _thread.allocate_lock()
except Exception:
    _lock = None

import os

SET_FIELDS = ("set_destination", "set_departure", "set_arrival",
              "set_waypoints", "set_short_name", "set_full_name")
PENDING_FILE = "device_patch_pending"   # device-owned; exists = dashboard behind
RETRY_MS = 300000                       # background retry after a failed PATCH

# [pending: None = not checked yet, generation: bumped by every mark_pending()]
_state = [None, 0]


def _locked(fn):
    if _lock is None:
        return fn()
    _lock.acquire()
    try:
        return fn()
    finally:
        _lock.release()


def mark_pending():
    """Note that the set_* fields changed and the dashboard needs them."""
    def _mark():
        _state[1] += 1
        _state[0] = True
        try:
            with open(PENDING_FILE, "w") as f:
                f.write("1")
        except Exception as e:
            print("[device_client] pending flag write failed:", repr(e))
    _locked(_mark)


def pending():
    """True while a set_* change hasn't reached the server yet."""
    if _state[0] is None:
        try:
            os.stat(PENDING_FILE)
            _state[0] = True
        except Exception:
            _state[0] = False
    return _state[0]


def generation():
    return _state[1]


def _current_set_fields(cfg=None):
    """The set_* fields from config.json as it is on flash now (read-only:
    this runs on the background thread, which must never write config)."""
    try:
        from config import _read_json_dict, CONFIG_FILE
        fresh = _read_json_dict(CONFIG_FILE)
        if fresh is not None:
            cfg = fresh
    except Exception:
        pass
    if not isinstance(cfg, dict):
        return None, {}
    return cfg, {k: cfg[k] for k in SET_FIELDS if k in cfg}


def push_pending(cfg=None):
    """PATCH the current set_* fields if a change is pending.
    Returns None if nothing was pending, else True/False for the PATCH."""
    if not pending():
        return None
    gen = _state[1]
    cfg, fields = _current_set_fields(cfg)
    if cfg is None or not fields:
        return False
    ok = patch_set_fields(cfg, fields)
    if ok:
        def _clear():
            # A mark_pending() during the request means newer values: keep
            # the flag so the next pass sends them.
            if _state[1] != gen:
                return
            _state[0] = False
            try:
                os.remove(PENDING_FILE)
            except Exception:
                pass
        _locked(_clear)
    return ok


def _endpoint(api_base):
    api_base = (api_base or "").strip().rstrip("/")
    if not api_base:
        return ""
    # Accept http://host and http://host/api alike.
    if api_base.endswith("/api"):
        return api_base + "/v1/device"
    return api_base + "/api/v1/device"


def _wifi_up():
    try:
        import network
        return network.WLAN(network.STA_IF).isconnected()
    except Exception:
        # No network module (host tests) — let the request attempt decide.
        return True


def patch_set_fields(cfg, fields):
    """PATCH the given set_* fields for this turtle. Returns True on 2xx."""
    if requests is None or not isinstance(cfg, dict) or not fields:
        return False

    device_id = str(cfg.get("device_id") or "").strip()
    device_key = str(cfg.get("device_key") or "").strip()
    url = _endpoint(cfg.get("api_base"))
    if not device_id or not device_key or not url:
        return False
    if not _wifi_up():
        return False

    # urequests adds Content-Type: application/json itself when json= is set.
    headers = {
        "X-Device-Id": device_id,
        "X-Device-Key": device_key,
        "Connection": "close",
    }

    if gc:
        try:
            gc.collect()
        except Exception:
            pass

    r = None
    try:
        r = requests.patch(url, json=fields, headers=headers, timeout=6)
        code = getattr(r, "status_code", 0)
        ok = 200 <= code < 300
        print("[device_client] PATCH", url, "->", code)
        return ok
    except Exception as e:
        print("[device_client] PATCH failed:", repr(e))
        return False
    finally:
        try:
            if r is not None:
                r.close()
        except Exception:
            pass
