# tests/i2c_scan.py
# Run with: mpremote run tests/i2c_scan.py
#
# Scans the I2C bus and checks for the HMC5883L magnetometer specifically.

from machine import I2C, Pin
import time

# Use the board HAL so pin map is always correct for whatever board is running.
try:
    from src.hal.board import i2c_pins as _i2c_pins
    _id, SCL_PIN, SDA_PIN, _freq = _i2c_pins()
except Exception:
    SCL_PIN = 6   # XIAO ESP32-S3 default
    SDA_PIN = 5

KNOWN = {
    0x0D: "QMC5883L (magnetometer clone / GY-271)",
    0x1E: "HMC5883L (magnetometer genuine)",
    0x36: "AS5600 (magnetic angle encoder)",
    0x38: "AHT10/AHT21 (temp/humidity)",
    0x3C: "OLED (SSD1306/SH1106)",
    0x40: "INA219 (battery monitor)",
    0x53: "ENS160 (CO2/TVOC)",
    0x62: "SCD41 (CO2)",
    0x68: "DS3231 (RTC)",
}

print("=" * 48)
print("  I2C bus scan  SCL={} SDA={}".format(SCL_PIN, SDA_PIN))
print("=" * 48)

i2c = I2C(0, scl=Pin(SCL_PIN), sda=Pin(SDA_PIN), freq=400000)

devices = i2c.scan()
if not devices:
    print("  No devices found — check wiring / power")
else:
    for addr in sorted(devices):
        label = KNOWN.get(addr, "unknown")
        print("  0x{:02X}  {}".format(addr, label))

print()

# ------------------------------------------------------------------
# HMC5883L targeted test
# ------------------------------------------------------------------
HMC_ADDR   = 0x1E
REG_ID_A   = 0x0A
REG_CRA    = 0x00
REG_CRB    = 0x01
REG_MODE   = 0x02
REG_DATA   = 0x03

print("--- HMC5883L probe ---")

if HMC_ADDR not in devices:
    print("  0x1E not found on bus.")
    print("  Check: SDA/SCL wiring, 3.3 V supply, pull-up resistors.")
else:
    # Read chip ID (registers 0x0A–0x0C must return b'H43')
    try:
        chip_id = i2c.readfrom_mem(HMC_ADDR, REG_ID_A, 3)
        print("  Chip ID bytes:", chip_id)
        if chip_id == b'H43':
            print("  ID OK — genuine HMC5883L")
        else:
            print("  WARNING: ID mismatch (expected b'H43') — may be a QMC5883L clone")
            print("  QMC5883L uses a different register map; this driver won't work with it.")
    except Exception as e:
        print("  ID read failed:", repr(e))

    # Configure continuous mode and take a reading
    try:
        # 8-sample avg, 15 Hz, normal; gain=1 (±1.3 Ga); continuous mode
        i2c.writeto_mem(HMC_ADDR, REG_CRA,  bytes([0b01110000]))
        i2c.writeto_mem(HMC_ADDR, REG_CRB,  bytes([0b00100000]))
        i2c.writeto_mem(HMC_ADDR, REG_MODE, bytes([0x00]))
        time.sleep_ms(70)   # first conversion takes ~67 ms at 15 Hz

        raw = i2c.readfrom_mem(HMC_ADDR, REG_DATA, 6)
        def s16(hi, lo):
            v = (hi << 8) | lo
            return v - 65536 if v >= 32768 else v
        x = s16(raw[0], raw[1])
        z = s16(raw[2], raw[3])
        y = s16(raw[4], raw[5])
        print("  Raw X={:6d}  Y={:6d}  Z={:6d}".format(x, y, z))

        if x in (-4096, 4096) or y in (-4096, 4096):
            print("  WARNING: overflow — sensor saturated, check gain setting")
        else:
            import math
            heading = math.atan2(y, x) * (180.0 / math.pi) % 360.0
            print("  Heading: {:.1f} degrees".format(heading))
            print("  HMC5883L working correctly.")
    except Exception as e:
        print("  Read failed:", repr(e))

# ------------------------------------------------------------------
# Servo probe (PWM — not I2C, so not in the scan above)
# ------------------------------------------------------------------
print("--- Servo probe (GPIO7 / D8) ---")

try:
    from src.hal.board import servo_pin as _servo_pin, servo_pwm_config as _servo_pwm_config
    _spin = _servo_pin()
    _scfg = _servo_pwm_config()  # (pin, freq_hz, min_us, center_us, max_us) or None
except Exception:
    _spin = 7    # XIAO ESP32-S3 default
    _scfg = None

if _spin is None:
    print("  No servo pin defined for this board — skipping.")
else:
    print("  Servo pin: GPIO{}".format(_spin))
    try:
        from machine import Pin, PWM
        _pwm = PWM(Pin(_spin))
        _freq  = _scfg[1] if _scfg else 50
        _min_us, _center_us, _max_us = (_scfg[2], _scfg[3], _scfg[4]) if _scfg else (1000, 1500, 2000)
        _pwm.freq(_freq)
        _period_us = int(1_000_000 / _freq)
        _duty = int((_center_us / _period_us) * 65535)
        _pwm.duty_u16(_duty)
        time.sleep_ms(300)
        _pwm.deinit()
        print("  PWM init OK — center pulse sent ({} µs @ {} Hz)".format(_center_us, _freq))
        print("  If a servo is wired, it should have moved to center.")
    except Exception as e:
        print("  Servo probe FAILED:", repr(e))

print("=" * 48)
