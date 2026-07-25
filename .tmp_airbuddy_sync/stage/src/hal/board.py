# src/hal/board.py
# Board facade: import this everywhere instead of hardcoding pins.

from src.hal.platform import platform_tag

_TAG = platform_tag()

if _TAG == "pico":
    from src.hal.board_pico import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        usb_power_present,
    )

elif _TAG == "esp32":
    from src.hal.board_esp32 import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        neopixel_pin,
        usb_power_present,
    )

elif _TAG == "esp32s3":
    from src.hal.board_esp32_s3 import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        neopixel_pin,
        usb_power_present,
    )

elif _TAG == "xiao_esp32s3":
    from src.hal.board_xiao_esp32_s3 import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        neopixel_pin,
        usb_power_present,
        servo_pin,
        servo_pwm_config,
        user_led_pin,
        user_led_active_value,
    )

else:
    # Conservative fallback: try ESP32-S3 first, since it is your newer target.
    from src.hal.board_esp32_s3 import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        neopixel_pin,
        usb_power_present,
    )


def tag():
    return _TAG


# Servo is only wired on XIAO ESP32-S3.  Stub out for all other boards so
# callers can unconditionally import servo_pin / servo_pwm_config from here.
try:
    servo_pin
except NameError:
    def servo_pin():
        return None

try:
    servo_pwm_config
except NameError:
    def servo_pwm_config():
        return None

# user_led_pin / user_led_active_value are only defined on XIAO ESP32-S3.
# Stubs return None / 1 so callers can check pin is None to skip.
try:
    user_led_pin
except NameError:
    def user_led_pin():
        return None

try:
    user_led_active_value
except NameError:
    def user_led_active_value():
        return 1