#!/usr/bin/env bash
set -e

# ------------------------------------------------------------
# When run as `curl -sSL ... | bash`, stdin is the pipe carrying the
# script itself — not the keyboard.  Every interactive `read` then gets
# EOF immediately and any "please answer" retry loop spins forever.
# Reconnect stdin to the controlling terminal so prompts work.
# ------------------------------------------------------------
if [ ! -t 0 ] && [ -r /dev/tty ]; then
    exec < /dev/tty
fi

# ============================================================
# airOS (airBuddy) Installer
# ------------------------------------------------------------
# First-time setup for the simple airBuddy XIAO ESP32-S3 —
# the air-quality-only firmware build (turtle_mode=false).
# Walks you through MicroPython flashing (if needed),
# configures your device, and uploads airOS firmware.
#
# Usage (one-liner):
#   bash <(curl -fsSL https://raw.githubusercontent.com/h2h-project/turtleOS/main/scripts/install_airOS.sh)
#
# For subsequent firmware updates use:
#   ./scripts/sync_airOS.sh
# ============================================================

cat <<'BANNER'

            ███               ███████     █████████
           ░░░              ███░░░░░███  ███░░░░░███
  ██████   ████  ████████  ███     ░░███░███    ░░░
 ░░░░░███ ░░███ ░░███░░███░███      ░███░░█████████
  ███████  ░███  ░███ ░░░ ░███      ░███ ░░░░░░░░███
 ███░░███  ░███  ░███     ░░███     ███  ███    ░███
░░████████ █████ █████     ░░░███████░  ░░█████████
 ░░░░░░░░ ░░░░░ ░░░░░        ░░░░░░░     ░░░░░░░░░

    D E V I C E   I N S T A L L E R

 _____                  _____ _          _____ _
|  |  |___ ___ _ _ _   |_   _| |_ _ _   |  _  |_|___
|    -|   | . | | | |    | | |   | | |  |     | |  _|_
|__|__|_|_|___|_____|    |_| |_|_|_  |  |__|__|_|_| |_|
                                 |___|

BANNER

echo "Welcome!  You're about to install airOS onto your Xiao ESP32-S3"
echo "microcontroller.  Make sure that this is in fact your circuit."
echo "This build is the simple air-quality-only version of the firmware —"
echo "no sail servo, no compass, no navigation."
echo

# ------------------------------------------------------------
# MicroPython flash check
# ------------------------------------------------------------

NEEDS_FLASH=false

while true; do
    read -r -p "First things first, have you flashed your board yet with the MicroPython firmware? y/n: " FLASHED
    case "$(echo "$FLASHED" | tr '[:upper:]' '[:lower:]')" in
        y|yes) NEEDS_FLASH=false; break ;;
        n|no)  NEEDS_FLASH=true;  break ;;
        *)     echo "Please answer y or n." ;;
    esac
done

echo

# ------------------------------------------------------------
# Workspace location (needed early for the tools venv)
# ------------------------------------------------------------

WORKDIR="$HOME/Documents/AirBuddy"
VENV_DIR="$WORKDIR/.tools-venv"
mkdir -p "$WORKDIR"

# ------------------------------------------------------------
# Dependency checks + auto-install into a local venv
# ------------------------------------------------------------

if ! command -v git >/dev/null 2>&1; then
    echo "Git is not installed. Install it first:"
    echo "  Ubuntu / Debian:  sudo apt install git"
    echo "  Mac (Homebrew):   brew install git"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required to run the installer tools."
    echo "Install it from https://python.org or via your package manager, then try again."
    exit 1
fi

# Helper: create the venv and bootstrap pip, with visible progress
_ensure_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        echo "  [1/2] Creating Python environment at $VENV_DIR ..."
        python3 -m venv "$VENV_DIR"
        echo "  [2/2] Upgrading pip inside the environment..."
        "$VENV_DIR/bin/pip" install --upgrade pip
        echo "  Environment ready."
        echo
    fi
}

