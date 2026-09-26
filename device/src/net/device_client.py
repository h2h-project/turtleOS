# src/net/device_client.py — push operator-set nav fields to the server.
#
# PATCH <api_base>/v1/device with X-Device-Id / X-Device-Key headers and a
# JSON body of the set_* fields. Best-effort: the local config.json is
# authoritative for navigation; this is only so the hopeturtles.org
# dashboard can show what the operator set. Any failure (no WiFi, offline,
# server error) is swallowed — the next boot re-pushes from step_api().

try:
    import urequests as requests
except Exception:
    requests = None

try:
    import gc
except Exception:
    gc = None


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
