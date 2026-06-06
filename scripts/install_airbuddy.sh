#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# AirBuddy Device Installer
# ------------------------------------------------------------
# Uploads AirBuddy MicroPython firmware from /device onto a
# connected Pico (RP2040), ESP32, ESP32-S3, or XIAO ESP32-S3
# board using mpremote.
#
# Usage:
#   ./scripts/install_airbuddy.sh
#   ./scripts/install_airbuddy.sh --fresh
#   ./scripts/install_airbuddy.sh --port /dev/ttyUSB0
#   ./scripts/install_airbuddy.sh --board esp32
#   ./scripts/install_airbuddy.sh --board esp32s3
#   ./scripts/install_airbuddy.sh --board xiao_esp32s3
#   ./scripts/install_airbuddy.sh --board pico
#   ./scripts/install_airbuddy.sh --overwrite-config
#
# Notes:
# - Flash MicroPython onto your board before running this.
# - Generates config.json interactively and uploads it.
# - Board type is auto-detected from sys.platform + uname.machine.
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE_DIR="$ROOT_DIR/device"
TMP_DIR="$ROOT_DIR/.tmp_airbuddy_install"
TMP_CONFIG="$TMP_DIR/config.json"

PORT="auto"
FRESH=0
OVERWRITE_CONFIG=0
BOARD_OVERRIDE=""

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

print_help() {
  cat <<EOF
AirBuddy Device Installer

Usage:
  ./scripts/install_airbuddy.sh [options]

Options:
  --fresh              Wipe old files from board before uploading
  --overwrite-config   Replace existing config.json on the board
  --port PORT          Serial port to use (default: auto)
  --board TYPE         Force board type: pico | esp32 | esp32s3 | xiao_esp32s3
  --help               Show this help

Examples:
  ./scripts/install_airbuddy.sh
  ./scripts/install_airbuddy.sh --fresh
  ./scripts/install_airbuddy.sh --port /dev/ttyUSB0
  ./scripts/install_airbuddy.sh --board xiao_esp32s3
  ./scripts/install_airbuddy.sh --fresh --overwrite-config
EOF
}

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

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

prompt_default() {
  local prompt="$1"
  local default="$2"
  local reply
  read -r -p "$prompt [$default]: " reply
  if [[ -z "${reply}" ]]; then
    echo "$default"
  else
    echo "$reply"
  fi
}

prompt_required() {
  local prompt="$1"
  local reply
  while true; do
    read -r -p "$prompt: " reply
    if [[ -n "${reply}" ]]; then
      echo "$reply"
      return
    fi
    echo "  (This one can't be blank — please give it a value.)"
  done
}