# Helper: install a package into the venv with visible pip output
_venv_install() {
    local pkg="$1"
    echo "  Installing $pkg (this may take a minute on a slow connection)..."
    "$VENV_DIR/bin/pip" install "$pkg"
}

# Locate or auto-install mpremote
if command -v mpremote >/dev/null 2>&1; then
    MPREMOTE_BIN="mpremote"
else
    echo "mpremote not found — setting up a local Python environment."
    echo "(Tools stay in $VENV_DIR and do not affect your system Python.)"
    echo
    _ensure_venv
    _venv_install mpremote
    MPREMOTE_BIN="$VENV_DIR/bin/mpremote"
    echo "  ✓  mpremote ready"
    echo
fi

# Locate or auto-install esptool (only needed when the board isn't yet flashed)
ESPTOOL_BIN=""
if [[ "$NEEDS_FLASH" == "true" ]]; then
    if command -v esptool.py >/dev/null 2>&1; then
        ESPTOOL_BIN="esptool.py"
    elif command -v esptool >/dev/null 2>&1; then
        ESPTOOL_BIN="esptool"
    elif [[ -x "$VENV_DIR/bin/esptool" ]]; then
        ESPTOOL_BIN="$VENV_DIR/bin/esptool"
    else
        echo "esptool not found — installing into the local Python environment."
        echo
        _ensure_venv
        _venv_install esptool
        ESPTOOL_BIN="$VENV_DIR/bin/esptool"
        echo "  ✓  esptool ready"
        echo
    fi
fi

# ------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------

msg() {
    echo
    echo "==> $1"
}

warn() {
    echo
    echo "WARNING: $1"
}

die() {
    echo
    echo "ERROR: $1"
    exit 1
}

prompt_default() {
    local prompt="$1" default="$2" reply
    read -r -p "$prompt [default: $default]: " reply
    if [[ -z "${reply}" ]]; then echo "$default"; else echo "$reply"; fi
}

prompt_required() {
    local prompt="$1" reply
    while true; do
        read -r -p "$prompt: " reply
        if [[ -n "${reply}" ]]; then echo "$reply"; return; fi
        echo "  (This field is required — please enter a value.)"
    done
}

prompt_yes_no() {
    local prompt="$1" default="$2" reply shown_default
    [[ "$default" == "y" ]] && shown_default="Y/n" || shown_default="y/N"
    while true; do
        read -r -p "$prompt [$shown_default]: " reply
        reply="${reply:-$default}"
        case "$(echo "$reply" | tr '[:upper:]' '[:lower:]')" in
            y|yes) echo "true";  return ;;
            n|no)  echo "false"; return ;;
            *)     echo "  (Please answer y or n.)" ;;
        esac
    done
}

escape_json_string() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

# ------------------------------------------------------------
# Clone / update the firmware repository
# (needed before flashing — the firmware .bin lives in resources/;
#  airOS is the turtle_mode=false build of the same firmware)
# ------------------------------------------------------------

msg "Setting up your airBuddy workspace"
echo "  $WORKDIR"

cd "$WORKDIR"

REPO_URL="https://github.com/h2h-project/turtleOS.git"

if [ ! -d "turtleOS" ]; then
    echo "Cloning the firmware repository..."
    git clone "$REPO_URL"
fi

cd turtleOS

echo "Syncing to the latest version from GitHub..."
git fetch origin
git reset --hard origin/main \
    || die "Could not sync repository. Check your internet connection and try again."
echo "  ✓  Repository up to date"

ROOT_DIR="$(pwd)"
DEVICE_DIR="$ROOT_DIR/device"
SCRIPTS_DIR="$ROOT_DIR/scripts"
RESOURCES_DIR="$ROOT_DIR/resources"
TMP_DIR="$ROOT_DIR/.tmp_airbuddy_sync"
TMP_CONFIG="$TMP_DIR/config.json"
STAGE_DIR="$TMP_DIR/stage"
PORT="auto"

