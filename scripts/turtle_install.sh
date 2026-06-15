#!/usr/bin/env bash
set -e

# ============================================================
# turtleOS Installer
# ------------------------------------------------------------
# First-time setup for the hope turtle XIAO ESP32-S3.
# Clones the turtleOS repo, walks you through configuration,
# and uploads the firmware to your connected board.
#
# Usage (one-liner):
#   bash <(curl -fsSL https://raw.githubusercontent.com/h2h-project/turtleOS/main/scripts/turtle_install.sh)
#
# For subsequent firmware updates use:
#   ./scripts/xiao_synker.sh
# ============================================================

cat <<'BANNER'

      ~   ~  ~    ~  ~  ~    ~  ~   ~   ~  ~
    ~  .-~~~-.  .-~~~-.  .-~~~-.  .-~~~-.  ~
   ~  (       )(       )(       )(       )  ~
    ~  '-~~~-'  '-~~~-'  '-~~~-'  '-~~~-'  ~
      ~   ~  ~    ~  ~  ~    ~  ~   ~   ~  ~

   _                       _____           _   _
  | |__   ___  _ __   ___ |_   _|_  _ _ _| |_| | ___
  | '_ \ / _ \| '_ \ / -_)  | || || | '__| __| |/ -_)
  | | | | (_) | |_) |  __/  | ||_,_|_|  \__|_|\___|
  |_| |_|\___/| .__/ \___|  |_|
              |_|               I N S T A L L E R

                  ~  set sail for hope  ~

BANNER

echo "Welcome! You're about to install turtleOS on your XIAO ESP32-S3."
echo
echo "This script will:"
echo "  1. check that Git and mpremote are installed"
echo "  2. download the turtleOS code to ~/Documents/HopeTurtle"
echo "  3. ask a few questions to configure your turtle"
echo "  4. upload the firmware to your connected XIAO ESP32-S3"
echo "  5. set the onboard clock and open the live boot log"
echo
echo "Before you start, make sure you have:"
echo "  - A XIAO ESP32-S3 with MicroPython already flashed (see first_time_setup.md)"
echo "  - Your WiFi network name and password"
echo "  - Your hope turtle device ID and key from hopeturtles.org"
echo

while true; do
    read -r -p "Ready to set sail? y/n: " READY
    case "${READY,,}" in
        y|yes) echo; break ;;
        n|no)  echo; echo "No problem — come back when you're ready. Fair winds!"; exit 0 ;;
        *)     echo "Please answer y or n." ;;
    esac
done

# ------------------------------------------------------------
# Dependency checks
# ------------------------------------------------------------

if ! command -v git >/dev/null 2>&1; then
    echo "Git doesn't seem to be installed on this machine."
    echo "Install it first:"
    echo "  Ubuntu / Debian:  sudo apt install git"
    echo "  Mac (Homebrew):   brew install git"
    exit 1
fi

if ! command -v mpremote >/dev/null 2>&1; then
    echo "mpremote is not installed. It's needed to upload firmware to the XIAO."
    echo "Install it with:"
    echo "  pip install mpremote"
    echo
    echo "Then plug in your XIAO and run this script again."
    exit 1
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
    read -r -p "$prompt [$default]: " reply
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
        case "${reply,,}" in
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
# Clone / update the turtleOS repository
# ------------------------------------------------------------

WORKDIR="$HOME/Documents/HopeTurtle"

msg "Setting up your turtleOS workspace"
echo "  $WORKDIR"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

REPO_URL="https://github.com/h2h-project/turtleOS.git"

if [ ! -d "turtleOS" ]; then
    echo "Pulling down the turtleOS repository..."
    git clone "$REPO_URL"
else
    echo "turtleOS repo already present — skipping download."
fi

cd turtleOS

ROOT_DIR="$(pwd)"
DEVICE_DIR="$ROOT_DIR/device"
SCRIPTS_DIR="$ROOT_DIR/scripts"
TMP_DIR="$ROOT_DIR/.tmp_xiao_sync"
TMP_CONFIG="$TMP_DIR/config.json"
STAGE_DIR="$TMP_DIR/stage"
PORT="auto"

[[ -d "$DEVICE_DIR" ]] || die "device/ folder not found at $DEVICE_DIR — the repo may be incomplete."

mkdir -p "$TMP_DIR"

MPREMOTE_CMD=(mpremote connect "$PORT")

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

MACHINE_LC="${MACHINE,,}"
if [[ "$MACHINE_LC" != *"xiao"* && "$MACHINE_LC" != *"esp32s3"* ]]; then
    warn "Machine string doesn't mention XIAO or ESP32-S3 ($MACHINE). Proceeding — verify you have the right board."
fi

# ------------------------------------------------------------
# Configure your turtle
# ------------------------------------------------------------

msg "Let's configure your turtle"
echo "Don't stress — you can always re-run this installer or edit"
echo "config.json on the device later to change any of these settings."
echo

echo "--- Network ---"
WIFI_ENABLED="$(prompt_yes_no "Enable WiFi? (required for telemetry uploads)" "y")"

WIFI_SSID=""
WIFI_PASSWORD=""
TELEMETRY_ENABLED="false"
TELEMETRY_POST_EVERY_S=120
API_BASE="http://hopeturtles.org"

