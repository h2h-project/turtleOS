# src/input/button.py
import time
from machine import Pin
from src.hal.board import btn_pin, btn_led_pin


class AirBuddyButton:
    """
    Cross-board AirBuddy button handler.

    Features:
      - Long hold -> "sleep"
      - Single / double / triple / quad click
      - Debounced
      - Non-blocking poll_action()
      - Optional LED while held
    """

    def __init__(
            self,
            gpio_pin=None,
            click_window_s=0.5,
            debounce_ms=50,
            hold_ms=3000,          # long hold triggers sleep
            led_gpio=None,
            led_active_high=True,
    ):
        # Resolve pins from HAL if not provided
        if gpio_pin is None:
            gpio_pin = btn_pin()

        if led_gpio is None:
            try:
                led_gpio = btn_led_pin()
            except Exception:
                led_gpio = None

        # Button input (wired to GND when pressed)
        self.pin = Pin(gpio_pin, Pin.IN, Pin.PULL_UP)

        self.click_window_ms = int(click_window_s * 1000)
        self.debounce_ms = int(debounce_ms)
        self.hold_ms = int(hold_ms)

        # Primary LED output (external button LED from btn_led_pin)
        self.led = None
        self.led_active_high = bool(led_active_high)
        if led_gpio is not None:
            self.led = Pin(int(led_gpio), Pin.OUT)

        # Secondary onboard user LED — driven alongside primary when both exist.
        # If no primary LED is configured, promoted to sole LED below.
        self.user_led = None
        self.user_led_active_high = True
        try:
            from src.hal.board import user_led_pin, user_led_active_value
            _ulp = user_led_pin()
            if _ulp is not None:
                self.user_led = Pin(int(_ulp), Pin.OUT)
                self.user_led_active_high = (int(user_led_active_value()) == 1)
        except Exception:
            pass

        # No primary LED — promote user_led so _set_led() covers it
        if self.led is None and self.user_led is not None:
            self.led = self.user_led
            self.led_active_high = self.user_led_active_high
            self.user_led = None

        self._set_led(False)

        # --- State ---
        now = time.ticks_ms()
        lvl = self.pin.value()

        self._last_level = lvl
        self._stable_level = lvl
        self._last_change_ms = now

        self._press_start_ms = None
        self._click_count = 0
        self._click_window_start_ms = None

    # --------------------------------------------------
    # LED helper
    # --------------------------------------------------

    def _set_led(self, on: bool):
        if self.led:
            self.led.value(1 if (on == self.led_active_high) else 0)
        if self.user_led:
            self.user_led.value(1 if (on == self.user_led_active_high) else 0)

    # --------------------------------------------------
    # Non-blocking poll
    # --------------------------------------------------

    def poll_action(self):
        """
        Returns:
            "sleep", "single", "double", "triple", "quad", or None
        """

        now = time.ticks_ms()
        level = self.pin.value()

        # Detect raw change
        if level != self._last_level:
            self._last_level = level
            self._last_change_ms = now

        # Debounce
        if level != self._stable_level:
            if time.ticks_diff(now, self._last_change_ms) >= self.debounce_ms:
                self._stable_level = level

                # Mirror LED while held
                self._set_led(self._stable_level == 0)

                # ---- Edge detected ----
                if self._stable_level == 0:
                    # pressed
                    self._press_start_ms = now
                else:
                    # released
                    if self._press_start_ms is not None:
                        held_ms = time.ticks_diff(now, self._press_start_ms)
                        self._press_start_ms = None

                        # Short press = click
                        if held_ms < self.hold_ms:
                            if self._click_count == 0:
                                self._click_window_start_ms = now
                            self._click_count += 1

                            # On the 3rd click, reset the window so there's a
                            # full click_window_ms to land the 4th (quad) click.
                            if self._click_count == 3:
                                self._click_window_start_ms = now

                            if self._click_count >= 4:
                                self._click_count = 0
                                self._click_window_start_ms = None
                                return "quad"

        # ---- Long hold → sleep ----
        if self._stable_level == 0 and self._press_start_ms is not None:
            if time.ticks_diff(now, self._press_start_ms) >= self.hold_ms:
                self._press_start_ms = None
                self._click_count = 0
                self._click_window_start_ms = None
                return "sleep"

        # ---- Emit clicks after window ----
        if self._click_count > 0 and self._click_window_start_ms is not None:
            if time.ticks_diff(now, self._click_window_start_ms) >= self.click_window_ms:
                n = self._click_count
                self._click_count = 0
                self._click_window_start_ms = None

                if n == 1:
                    return "single"
                if n == 2:
                    return "double"
                if n == 3:
                    return "triple"
                return "quad"

        return None

    def is_interacting(self):
        """True while a click sequence is in progress (button held or clicks pending)."""
        return self._press_start_ms is not None or self._click_count > 0

    def reset(self):
        now = time.ticks_ms()
        lvl = self.pin.value()

        self._last_level = lvl
        self._stable_level = lvl
        self._last_change_ms = now

        self._press_start_ms = None
        self._click_count = 0
        self._click_window_start_ms = None

        self._set_led(lvl == 0)