[[ -d "$DEVICE_DIR" ]] || die "device/ folder not found at $DEVICE_DIR — the repo may be incomplete."
[[ -d "$RESOURCES_DIR" ]] || die "resources/ folder not found at $RESOURCES_DIR — the repo may be incomplete."

mkdir -p "$TMP_DIR"

MPREMOTE_CMD=("$MPREMOTE_BIN" connect "$PORT")

# ------------------------------------------------------------
# Flash MicroPython firmware (if the user hasn't done it yet)
# ------------------------------------------------------------

if [[ "$NEEDS_FLASH" == "true" ]]; then
    msg "Flashing MicroPython onto your XIAO ESP32-S3"

    BIN_FILE=$(ls "$RESOURCES_DIR"/ESP32_GENERIC_S3-SPIRAM_OCT-*.bin 2>/dev/null | head -1)
    [[ -n "$BIN_FILE" ]] \
        || die "MicroPython firmware (.bin) not found in $RESOURCES_DIR — the repo may be incomplete."

    echo "Firmware: $(basename "$BIN_FILE")"
    echo

    # Print bootloader instructions (called again on each retry)
    _bootloader_instructions() {
        echo "To put the XIAO ESP32-S3 into bootloader mode:"
        echo
        echo "  1. Unplug the USB cable if it is connected."
        echo "  2. Locate the small BOOT button on the back of the board (labelled 'B')."
        echo "  3. Press and hold the BOOT button."
        echo "  4. While holding BOOT, plug the USB cable back in."
        echo "  5. Release the BOOT button — the board is now in bootloader mode."
        echo
        echo "The board will appear as a new USB device but will NOT show a REPL prompt."
        echo "That is expected — it means it is ready to be flashed."
        echo
    }

    # Detect available serial ports and set FLASH_PORT
    # Uses wc -l (always exits 0) to avoid the grep-c double-zero bug.
    _detect_flash_port() {
        local ports count
        if [[ "$(uname)" == "Darwin" ]]; then
            ports=$(ls /dev/cu.usbmodem* /dev/cu.SLAB_USBtoUART* 2>/dev/null || true)
        else
            ports=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)
        fi

        count=0
        [[ -n "$ports" ]] && count=$(printf '%s\n' "$ports" | wc -l | tr -d '[:space:]')

        if [[ "$count" -eq 1 ]]; then
            FLASH_PORT="$ports"
            echo "Found board at: $FLASH_PORT"
        elif [[ "$count" -gt 1 ]]; then
            echo "Multiple USB ports detected:"
            local i=1
            while IFS= read -r p; do
                echo "  $i) $p"
                i=$((i + 1))
            done <<< "$ports"
            echo
            read -r -p "Enter the number of your board's port: " PORT_NUM
            FLASH_PORT=$(printf '%s\n' "$ports" | sed -n "${PORT_NUM}p")
        else
            echo "No USB serial port detected automatically."
            echo "Common locations:"
            echo "  macOS:  /dev/cu.usbmodem1234  or  /dev/cu.SLAB_USBtoUART"
            echo "  Linux:  /dev/ttyACM0  or  /dev/ttyUSB0"
            read -r -p "Enter the port path for your board: " FLASH_PORT
        fi
    }

    FLASH_DONE=false
    _bootloader_instructions

    while [[ "$FLASH_DONE" == "false" ]]; do
        read -r -p "Press Enter once the board is in bootloader mode..."
        echo

        FLASH_PORT=""
        _detect_flash_port

        if [[ -z "$FLASH_PORT" ]]; then
            echo "No port selected — skipping this attempt."
        else
            echo
            echo "Step 1/2  Erasing flash (≈10 s)..."
            if "$ESPTOOL_BIN" --chip esp32s3 --port "$FLASH_PORT" erase-flash; then
                echo
                echo "Step 2/2  Writing MicroPython firmware (≈30 s)..."
                if "$ESPTOOL_BIN" --chip esp32s3 --port "$FLASH_PORT" --baud 921600 \
                        write-flash -z 0 "$BIN_FILE"; then
                    FLASH_DONE=true
                else
                    warn "Write step failed."
                fi
            else
                warn "Erase step failed."
            fi
        fi

        if [[ "$FLASH_DONE" == "false" ]]; then
            echo
            echo "Common causes of failure:"
            echo "  - The board was not fully in bootloader mode when the command ran."
            echo "  - The port became unavailable (boards sometimes re-enumerate on connect)."
            echo "  - Wrong port selected — unplug and reconnect to confirm the path."
            echo
            read -r -p "Try again? y/n: " RETRY_FLASH
            case "$(echo "$RETRY_FLASH" | tr '[:upper:]' '[:lower:]')" in
                y|yes)
                    echo
                    _bootloader_instructions
                    ;;
                *)
                    die "Flash aborted. Run the installer again when you're ready."
                    ;;
            esac
        fi
    done

    echo
    echo "  ✓  MicroPython flashed successfully!"
    echo
    echo "Now unplug the USB cable and plug it back in."
    echo "The board will boot into MicroPython."
    echo

    read -r -p "Press Enter once you have reconnected the board..."
    echo
