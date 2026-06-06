# tests/sensor_debug.py
# Run with: mpremote connect /dev/cu.usbmodem14101 run tests/sensor_debug.py
#
# Quick check: QMC5883L headings + ENS160/AHT21 readings on the correct I2C bus.

from machine import I2C, Pin
import time, math

# Use board HAL so pins are always correct; 400 kHz matches the running app.
try:
    from src.hal.board import i2c_pins as _ip
    _id, SCL, SDA, FREQ = _ip()
except Exception:
    SCL, SDA, FREQ = 6, 5, 400_000

i2c = I2C(0, scl=Pin(SCL), sda=Pin(SDA), freq=FREQ)
print("I2C devices:", [hex(a) for a in i2c.scan()])
print()

# --- QMC5883L heading test (10 readings) ---
print("=== QMC5883L (0x0D) ===")
try:
    i2c.writeto_mem(0x0D, 0x0B, bytes([0x01]))  # SET/RESET
    i2c.writeto_mem(0x0D, 0x09, bytes([0x05]))  # OSR=512, 2G, 50Hz, continuous
    time.sleep_ms(50)
    for n in range(10):
        time.sleep_ms(100)
        d = i2c.readfrom_mem(0x0D, 0x00, 6)
        def s16(lo, hi):
            v = (hi << 8) | lo
            return v - 65536 if v >= 32768 else v
        x = s16(d[0], d[1]); y = s16(d[2], d[3]); z = s16(d[4], d[5])
        h = math.atan2(y, x) * 180 / math.pi % 360
        print("  #{}: X={:6d} Y={:6d} Z={:6d}  heading={:.1f}°".format(n, x, y, z, h))
except Exception as e:
    print("  FAIL:", repr(e))

print()

# --- AHT20/AHT21 temp/humidity (0x38) ---
print("=== AHT20/AHT21 (0x38) ===")
try:
    try:
        i2c.writeto(0x38, b"\xBA")      # soft reset (best-effort)
    except Exception:
        pass
    time.sleep_ms(30)
    # Try AHT20/AHT21 init (0xBE) then AHT10 fallback (0xE1)
    _init_ok = False
    for _cmd in (b"\xBE\x08\x00", b"\xE1\x08\x00"):
        try:
            i2c.writeto(0x38, _cmd)
            time.sleep_ms(20)
            _init_ok = True
            print("  init OK with", hex(_cmd[0]))
            break
        except Exception:
            pass
    if not _init_ok:
        raise OSError("init failed")
    i2c.writeto(0x38, b"\xAC\x33\x00") # trigger
    time.sleep_ms(100)
    d = i2c.readfrom(0x38, 6)
    rh_raw = ((d[1] & 0x0F) << 16) | (d[2] << 8) | d[3]
    t_raw  = ((d[3] & 0x0F) << 16) | (d[4] << 8) | d[5]
    rh = rh_raw / 1048576.0 * 100.0
    t  = t_raw  / 1048576.0 * 200.0 - 50.0
    print("  Temp: {:.1f} C   RH: {:.1f} %".format(t, rh))
except Exception as e:
    print("  FAIL:", repr(e))

print()

# --- ENS160 status + eCO2/TVOC (0x53) ---
print("=== ENS160 (0x53) ===")
try:
    fw = i2c.readfrom_mem(0x53, 0x10, 3)
    print("  FW version: {}.{}.{}".format(fw[2], fw[1], fw[0]))
    status = i2c.readfrom_mem(0x53, 0x20, 1)[0]
    mode_map = {0:"sleep", 1:"idle", 2:"standard", 3:"LP", 4:"ULP"}
    op_mode = status & 0x07
    validity = (status >> 2) & 0x03
    print("  DEVICE_STATUS: 0x{:02X}  (op_mode={}, validity={})".format(
        status, mode_map.get(op_mode, str(op_mode)), validity))

    # Put into standard mode (mode 2) if not already
    cur_mode = i2c.readfrom_mem(0x53, 0x10, 1)[0] & 0x07
    if cur_mode != 2:
        i2c.writeto_mem(0x53, 0x10, bytes([0x02]))
        time.sleep_ms(250)
        print("  Set to standard mode, waiting 250ms ...")

    aqi  = i2c.readfrom_mem(0x53, 0x21, 1)[0] & 0x07
    tvoc = int.from_bytes(i2c.readfrom_mem(0x53, 0x22, 2), 'little')
    eco2 = int.from_bytes(i2c.readfrom_mem(0x53, 0x24, 2), 'little')
    print("  AQI={} eCO2={} ppm  TVOC={} ppb".format(aqi, eco2, tvoc))
    if eco2 == 0 and tvoc == 0:
        print("  NOTE: zeros may be normal during ENS160 initial warm-up (~60s after first power-on)")
except Exception as e:
    print("  FAIL:", repr(e))

print()
print("Done.")