prompt_yes_no() {
  local prompt="$1"
  local default="$2"   # y or n
  local reply
  local shown_default

  if [[ "$default" == "y" ]]; then
    shown_default="Y/n"
  else
    shown_default="y/N"
  fi

  while true; do
    read -r -p "$prompt [$shown_default]: " reply
    reply="${reply:-$default}"
    case "${reply,,}" in
      y|yes) echo "true"; return ;;
      n|no)  echo "false"; return ;;
      *) echo "  (Please answer y or n.)" ;;
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
# Parse args
# ------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh)
      FRESH=1
      shift
      ;;
    --overwrite-config)
      OVERWRITE_CONFIG=1
      shift
      ;;
    --port)
      [[ $# -ge 2 ]] || die "--port requires a value"
      PORT="$2"
      shift 2
      ;;
    --board)
      [[ $# -ge 2 ]] || die "--board requires a value"
      BOARD_OVERRIDE="$2"
      shift 2
      ;;
    --help)
      print_help
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

# ------------------------------------------------------------
# Intro
# ------------------------------------------------------------

echo
echo "==> AirBuddy Device Installer"
echo
echo "Let's load up your board! Here's what's about to happen:"
echo "  1. detect your connected MicroPython board"
echo "  2. ask a few quick setup questions"
echo "  3. generate config.json"
echo "  4. upload the AirBuddy firmware"
echo "  5. reset the board"
echo

# ------------------------------------------------------------
# Checks
# ------------------------------------------------------------

[[ -d "$DEVICE_DIR" ]] || die "Couldn't find the device/ folder at: $DEVICE_DIR"

if ! command_exists mpremote; then
  die "mpremote isn't installed. Get it with: pip install mpremote"
fi

mkdir -p "$TMP_DIR"

MPREMOTE=(mpremote connect "$PORT")

msg "Checking for a connected MicroPython board"
if ! "${MPREMOTE[@]}" exec "print('MicroPython connection OK')" >/dev/null 2>&1; then
  die "No MicroPython board found on port '$PORT'. Make sure MicroPython is flashed, then reconnect and try again."
fi

# ------------------------------------------------------------
# Detect platform
# ------------------------------------------------------------

msg "Detecting board type"

RAW_PLATFORM="$("${MPREMOTE[@]}" exec "import sys; print(sys.platform)" 2>/dev/null | tail -n 1 | tr -d '\r')"
[[ -n "$RAW_PLATFORM" ]] || die "Couldn't read sys.platform from the board."

echo "Detected sys.platform = $RAW_PLATFORM"

BOARD_TYPE=""

if [[ -n "$BOARD_OVERRIDE" ]]; then
  case "$BOARD_OVERRIDE" in
    pico|esp32|esp32s3|xiao_esp32s3)
      BOARD_TYPE="$BOARD_OVERRIDE"
      echo "Using board override: $BOARD_TYPE"
      ;;
    *)
      die "Unsupported --board value: $BOARD_OVERRIDE (use pico | esp32 | esp32s3 | xiao_esp32s3)"
      ;;
  esac
else
  case "$RAW_PLATFORM" in
    rp2)
      BOARD_TYPE="pico"
      ;;
    esp32)
      # sys.platform is "esp32" for all ESP32 variants; query uname.machine to
      # distinguish the XIAO ESP32-S3 and generic ESP32-S3 from plain ESP32.
      RAW_MACHINE="$("${MPREMOTE[@]}" exec "import uos; print(uos.uname().machine)" 2>/dev/null | tail -n 1 | tr -d '\r')"
      RAW_MACHINE_LC="${RAW_MACHINE,,}"
      echo "Detected uname.machine = $RAW_MACHINE"
      if [[ "$RAW_MACHINE_LC" == *"xiao"* ]]; then
        BOARD_TYPE="xiao_esp32s3"
      elif [[ "$RAW_MACHINE_LC" == *"esp32s3"* ]]; then
        BOARD_TYPE="esp32s3"
      else
        BOARD_TYPE="esp32"
      fi
      ;;
    *)
      die "Unsupported MicroPython platform: $RAW_PLATFORM"
      ;;
  esac
fi

echo "Board type: $BOARD_TYPE"

# ------------------------------------------------------------
# Fresh cleanup (optional)
# ------------------------------------------------------------

if [[ "$FRESH" -eq 1 ]]; then
  msg "Fresh install: clearing old files from the board"

  "${MPREMOTE[@]}" exec "
import os

def is_dir(path):
    try:
        return bool(os.stat(path)[0] & 0x4000)
    except:
        return False

def rm_tree(path):
    try:
        if is_dir(path):
            for name in os.listdir(path):
                rm_tree(path + '/' + name)
            os.rmdir(path)
        else:
            os.remove(path)
    except Exception as e:
        print('warn:', path, e)

for name in os.listdir('/'):
    if name not in ('boot.py', 'config.json'):
        rm_tree('/' + name)
" >/dev/null || true

  echo "Board cleared."
fi

# ------------------------------------------------------------
# Gather config values
# ------------------------------------------------------------