fi

# ------------------------------------------------------------
# Ready to install airOS?
# ------------------------------------------------------------

FIRMWARE_VERSION="$(grep -E 'VERSION_NUM[[:space:]]*=[[:space:]]*"' "$DEVICE_DIR/src/app/booter.py" 2>/dev/null \
    | head -1 | sed 's/.*"\([^"]*\)".*/\1/')"
[[ -n "$FIRMWARE_VERSION" ]] || FIRMWARE_VERSION="unknown"

echo "Your Xiao ESP32-S3 is now set up to run MicroPython!"
echo
while true; do
    read -r -p "Are you ready to install the latest version of airOS on your board (version $FIRMWARE_VERSION)? y/n: " READY
    case "$(echo "$READY" | tr '[:upper:]' '[:lower:]')" in
        y|yes) echo; break ;;
        n|no)  echo; echo "No problem — come back when you're ready!"; exit 0 ;;
        *)     echo "Please answer y or n." ;;
    esac
done

# ------------------------------------------------------------
# Connect to board and verify it is a XIAO ESP32-S3
# ------------------------------------------------------------

msg "Connecting to your XIAO ESP32-S3"
echo "Make sure the board is plugged in via USB..."
echo

"${MPREMOTE_CMD[@]}" exec "print('connected')" >/dev/null 2>&1 \
    || die "No MicroPython board found. Check the USB connection and make sure MicroPython is flashed, then try again."

PLATFORM="$("${MPREMOTE_CMD[@]}" exec "import sys; print(sys.platform)" 2>/dev/null | tail -n1 | tr -d '\r')"
[[ "$PLATFORM" == "esp32" ]] \
    || die "Expected an ESP32 board (sys.platform=esp32) but got: $PLATFORM. This installer is for the XIAO ESP32-S3 only."

MACHINE="$("${MPREMOTE_CMD[@]}" exec "import uos; print(uos.uname().machine)" 2>/dev/null | tail -n1 | tr -d '\r')"
echo "Board: $MACHINE — OK"

MACHINE_LC="$(echo "$MACHINE" | tr '[:upper:]' '[:lower:]')"
if [[ "$MACHINE_LC" != *"xiao"* && "$MACHINE_LC" != *"esp32s3"* ]]; then
    warn "Machine string doesn't mention XIAO or ESP32-S3 ($MACHINE). Proceeding — verify you have the right board."
fi

# ------------------------------------------------------------
# Configure your airBuddy
# ------------------------------------------------------------

msg "Let's configure your airBuddy"
echo "Don't stress — you can always re-run this installer or edit"
echo "config.json on the device later to change any of these settings."
echo

