# First Time Setup — hope turtle

If you're setting up a hope turtle for the first time, you're in the right place.

Don't worry if you're new to microcontrollers or MicroPython. This guide walks step-by-step through the entire process. Follow the steps carefully and you'll have your turtle up and running.

---

## Overview

The setup process has five steps:

1. Create your hope turtle account and register your device
2. Install MicroPython firmware on your XIAO ESP32-S3
3. Run the turtle installer
4. Wire your hardware
5. First boot

Once complete, your hope turtle will animate on the OLED, read its compass and GPS, and post telemetry to your hopeturtles.org dashboard when WiFi is in range.

---

## 1. Create Your hopeturtles.org Account

Before setting up your device, you need a **hopeturtles.org account** so you can register your turtle and receive your device credentials.

Visit:

**https://hopeturtles.org**

After creating an account:

1. Go to your dashboard
2. Register your turtle device
3. Note down your **device ID** and **device key** — the installer will ask for these

---

## 2. Install MicroPython Firmware

turtleOS runs on **MicroPython** — a version of Python designed for microcontrollers. You must flash MicroPython onto your XIAO ESP32-S3 before running the installer.

### Install the tools

```bash
pip install esptool mpremote
```

### Download MicroPython for the XIAO ESP32-S3

Go to:

**https://micropython.org/download/ESP32_GENERIC_S3/**

Download the latest `.bin` file (look for the `SPIRAM` variant if available, as the XIAO has 8 MB PSRAM).

### Find your board's port

Plug in your XIAO via USB-C, then run:

```bash
# Linux / Raspberry Pi
ls /dev/ttyUSB* /dev/ttyACM*

# Mac
ls /dev/cu.usb*
```

Note the port name (e.g. `/dev/ttyUSB0` or `/dev/cu.usbmodem14301`).

### Erase and flash

```bash
# Erase existing flash
esptool.py --chip esp32s3 --port /dev/ttyUSB0 erase_flash

# Flash MicroPython (replace the filename with the one you downloaded)
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 \
  write_flash -z 0x0 ~/Downloads/ESP32_GENERIC_S3-SPIRAM_OCT-20251209-v1.27.0.bin
```

### Verify the install

```bash
mpremote connect /dev/ttyUSB0 repl
```

You should see a `>>>` Python prompt. Press `Ctrl+]` to exit.

---

## 3. Run the Turtle Installer

The installer downloads the turtleOS code, asks you a few questions about your setup, generates your `config.json`, and uploads everything to the board.

Run this single command:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/h2h-project/turtleOS/main/scripts/turtle_install.sh)
```

The installer will:

- Check that `git` and `mpremote` are installed
- Download turtleOS to `~/Documents/HopeTurtle/turtleOS`
- Ask for your WiFi name and password
- Ask for your device ID and key
- Ask about GPS, servo, compass offset, and timezone
- Upload the firmware and config to your XIAO
- Set the onboard RTC clock
- Open the live boot log so you can watch the first startup

**Have ready before you run it:**

| Value | Where to get it |
|---|---|
| WiFi SSID | Your router |
| WiFi password | Your router |
| Device ID | hopeturtles.org dashboard |
| Device key | hopeturtles.org dashboard |
| Timezone offset from UTC | e.g. +8 for Manila, −5 for New York |

---

## 4. Wire Your Hardware

With firmware installed, wire up the sensors and power system.

Follow the full wiring reference here: **[wiring_guide.md](wiring_guide.md)**

Key connections at a glance:

| Module | XIAO Pins |
|---|---|
| OLED, RTC, compass, encoder, INA219 (I2C bus) | SDA=D4 (GPIO5), SCL=D5 (GPIO6) |
| GPS module | TX→D6 (GPIO43), RX←D7 (GPIO44) |
| MG996R servo signal | D8 (GPIO7) |
| Button | D3 (GPIO4) |
| Power in (from bq25185 5V) | 5V pin |

Check the full wiring guide for the servo power supply (6V via Pololu S13V25F6 — the servo is **not** powered from the XIAO).

---

## 5. First Boot

Once everything is wired:

1. Flip the main power switch
2. The OLED will show a boot progress bar
3. After boot, the animated turtle idle screen appears

**Button controls:**

| Click | Action |
|---|---|
| Single click | Sensor carousel (compass → sailpoint → servo → battery) |
| Triple click | Connectivity carousel (WiFi → online → telemetry → device) |
| Hold 2 s | Time → battery → sleep screens |
| Double click | Machine state / luff test screen |

When WiFi is in range, the turtle connects automatically and posts telemetry to your hopeturtles.org dashboard.

---

## For Future Firmware Updates

After the initial install, use `xiao_synker.sh` to push firmware updates to the board:

```bash
cd ~/Documents/HopeTurtle/turtleOS

# Pull the latest code from GitHub
git pull

# Sync to the connected XIAO (choose hard reset or incremental sync)
./scripts/xiao_synker.sh

# Or force a full wipe and re-upload
./scripts/xiao_synker.sh --fresh
```

`xiao_synker.sh` preserves your `config.json` (it uses `scripts/xiao_config.json` as the base). Edit that file if you need to change settings without running the full installer again.

---

## Troubleshooting

**Board not found by mpremote:** Check the USB cable (some cables are charge-only). Try `mpremote connect /dev/ttyUSB0 repl` with the explicit port name.

**Upload fails partway through:** Run `./scripts/xiao_synker.sh --fresh` to do a clean wipe and re-upload.

**OLED blank after boot:** Check I2C wiring (SDA to GPIO5, SCL to GPIO6). Run the I2C scanner to confirm devices are detected:

```bash
mpremote connect auto run tests/i2c_scan.py
```

**Compass heading wrong:** Set `compass_offset_deg` in `config.json` — point the turtle North, read the raw heading on the Compass screen, and enter the negative of that value.

**Need help?** Open an issue on the project repository:

**https://github.com/h2h-project/turtleOS**
