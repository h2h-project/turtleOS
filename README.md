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

## Get Building

Ready to build your own hope turtle? These guides will take you from components to a working vessel:

- **[Hardware Stack](hardware_stack.md)** — full component list with links to example listings. Every part is chosen to be inexpensive and globally sourceable.
- **[Wiring Guide](wiring_guide.md)** — step-by-step wiring reference for the XIAO ESP32-S3, covering power architecture, I2C sensors, GPS, servo, and button.
- **[Turtle Shells](https://github.com/h2h-project/turtle_Shells/blob/main/README.md)** — STL files and carpentry plans for the physical hull, cage, and chassis that make up the turtle body. Parts can be 3D printed or built by a local carpenter.