echo "--- Network ---"
WIFI_ENABLED="$(prompt_yes_no "Enable WiFi? (required for telemetry uploads)" "y")"

WIFI_SSID=""
WIFI_PASSWORD=""
TELEMETRY_ENABLED="false"
TELEMETRY_POST_EVERY_S=120
API_BASE="http://air2.earthen.io"

if [[ "$WIFI_ENABLED" == "true" ]]; then
    WIFI_SSID="$(prompt_required "WiFi network name (SSID)")"
    WIFI_PASSWORD="$(prompt_required "WiFi password")"
    TELEMETRY_ENABLED="$(prompt_yes_no "Enable telemetry uploads to your air2.earthen.io dashboard?" "y")"
    if [[ "$TELEMETRY_ENABLED" == "true" ]]; then
        echo "  tip: hit return to use the default value"
        TELEMETRY_POST_EVERY_S="$(prompt_default "How often to upload readings (seconds)" "120")"
    fi
fi

echo
echo "--- Device registration ---"
echo "Your device ID and key come from your air2.earthen.io account."
echo "If you haven't registered your device yet, visit:"
echo "  http://air2.earthen.io"
echo "and create an account, then register your device to receive these."
echo

DEVICE_ID="$(prompt_required "Device ID")"
DEVICE_KEY="$(prompt_required "Device key")"

echo
echo "--- Time zone ---"
echo "How many hours ahead of (or behind) UTC is your local time?"
echo "  Examples:  +8 = Hong Kong/Manila,  +7 = Jakarta,  +5.5 = India,  +3 = Istanbul,  0 = UTC/London"
echo "  Behind UTC: -5 = New York,  -6 = Chicago,  -8 = Los Angeles"
TIMEZONE_HOURS="$(prompt_default "Hours offset from UTC (leave blank to skip)" "")"

TIMEZONE_OFFSET_MIN=""
if [[ -n "$TIMEZONE_HOURS" ]]; then
    TIMEZONE_OFFSET_MIN="$(awk "BEGIN{printf \"%d\", $TIMEZONE_HOURS * 60}")"
fi

echo
echo "--- Sensors and hardware ---"
GPS_ENABLED="$(prompt_yes_no "Is a GPS module wired to the UART pins (D6/D7)?" "n")"

echo
echo "--- Display ---"
echo "Most OLED modules need no offset. If your display is slightly misaligned"
echo "horizontally (SH1106 variant), try an offset of 2."
OLED_COL_OFFSET="$(prompt_default "OLED column offset" "0")"

# airOS has no sail servo, compass, or navigation mission — this is the
# simple air-quality-only build, so none of that is asked here.
TURTLE_MODE="false"
JOKE_MODE="true"
SERVO_PRESENT="false"

# ------------------------------------------------------------
# Generate config.json
# ------------------------------------------------------------

msg "Generating config.json"

TZ_JSON="null"
if [[ -n "$TIMEZONE_OFFSET_MIN" ]]; then
    TZ_JSON="$TIMEZONE_OFFSET_MIN"
fi

cat > "$TMP_CONFIG" <<EOF
{
  "board_type": "xiao_esp32s3",
  "turtle_mode": $TURTLE_MODE,
  "joke_mode": $JOKE_MODE,
  "gps_enabled": $GPS_ENABLED,
  "servo_present": $SERVO_PRESENT,
  "oled_col_offset": $OLED_COL_OFFSET,
  "wifi_enabled": $WIFI_ENABLED,
  "wifi_ssid": "$(escape_json_string "$WIFI_SSID")",
  "wifi_password": "$(escape_json_string "$WIFI_PASSWORD")",
  "telemetry_enabled": $TELEMETRY_ENABLED,
  "telemetry_post_every_s": $TELEMETRY_POST_EVERY_S,
  "api_base": "$(escape_json_string "$API_BASE")",
  "device_id": "$(escape_json_string "$DEVICE_ID")",
  "device_key": "$(escape_json_string "$DEVICE_KEY")",
  "timezone_offset_min": $TZ_JSON
}
EOF

