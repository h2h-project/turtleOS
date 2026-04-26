# src/hal/board_xiao_esp32_s3.py
# XIAO ESP32-S3 / XS3 pin map + common initializers for AirBuddy.
#
# Board orientation used in AirBuddy docs:
#   - USB-C port facing up
#   - component side visible
#
# AirBuddy wiring summary:
#   - D3 / GPIO4  -> Button signal
#   - D4 / GPIO5  -> I2C SDA
#   - D5 / GPIO6  -> I2C SCL
#   - D6 / GPIO43 -> GPS RX  (XS3 TX -> GPS RX)
#   - D7 / GPIO44 -> GPS TX  (GPS TX -> XS3 RX)
#
# Sensor power:
#   - 3V3 -> OLED VCC, RTC VCC, SCD41 VCC, AHT10/AHT21 VCC, INA219 VCC
#   - GND -> shared ground
#
# Battery:
#   - LiPo connects to the XIAO battery pads/connector, not to 3V3.
#   - Optional INA219 can be placed in series between LiPo+ and BAT+
#     for battery voltage/current telemetry.

from machine import Pin, I2C


# ------------------------------------------------------------
# Button (AirBuddy)
# ------------------------------------------------------------
# XIAO ESP32-S3 wiring:
#   - D3 / GPIO4 -> button signal
#   - GND        -> button ground
#
# Using PULL_UP in the application logic means the button reads:
#   - 1 when idle
#   - 0 when pressed
#
BTN_PIN = 4


def btn_pin():
    """
    Returns the GPIO number used for the main AirBuddy button.
    Kept as a function so src/app/main.py can call it consistently across boards.
    """
    return BTN_PIN


# ------------------------------------------------------------
# Optional button LED
# ------------------------------------------------------------
# The XIAO ESP32-S3 does not expose a simple external button LED pin
# in the same way as the Pico AirBuddy wiring.
#
# GPIO48 is associated with onboard RGB/NeoPixel-style LED behavior on
# some ESP32-S3 boards, but it is not a normal button LED output for this
# AirBuddy wiring map.
#
# Returning None lets higher-level code detect that there is no configured
# button LED for this board.
BTN_LED_PIN = None


def btn_led_pin():
    """
    Return the optional button LED GPIO.

    For the XIAO ESP32-S3 AirBuddy wiring, no dedicated button LED is
    currently assigned.
    """
    return BTN_LED_PIN


# ------------------------------------------------------------
# I2C (OLED + DS3231 + SCD41 + AHT10/AHT21 + INA219)
# ------------------------------------------------------------
# XIAO ESP32-S3 shared I2C bus:
#   - D4 / GPIO5 -> SDA
#   - D5 / GPIO6 -> SCL
#
# All of the following can share this bus:
#   - OLED display
#   - DS3231 RTC
#   - SCD41 CO2 sensor
#   - AHT10 or AHT21 temp/RH sensor
#   - INA219 battery/current sensor
#
# Typical addresses:
#   - AHT10/AHT21 = 0x38
#   - OLED        = 0x3C
#   - INA219      = 0x40, depending on module address pins
#   - SCD41       = 0x62
#   - DS3231      = 0x68
#
# Important:
#   AHT10 and AHT21 usually both use 0x38. Do not put both on the same
#   I2C bus unless you use a mux or one module has a changed address.
#
I2C_ID = 0
I2C_SDA = 5
I2C_SCL = 6
I2C_FREQ = 400_000


def init_i2c():
    """
    Create and return the shared I2C bus used by OLED, RTC, sensors, etc.
    """
    return I2C(I2C_ID, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=I2C_FREQ)


def i2c_pins():
    """
    Returns (i2c_id, scl_pin, sda_pin, freq_hz).

    Useful for OLED/sensor constructors that want explicit pins.
    Note the order is kept compatible with the existing AirBuddy HAL:
        (I2C_ID, I2C_SCL, I2C_SDA, I2C_FREQ)
    """
    return (I2C_ID, I2C_SCL, I2C_SDA, I2C_FREQ)


# ------------------------------------------------------------
# GPS (Ublox NEO-6M or similar)
# ------------------------------------------------------------
# XIAO ESP32-S3 UART wiring:
#   - D6 / GPIO43 / TX -> GPS RX
#   - D7 / GPIO44 / RX <- GPS TX
#
# UART wiring crosses:
#   - XS3 TX goes to GPS RX
#   - GPS TX goes to XS3 RX
#
GPS_UART_ID = 1
GPS_BAUD = 9600
GPS_TX_PIN = 43
GPS_RX_PIN = 44


def gps_pins():
    """
    Returns (uart_id, baud, tx_pin, rx_pin).
    """
    return (GPS_UART_ID, GPS_BAUD, GPS_TX_PIN, GPS_RX_PIN)


# ------------------------------------------------------------
# Power source detection
# ------------------------------------------------------------
# The XIAO ESP32-S3 has onboard LiPo charge/discharge management,
# but MicroPython does not provide a universal built-in USB/VBUS detect
# signal for this board.
#
# Therefore:
#   - usb_power_present() cannot be reliably implemented generically
#   - if you later wire a dedicated USB/VBUS detect signal to a GPIO,
#     define USB_DETECT_PIN here and set USB_DETECT_ACTIVE accordingly
#
# For now this returns False by default.
# ------------------------------------------------------------

USB_DETECT_PIN = None
USB_DETECT_ACTIVE = 1


def usb_power_present():
    """
    Return True if USB/VBUS power is present.

    On the XIAO ESP32-S3 there is no generic MicroPython-safe VBUS detect
    pin configured in the AirBuddy wiring map, so this currently returns
    False unless USB_DETECT_PIN is explicitly configured later.
    """
    try:
        if USB_DETECT_PIN is None:
            return False

        pin = Pin(USB_DETECT_PIN, Pin.IN)
        val = pin.value()
        return bool(val == USB_DETECT_ACTIVE)

    except Exception:
        return False