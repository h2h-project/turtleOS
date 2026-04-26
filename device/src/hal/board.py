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
        usb_power_present,
    )

elif _TAG == "esp32s3":
    from src.hal.board_esp32_s3 import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        usb_power_present,
    )

elif _TAG == "xiao_esp32s3":
    from src.hal.board_xiao_esp32_s3 import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        usb_power_present,
    )

else:
    # Conservative fallback: try ESP32-S3 first, since it is your newer target.
    from src.hal.board_esp32_s3 import (
        init_i2c,
        i2c_pins,
        gps_pins,
        btn_pin,
        btn_led_pin,
        usb_power_present,
    )


def tag():
    return _TAG