Soon we add these telemetry fields:

scd41_co2_ppm

scd41_temp_c

scd41_humidity

ina219_present

battery_bus_voltage_v

battery_shunt_voltage_mv

battery_current_ma

battery_power_mw

battery_load_voltage_v

usb_power_present

---

But also, before that we calculate the battery level and send that too.  Maybe we send that only instead of all the battery details.  We really only need the main one.


--------------


Better Way to Save Flash: Pre-compile to .mpy

The most effective per-file storage reduction is to run mpy-cross on each .py file and deploy the .mpy bytecode instead:
- .mpy files are typically 30–50% smaller than .py source.
- They also import faster and allocate less RAM during import (bytecode is already in the compact format MicroPython needs).
- All 30+ device .py files would benefit, saving far more total space than merging 3 files.

This is a separate task — flagging it here as the higher-impact alternative.
