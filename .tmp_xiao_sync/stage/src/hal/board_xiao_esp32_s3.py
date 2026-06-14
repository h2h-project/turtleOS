# src/hal/board_xiao_esp32_s3.py
# XIAO ESP32-S3 / XS3 pin map + common initializers for AirBuddy.
#
# Board orientation used in AirBuddy docs:
#   - USB-C port facing up
#   - component side visible
#
# AirBuddy wiring summary:
#   - D0 / GPIO1  -> Button LED signal (active HIGH, wire LED + resistor to GND)
#   - D3 / GPIO4  -> Button signal
#   - D4 / GPIO5  -> I2C SDA
#   - D5 / GPIO6  -> I2C SCL
#   - D6 / GPIO43 -> GPS RX  (XS3 TX -> GPS RX)
#   - D7 / GPIO44 -> GPS TX  (GPS TX -> XS3 RX)
#   - D8 / GPIO7  -> Servo PWM signal, e.g. MG996R rudder actuator
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
# D0 / GPIO1 -> external button LED signal (active HIGH, wire LED + resistor to GND)
#
# The onboard user LED (GPIO21, active LOW) is driven simultaneously by
# button.py as a secondary LED — both light up when the button is pressed.
BTN_LED_PIN = 1


def btn_led_pin():
    """Return the GPIO number for the external button LED (active HIGH)."""
    return BTN_LED_PIN


# ------------------------------------------------------------
# Onboard user LED
# ------------------------------------------------------------
# The standard Seeed XIAO ESP32-S3 has a normal single-color USER_LED
# on GPIO21.
#
# Important:
#   The LED is active LOW:
#     - pin.value(0) -> LED ON
#     - pin.value(1) -> LED OFF
#
# This is NOT a NeoPixel / WS2812 RGB LED on the standard XIAO ESP32-S3.
#
USER_LED_PIN = 21
USER_LED_ACTIVE = 0


def user_led_pin():
    """Return the GPIO number for the onboard user LED."""
    return USER_LED_PIN


def user_led_active_value():
    """Return the logic value that turns the onboard user LED on."""
    return USER_LED_ACTIVE


def neopixel_pin():
    """
    Return the GPIO number for an onboard NeoPixel LED, or None.

    The standard XIAO ESP32-S3 uses a normal active-low user LED,
    not a WS2812/NeoPixel.
    """
    return None


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


# ------------------------------------------------------------
# Servo output (MG996R sail actuator)
# ------------------------------------------------------------
# XIAO ESP32-S3 servo wiring:
#   - D8 / GPIO7 -> servo PWM signal wire, usually orange/yellow
#   - External 5V-6V supply + -> servo red wire
#   - External supply GND -> servo brown/black wire
#   - External supply GND must also connect to XS3 GND
#
# IMPORTANT:
#   Do NOT power the MG996R from the XIAO 3V3 pin.
#   The MG996R can pull large current spikes under load or stall.
#   Use a separate 5V-6V supply/BEC capable of at least 2A (3A+ preferred).
#
# Standard hobby servo PWM:
#   - 50 Hz frequency
#   - 1000 us = one end, 1500 us = center, 2000 us = other end
#
SERVO_PIN = 7       # D8 / GPIO7
SERVO_FREQ_HZ = 50
SERVO_MIN_US = 1000
SERVO_CENTER_US = 1500
SERVO_MAX_US = 2000


def servo_pin():
    """Return the GPIO number used for the MG996R sail servo PWM signal."""
    return SERVO_PIN


def servo_pwm_config():
    """
    Return servo PWM config as (pin, freq_hz, min_us, center_us, max_us).
    Pass these into src/drivers/servo.py Servo constructor.
    """
    return (SERVO_PIN, SERVO_FREQ_HZ, SERVO_MIN_US, SERVO_CENTER_US, SERVO_MAX_US)