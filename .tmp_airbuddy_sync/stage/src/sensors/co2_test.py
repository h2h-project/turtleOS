from machine import Pin, I2C
import time
from src.drivers.scd4x import SCD4X

i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=100000)
sensor = SCD4X(i2c)

print("I2C scan:", [hex(x) for x in i2c.scan()])

try:
    serial = sensor.get_serial_number()
    print("Serial:", hex(serial))
except Exception as e:
    print("Serial read failed:", e)

sensor.ensure_started()
print("Started periodic measurement...")
print("Waiting 5 seconds for first sample...")
time.sleep(5)

for n in range(10):
    try:
        if sensor.data_ready():
            co2, temp_c, rh = sensor.read_measurement()
            print("Loop", n + 1)
            print("CO2:", co2, "ppm")
            print("Temp:", round(temp_c, 2), "C")
            print("RH:", round(rh, 2), "%")
            print("---")
        else:
            print("Loop", n + 1, "data not ready")
    except Exception as e:
        print("Loop", n + 1, "error:", e)

    time.sleep(5)