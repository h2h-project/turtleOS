![XIAO ESP32-S3 Pinout](https://p.kagi.com/proxy/image-154-1024x468.png?c=Vfl6XPJbDCiWZ5UKwg9Lo8BihlEKTSJ4r20X3vC41dhmkohmp8vmADbNdp-PeK2mosiJybrUhyZa4l0vDjbULW9Uo9nTQwwiOJKsJcSqQOpBPKAr6UXUIut_O9Hl33jl)

# Hope Turtle Wiring Guide — XIAO ESP32-S3

This guide documents the turtleOS wiring layout for the **Seeed Studio XIAO ESP32-S3**.

The pin chart below matches this board orientation:

- **USB-C port facing up**
- **component side visible**
- pins read from **top to bottom** on each side

We highly recommend following the wire color schema below and using our color conventions with your actual wires.  We'll do our best to stick to these colors in future versions.

---
## XIAO ESP32-S3 Pin Usage

| Left Side (Top → Bottom) | Right Side (Top → Bottom) |
|---|---|
| 🔲 **1** GPIO1 / A0 / D0 — RESERVED: GPS_WAKEUP (stacked L76K GNSS module) | 🟥 **1** 5V — No input |
| 🟨 **2** GPIO2 / A1 / D1 → BUTTON LED (active HIGH, LED + resistor to GND) | ⚫ **2** GND → shared ground |
| 🟨 **3** GPIO3 / A2 / D2 → **I2C_EXT SCL** → GY-521 SCL, GY-273 SCL | 🟥 **3** 3V3 → OLED VCC, RTC VCC, AS5600 VCC, INA219 VCC, AHT20+BMP280 VCC, GY-521 VCC, GY-273 VCC |
| 🟪 **4** GPIO4 / A3 / D3 → BUTTON | 🔲 **4** GPIO9 / A10 / D10 / MOSI — RESERVED: GPS_RESET (stacked L76K GNSS module) |
| 🟩 **5** GPIO5 / A4 / D4 / SDA → **I2C_SYS SDA** → OLED SDA, RTC SDA, AS5600 SDA, INA219 SDA, AHT20+BMP280 SDA | 🟩 **5** GPIO8 / A9 / D9 / MISO → **I2C_EXT SDA** → GY-521 SDA, GY-273 SDA |
| 🟨 **6** GPIO6 / A5 / D5 / SCL → **I2C_SYS SCL** → OLED SCL, RTC SCL, AS5600 SCL, INA219 SCL, AHT20+BMP280 SCL | 🟨 **6** GPIO7 / A8 / D8 / SCK → SERVO signal |
| 🔵 **7** GPIO43 / D6 / TX → GPS RX | 🟠 **7** GPIO44 / D7 / RX ← GPS TX |


---

## I2C Buses

The latest version of turtleOS (v2.4) lays the foundation for forthcoming turtleShell v3 PCB by establishing two I2C buses. We'll have one for sensors and components that are hardwired to the PCB (i.e our OLED, INA219, AHT20, etc.) and one for "external" sensors that are wired in to the PCBs built in JST ports.  We'll call these the system and external buses (I2C_SYS and I2C_EXT respectively). As of turtleOS 2.4 only the IMU and compass are on I2C_EXT; the remaining sensors migrate as the v3.0 PCB develops.

| Bus | Peripheral | SDA | SCL | Speed | Devices | Notes |
|---|---|---|---|---|---|---|
| **I2C_SYS** | I2C(0) | GPIO5 (D4) | GPIO6 (D5) | 400 kHz | OLED 0x3C, INA219 0x40, AS5600 0x36, DS3231 0x68, AHT20 0x38, BMP280 0x77 (or 0x76) | Onboard/system bus. `init_i2c()` in firmware. |
| **I2C_EXT** | I2C(1) | GPIO8 (D9) | GPIO3 (D2) | 400 kHz | GY-521 MPU6050 0x68, GY-273 QMC5883L 0x0D / HMC5883L 0x1E | External/plug-in bus. `init_i2c_ext()` in firmware.|

**Why two buses?** The GY-521's MPU6050 answers at 0x68, the same address as the DS3231 clock. Giving each its own bus avoids the collision without board surgery. More importantly, every I2C module brings its own pull-up resistors, and they add up in parallel. A few on one pair of pins is fine, but after 5–6 the bus starts failing. Two buses keep the parallel total above about 1 kΩ on each (measure SDA to 3V3 with the power off).

---

## IMU and Compass Wiring (I2C_EXT)

The GY-521 (MPU6050 accelerometer + gyroscope) and the GY-273 (QMC5883L / HMC5883L compass) are two separate boards. **Both go on I2C_EXT.** turtleOS finds the IMU first, then looks for the compass on the same bus; a compass on I2C_SYS is not used while an IMU is attached, and boot reports "Compass FAIL".

| Board pin | XIAO pin | Note |
|---|---|---|
| VCC | 3V3 | Both boards |
| GND | GND | Both boards |
| SCL | D2 (GPIO3) | I2C_EXT SCL, both boards |
| SDA | D9 (GPIO8) | I2C_EXT SDA, both boards |
| GY-521 XDA, XCL, AD0, INT | not connected | Leave AD0 unconnected so the MPU6050 stays at 0x68 |
| GY-273 DRDY | not connected | |

**Mounting:**

- **GY-521:** silkscreen **X arrow pointing to the bow**, so pitch is nose-up positive and roll is starboard-down positive.
- **GY-273:** mount it **flat**, with its silkscreen **X arrow pointing to the bow**. Heading is the magnetometer's X axis, and turtleOS does not correct for tilt. Keep it well away from the servo, batteries and power wiring, which disturb the reading.

---

## Wire Color Schema

| Color | Purpose |
|---|---|
| 🟥 Red | Power |
| ⚫ Black | Ground |
| 🟩 Green | I2C SDA (both buses) |
| 🟨 Yellow | I2C SCL (both buses) / button LED / servo PWM |
| 🔵 Blue | UART TX from XIAO to GPS RX |
| 🟠 Orange | UART RX on XIAO from GPS TX |
| 🟪 Purple | Button signal |
| ⬛ Dark | Reserved by stacked GNSS module (do not use) |
| ⬜ White | Spare / unused GPIO |     


---

## I2C_SYS Sensor Wiring

Every module on the system bus uses the same four wires:

| Sensor Pin | XIAO ESP32-S3 Pin |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO5 / D4 / SDA |
| SCL | GPIO6 / D5 / SCL |

Current I2C_SYS modules:

- OLED display
- DS3231 RTC module
- AS5600 magnetic angle encoder
- INA219 voltage/current monitor
- AHT20 + BMP280 temperature, humidity and pressure module

The IMU and compass are on I2C_EXT; see [IMU and Compass Wiring](#imu-and-compass-wiring-i2c_ext).

---

## GPS UART Wiring

UART wiring crosses between the XIAO and the GPS module:

| GPS Module Pin | XIAO ESP32-S3 Pin |
|---|---|
| GPS VCC | 3V3 or 5V, depending on GPS module |
| GPS GND | GND |
| GPS RX | GPIO43 / D6 / TX |
| GPS TX | GPIO44 / D7 / RX |

In plain terms:

- XIAO TX → GPS RX
- GPS TX → XIAO RX


---

## Button Wiring

| Action Button Pin | XIAO ESP32-S3 Pin |
|---|---|
| Button signal | GPIO4 / D3 |
| Button ground | GND |

The button uses a pull-up input in software:

| Button State | GPIO Reading |
|---|---|
| Idle | 1 |
| Pressed | 0 |

---

## Button LED Wiring

| Button LED Pin | XIAO ESP32-S3 Pin |
|---|---|
| LED signal | GPIO2 / D1 |
| LED ground | GND |

This drives the LED built into the button. It moved from D0 to D1 because the stacked GNSS module reserves D0.
