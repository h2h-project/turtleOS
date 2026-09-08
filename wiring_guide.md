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
| ⬛ **1** GPIO1 / A0 / D0 — RESERVED: GPS_WAKEUP (stacked L76K GNSS module) | 🟥 **1** 5V — No input |
| 🟨 **2** GPIO2 / A1 / D1 → BUTTON LED (active HIGH, LED + resistor to GND) | ⚫ **2** GND → shared ground |
| 🟨 **3** GPIO3 / A2 / D2 → **I2C_EXT SCL** → GY-87 SCL | 🟥 **3** 3V3 → OLED VCC, RTC VCC, AS5600 VCC, INA219 VCC, AHT20 VCC |
| 🟪 **4** GPIO4 / A3 / D3 → BUTTON | ⬛ **4** GPIO9 / A10 / D10 / MOSI — RESERVED: GPS_RESET (stacked L76K GNSS module) |
| 🟩 **5** GPIO5 / A4 / D4 / SDA → **I2C_SYS SDA** → OLED SDA, RTC SDA, AS5600 SDA, INA219 SDA, AHT20 SDA | 🟩 **5** GPIO8 / A9 / D9 / MISO → **I2C_EXT SDA** → GY-87 SDA |
| 🟨 **6** GPIO6 / A5 / D5 / SCL → **I2C_SYS SCL** → OLED SCL, RTC SCL, AS5600 SCL, INA219 SCL, AHT20 SCL | 🟨 **6** GPIO7 / A8 / D8 / SCK → SERVO signal |
| 🔵 **7** GPIO43 / D6 / TX → GPS RX | 🟠 **7** GPIO44 / D7 / RX ← GPS TX |


---

## I2C Buses

The latest version of turtleOS (v2.4) lays the foundation for forthcoming turtleShell v3 PCB by establishing two I2C buses. We'll have one for sensors and components that are hardwired to the PCB (i.e our OLED, INA219, AHT20, etc.) and one for "external" sensors that are wired in to the PCBs built in JST ports.  We'll call these the system and external buses (I2C_SYS and I2C_EXT respectively). As of turtleOS 2.4 only the GY-87 has m remaining sensors migrate as the v3.0 PCB develops.

| Bus | Peripheral | SDA | SCL | Speed | Devices | Notes |
|---|---|---|---|---|---|---|
| **I2C_SYS** | I2C(0) | GPIO5 (D4) | GPIO6 (D5) | 400 kHz | OLED 0x3C, DS3231 0x68HT20 0x38 | Onboard/system bus. `init_i2c()` in firmware. |
| **I2C_EXT** | I2C(1) | GPIO8 (D9) | GPIO3 (D2) | 400 kHz | GY-87: MPU6050 0x68, HMC5883L 0x1E / QMC5883L 0x0D (after bypass), BMP180 0x77 | External/plug-in bus. `init_i2c_ext()` in firmware.|

**Why two buses?** Alas the GY-87's MPU6050 answers at 0x68, the same address as the DS3 Clcok we're using. Giving each their own bus avoids the collision without board surgery and takes the GY-87's 2.2 kΩ pull-ups off the system bus.  But more significantly, we're starting to max out pull-up resistance on the baord by having 6+ I2C components.  Each I2C_SYS module brings its own pull-ups.  This works fine for a few, but after 5-6 on one set of pins it starts causing problems.  So with two buses, we weep the parallel total above about 1 kΩ (measure SDA to 3V3 with power off).

---

## GY-87 10DOF IMU Wiring (I2C_EXT)

| GY-87 pin | XIAO pin | Note |
|---|---|---|
| VCC_IN | 5V | The GY-87 has its own 3.3 V regulator. Do not feed 3.3 V into VCC_I
| 3.3V | not connected | Regulator output, not an input. |
| GND | GND | |
| SCL | D2 (GPIO3) | I2C_EXT SCL |
| SDA | D9 (GPIO8) | I2C_EXT SDA |
| FSYNC, INTA, DRDY | not connected | |

Mount the board with the silkscreen **X arrow pointing to the bow** so pitch is nose-up positive and roll is starboard-down positive.

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

## I2C Sensor Bus

All I2C modules share the same four wires:

| Sensor Pin | XIAO ESP32-S3 Pin |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO5 / D4 / SDA |
| SCL | GPIO6 / D5 / SCL |

Current turtleOS I2C modules:

- OLED display
- RTC module
- QMC5883L compass
- AS5600 magnetic angle encoder
- INA219 voltage/current monitor


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
| LED signal | GPIO1 |
| LED ground | GND |

This is intended for a the LED indicator built into the button.



```