echo
echo "--------- config preview ---------"
cat "$TMP_CONFIG"
echo "----------------------------------"
echo

CONFIRM="$(prompt_yes_no "Does this look right?" "y")"
if [[ "$CONFIRM" == "false" ]]; then
    echo
    echo "No problem — run the installer again to re-enter your settings."
    exit 0
fi

# ------------------------------------------------------------
# Wipe board and stage firmware
# ------------------------------------------------------------

msg "Wiping old files from the board"

"${MPREMOTE_CMD[@]}" exec "
import os

def is_dir(p):
    try: return bool(os.stat(p)[0] & 0x4000)
    except: return False

def rm_tree(p):
    try:
        if is_dir(p):
            for n in os.listdir(p): rm_tree(p + '/' + n)
            os.rmdir(p)
        else:
            os.remove(p)
    except Exception as e:
        print('warn:', p, repr(e))

for name in os.listdir('/'):
    rm_tree('/' + name)

print('Board cleared.')
" || warn "Some files could not be removed — continuing anyway."

msg "Staging airOS firmware"

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

rsync -a \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude '.gitignore' \
    `# ── Device-owned runtime data (never overwrite the board's own records) ─` \
    --exclude 'telemetry_queue.json' \
    --exclude 'telemetry_last_sent.json' \
    --exclude 'src/hal/board_pico.py' \
    --exclude 'src/ui/flows_pico.py' \
    --exclude 'src/ui/glyphs_pico.py' \
    --exclude 'src/app/main_pico.py' \
    "$DEVICE_DIR/" "$STAGE_DIR/"

# ------------------------------------------------------------
# Upload firmware and config
# ------------------------------------------------------------

msg "Uploading airOS firmware"

"${MPREMOTE_CMD[@]}" fs cp -r "$STAGE_DIR/." : \
    || die "Upload failed. Try reconnecting the board and running again."

echo "Firmware uploaded."

msg "Uploading config.json"

"${MPREMOTE_CMD[@]}" fs cp "$TMP_CONFIG" :config.json \
    || die "Failed to upload config.json."

echo "config.json uploaded."

# ------------------------------------------------------------
# Set the RTC clock
# ------------------------------------------------------------

msg "Set the onboard RTC clock"
echo "The DS3231 RTC keeps time even when the device is off."
echo "It always stores UTC — your timezone offset is applied at display time."
echo
echo "  1) Use current system time (UTC)"
echo "  2) Enter a custom UTC time"
echo "  3) Skip — set it manually later via REPL"
echo

while true; do
    read -r -p "Enter 1, 2, or 3: " rtc_reply
    case "$rtc_reply" in
        1|2|3) RTC_CHOICE="$rtc_reply"; break ;;
        *) echo "  (Please enter 1, 2, or 3.)" ;;
    esac
done

RTC_YEAR="" RTC_MONTH="" RTC_DAY="" RTC_WEEKDAY="" RTC_HOUR="" RTC_MIN="" RTC_SEC=""

if [[ "$RTC_CHOICE" == "1" ]]; then
    read -r RTC_YEAR RTC_MONTH RTC_DAY RTC_WEEKDAY RTC_HOUR RTC_MIN RTC_SEC \
        < <(date -u "+%Y %m %d %u %H %M %S")

elif [[ "$RTC_CHOICE" == "2" ]]; then
    echo
    echo "Enter UTC date and time:"
    read -r -p "  Year        [e.g. 2026]: " RTC_YEAR
    read -r -p "  Month       [1-12]:      " RTC_MONTH
    read -r -p "  Day         [1-31]:      " RTC_DAY
    read -r -p "  Hour (UTC)  [0-23]:      " RTC_HOUR
    read -r -p "  Minute      [0-59]:      " RTC_MIN
    read -r -p "  Second      [0-59]:      " RTC_SEC
    RTC_WEEKDAY=$(python3 -c \
        "import datetime; print(datetime.date($((10#${RTC_YEAR:-2026})),$((10#${RTC_MONTH:-1})),$((10#${RTC_DAY:-1}))).isoweekday())" \
        2>/dev/null) || true
    [[ -z "$RTC_WEEKDAY" ]] && RTC_WEEKDAY=1
