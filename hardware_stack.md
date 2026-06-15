# Hope Turtle Hardware

The hope turtle is built from **globally accessible, inexpensive components**. Every part in this list was chosen because it can be sourced through online marketplaces or local electronics suppliers in most countries — no specialist distributors, no proprietary supply chains. The total component cost for a complete hope turtle control system is intentionally kept low so that community builders, schools, and humanitarian organisations anywhere in the world can join the project.

The links in the Example column point to Amazon UK listings as a convenient reference. In most cases identical or equivalent parts are available through local marketplaces (AliExpress, Shopee, Mercado Libre, local electronics shops) for even less. Wherever possible, standard I²C and UART modules are used so that builders can substitute equivalent boards without rewriting firmware.

---

## Hardware stack

| Component                                 | Role | Dev Status    | Example |
|-------------------------------------------|---|---------------|---|
| Seeed Studio XIAO ESP32-S3                | Main MCU — runs turtleOS, built-in WiFi for telemetry | Core          | <a href="https://www.amazon.co.uk/ESP32S3-2-4GHz-Wi-Fi-Dual-core-Supported-Efficiency-Interface/dp/B0BYSB66S5" target="_blank">🔗</a> |
| MG996R sail servo                         | Sail actuator — PWM-driven boom control at 50 Hz | Active        | <a href="https://www.amazon.co.uk/Towerpro-MG996R-Servo-10kg-0-20sec/dp/B00URCIGBQ" target="_blank">🔗</a> |
| GPS Tracker                               | GNSS add on Module for XIAO | Core          | <a href="https://thepihut.com/products/gnss-add-on-module-for-seeed-studio-xiao" target="_blank">🔗</a> |
| AS5600 magnetic angle encoder             | Sail boom position feedback — closed-loop servo control | Active        | <a href="https://www.amazon.co.uk/HALJIA-Induction-Measurement-Magnetized-Precision/dp/B08BCB899Q" target="_blank">🔗</a> |
| INA219 current/power monitor              | Battery voltage, current, and charge estimation over I2C | Active        | <a href="https://www.amazon.co.uk/INA219-Bi-directional-Current-Supply-Monitor/dp/B07YDH2PCY" target="_blank">🔗</a> |
| SSD1306 / SH1106 OLED (128×64)            | Navigation display — heading, GPS state, battery, turtle animation | Core          | <a href="https://www.amazon.co.uk/AZDelivery-0-96-inch-Display-Parent/dp/B081NFJP68" target="_blank">🔗</a> |
| DS3231 RTC                                | UTC timekeeping — survives power-off without network sync | CORE          | <a href="https://www.amazon.co.uk/Wishiot-AT24C32-Raspberry-Mega2560-Leonardo/dp/B0BTM8HHX2" target="_blank">🔗</a> |
| ProtoMate for XIAO                        | Circuit board to place Xiao and connect wires | CORE          | <a href="https://www.amazon.co.uk/Seeeduino-Expansion-Peripherals-Expandable-Interfaces/dp/B08NDZ3WCP" target="_blank">🔗</a> |
| AHT21 + ENS160 circuit                    | One circuit that does temp, humidity and TVOC | active        | <a href="https://www.amazon.co.uk/ARCELI-Quality-Temperature-Humidity-Purification/dp/B0CRTVMM7N" target="_blank">🔗</a> |
| ICM-20948                                 | 9-DOF heading, pitch, roll — accurate orientation independent of magnetic interference | CORE - In dev | <a href="https://www.amazon.co.uk/9DOF-IMU-BREAKOUT-ICM-20948-Q/dp/B07VNV3WKL" target="_blank">🔗</a> |
| Pololu S13V25F6 voltage regulator         | Regulated 6V 2.5A output — stable power to servo and MCU from variable battery voltage | CORE - In dev | <a href="https://www.pololu.com/product/4981" target="_blank">🔗</a> |
| Adafruit bq25185 solar charger            | USB / DC / solar charging with 5V boost — LiPo charge management and regulated power delivery | CORE - In dev | <a href="https://www.amazon.co.uk/Adafruit-bq25185-Charging-Module-6106/dp/B0DXK6YZX8" target="_blank">🔗</a> |
| 21700 Li-ion cell (4200mAh 3.7V 30A)      | Primary energy storage — high-capacity, high-discharge cell for extended voyages | CORE          | <a href="https://www.amazon.co.uk/Vapcell-4200mAh-21700-Rechargeable-Battery/dp/B0DFCZHQ6L" target="_blank">🔗</a> |
| Xiao Wio-SX1262 Kit for Meshtastic & LoRa | Long-range mesh radio — field telemetry and command relay without WiFi infrastructure | Future        | <a href="https://www.amazon.co.uk/XIAO-ESP32S3-Wio-SX1262-Development-Meshtastic/dp/B0DZCQ1FG3" target="_blank">🔗</a> |

