import usocket as socket
import time


# --- DNS resolve cache -------------------------------------------------
# socket.getaddrinfo() is a synchronous C call into the lwIP resolver that
# does not yield the MicroPython GIL, so while it runs NOTHING else executes
# — not the button poll, not the OLED, not the GPS drain — even though the
# caller is on the background thread. On a network with no reachable DNS it
# retries across both servers for 10-15 s, which froze the whole UI on every
# telemetry send.
#
# Note the socket timeout passed to request() cannot help: settimeout() is
# applied to the socket *after* the resolve, so it bounds connect/recv only.
# The only fix is to not call getaddrinfo, hence this cache.
#
# Failures are cached too (negative caching). Without that, a device
# associated to an AP with no working DNS — the common field case — pays the
# full stall again on every single send.
_DNS_TTL_MS = 600000        # 10 min: successful resolve
_DNS_FAIL_TTL_MS = 300000   # 5 min: failed resolve

# (host, port) -> (addrinfo_or_None, expiry_ticks_ms)
_dns_cache = {}


def invalidate_dns():
    """Drop all cached resolves. Call on WiFi (re)association — a new network
    means new DNS servers and possibly a different answer for the same host."""
    _dns_cache.clear()


def _resolve(host, port):
    """getaddrinfo() with a TTL cache in front of it. Raises OSError on a
    cached failure without touching the network."""
    key = (host, port)
    now = time.ticks_ms()

    ent = _dns_cache.get(key)
    if ent is not None and time.ticks_diff(ent[1], now) > 0:
        if ent[0] is None:
            raise OSError("dns fail (cached): " + host)
        return ent[0]

    try:
        ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0]
    except Exception:
        _dns_cache[key] = (None, time.ticks_add(now, _DNS_FAIL_TTL_MS))
        raise

    _dns_cache[key] = (ai, time.ticks_add(now, _DNS_TTL_MS))
    return ai


class Response:
    def __init__(self, f):
        self.raw = f
        self.encoding = "utf-8"
        self._cached = None

    def close(self):
        if self.raw:
            self.raw.close()
            self.raw = None
        self._cached = None

    @property
    def content(self):
        if self._cached is None:
            try:
                self._cached = self.raw.read()
            finally:
                self.raw.close()
                self.raw = None
        return self._cached

    @property
    def text(self):
        return str(self.content, self.encoding)

    def json(self):
        import ujson
        return ujson.loads(self.content)


def request(method, url, data=None, json=None, headers={}, timeout=None):
    try:
        proto, _, host, path = url.split("/", 3)
    except ValueError:
        proto, _, host = url.split("/", 2)
        path = ""

    if proto == "http:":
        port = 80
    elif proto == "https:":
        port = 443
    else:
        raise ValueError("Unsupported protocol: " + proto)

    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)

    ai = _resolve(host, port)
    s = socket.socket(ai[0], ai[1], ai[2])

    if timeout is not None:
        s.settimeout(timeout)   # applied before connect so handshake also respects it

    try:
        s.connect(ai[-1])
        if proto == "https:":
            import ussl
            s = ussl.wrap_socket(s, server_hostname=host)

        s.write(b"%s /%s HTTP/1.0\r\nHost: %s\r\n" % (
            method.encode(), path.encode(), host.encode()
        ))
        for k, v in headers.items():
            s.write(("%s: %s\r\n" % (k, v)).encode())

        if json is not None:
            import ujson
            data = ujson.dumps(json)
            s.write(b"Content-Type: application/json\r\n")

        if data:
            if isinstance(data, str):
                data = data.encode()
            s.write(b"Content-Length: %d\r\n" % len(data))

        s.write(b"\r\n")
        if data:
            s.write(data)

        l = s.readline()
        l = l.split(None, 2)
        status = int(l[1])
        reason = l[2].rstrip() if len(l) > 2 else b""

        while True:
            l = s.readline()
            if not l or l == b"\r\n":
                break

        resp = Response(s)
        resp.status_code = status
        resp.reason = reason
        return resp

    except OSError:
        s.close()
        raise


def head(url, **kw):
    return request("HEAD", url, **kw)

def get(url, **kw):
    return request("GET", url, **kw)

def post(url, **kw):
    return request("POST", url, **kw)

def put(url, **kw):
    return request("PUT", url, **kw)

def patch(url, **kw):
    return request("PATCH", url, **kw)

def delete(url, **kw):
    return request("DELETE", url, **kw)
