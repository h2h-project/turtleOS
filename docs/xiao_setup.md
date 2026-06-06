Ready to install airOS on your XIAO ESP32-S3 and get rocking with your AirBuddy? We've worked
hard to make this easy. This guide will walk you step-by-step through the setup process.
Follow the steps below carefully and you should have your AirBuddy up and running in a jiffy.

------------------------------------------------------------------------

# Overview of the Setup Process

The AirBuddy setup process has five main steps:

1. Create your Buwana AirBuddy account and register your device
2. Install MicroPython firmware on your board
3. Download the AirBuddy software and run the installation script
4. Wire your sensors
5. Boot your AirBuddy and check your dashboard

Once complete, your AirBuddy will begin sending telemetry to your very own home dashboard!
Cool? Let's get started...

------------------------------------------------------------------------

# 1. Create Your AirBuddy Account

Before setting up your device, you will need to create an **AirBuddy account**. We proudly
use the awesome Buwana regenerative account system to avoid all the big corporate platforms
(which happen to be some of the world's biggest emitters!). If you already have a Buwana
account (for other awesome regenerative apps like GoBrik and Earthcal) then simply use your
existing credentials to log in and connect to AirBuddy.

Your AirBuddy account allows you to:

-   Register your home
-   Register your room(s)
-   Register your AirBuddy device
-   Receive the **device key** required for configuration
-   Connect to a Buwana Community
-   Connect to your local watershed region (yes to ecological borders and no to political ones!)

Please sign up here:

https://buwana.ecobricks.org/en/signup-1.php?app=airb_ca090536efc8

After signing up:

1.  Open up your Dashboard and look for the form at the bottom to register your device
2.  Create your home
3.  Add a room
4.  Register your AirBuddy device

Once the device is registered you will receive:

-   Your **device ID**
-   Your **device key**

Keep these handy — you will need them when running the installation script in step 3.

------------------------------------------------------------------------

# 2. Install MicroPython Firmware

AirBuddy runs on **MicroPython**, a lightweight version of Python designed for
microcontrollers. Before installing the AirBuddy software, you must first flash the correct
MicroPython firmware onto your XIAO board.

## Download the Firmware

Download the latest ESP32-S3 MicroPython firmware from the official site:

https://micropython.org/download/ESP32_GENERIC_S3/

Be sure to download the **Octal-SPIRAM** variant in `.bin` format.

## Install Flashing Tools

```
sudo apt update
sudo apt install python3-pip python3-venv
pip install --user esptool mpremote
```

## Put the XIAO into Bootloader Mode

Before you can flash the board, you must put the XIAO into **download mode**. The board will
not accept firmware commands unless this step is done first.

1.  Hold the **BOOT** button — the small button on the underside of the board, near the USB-C port
2.  While holding BOOT, briefly press and release the **RST** (reset) button
3.  Release the BOOT button

The board is now in bootloader mode and ready to be flashed.

## Find the Port

With the board in bootloader mode, check which port it appears on...

```
ls /dev/ttyACM*
```

Typical result:

```
/dev/ttyACM0
```

## Erase the Board

```
esptool --chip esp32s3 --port /dev/ttyACM0 erase_flash
```

## Flash the Firmware

Replace the filename below with the exact name of the `.bin` file you downloaded:

```
esptool --chip esp32s3 --port /dev/ttyACM0 --baud 460800 write_flash -z 0x0 ~/Downloads/ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin
```

## Verify the Connection

After flashing, the board will reboot into MicroPython. Test that you can connect:

```
mpremote connect /dev/ttyACM0 repl
```

You should see the MicroPython `>>>` prompt. Press `Ctrl+]` to exit.

Done!

------------------------------------------------------------------------

# 3. Install the AirBuddy Software

AirBuddy includes an installation script that copies the necessary software to your device
and configures it — all in a single command:

```
bash <(curl -fsSL https://raw.githubusercontent.com/russs95/airbuddy_v2/main/scripts/bootstrap_airbuddy.sh)
```

The installer will guide you through the setup. Before running it, have the following ready:

-   Your **WiFi network name**
-   Your **WiFi password**
-   Your **AirBuddy device ID**
-   Your **AirBuddy device key**
-   Whether you want **GPS enabled**
-   Whether you want **telemetry uploads enabled**
-   How often the device should upload data (in seconds)
-   Your **timezone offset from UTC** (in minutes)
-   The **AirBuddy server address** (a default will be provided)

The installer will also ask a few XIAO-specific questions about optional hardware such as a
servo, compass, and LED — just answer n to anything you haven't wired yet.

## Manual Installation (Fallback)

If the bootstrap command fails, you can clone the repository and run the installer directly:

```
git clone https://github.com/russs95/airbuddy_v2
cd airbuddy_v2
./scripts/install_airbuddy.sh
```

------------------------------------------------------------------------

# 4. Wire Your Sensors

AirBuddy requires several sensors and components connected to your board.

Follow the wiring guide for the XIAO ESP32-S3:

https://github.com/russs95/airbuddy_v2/wiki/Wiring-GUide-XIAO-ESP32%E2%80%90S3

------------------------------------------------------------------------

# 5. Boot Your AirBuddy

Once everything is installed and wired:

1.  Plug in your AirBuddy device
2.  The system will boot automatically
3.  The OLED display will activate

AirBuddy uses a **single-button click interface**. You can navigate between screens by
clicking the button.

Learn how the interface works here:

https://github.com/russs95/airbuddy_v2/wiki/AirBuddy-%E2%80%94-Interface-Click-Logic

------------------------------------------------------------------------

# Check Your AirBuddy Dashboard

Once your device connects to WiFi and begins transmitting telemetry, you can view the data
on your dashboard.

Visit:

https://air2.earthen.io/

Log in using your **Buwana account** and confirm that your sensor data is arriving.

------------------------------------------------------------------------

# Troubleshooting

If something doesn't work the first time, don't worry. Most setup issues are easy to resolve.

Start by checking:

-   The **AirBuddy Wiki**
-   The wiring guides
-   Your configuration answers

If you still need help, you can ask questions on the project repository:

https://github.com/russs95/airbuddy_v2

More troubleshooting guidance will be added here in future updates.

------------------------------------------------------------------------

# Welcome to the AirBuddy Network

By running an AirBuddy device you are helping build a **community-powered air monitoring
network**.

When many homes in a neighborhood measure their air together, communities can better
understand pollution sources and advocate for cleaner environments.

Thank you for being part of the solution.