## Key Components

| Component | Role                                                                                       | Dev Status | Example |
|---|--------------------------------------------------------------------------------------------|---|---|
| Tactile button | User input — screen carousel, config toggles, debug gate                                   | CORE | <a href="https://www.amazon.co.uk/Ultra-Momentary-Waterproof-Button-Switch/dp/B0H49MCSHL" target="_blank">🔗</a> |
| Physical on/off switch | Main power cutoff — required for field safety and battery conservation between deployments | CORE | <a href="https://www.amazon.co.uk/Gebildet-Self-Lock-Terminals-Stainless-Waterproof/dp/B095P1LGR3" target="_blank">🔗</a> |
| 3M screws x8 | 12mm long Tappered screws for connecting circuit boards to spacers                         | CORE | <a href="https://www.amazon.co.uk/12mm-Socket-Countersunk-Machine-Screw/dp/B07MCH9QSH" target="_blank">🔗</a> |
| 3M PCB spaces x5 | 2cm long spacers to seperate circuit boards                                                | CORE | <a href="https://www.amazon.co.uk/YOKIVE-Standoffs-Consistent-Motherboard-Electronics/dp/B0BWXKBY8R" target="_blank">🔗</a> |
| Blue tact | Glue paste for adding circuits onto boards                                                 | optional | <a href="https://www.amazon.co.uk/Bostik-Multipurpose-Reusable-Adhesive-Non-Toxic/dp/B0001OZI70" target="_blank">🔗</a> |
| 1M screws | 6mm long screws for connecting circuits to boards                                          | CORE | <a href="https://www.amazon.co.uk/sourcingmap-100pcs-Stainless-Phillips-Tapping/dp/B01KXS7TOI/" target="_blank">🔗</a> |
| Dupont Cables | Selection of MF, FF, MM 5cm/10cm/20cm colored jumper cables                                | CORE | <a href="https://www.amazon.co.uk/dp/B0BTT48V7P" target="_blank">🔗</a> |
| GT2 timing pulley x2 (8mm bore, 6mm belt) | Drive pulleys for sail boom actuation mechanism                                            | CORE | <a href="https://www.amazon.co.uk/Saiper-Timing-Belt-Pulley-Teeth/dp/B07M5PCM2V/" target="_blank">🔗</a> |
| GT2 timing belt 45T, 6mm width x1 | Closed-loop drive belt connecting servo pulley to boom                                     | CORE | <a href="https://www.amazon.co.uk/Zzhyu-Premium-Timing-Closed-Printer-Resistant/dp/B0CDJJ2YYQ" target="_blank">🔗</a> |
| 608 bearings x3 | Standard 8×22×7mm ball bearings for rotary pivot mounts                                    | CORE | <a href="https://www.amazon.co.uk/Skateboard-Bearings-Double-Shielded-Silver/dp/B002BBGTK6" target="_blank">🔗</a> |
| Flange x1 (5mm bore, 22mm OD, 12mm height) | Shaft flange for boom pivot mounting                                                       | CORE | <a href="https://www.amazon.co.uk/DMiotech-Coupling-Connector-Support-Coupler/dp/B0C1SK8KW1" target="_blank">🔗</a> |
