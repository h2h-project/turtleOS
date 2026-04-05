#src/app/boot_guard.py

import time
import os
from machine import Pin

def _flag_exists(name):
    try:
        os.stat(name)
        return True
    except:
        return False

def debug_requested_at_boot(gpio_pin=15,hold_ms=2000):
    pin=Pin(gpio_pin,Pin.IN,Pin.PULL_UP)
    start=time.ticks_ms()
    if pin.value()!=0:
        return False
    while time.ticks_diff(time.ticks_ms(),start)<hold_ms:
        if pin.value()!=0:
            return False
        time.sleep_ms(10)
    return True

def enforce_debug_guard(btn_pin=15,debug_flag_file="debug_mode"):
    if _flag_exists(debug_flag_file):
        print("===AirBuddyDEBUGMODE===")
        print("Reason:debug flag file present:",debug_flag_file)
        print("To exit:import os,machine;os.remove('debug_mode');machine.reset()")
        raise SystemExit
    if debug_requested_at_boot(gpio_pin=btn_pin):
        print("===AirBuddyDEBUGMODE===")
        print("Reason:button held at boot.")
        print("Dropping to REPL.")
        raise SystemExit
