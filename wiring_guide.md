# Hope Turtle Wiring Guide — XIAO ESP32-S3

This guide documents the turtleOS wiring layout for the **Seeed Studio XIAO ESP32-S3**.

We highly recommend following the wire color schema below and using our color conventions with your actual wires.  We'll do our best to stick to these colors in future versions.

---

## XIAO Top Pin Usage

![XIAO ESP32-S3 front pinout (Seeed Studio)](https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/img/XIAO_ESP32-S3_front_pinout.png)

The pin chart below matches this board orientation:

- **USB-C port facing up**
- **component side visible**
- pins read from **top to bottom** on each side

| Left Side (Top → Bottom) | Right Side (Top → Bottom) |
|---|---|
| 🔲 **1** GPIO1 / A0 / D0 — RESERVED: GPS_WAKEUP (stacked L76K GNSS module) | 🟥 **1** 5V (VBUS) — no 5 V rail on battery power. Optional: panel USB-C socket VBUS in, through a Schottky diode (see [adding_servo_switch.md](adding_servo_switch.md#how-it-connects)) |
| 🟨 **2** GPIO2 / A1 / D1 → BUTTON LED (active HIGH, LED + resistor to GND) | ⚫ **2** GND → shared ground (all modules, servo SSR GND, servo regulator GND) |
| 🟨 **3** GPIO3 / A2 / D2 → **I2C_EXT SCL** → GY-521 SCL, GY-273 SCL | 🟥 **3** 3V3 → OLED, RTC, AS5600, INA219, AHT20+BMP280, GY-521, GY-273, servo SSR VIN |
| 🟪 **4** GPIO4 / A3 / D3 → BUTTON | 🔲 **4** GPIO9 / A10 / D10 / MOSI — RESERVED: GPS_RESET (stacked L76K GNSS module) |
| 🟩 **5** GPIO5 / A4 / D4 / SDA → **I2C_SYS SDA** → OLED, RTC, AS5600, INA219, AHT20+BMP280 | 🟩 **5** GPIO8 / A9 / D9 / MISO → **I2C_EXT SDA** → GY-521 SDA, GY-273 SDA |
| 🟨 **6** GPIO6 / A5 / D5 / SCL → **I2C_SYS SCL** → OLED, RTC, AS5600, INA219, AHT20+BMP280 | 🟨 **6** GPIO7 / A8 / D8 / SCK → SERVO signal |
| 🔵 **7** GPIO43 / D6 / TX → GPS RX (stacked L76K GNSS module) | 🟠 **7** GPIO44 / D7 / RX ← GPS TX (stacked L76K GNSS module) |

With the GNSS module stacked, every header pin is taken. The module's UART (D6/D7) needs no wires: it connects through the stacking header.

---

## XIAO Bottom Pad Usage

![XIAO ESP32-S3 underside pads (Seeed Studio)](https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/img/XIAO_ESP32-S3_back_pinout.png)

The underside of the XIAO carries the battery pads and a cluster of extra pads. Solder any wires here **before stacking the GNSS module**, because the pads can't be reached afterwards, and strain-relieve them with hot glue or Kapton tape: they lift easily.

| Pad | GPIO | Seeed function | turtleOS use |
|---|---|---|---|
| **BAT+** | — | Battery + (charger input/output) | 🟥 System battery + from the main switch (after the INA219). See [Power_System.md](Power_System.md) |
| **BAT−** | — | Battery − | ⚫ Common negative (battery −, INA219 GND, CN3065 BATT−). Never switched |
| **MTDO** | GPIO40 | JTAG | 🟨 **Servo power switch EN** (Pololu #5421 SSR, HIGH = servo rail on). See [adding_servo_switch.md](adding_servo_switch.md) |
| MTDI | GPIO41 | JTAG | ⬜ Free |
| MTCK | GPIO39 | JTAG | ⬜ Free |
| MTMS | GPIO42 | JTAG | ⬜ Free |
| GND | — | Ground | ⬜ Free (extra ground point) |
| EN | — | Chip enable / reset | Not used. Pulling it low resets the XIAO |
| USB_D+ / USB_D− | — | USB data lines (same as the USB-C socket) | Optional: panel-mount USB-C socket in the cap wall. See [adding_servo_switch.md](adding_servo_switch.md#how-it-connects) |

GPIO40 is not a strapping pin, so the servo switch doesn't affect how the chip boots.

---

## The External and System Buses

The latest version of turtleOS (v2.4) lays the foundation for forthcoming turtleShell v3 PCB by establishing two I2C buses. We'll have one for sensors and components that are hardwired to the PCB (i.e our OLED, INA219, AHT20, etc.) and one for "external" sensors that are wired in to the PCBs built in JST ports.  We'll call these the system and external buses (I2C_SYS and I2C_EXT respectively). As of turtleOS 2.4 only the IMU and compass are on I2C_EXT; the remaining sensors migrate as the v3.0 PCB develops.

| Bus | Peripheral | SDA | SCL | Speed | Notes |
|---|---|---|---|---|---|
| **I2C_SYS** | I2C(0) | GPIO5 (D4) | GPIO6 (D5) | 400 kHz | Onboard/system bus. `init_i2c()` in firmware. |
| **I2C_EXT** | I2C(1) | GPIO8 (D9) | GPIO3 (D2) | 400 kHz | External/plug-in bus. `init_i2c_ext()` in firmware.|

**Why two buses?** The GY-521's MPU6050 answers at 0x68, the same address as the DS3231 clock. Giving each its own bus avoids the collision without board surgery. More importantly, every I2C module brings its own pull-up resistors, and they add up in parallel. A few on one pair of pins is fine, but after 5–6 the bus starts failing. Two buses keep the parallel total above about 1 kΩ on each (measure SDA to 3V3 with the power off).

### I2C_SYS devices

| Chip | Module | Address | What it does | Driver | Mode |
|---|---|---|---|---|---|
| SSD1306 / SH1106 | OLED display, 128×64 | 0x3C | Screens, turtle animation, menus | `OLED` / `SSD1306_I2C` (`src/ui/oled.py`) | both |
| DS3231 | Real-time clock | 0x68 | Keeps UTC time through power-off | `DS3231` (`src/drivers/ds3231.py`) | both |
| AS5600 | Sail angle encoder | 0x36 | Magnetic angle of the sail pivot; finds the wind during a luff sweep | `AS5600` (`src/drivers/as5600.py`) | turtleOS |
| INA219 | Battery monitor | 0x40 | Battery voltage and net charge/discharge current | `INA219` (`src/drivers/ina219.py`) | turtleOS |
| AHT20 | AHT20 + BMP280 module | 0x38 | Air temperature and relative humidity | `AHT21` (`src/sensors/air.py`), via `AirSensor` | both |


### I2C_EXT devices

| Chip | Module | Address | What it does | Driver | Mode |
|---|---|---|---|---|---|
| MPU6050 | GY-521 IMU | 0x68 (0x69 if AD0 high) | Accelerometer + gyroscope: hull pitch and roll | `MPU6050` (`src/drivers/mpu6050.py`), wrapped by `GY87` (`src/drivers/gy87.py`) | turtleOS |
| QMC5883L / HMC5883L | GY-273 compass | 0x0D / 0x1E (0x2C for a QMC5883P clone) | Three-axis magnetometer: heading | `QMC5883L` / `HMC5883L` / `QMC5883P` (`src/drivers/hmc5883l_qmc5883l.py`), via `GY87` | turtleOS |


turtleOS probes I2C_EXT once at boot and shares the one `GY87` instance with navigation, the compass screen and telemetry.

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

The Seeed XIAO GNSS module (L76K) **stacks directly onto the XIAO**, so there are no GPS wires to run. Through the stacking header it uses:

| GNSS module signal | XIAO pin | Notes |
|---|---|---|
| GPS_RXD | GPIO43 / D6 / TX | XIAO TX → GPS RX, 9600 baud |
| GPS_TXD | GPIO44 / D7 / RX | GPS TX → XIAO RX |
| GPS_WAKEUP | GPIO1 / D0 | Reserved: don't reuse |
| GPS_RESET | GPIO9 / D10 | Reserved: don't reuse |
| Power, GND | header power pins | |

Solder any underside-pad wires (servo switch, USB) **before** stacking the module. See [XIAO Bottom Pad Usage](#xiao-bottom-pad-usage).

---|---|
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
