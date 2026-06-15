![XIAO ESP32-S3 Pinout](https://p.kagi.com/proxy/image-154-1024x468.png?c=Vfl6XPJbDCiWZ5UKwg9Lo8BihlEKTSJ4r20X3vC41dhmkohmp8vmADbNdp-PeK2mosiJybrUhyZa4l0vDjbULW9Uo9nTQwwiOJKsJcSqQOpBPKAr6UXUIut_O9Hl33jl)

# Hope Turtle Wiring Guide — XIAO ESP32-S3

This guide documents the turtleOS wiring layout for the **Seeed Studio XIAO ESP32-S3**.

The pin chart below matches this board orientation:

- **USB-C port facing up**
- **component side visible**
- pins read from **top to bottom** on each side

We highly recommend following the wire color schema below. Future hope turtle wiring guides and diagrams will use the same color convention.

---

## XIAO ESP32-S3 Pin Usage

| Left Side (Top → Bottom) | Right Side (Top → Bottom) |
|---|---|
| 🟨 **1** GPIO1 / Button LED | 🟥 **1** 5V — 5V input from bq25185 |
| ⬜ **2** GPIO2 / A1 / D1 — spare | ⚫ **2** GND → shared ground |
| ⬜ **3** GPIO3 / A2 / D2 — spare | 🟥 **3** 3V3 → OLED VCC, RTC VCC, QMC5883L VCC, AS5600 VCC, INA219 VCC |
| 🟪 **4** GPIO4 / A3 / D3 → BUTTON | ⬜ **4** GPIO9 / A10 / D10 / MOSI — spare |
| 🟩 **5** GPIO5 / A4 / D4 / SDA → OLED SDA, RTC SDA, QMC5883L SDA, AS5600 SDA, INA219 SDA | ⬜ **5** GPIO8 / A9 / D9 / MISO — spare |
| 🟨 **6** GPIO6 / A5 / D5 / SCL → OLED SCL, RTC SCL, QMC5883L SCL, AS5600 SCL, INA219 SCL | 🟨 **6** GPIO7 / A8 / D8 / SCK → SERVO signal |
| 🔵 **7** GPIO43 / D6 / TX → GPS RX | 🟠 **7** GPIO44 / D7 / RX ← GPS TX |

> **Important:** The XIAO 5V pin is used as a regulated 5V input from the bq25185 boost board. The MG996R servo is **not** powered from the XIAO. The servo has its own 6V regulator and only shares ground and a PWM signal with the XIAO.

---

## Wire Color Schema

| Color | Purpose |
|---|---|
| 🟥 Red | Power |
| ⚫ Black | Ground |
| 🟩 Green | I2C SDA |
| 🟨 Yellow | I2C SCL / button LED |
| 🔵 Blue | UART TX from XIAO to GPS RX |
| 🟠 Orange | UART RX on XIAO from GPS TX |
| 🟪 Purple | Button signal |
| 🟨 Yellow | Servo PWM signal |
| ⬜ White | Spare / unused GPIO |

---

## Power Supply Architecture

The hope turtle uses a **single-cell 3.7V lithium-ion battery system** with two regulated power paths:

1. A clean **5V regulated line** for the XIAO ESP32-S3 and sensor system.
2. A separate **6V regulated line** for the MG996R servo.

This keeps servo current spikes and motor noise away from the XIAO, GPS, and I2C sensors.

Recommended battery:

| Battery | Purpose |
|---|---|
| 1 × 21700 lithium-ion cell, 3.7V nominal, high-discharge type | Main hope turtle battery |

Recommended power modules:

| Module | Purpose |
|---|---|
| Adafruit bq25185 USB / DC / Solar Charger with 5V Boost Board | Charges the 1S battery and outputs regulated 5V for the XIAO |
| Pololu S13V25F6 6V Step-Up/Step-Down Regulator | Provides regulated 6V power for the MG996R servo |
| INA219 voltage/current monitor | Measures battery voltage/current telemetry |
| Main on/off switch | Disconnects battery power from the hope turtle system |

---

## Power Flow Overview

```text
21700 3.7V battery
   │
   ├── Main on/off switch
   │
   ├── INA219 current/voltage monitor
   │
   ├── Adafruit bq25185 charger + 5V boost
   │       └── 5V OUT → XIAO 5V/VBUS
   │
   └── Pololu S13V25F6 regulator
           └── 6V OUT → MG996R servo power
```

All grounds must be connected together:

```text
Battery GND
bq25185 GND
Pololu regulator GND
XIAO GND
Servo GND
Sensor GND
```

---

## Main Battery Wiring

The hope turtle battery is a single-cell lithium-ion battery:

| Battery Wire | Connects To |
|---|---|
| Battery + | Main on/off switch input |
| Battery - | Shared system ground |

The main on/off switch should be placed on the positive battery line.

```text
Battery + → On/off switch → hope turtle power input
Battery - → Shared GND
```

This allows the complete hope turtle to be powered down from one physical switch.

---

## INA219 Battery Monitor Placement

The INA219 is used to measure battery voltage and current for hope turtle telemetry.

Recommended placement for measuring total battery output:

```text
Battery + → On/off switch → INA219 VIN+
INA219 VIN- → split to bq25185 BAT+ and Pololu VIN+
Battery - → shared GND
```

This lets the INA219 measure the combined battery current used by:

- XIAO + sensors
- GPS module
- servo regulator
- other connected modules

| INA219 Pin | Connects To |
|---|---|
| VIN+ | Output from main on/off switch |
| VIN- | Positive power rail feeding bq25185 and Pololu regulator |
| VCC | XIAO 3V3 |
| GND | Shared GND |
| SDA | GPIO5 / D4 / SDA |
| SCL | GPIO6 / D5 / SCL |

Important note: when the bq25185 is charging the battery, INA219 readings may be harder to interpret because current may flow into the battery instead of only out of it.

For first power testing, it is acceptable to leave the INA219 out until the XIAO and servo power rails have been confirmed stable.

---

## XIAO Power Wiring

The XIAO ESP32-S3 is powered from the regulated **5V output** of the bq25185 boost board.

| bq25185 Pin | XIAO ESP32-S3 Pin |
|---|---|
| 5V OUT | 5V / VBUS |
| GND | GND |

Correct:

```text
bq25185 5V OUT → XIAO 5V/VBUS
bq25185 GND    → XIAO GND
```

Incorrect:

```text
bq25185 5V OUT → XIAO BAT+
```

Do **not** connect the bq25185 5V output to the XIAO battery pads.

The XIAO battery pads are for a direct single-cell LiPo connection only. In this wiring design, the bq25185 handles battery charging and 5V regulation externally.

---

## Servo Power Wiring

The MG996R servo is powered separately from the XIAO using the Pololu S13V25F6 regulator set to 6V.

| Pololu S13V25F6 Pin | Connects To |
|---|---|
| VIN | Battery positive after INA219 |
| GND | Shared GND |
| VOUT 6V | MG996R red wire |
| GND | MG996R brown/black wire |

The XIAO only sends the control signal:

| MG996R Servo Wire | Connects To |
|---|---|
| Red | Pololu 6V OUT |
| Brown | Shared GND |
|  Yellow | XIAO GPIO7 / D8 |

The MG996R must **not** be powered from the XIAO 5V pin, 3V3 pin, or battery pads.


---

## Servo Safety Notes

The MG996R can draw large current spikes, especially when starting, pushing against load, or stalled.

For first tests:

- remove the servo horn
- keep the servo unloaded
- test small movements around center
- confirm the Pololu regulator output with a multimeter before connecting the servo
- add a capacitor near the servo power input if resets or twitching occur


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


## First Power-Up Checklist

Before powering the complete hope turtle:

1. Confirm battery polarity.
2. Confirm the on/off switch interrupts battery positive.
3. Confirm the bq25185 output is 5V before connecting it to XIAO 5V/VBUS.
4. Confirm the Pololu regulator output is 6V before connecting the MG996R.
5. Confirm all grounds are common.
6. Confirm the servo red wire is **not** connected to the XIAO.
7. Confirm GPIO7 / D8 goes only to the servo signal wire.
8. Start with the servo unloaded and test small movements only.

---

## Power Summary

```text
1 × 21700 3.7V battery
   │
   ├── bq25185 charger + 5V boost
   │       └── XIAO 5V/VBUS
   │
   └── Pololu S13V25F6 6V regulator
           └── MG996R servo power

XIAO GPIO7 / D8 → MG996R signal
All grounds connected together
```
