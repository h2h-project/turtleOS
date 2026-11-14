# turtleOS
**The official microcontroller navigation system for the hope turtle**

---

## Overview

Hope turtles are autonomous marine vehicles made from 95% organic material that are able to self-navigate within ±5 km to a coastal destination to deliver food, building materials, and hope. This repository holds the code for the microcontroller operating system that enables the operation, power management, sensor integration, and overall navigation of the turtle. The codebase is MicroPython running on a Seeed Studio XIAO ESP32-S3.  Note the H2H project also has separate repositories for the project's PCB, Android Wrangler App, the turtle body, and more.

---

## How it works

The hope turtle is a wind-powered vessel. Its brain is a [Seeed Studio XIAO ESP32-S3](https://www.seeedstudio.com/XIAO-ESP32S3-p-5627.html), a thumbnail-sized board with a dual-core ESP32-S3, built-in WiFi and Bluetooth, and 8 MB of PSRAM, running turtleOS in [MicroPython](https://micropython.org/). turtleOS closes the navigation loop entirely on the microcontroller, so no shore-side control link is needed during a voyage. For testing and field trials, the companion Android app **Turtle Wrangler** connects to the turtle over Bluetooth. From a short distance it can read live telemetry, move the sail, and start and end journeys. Wrangler is a testing tool only. A turtle on a mission navigates without it fully autonomously using simple routing logic (there's no internet connection or AI involed).

**Positioning.** The Seeed Studio XIAO GNSS module (an L76K receiver) stacks directly onto the XIAO and streams NMEA sentences over UART at 9600 baud, once per second. turtleOS parses incoming fixes continuously to keep a current latitude/longitude estimate. Once a fix is acquired, the device knows where it is on the water.

**Heading.** Heading comes from a pair of chips sharing the turtle's external I2C bus: a GY-273 three-axis compass (QMC5883L/HMC5883L) and a GY-521 MPU6050 IMU (accelerometer + gyroscope). The compass measures the local magnetic field and outputs a bearing in degrees. A configurable declination offset corrects for local magnetic variation so that compass north tracks true north as closely as the sensor allows. The IMU reports the hull's pitch and roll, and both are logged in telemetry.

**Course calculation.** turtleOS computes the great-circle bearing from the current GPS fix to the stored destination waypoint. Comparing that bearing against the live compass heading gives the heading error — the angular difference between where the turtle is pointed and where it needs to go.

**Sail actuation.** A positional servo (an MG996R by default, with the pulse range set in config to suit the servo fitted) drives the sail. Each navigation cycle, a PID controller turns the heading error into a sail angle and turtleOS writes the matching PWM pulse. The turtle has no wind vane. Instead, an AS5600 magnetic angle encoder on the sail pivot finds the wind with a **luff sweep**: the servo swings the sail slowly from one end stop to the other and back. The encoder picks up the jitter of a fluttering sail where it crosses the wind. The midpoint of the two flutter onsets gives the apparent wind angle. The operator starts the first sweep from the turtle's button or from Wrangler. Once the wind is found, the turtle starts sailing and repeats the sweep on its own about every 10 minutes. On arrival, on a fault, or when the battery runs low, the sail is feathered to centre. To save power, a solid-state switch cuts the servo's supply after 30 seconds without a move (configurable). The fitted servo holds the sail by friction while unpowered.

**Power management.** An INA219 current-sense IC sits in the battery circuit and reports bus voltage, shunt voltage, and instantaneous current over I2C. turtleOS logs power draw and can estimate remaining capacity, allowing the navigation algorithm to make conservative decisions (heaving to, reducing telemetry rate) when energy is low.

**Onboard display.** A 128×64 OLED shows heading, bearing-to-destination, GPS fix status, battery state, and servo angle at a glance. The animated turtle idle screen indicates the system is running and waiting for the next navigation cycle.

**Telemetry.** When WiFi is in range of a shore access point, turtleOS posts a JSON telemetry packet (position, heading, battery, sensor readings) to a REST endpoint at configurable intervals. This gives shore operators a breadcrumb trail without the turtle depending on a live control link to navigate.

**airOS fallback.** Setting `turtle_mode: false` in `config.json` switches the device into airOS mode — an air quality monitor that reads CO₂, TVOC, temperature, and humidity from an ENS160 + AHT21 sensor pair. The same hardware stack and WiFi telemetry pipeline are shared between both modes.

---

## Get Building

Ready to build your own hope turtle? These guides will take you from components to a working vessel:

- **[Hardware Stack](hardware_stack.md)** — full component list with links to example listings. Every part is chosen to be inexpensive and globally sourceable.
- **[Wiring Guide](wiring_guide.md)** — step-by-step wiring reference for the XIAO ESP32-S3, covering power architecture, I2C sensors, GPS, servo, and button.
- **[Power Sytem](Power_System.md)** — The Turtle's power archetecture, batteries, charging and sensing.
- **[Turtle Shells](https://github.com/h2h-project/turtle_Shells/blob/main/README.md)** — STL files and carpentry plans for the physical hull, cage, and chassis that make up the turtle body. Parts can be 3D printed or built by a local carpenter.