msg "A few quick questions about your setup"
echo "Don't stress — you can always re-run this script or edit config.json"
echo "directly on the device to change anything later."
echo

GPS_ENABLED="$(prompt_yes_no "Would you like to enable GPS?  Totally optional:  only enable if you will be installing a GPS module to your board." "n")"
WIFI_ENABLED="$(prompt_yes_no "Would you like to enable WiFi?  Important: Only enable if your board has a Wifi chip." "y")"

WIFI_SSID=""
WIFI_PASSWORD=""
TELEMETRY_ENABLED="false"
TELEMETRY_POST_EVERY_S=120
API_BASE="http://air2.earthen.io"

if [[ "$WIFI_ENABLED" == "true" ]]; then
  WIFI_SSID="$(prompt_required "WiFi network name (SSID)")"
  WIFI_PASSWORD="$(prompt_required "WiFi password")"
  TELEMETRY_ENABLED="$(prompt_yes_no "Enable telemetry uploads to your Buwana dashboard?" "y")"
  TELEMETRY_POST_EVERY_S="$(prompt_default "How often to upload readings (seconds)" "120")"
fi

echo
echo "Almost there! The next two values come from your Buwana AirBuddy account."
echo "Don't have one yet? Sign up here:"
echo "  https://buwana.ecobricks.org/en/signup-1.php?app=airb_ca090536efc8"
echo

DEVICE_ID="$(prompt_required "Device ID")"
DEVICE_KEY="$(prompt_required "Device key")"

echo
echo "How many hours ahead of (or behind) UTC is your timezone?"
echo "  Examples:  +8 = Hong Kong / Manila,  +7 = Jakarta,  +5.5 = India,  0 = UTC"
echo "  Behind UTC:  -5 = New York,  -6 = Chicago,  -8 = Los Angeles"
TIMEZONE_HOURS="$(prompt_default "Hours offset from UTC (e.g. 8, -5, 5.5 — leave blank to skip)" "")"

TIMEZONE_OFFSET_MIN=""
if [[ -n "$TIMEZONE_HOURS" ]]; then
  TIMEZONE_OFFSET_MIN="$(awk "BEGIN{printf \"%d\", $TIMEZONE_HOURS * 60}")"
fi

echo
echo "Are you using a large OLED display or a small one?  They have slightly"
echo "different pixel dimensions.  Small ones need a horizontal 2 pixel offset,"
echo "large ones 0.  Please specify your OLED offset:"
OLED_COL_OFFSET="$(prompt_default "OLED column offset" "0")"

# ------------------------------------------------------------
# XIAO ESP32-S3 extras
# ------------------------------------------------------------

SERVO_PRESENT="false"
TURTLE_MODE="false"
MORSE_BLESS="false"
JOKE_MODE="false"
COMPASS_OFFSET_DEG=0

if [[ "$BOARD_TYPE" == "xiao_esp32s3" ]]; then
  echo
  msg "XIAO ESP32-S3 extras"
  echo "Your XIAO board supports a few optional features. Answer n to anything"
  echo "you haven't wired yet — you can always re-run this script to reconfigure."
  echo

  SERVO_PRESENT="$(prompt_yes_no "Is a servo (MG996R) physically wired to pin D8 / GPIO7?" "n")"
  TURTLE_MODE="$(prompt_yes_no "Enable Turtle mode? (Boot screen shows 'turtleOS' instead of 'airOS')" "n")"
  JOKE_MODE="$(prompt_yes_no "Enable Joke mode? (Quad-click shows the scary self-destruct screen instead of the chill turtle animation)" "n")"
  MORSE_BLESS="$(prompt_yes_no "Enable Morse bless? (Button LED blinks in Morse before each telemetry upload — only useful if a button LED is wired to D0)" "n")"

  echo
  echo "Compass calibration: point the board North, note the raw heading on the"
  echo "Compass screen, then enter the negative of that value here to zero the"
  echo "offset.  For example, if the raw reading is 80, enter -80."
  echo "Leave blank to use 0 and calibrate later."
  COMPASS_INPUT="$(prompt_default "Compass offset (degrees)" "0")"
  COMPASS_OFFSET_DEG="${COMPASS_INPUT:-0}"
