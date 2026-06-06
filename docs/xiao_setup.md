Ready to install airOS on your Xiao ESP32-S3 and get rocking with your airBuddy?   We've worked hard to make this easy.  This guide
will walk you step-by-step through the setup process. If you follow the
steps below carefully, you should have your AirBuddy up and
running in a jiffy.

------------------------------------------------------------------------

# Overview of the Setup Process

The AirBuddy setup process has five main steps:

1.  Create your Buwana AirBuddy account 
2. Register your device and get is device_id and generate a device_key\
2.  Install MicroPython firmware on your board\
3.  Download the AirBuddy code\
4.  Run the installation script and configure your Xiao device\
5.  Wire your sensors and boot the system

Once complete, your AirBuddy will begin sending telemetry to your
very own home dashboard!  Cool?  Let's get started....

------------------------------------------------------------------------

# 1. Create Your AirBuddy Account

Before setting up your device, you will need to create an **
AirBuddy account**.  We proudly use the awesome Buwana regenerative account system to avoid all the big corporate platforms (which happen to be the some of the world's biggest emitters!).  If you already have a Buwana account (for other awesome regenerative apps like GoBrik and Earthcal) then simmply use your existing credentials to log in and connect to AirBuddy.

Your AirBuddy account allows you to:

-   Register your home
-   Register your room(s)
-   Register your AirBuddy device
-   Receive the **device key** required for configuration
-   Connect to a Buwana Community
-   Connect to your local watershed region (yes to ecological borders and no to political ones!).

Please sign up here:

https://buwana.ecobricks.org/en/signup-1.php?app=airb_ca090536efc8

After signing up:

1.  Open up your Dashboard and look for the form at the bottom to register your device
2.  Create your home\
3.  Add a room\
4.  Register your AirBuddy device

Once the device is registered you will receive:

-   Your **device ID**
-   Your **device key**

These will be required when running the installation script later in
this guide.

------------------------------------------------------------------------

# 2. Install MicroPython Firmware

AirBuddy runs on **MicroPython**, a lightweight version of Python
designed for microcontrollers.  Before installing the AirBuddy software, you must first install the
correct firmware for your xiao board.

We currently support:

-   ESP32 S3 
-   ESP32 microcontrollers
-   Raspberry Pi Pico / RP2040 boards

Download the firmware for your board from the official MicroPython
website:

## ESP32-S3 Xiao Firmware
https://micropython.org/download/ESP32_GENERIC_S3/

Be sure you download the latest Octal-SPIRAM  in .bin format.

Then, once downloaded install the tools you'll need for flash it to your board:

`sudo apt update
sudo apt install python3-pip python3-venv
pip install --user esptool mpremote`

Plug the Xiao in and check the port:

`ls /dev/ttyUSB*`

Typical result:

/dev/ttyUSB0

Erase what was there before:

`esptool.py --chip esp32 --port /dev/ttyUSB0 erase_flash`

Flash the firmware you've downloaded:

esptool --port /dev/ttyUSB0 --baud 460800 write-flash -z 0x1000 ~/Downloads/ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin
`

Test that you can now access the Xiao by mpremote:

`
mpremote connect /dev/ttyUSB0 repl
`

Done!



------------------------------------------------------------------------


# 3. Install the AirBuddy Software

AirBuddy includes an installation script that copies the necessary
software to your device and helps configure it with a single command:

   `bash <(curl -fsSL https://raw.githubusercontent.com/russs95/airbuddy_v2/main/scripts/bootstrap_airbuddy.sh)`

The installer will guide you through the setup.

Before running it, it good to have your configuration options ready to go.  You'll need:

-   Your **WiFi network name**
-   Your **WiFi password**
-   Your **AirBuddy device ID**
-   Your **AirBuddy device key**
-   Whether you want **GPS enabled**
-   Whether you want **telemetry uploads enabled**
-   How often the device should upload data (in seconds)
-   Your **timezone offset from UTC (in minutes)**
-   The **AirBuddy server address** (default will be provided)

These values will be used to automatically generate your device
configuration.

# Regular Git Installation


If the installer fails, you can do installation the (slightly) more complex manual way.

Run:

   `git clone https://github.com/russs95/airbuddy_v2`
    `cd airbuddy_v2`

------------------------------------------------------------------------

# 5. Wire Your Sensors

AirBuddy requires several sensors and components connected to your
board.

Follow the wiring guide for your board:

## XIAO ESP32 S# Wiring Guide

https://github.com/russs95/airbuddy_v2/wiki/Wiring-GUide-XIAO-ESP32%E2%80%90S3



------------------------------------------------------------------------

# 6. Boot Your AirBuddy

Once everything is installed and wired:

1.  Plug in your AirBuddy device
2.  The system will boot automatically
3.  The OLED display will activate

AirBuddy uses a **single-button click interface**.

You can navigate between screens by clicking the button.

Learn how the interface works here:

https://github.com/russs95/airbuddy_v2/wiki/AirBuddy-%E2%80%94-Interface-Click-Logic

------------------------------------------------------------------------

# Check Your AirBuddy Dashboard

Once your device connects to WiFi and begins transmitting telemetry, you
can view the data on your dashboard.

Visit:

https://air2.earthen.io/

Log in using your **Buwana account** and confirm that your sensor data
is arriving.

------------------------------------------------------------------------

# Troubleshooting

If something doesn't work the first time, don't worry. Most setup issues
are easy to resolve.

Start by checking:

-   The **AirBuddy Wiki**
-   The wiring guides
-   Your configuration answers

If you still need help, you can ask questions on the project repository:

https://github.com/russs95/airbuddy_v2

More troubleshooting guidance will be added here in future updates.

------------------------------------------------------------------------

# Welcome to the AirBuddy Network

By running an AirBuddy device you are helping build a
**community-powered air monitoring network**.

When many homes in a neighborhood measure their air together,
communities can better understand pollution sources and advocate for
cleaner environments.

Thank you for being part of the solution.