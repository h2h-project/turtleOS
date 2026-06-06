# src/hal/board_pico.py
# Pico / Pico W pin map + common initializers.

from machine import Pin, I2C

# ------------------------------------------------------------
# Button (AirBuddy)
# ------------------------------------------------------------
# Physical wiring:
#   - GP15 -> button signal
#   - GND28 -> ground
BTN_PIN = 15
BTN_LED_PIN = 18  # GP18 -> button LED control (output)

def btn_led_pin():
    return BTN_LED_PIN

def btn_pin():
    """
    Returns the GPIO number used for the main AirBuddy button.
    Kept as a function so src/app/main.py can call it consistently across boards.
    """
    return BTN_PIN


# ------------------------------------------------------------
# I2C (OLED + DS3231)
# ------------------------------------------------------------
# Your Pico wiring:
#   - SCL = GP1
#   - SDA = GP0
I2C_ID = 0
I2C_SCL = 1
I2C_SDA = 0
I2C_FREQ = 400_000


def init_i2c():
    """
    Create and return the shared I2C bus used by OLED + DS3231 (and sensors if needed).
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
# Your existing Pico values
GPS_UART_ID = 1
GPS_BAUD = 9600
GPS_TX_PIN = 8
GPS_RX_PIN = 9


def gps_pins():
    """
    Returns (uart_id, baud, tx_pin, rx_pin)
    """
    return (GPS_UART_ID, GPS_BAUD, GPS_TX_PIN, GPS_RX_PIN)


# ------------------------------------------------------------
# Power source detection
# ------------------------------------------------------------
# On Pico / Pico W, VBUS detect is commonly exposed on GP24.
# This allows us to tell whether USB 5V is present.
#
# Important:
# - True means USB/VBUS is present
# - False means USB/VBUS is not detected
# - This does NOT fully describe charger/battery path behavior;
#   it only tells us whether USB power is present
# ------------------------------------------------------------

USB_DETECT_PIN = 24
USB_DETECT_ACTIVE = 1

def usb_power_present():
    """
    Return True if USB/VBUS power is present on the Pico board.

    Uses GP24 as VBUS detect, which is standard for Pico-class boards.
    Returns False on any read/setup failure.
    """
    try:
        pin = Pin(USB_DETECT_PIN, Pin.IN)
        val = pin.value()
        return bool(val == USB_DETECT_ACTIVE)
    except Exception:
        return False