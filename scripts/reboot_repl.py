#!/usr/bin/env python3
"""Soft-reboot a MicroPython board and drop straight into its REPL.

Used by the sync scripts' open_repl(). `mpremote soft-reset repl` can't do
this: mpremote resets from the *raw* REPL, and MicroPython skips main.py
after a raw-REPL soft reset, so the boot log never appears. Here the reset
is sent from the friendly REPL (Ctrl-C to stop the app, Ctrl-B to leave raw
mode, Ctrl-D to reboot), and the terminal opens on the same port handle, so
nothing printed during boot is lost.

Needs pyserial — run it with mpremote's interpreter, which always has it.

    reboot_repl.py [PORT|auto]      Ctrl-] exits.
"""

import sys
import time

import serial
from serial.tools import list_ports
from serial.tools.miniterm import Miniterm


def resolve_port(port):
    if port and port != "auto":
        return port
    # Same rule as `mpremote connect auto`: first USB serial device.
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        if p.vid is not None:
            return p.device
    sys.exit("reboot_repl: no USB serial device found")


def main():
    port = resolve_port(sys.argv[1] if len(sys.argv) > 1 else "auto")
    ser = serial.Serial(port, 115200, timeout=0.2)

    # Stop the running app and discard its tail (KeyboardInterrupt traceback).
    ser.write(b"\r\x03\x03")
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(b"\x02")          # raw REPL -> friendly, so main.py runs on reset
    time.sleep(0.1)
    ser.reset_input_buffer()
    ser.write(b"\x04")          # soft reboot

    term = Miniterm(ser, echo=False, eol="crlf", filters=["direct"])
    term.exit_character = chr(0x1D)   # Ctrl-]
    term.menu_character = chr(0x14)   # Ctrl-T
    term.raw = False
    term.set_rx_encoding("utf-8", "replace")
    term.set_tx_encoding("utf-8")
    term.start()
    try:
        term.join(True)
    except KeyboardInterrupt:
        pass
    term.join()
    term.close()


if __name__ == "__main__":
    main()
