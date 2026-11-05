# src/hal/i2c_retry.py — I2C bus wrapper that retries a failed transfer once.
#
# Why: with the AHT21 module on I2C_SYS, the FIRST transaction after the bus
# has been idle ~5-100 ms NACKs (OSError 19, ENODEV) for every device on the
# bus, and the immediate retry always succeeds (measured Nov 2026, 240/240;
# tests/oled_bus_test.py reproduces it). Back-to-back transfers never fail.
# The OLED driver's init is ~50 writes and treats the first error as "no
# OLED", so the turtle booted headless; the INA219 and barometer took the
# same failures silently.
#
# Only NACK/IO errors are retried. A timeout (110/116, a hung bus) is not:
# retrying would double the wait, and callers such as init_oled() report it
# separately. An absent device now costs two NACKs instead of one (~0.1 ms).
#
# Caveat: a NACK in the middle of a data write is retried from the start, so
# the bytes that did land are sent again. Every write in the tree is a
# register/command write that is safe to repeat, and an OLED page write
# repeated mid-frame is redrawn on the next frame.

_RETRY_ERRNOS = (5, 19)   # EIO, ENODEV — address or data NACK


class RetryI2C:
    """Wraps a machine.I2C. Same methods; everything not listed passes through."""

    def __init__(self, i2c):
        self._i2c = i2c

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except OSError as e:
            if e.args and e.args[0] in _RETRY_ERRNOS:
                return fn(*args)
            raise

    def writeto(self, *a):
        return self._call(self._i2c.writeto, *a)

    def writevto(self, *a):
        return self._call(self._i2c.writevto, *a)

    def readfrom(self, *a):
        return self._call(self._i2c.readfrom, *a)

    def readfrom_into(self, *a):
        return self._call(self._i2c.readfrom_into, *a)

    def readfrom_mem(self, *a, **kw):
        return self._call(lambda *x: self._i2c.readfrom_mem(*x, **kw), *a)

    def readfrom_mem_into(self, *a, **kw):
        return self._call(lambda *x: self._i2c.readfrom_mem_into(*x, **kw), *a)

    def writeto_mem(self, *a, **kw):
        return self._call(lambda *x: self._i2c.writeto_mem(*x, **kw), *a)

    def __getattr__(self, name):
        # scan(), init(), deinit(), ... — scan needs no retry: its first probe
        # (0x08) is an empty address, which absorbs the wake-up NACK.
        return getattr(self._i2c, name)
