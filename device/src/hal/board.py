# src/hal/board.py
# Board facade: import pins from here, never hardcode them elsewhere.
#
# turtleOS runs on the Seeed XIAO ESP32-S3 only, so this always re-exports
# board_xiao_esp32_s3. It used to pick a pin map from platform_tag(), and a
# turtle whose firmware didn't identify as a XIAO (and had no board_type in
# config.json) silently got the generic ESP32-S3 map: button LED on GPIO48,
# no servo pin, no I2C_EXT. A port to new hardware adds its own board module
# and switches here.

from src.hal.platform import platform_tag
from src.hal.board_xiao_esp32_s3 import (  # noqa: F401
    init_i2c,
    i2c_pins,
    gps_pins,
    btn_pin,
    btn_led_pin,
    neopixel_pin,
    usb_power_present,
    servo_pin,
    servo_pwm_config,
    servo_power_pin,
    user_led_pin,
    user_led_active_value,
    init_i2c_ext,
    i2c_ext_pins,
)


def tag():
    """The detected platform tag, for logs and Device Information only."""
    return platform_tag()
