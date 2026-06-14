# turtleOS
**The official microcontroller navigation system for the hope turtle**

---

## Overview

The hope turtle project envisions autonomous marine vehicles made from 95% organic material that are able to self-navigate within ±5 km to a coastal destination to deliver food, building materials, and hope. This repository holds the code for the microcontroller operating system that enables the operation, power management, sensor integration, and overall navigation of the turtle. The codebase is MicroPython running on a Seeed Studio XIAO ESP32-S3.

---

## How it works

The hope turtle is a wind-powered vessel. turtleOS closes the navigation loop entirely on the microcontroller, without any shore-side control link required during a voyage.

**Positioning.** A u-blox NEO-6M GPS module streams NMEA sentences over UART. turtleOS parses incoming fixes continuously to maintain a current latitude/longitude estimate. Once a fix is acquired, the device knows where it is on the water.

**Heading.** A QMC5883L three-axis compass (mounted flat on the hull) measures the local magnetic field vector and outputs a bearing in degrees. A configurable declination offset corrects for local magnetic variation so that compass north tracks true north as closely as the sensor allows.

**Course calculation.** turtleOS computes the great-circle bearing from the current GPS fix to the stored destination waypoint. Comparing that bearing against the live compass heading gives the cross-track error — the angular difference between where the turtle is pointed and where it needs to go.

**Sail actuation.** An MG996R servo drives the sail boom. turtleOS maps the cross-track error to a target boom angle and writes the corresponding PWM pulse to the servo. The sail then swings toward the wind angle that best moves the hull toward the destination. An AS5600 magnetic angle encoder on the boom pivot provides closed-loop feedback so the servo knows the actual sail position rather than relying on open-loop pulse counting.

**Power management.** An INA219 current-sense IC sits in the battery circuit and reports bus voltage, shunt voltage, and instantaneous current over I2C. turtleOS logs power draw and can estimate remaining capacity, allowing the navigation algorithm to make conservative decisions (heaving to, reducing telemetry rate) when energy is low.

**Onboard display.** A 128×64 OLED shows heading, bearing-to-destination, GPS fix status, battery state, and servo angle at a glance. The animated turtle idle screen indicates the system is running and waiting for the next navigation cycle.

**Telemetry.** When WiFi is in range of a shore access point, turtleOS posts a JSON telemetry packet (position, heading, battery, sensor readings) to a REST endpoint at configurable intervals. This gives shore operators a breadcrumb trail without the turtle depending on a live control link to navigate.

**airOS fallback.** Setting `turtle_mode: false` in `config.json` switches the device into airOS mode — an air quality monitor that reads CO₂, TVOC, temperature, and humidity from an ENS160 + AHT21 sensor pair. The same hardware stack and WiFi telemetry pipeline are shared between both modes.

---

## Hardware stack

| Component | Role | Dev Status |
|---|---|---|
| Seeed Studio XIAO ESP32-S3 | Main MCU — runs turtleOS, built-in WiFi for telemetry | CORE |
| MG996R sail servo | Sail actuator — PWM-driven boom control at 50 Hz | Active |
| u-blox NEO-6M GPS | Position fix — NMEA lat/lon stream over UART | Active |
| GY-271 / QMC5883L compass | Magnetic heading — bearing for course-error calculation | Active |
| AS5600 magnetic angle encoder | Sail boom position feedback — closed-loop servo control | In development |
| INA219 current/power monitor | Battery voltage, current, and charge estimation over I2C | In development |
| SSD1306 / SH1106 OLED (128×64) | Navigation display — heading, GPS state, battery, turtle animation | CORE |
| DS3231 RTC | UTC timekeeping — survives power-off without network sync | CORE |
| ProtoMate for XIAO  | Circuit board to place Xiao and connect wires | CORE |
| AHT21 + ENS160 circuit | One circuit that does temp, humidity and TVOC | active |
| ICM-20948 | 9-DOF heading, pitch, roll — accurate orientation independent of magnetic interference | CORE - In dev |
| Pololu S13V25F6 voltage regulator | Regulated 6V 2.5A output — stable power to servo and MCU from variable battery voltage | CORE - In dev |
| Adafruit bq25185 solar charger | USB / DC / solar charging with 5V boost — LiPo charge management and regulated power delivery | CORE - In dev |
| 21700 Li-ion cell (4200mAh 3.7V 30A) | Primary energy storage — high-capacity, high-discharge cell for extended voyages | CORE |
| Xiao Wio-SX1262 Kit for Meshtastic & LoRa | Long-range mesh radio — field telemetry and command relay without WiFi infrastructure | Future |

## Key Components

| Component | Role | Dev Status |
|---|---|---|
| Tactile button | User input — screen carousel, config toggles, debug gate | CORE |
| Physical on/off switch | Main power cutoff — required for field safety and battery conservation between deployments | CORE |
| 3M screws x8 | 12mm long Tappered screws for connecting circuit boards to spacers | CORE |
| 3M PCB spaces x5 | 2cm long spacers to seperate circuit boards | CORE |
| Blue tact | Glue paste for adding circuits onto boards | optional |
| 1M screws | 8mm long screws for connecting circuits to boards | CORE |
| Dupont Cables | Selection of MF, FF, MM 5cm/10cm/20cm colored jumper cables | CORE |


## Physical Components

The hope turtles various physical components can be either 3D printed or made out of wood by you or your local carpenter.  Hang tight... we're still setting up the repository of STLs and carpentry PDFs.  It should be ready soon. 