if [[ "$WIFI_ENABLED" == "true" ]]; then
    WIFI_SSID="$(prompt_required "WiFi network name (SSID)")"
    WIFI_PASSWORD="$(prompt_required "WiFi password")"
    TELEMETRY_ENABLED="$(prompt_yes_no "Enable telemetry uploads to your hopeturtles.org dashboard?" "y")"
    if [[ "$TELEMETRY_ENABLED" == "true" ]]; then
        TELEMETRY_POST_EVERY_S="$(prompt_default "How often to upload readings (seconds)" "120")"
    fi
fi

echo
echo "--- Device registration ---"
echo "Your device ID and key come from your hopeturtles.org account."
echo "If you haven't registered your turtle yet, visit:"
echo "  https://hopeturtles.org"
echo "and create an account, then register your device to receive these."
echo

DEVICE_ID="$(prompt_required "Device ID")"
DEVICE_KEY="$(prompt_required "Device key")"

echo
echo "--- Time zone ---"
echo "How many hours ahead of (or behind) UTC is your local time?"
echo "  Examples:  +8 = Hong Kong/Manila,  +7 = Jakarta,  +5.5 = India,  0 = UTC/London"
echo "  Behind UTC: -5 = New York,  -6 = Chicago,  -8 = Los Angeles"
TIMEZONE_HOURS="$(prompt_default "Hours offset from UTC (leave blank to skip)" "")"

TIMEZONE_OFFSET_MIN=""
if [[ -n "$TIMEZONE_HOURS" ]]; then
    TIMEZONE_OFFSET_MIN="$(awk "BEGIN{printf \"%d\", $TIMEZONE_HOURS * 60}")"
fi

echo
echo "--- Sensors and hardware ---"
GPS_ENABLED="$(prompt_yes_no "Is a GPS module wired to the UART pins (D6/D7)?" "n")"
SERVO_PRESENT="$(prompt_yes_no "Is the MG996R sail servo wired to pin D8 / GPIO7?" "y")"

echo
echo "--- Compass calibration ---"
echo "Point the turtle North, note the raw heading shown on the Compass screen,"
echo "then enter the negative of that number here to zero the offset."
echo "Example: if the raw reading is 80 when pointing North, enter -80."
echo "Leave blank to use 0 and calibrate later."
COMPASS_INPUT="$(prompt_default "Compass offset (degrees)" "0")"
COMPASS_OFFSET_DEG="${COMPASS_INPUT:-0}"

echo
echo "--- Display ---"
echo "Most OLED modules need no offset. If your display is slightly misaligned"
echo "horizontally (SH1106 variant), try an offset of 2."
OLED_COL_OFFSET="$(prompt_default "OLED column offset" "0")"

# turtle_mode is always true for this installer
TURTLE_MODE="true"
JOKE_MODE="false"

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
  "turtle_mode": $TURTLE_MODE,
  "gps_enabled": $GPS_ENABLED,
  "wifi_enabled": $WIFI_ENABLED,
  "wifi_ssid": "$(escape_json_string "$WIFI_SSID")",
  "wifi_password": "$(escape_json_string "$WIFI_PASSWORD")",
  "telemetry_enabled": $TELEMETRY_ENABLED,
  "telemetry_post_every_s": $TELEMETRY_POST_EVERY_S,
  "api_base": "$(escape_json_string "$API_BASE")",
  "device_id": "$(escape_json_string "$DEVICE_ID")",
  "device_key": "$(escape_json_string "$DEVICE_KEY")",
  "timezone_offset_min": $TZ_JSON,
  "oled_col_offset": $OLED_COL_OFFSET,
  "servo_present": $SERVO_PRESENT,
  "joke_mode": $JOKE_MODE,
  "compass_offset_deg": $COMPASS_OFFSET_DEG
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

msg "Staging turtleOS firmware"

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

rsync -a \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude '.gitignore' \
    --exclude 'src/hal/board_pico.py' \
    --exclude 'src/ui/flows_pico.py' \
    --exclude 'src/ui/glyphs_pico.py' \
    --exclude 'src/app/main_pico.py' \
    "$DEVICE_DIR/" "$STAGE_DIR/"

# ------------------------------------------------------------
# Upload firmware and config
# ------------------------------------------------------------

msg "Uploading turtleOS firmware"

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
echo "The DS3231 RTC keeps time even when the turtle is off."
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
echo "  ✓  turtleOS is installed!"
echo

cat <<'WAVES'

      ~   ~  ~    ~  ~  ~    ~  ~   ~   ~  ~
    ~  .-~~~-.  .-~~~-.  .-~~~-.  .-~~~-.  ~
   ~  (       )(       )(       )(       )  ~
    ~  '-~~~-'  '-~~~-'  '-~~~-'  '-~~~-'  ~
      ~   ~  ~    ~  ~  ~    ~  ~   ~   ~  ~

WAVES

echo "Your hope turtle is ready.  Wire up your hardware and it's time to sail!"
echo
echo "Watch the first boot live (press Ctrl+D inside the REPL to reboot, Ctrl+] to exit):"
echo
mpremote connect "$PORT" repl
echo
echo "For future firmware updates, run:"
echo "  ./scripts/xiao_synker.sh"
echo
echo "Full wiring guide: wiring_guide.md"
echo "Hardware list:     hardware_stack.md"
echo "Project home:      https://hopeturtles.org"
echo
