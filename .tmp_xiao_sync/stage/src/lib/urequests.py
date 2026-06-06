import usocket as socket


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

    ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0]
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
