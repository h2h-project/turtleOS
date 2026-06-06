# src/hal/board_esp32.py
# ESP32 pin map + common initializers.

from machine import Pin, I2C

# ------------------------------------------------------------
# Button (AirBuddy)
# ------------------------------------------------------------
# Recommended wiring (safer than strapping pins):
#   - GPIO4  -> button signal (use internal pull-up)
#   - GND    -> button ground
#
# Optional button LED:
#   - GPIO18 -> LED + resistor -> GND   (active-high)
# ------------------------------------------------------------

BTN_PIN = 4
BTN_LED_PIN = 18  # change if needed
NEOPIXEL_PIN = None  # plain ESP32 boards rarely have an onboard WS2812; override if yours does

def btn_pin():
    return BTN_PIN

def btn_led_pin():
    return BTN_LED_PIN

def neopixel_pin():
    """Return the GPIO for the onboard WS2812 NeoPixel, or None if not present."""
    return NEOPIXEL_PIN


# ------------------------------------------------------------
# I2C (OLED + DS3231 + sensors)
# ------------------------------------------------------------
# Common ESP32 I2C pins
I2C_ID = 0
I2C_SCL = 22
I2C_SDA = 21
I2C_FREQ = 400_000

def init_i2c():
    return I2C(I2C_ID, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=I2C_FREQ)

def i2c_pins():
    # (i2c_id, scl, sda, freq)
    return (I2C_ID, I2C_SCL, I2C_SDA, I2C_FREQ)


# ------------------------------------------------------------
# GPS (Ublox)
# ------------------------------------------------------------
GPS_UART_ID = 2
GPS_BAUD = 9600
GPS_TX_PIN = 17
GPS_RX_PIN = 16

def gps_pins():
    return (GPS_UART_ID, GPS_BAUD, GPS_TX_PIN, GPS_RX_PIN)


# ------------------------------------------------------------
# Power source detection
# ------------------------------------------------------------
# ESP32 boards vary widely.
# There is no universal USB/VBUS detect mechanism across all boards.
#
# If your specific board exposes USB/VBUS or charger status on a GPIO,
# set USB_DETECT_PIN to that GPIO number and adjust USB_DETECT_ACTIVE.
#
# Examples:
#   USB_DETECT_PIN = 35
#   USB_DETECT_ACTIVE = 1
#
# For now, default to "unknown / not detected" by returning False.
# This keeps the HAL stable and honest until real hardware detection exists.
# ------------------------------------------------------------

USB_DETECT_PIN = None       # set to GPIO number if your board has one
USB_DETECT_ACTIVE = 1       # 1 = high means USB present, 0 = low means USB present

def usb_power_present():
    """
    Return True if USB/VBUS power is detected on this ESP32 board.

    Default behavior:
      - returns False if no dedicated detect pin is configured

    If a real detect pin is available on your board:
      - set USB_DETECT_PIN above
      - set USB_DETECT_ACTIVE to match the hardware logic
    """
    if USB_DETECT_PIN is None:
        return False

    try:
        pin = Pin(USB_DETECT_PIN, Pin.IN)
        val = pin.value()
        return bool(val == USB_DETECT_ACTIVE)
    except Exception:
        return False