# tests/i2c_find_pins.py
# Run with: mpremote connect /dev/cu.usbmodem14101 run tests/i2c_find_pins.py
#
# Brute-forces SCL/SDA pin pairs across both I2C buses to find which one
# your devices respond on.  Tries board-appropriate pairs first.

import machine
import time
import sys

try:
    import uos
    _uname = uos.uname()
    print("Board:", _uname.machine)
    _platform = sys.platform
except Exception:
    _uname = None
    _platform = "unknown"

KNOWN = {
    0x0D: "QMC5883L",
    0x1E: "HMC5883L",
    0x36: "AS5600",
    0x38: "AHT10/AHT21",
    0x3C: "OLED",
    0x40: "INA219",
    0x53: "ENS160",
    0x62: "SCD41",
    0x68: "DS3231",
}

# RP2040 (Pico / Pico W) I2C pin map:
#   I2C0: SDA = GP0/4/8/12/16/20   SCL = GP1/5/9/13/17/21
#   I2C1: SDA = GP2/6/10/14/18/26  SCL = GP3/7/11/15/19/27
#
# ESP32-S3 / XIAO pairs listed after.

if _platform == "rp2":
    # (bus_id, scl, sda)
    PAIRS = [
        (0,  1,  0),   # default AirBuddy Pico wiring
        (0,  5,  4),
        (0,  9,  8),
        (0, 13, 12),
        (0, 17, 16),
        (0, 21, 20),
        (1,  3,  2),
        (1,  7,  6),
        (1, 11, 10),
        (1, 15, 14),
        (1, 19, 18),
        (1, 27, 26),
    ]
else:
    # ESP32 variants — all use I2C(0) with flexible pin assignment
    PAIRS = [
        (0,  9,  6),   # ESP32-S3-N16-R8
        (0,  6,  5),   # XIAO ESP32-S3 (D5/D4)
        (0, 22, 21),   # Classic ESP32
        (0,  5,  4),
        (0,  1,  0),
        (0, 13, 12),
        (0, 16, 17),
    ]

print()
print("Scanning {} pin pairs ...".format(len(PAIRS)))
print()

found_any = False

for entry in PAIRS:
    bus_id, scl_pin, sda_pin = entry
    try:
        i2c = machine.I2C(bus_id, scl=machine.Pin(scl_pin), sda=machine.Pin(sda_pin), freq=100000)
        time.sleep_ms(20)
        devices = i2c.scan()
        try:
            i2c.deinit()
        except Exception:
            pass
        if devices:
            print("  I2C({}) SCL={:2d} SDA={:2d} -> FOUND: {}".format(
                bus_id, scl_pin, sda_pin,
                ", ".join("0x{:02X}({})".format(d, KNOWN.get(d, "?")) for d in sorted(devices))
            ))
            found_any = True
        else:
            print("  I2C({}) SCL={:2d} SDA={:2d} -> nothing".format(bus_id, scl_pin, sda_pin))
    except Exception as e:
        print("  I2C({}) SCL={:2d} SDA={:2d} -> error: {}".format(bus_id, scl_pin, sda_pin, repr(e)))
    time.sleep_ms(30)

print()
if found_any:
    print("Update board_pico.py with the I2C bus + SCL/SDA pair that found your devices.")
else:
    print("No devices found on any pair.")
    print()
    print("Checklist:")
    print("  1. Pull-up resistors: 4.7k from SDA->3.3V AND SCL->3.3V (most common cause)")
    print("  2. Power: sensor VCC connected to 3.3V pin (NOT 5V), GND to GND")
    print("  3. Pins: on Pico, GP0=physical pin 1, GP1=physical pin 2 (top-left near USB)")
    print("  4. Connections: reseat all breadboard jumpers — they fail silently")