fi

if [[ "$RTC_CHOICE" == "1" || "$RTC_CHOICE" == "2" ]]; then
    RTC_YEAR=$((10#${RTC_YEAR}));   RTC_MONTH=$((10#${RTC_MONTH}))
    RTC_DAY=$((10#${RTC_DAY}));     RTC_WEEKDAY=$((10#${RTC_WEEKDAY}))
    RTC_HOUR=$((10#${RTC_HOUR}));   RTC_MIN=$((10#${RTC_MIN}))
    RTC_SEC=$((10#${RTC_SEC}))

    msg "Setting RTC — $(printf '%04d-%02d-%02d %02d:%02d:%02d' \
        $RTC_YEAR $RTC_MONTH $RTC_DAY $RTC_HOUR $RTC_MIN $RTC_SEC) UTC"

    "${MPREMOTE_CMD[@]}" exec "
import sys
sys.path.insert(0, '/src/lib')
sys.path.insert(0, '/src')
from machine import Pin, I2C
i2c = I2C(0, scl=Pin(6), sda=Pin(5), freq=400000)
try:
    from src.drivers.ds3231 import DS3231
    ds = DS3231(i2c)
    ds.datetime(($RTC_YEAR, $RTC_MONTH, $RTC_DAY, $RTC_WEEKDAY, $RTC_HOUR, $RTC_MIN, $RTC_SEC))
    ds.clear_lost_power()
    yr, mo, dy, wd, hh, mm, ss = ds.datetime()
    print('RTC confirmed: {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC (wd={})'.format(yr,mo,dy,hh,mm,ss,wd))
except Exception as e:
    print('RTC ERROR:', repr(e))
" || warn "RTC setup had errors — you can set the clock manually via REPL later."

else
    echo "RTC skipped."
fi

# ------------------------------------------------------------
# Flash usage
# ------------------------------------------------------------

msg "Flash usage"
"${MPREMOTE_CMD[@]}" exec "
import uos
s = uos.statvfs('/')
total_kb = s[0] * s[2] // 1024
free_kb  = s[0] * s[3] // 1024
used_kb  = total_kb - free_kb
print('Total: {} KB  Used: {} KB  Free: {} KB'.format(total_kb, used_kb, free_kb))
" || true

# ------------------------------------------------------------
# Done — open REPL to watch first boot
# ------------------------------------------------------------

echo
echo "  ✓  airOS is installed!"
echo

cat <<'WAVES'

,--. ,--.                              ,--------.,--.                    ,---.  ,--.
|  .'   /,--,--,  ,---. ,--.   ,--.    '--.  .--'|  ,---. ,--. ,--.     /  O  \ `--',--.--.
|  .   ' |      \| .-. ||  |.'.|  |       |  |   |  .-.  | \  '  /     |  .-.  |,--.|  .--'
|  |\   \|  ||  |' '-' '|   .'.   |       |  |   |  | |  |  \   '      |  | |  ||  ||  |.--.
`--' '--'`--''--' `---' '--'   '--'       `--'   `--' `--'.-'  /       `--' `--'`--'`--''--'
                                                          `---'

WAVES

echo "Your airBuddy is ready.  Wire up your sensors and start monitoring!"
echo
echo "Watch the first boot live (press Ctrl+D inside the REPL to reboot, Ctrl+] to exit):"
echo
"$MPREMOTE_BIN" connect "$PORT" repl
echo
echo "For future firmware updates, run:"
echo "  ./scripts/sync_airOS.sh"
echo
echo "Full wiring guide: wiring_guide.md"
echo "Hardware list:     hardware_stack.md"
echo
