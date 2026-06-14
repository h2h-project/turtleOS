# src/hal/board_esp32s3.py
# ESP32-S3 pin map + common initializers for AirBuddy.

from machine import Pin, I2C

# ------------------------------------------------------------
# Button (AirBuddy)
# ------------------------------------------------------------
# Assumed wiring for now:
#   - GPIO15 -> button signal
#   - GND     -> ground
#
# Using PULL_UP so the button reads:
#   - 1 when idle
#   - 0 when pressed
#
# Change this if your physical ESP32-S3 wiring differs.
BTN_PIN = 4

# Optional button LED control pin.
# This is a placeholder choice based on your previous Pico layout style.
# Many ESP32-S3 boards will NOT have a usable LED on GPIO18,
# so adjust or ignore if not physically wired.
BTN_LED_PIN = 48

# WS2812 RGB NeoPixel — only present on specific ESP32-S3 dev boards (e.g. GPIO48 on some).
# Generic ESP32-S3 targets return None; override in a board-specific file if you have one wired.
NEOPIXEL_PIN = None


def btn_led_pin():
    return BTN_LED_PIN


def neopixel_pin():
    """Return the GPIO number for the onboard WS2812 NeoPixel LED, or None if not present."""
    return NEOPIXEL_PIN


def btn_pin():
    """
    Returns the GPIO number used for the main AirBuddy button.
    Kept as a function so src/app/main.py can call it consistently across boards.
    """
    return BTN_PIN


# ------------------------------------------------------------
# I2C (OLED + DS3231 + SCD41 + AHT10 + INA219)
# ------------------------------------------------------------
# Recommended ESP32-S3 shared I2C bus:
#   - SDA = GPIO6
#   - SCL = GPIO9
#
# All of the following can share this bus:
#   - OLED
#   - DS3231 RTC
#   - SCD41
#   - AHT10  (or AHT21, but not both on same bus because both are usually 0x38)
#   - INA219 battery/current sensor
#
# Typical addresses:
#   - AHT10  = 0x38
#   - OLED   = 0x3C
#   - INA219 = 0x40
#   - SCD41  = 0x62
#   - DS3231 = 0x68
I2C_ID = 0
I2C_SCL = 9
I2C_SDA = 6
I2C_FREQ = 400_000

def init_i2c():
    """
    Create and return the shared I2C bus used by OLED, RTC, sensors, etc.
    """
    return I2C(I2C_ID, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=I2C_FREQ)


def i2c_pins():
    """
    Returns (i2c_id, scl_pin, sda_pin, freq_hz)
    Useful for OLED/sensor constructors that want explicit pins.
    """
    return (I2C_ID, I2C_SCL, I2C_SDA, I2C_FREQ)


# ------------------------------------------------------------
# GPS (Ublox NEO-6M)
# ------------------------------------------------------------
# Recommended ESP32-S3 UART wiring:
#   - ESP32-S3 TX GPIO43 -> GPS RX
#   - ESP32-S3 RX GPIO44 -> GPS TX
#
# Note:
# - GPS TX must go to ESP RX
# - GPS RX must go to ESP TX
#
# These are safe, ordinary GPIOs for UART use on ESP32-S3.
GPS_UART_ID = 1
GPS_BAUD = 9600
GPS_TX_PIN = 43
GPS_RX_PIN = 44


def gps_pins():
    """
    Returns (uart_id, baud, tx_pin, rx_pin)
    """
    return (GPS_UART_ID, GPS_BAUD, GPS_TX_PIN, GPS_RX_PIN)


# ------------------------------------------------------------
# Power source detection
# ------------------------------------------------------------
# Unlike the Pico / Pico W, ESP32-S3 boards do NOT have a universal,
# standard VBUS-detect GPIO equivalent to GP24.
#
# Therefore:
# - usb_power_present() cannot be reliably implemented generically
# - if you later wire a dedicated USB/VBUS detect signal to a GPIO,
#   you can define it here and implement detection properly
#
# For now this returns False by default.
# ------------------------------------------------------------

USB_DETECT_PIN = None
USB_DETECT_ACTIVE = 1


def usb_power_present():
    """
    Return True if USB/VBUS power is present.

    On generic ESP32-S3 boards there is no standard built-in VBUS detect pin
    like the Pico GP24 arrangement, so this currently returns False unless
    USB_DETECT_PIN is explicitly configured later.
    """
    try:
        if USB_DETECT_PIN is None:
            return False
        pin = Pin(USB_DETECT_PIN, Pin.IN)
        val = pin.value()
        return bool(val == USB_DETECT_ACTIVE)
    except Exception:
        return False