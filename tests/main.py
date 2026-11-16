# main.py — Pico W + SSD1306 + AHT21 + ENS160 quick dashboard
from machine import Pin, I2C
import time
import framebuf

# -----------------------
# I2C setup
# -----------------------
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=100000)

devices = i2c.scan()
print("I2C:", [hex(d) for d in devices])

OLED_ADDR = 0x3C
AHT21_ADDR = 0x38
ENS160_ADDR = 0x53

if OLED_ADDR not in devices:
    raise RuntimeError("OLED not found at 0x3C")
if AHT21_ADDR not in devices:
    raise RuntimeError("AHT21 not found at 0x38")
if ENS160_ADDR not in devices:
    raise RuntimeError("ENS160 not found at 0x53")

# -----------------------
# SSD1306 OLED driver
# -----------------------
class SSD1306_I2C(framebuf.FrameBuffer):
    def __init__(self, width, height, i2c, addr):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.pages = self.height // 8
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def write_cmd(self, cmd):
        self.i2c.writeto(self.addr, bytes([0x00, cmd]))

    def init_display(self):
        for cmd in (
                0xAE,       # display off
                0x20, 0x00, # memory addressing mode
                0x40,       # start line
                0xA1,       # seg remap
                0xC8,       # COM scan dec
                0xDA, 0x12, # COM pins
                0x81, 0x7F, # contrast
                0xA4,       # display follows RAM
                0xA6,       # normal display
                0xD5, 0x80, # display clock divide
                0x8D, 0x14, # charge pump
                0xAF        # display on
        ):
            self.write_cmd(cmd)
        self.fill(0)
        self.show()

    def show(self):
        for page in range(self.pages):
            self.write_cmd(0xB0 + page)
            self.write_cmd(0x00)
            self.write_cmd(0x10)
            start = self.width * page
            end = start + self.width
            self.i2c.writeto(self.addr, b"\x40" + self.buffer[start:end])

oled = SSD1306_I2C(128, 64, i2c, OLED_ADDR)

# -----------------------
# AHT21 (Temp/Humidity)
# -----------------------
def aht21_read():
    # Trigger measurement (AHT2x typical command)
    i2c.writeto(AHT21_ADDR, b"\xAC\x33\x00")
    time.sleep_ms(85)

    data = i2c.readfrom(AHT21_ADDR, 6)
    # data[0] is status; remaining contain 20-bit humidity and temp
    raw_h = ((data[1] << 12) | (data[2] << 4) | (data[3] >> 4)) & 0xFFFFF
    raw_t = (((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]) & 0xFFFFF

    humidity = raw_h * 100.0 / 1048576.0
    temp_c = raw_t * 200.0 / 1048576.0 - 50.0
    return temp_c, humidity

# -----------------------
# ENS160 (basic)
# -----------------------
# Register map (common ENS160)
_REG_PART_ID   = 0x00  # 2 bytes
_REG_OPMODE    = 0x10
_REG_CONFIG    = 0x11
_REG_DATA_AQI  = 0x21
_REG_DATA_TVOC = 0x22  # 2 bytes
_REG_DATA_ECO2 = 0x24  # 2 bytes
_REG_TEMP_IN   = 0x13  # 2 bytes (temp compensation)
_REG_RH_IN     = 0x15  # 2 bytes (RH compensation)

_OPMODE_RESET  = 0xF0
_OPMODE_STD    = 0x02

def ens160_write8(reg, val):
    i2c.writeto(ENS160_ADDR, bytes([reg, val]))

def ens160_write16(reg, val):
    i2c.writeto(ENS160_ADDR, bytes([reg, val & 0xFF, (val >> 8) & 0xFF]))

def ens160_read(reg, n):
    i2c.writeto(ENS160_ADDR, bytes([reg]))
    return i2c.readfrom(ENS160_ADDR, n)

def ens160_init():
    part = ens160_read(_REG_PART_ID, 2)
    part_id = part[0] | (part[1] << 8)
    print("ENS160 PART_ID:", hex(part_id))

    # Put into standard mode
    ens160_write8(_REG_OPMODE, _OPMODE_STD)
    time.sleep_ms(50)

def ens160_set_comp(temp_c, rh):
    # ENS160 expects:
    # temperature in Kelvin * 64
    # humidity in %RH * 512
    temp_k = temp_c + 273.15
    tval = int(temp_k * 64)
    hval = int(rh * 512)

    ens160_write16(_REG_TEMP_IN, tval)
    ens160_write16(_REG_RH_IN, hval)

def ens160_read_air():
    aqi = ens160_read(_REG_DATA_AQI, 1)[0]
    tvoc = ens160_read(_REG_DATA_TVOC, 2)
    eco2 = ens160_read(_REG_DATA_ECO2, 2)
    tvoc_ppb = tvoc[0] | (tvoc[1] << 8)
    eco2_ppm = eco2[0] | (eco2[1] << 8)
    return aqi, tvoc_ppb, eco2_ppm

ens160_init()

# -----------------------
# Main loop
# -----------------------
oled.fill(0)
oled.text("AirBuddy 2.1", 0, 0)
oled.text("Sensors online", 0, 12)
oled.show()
time.sleep(1)

while True:
    try:
        temp_c, rh = aht21_read()
        ens160_set_comp(temp_c, rh)
        aqi, tvoc, eco2 = ens160_read_air()

        print("T=%.1fC RH=%.1f%%  AQI=%d  TVOC=%dppb  eCO2=%dppm" % (temp_c, rh, aqi, tvoc, eco2))

        oled.fill(0)
        oled.text("AirBuddy 2.1", 0, 0)
        oled.text("T: %.1f C" % temp_c, 0, 14)
        oled.text("RH: %.1f %%" % rh, 0, 26)
        oled.text("AQI: %d" % aqi, 0, 40)
        oled.text("TVOC:%d eCO2:%d" % (tvoc, eco2), 0, 52)
        oled.show()

    except Exception as e:
        print("ERR:", e)
        oled.fill(0)
        oled.text("Sensor error", 0, 0)
        oled.text(str(e)[:16], 0, 16)
        oled.show()

    time.sleep(1)