fi

# ------------------------------------------------------------
# Generate config.json
# ------------------------------------------------------------

msg "Generating config.json"

mkdir -p "$TMP_DIR"

TZ_JSON="null"
if [[ -n "$TIMEZONE_OFFSET_MIN" ]]; then
  TZ_JSON="$TIMEZONE_OFFSET_MIN"
fi

cat > "$TMP_CONFIG" <<EOF
{
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
  "turtle_mode": $TURTLE_MODE,
  "joke_mode": $JOKE_MODE,
  "morse_bless": $MORSE_BLESS,
  "compass_offset_deg": $COMPASS_OFFSET_DEG
}
EOF

echo "Generated: $TMP_CONFIG"
echo
echo "--------- config preview ---------"
cat "$TMP_CONFIG"
echo "----------------------------------"

# ------------------------------------------------------------
# Check existing config.json on board
# ------------------------------------------------------------

CONFIG_EXISTS="$("${MPREMOTE[@]}" exec "import os; print('config.json' in os.listdir('/'))" 2>/dev/null | tail -n 1 | tr -d '\r' || true)"

if [[ "$CONFIG_EXISTS" == "True" && "$OVERWRITE_CONFIG" -ne 1 ]]; then
  echo
  echo "There's already a config.json on this device."
  REPLY="$(prompt_yes_no "Overwrite it with your new settings?" "n")"
  if [[ "$REPLY" == "true" ]]; then
    OVERWRITE_CONFIG=1
  fi
fi

# ------------------------------------------------------------
# Upload device files
# ------------------------------------------------------------

msg "Uploading AirBuddy firmware"

# Stage a clean copy of device/ — rsync excludes __pycache__ and *.pyc so
# CPython bytecode artefacts never land on the board's flash.
STAGE_DIR="$TMP_DIR/stage"
rm -rf "$STAGE_DIR"
rsync -a \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  "$DEVICE_DIR/" "$STAGE_DIR/"

"${MPREMOTE[@]}" fs cp -r "$STAGE_DIR/." : || die "Failed to upload device files."

echo "Firmware uploaded."

# ------------------------------------------------------------
# Upload config.json
# ------------------------------------------------------------

if [[ "$CONFIG_EXISTS" != "True" || "$OVERWRITE_CONFIG" -eq 1 ]]; then
  msg "Uploading config.json"
  "${MPREMOTE[@]}" fs cp "$TMP_CONFIG" :config.json || die "Failed to upload config.json"
  echo "config.json uploaded."
else
  warn "Keeping the existing config.json on the device."
fi

# ------------------------------------------------------------
# Show board filesystem
# ------------------------------------------------------------

msg "Board filesystem"
"${MPREMOTE[@]}" fs ls || true

# ------------------------------------------------------------
# Reset board
# ------------------------------------------------------------

msg "Resetting board"
"${MPREMOTE[@]}" reset || true

# ------------------------------------------------------------
# Done!
# ------------------------------------------------------------

echo
echo "  ✓  AirBuddy is installed and your board is booting up!"
echo
echo "Watch it wake up live with:"
echo "  mpremote connect $PORT repl"
echo
echo "If something looks off, try a clean reinstall:"
echo "  ./scripts/install_airbuddy.sh --fresh"
echo
cat <<'CLOUDS'

        .  .  .   .  .  .   .  .  .
      .  .-~~~-.   .-~~~-.   .-~~~-.  .
     .  (       ) (       ) (       )  .
    .    '-~~~-'   '-~~~-'   '-~~~-'   .
      .  .  .   .  .  .   .  .  .  .

CLOUDS
echo "You're good to go.  Happy breathing!"
echo
