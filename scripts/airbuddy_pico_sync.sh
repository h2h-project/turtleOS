#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# AirBuddy Pico Sync
# ------------------------------------------------------------
# Uploads a Pico-optimised subset of the AirBuddy firmware to
# a connected Raspberry Pi Pico (RP2040).
#
# Excluded: ESP32 HAL files, screens not used on Pico (battery,
# compass, destination, device, gps, sailpoint, selfdestruct,
# servo, turtle_waiting), drivers for absent hardware (compass,
# INA219, servo, AS5600, SCD41, GPS), legacy/alternate screens
# (co2, temp2, frowny).  Recovers ~200 KB of flash vs full sync.
#
# Usage:
#   ./scripts/airbuddy_pico_sync.sh
#   ./scripts/airbuddy_pico_sync.sh --fresh
#   ./scripts/airbuddy_pico_sync.sh --port /dev/cu.usbmodem141301
#
# Modes (interactive prompt unless --fresh is given):
#   Hard reset  — wipe all files from the Pico, then upload clean set
#   Sync        — upload changed files and remove any excluded leftovers
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE_DIR="$ROOT_DIR/device"
SCRIPTS_DIR="$ROOT_DIR/scripts"
TMP_DIR="$ROOT_DIR/.tmp_pico_sync"
STAGE_DIR="$TMP_DIR/stage"

PORT="auto"
FRESH=0

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

msg()  { echo; echo "==> $1"; }
warn() { echo; echo "WARNING: $1"; }
die()  { echo; echo "ERROR: $1"; exit 1; }

print_help() {
  cat <<EOF
AirBuddy Pico Sync — uploads a lean Pico-only build

Usage:
  ./scripts/airbuddy_pico_sync.sh [options]

Options:
  --fresh         Non-interactive hard reset (wipe board, then upload)
  --port PORT     Serial port (default: auto)
  --help          Show this help

EOF
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
      *) echo "  (Please answer y or n.)" ;;
    esac
  done
}

# ------------------------------------------------------------
# Parse args
# ------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh)  FRESH=1; shift ;;
    --port)   [[ $# -ge 2 ]] || die "--port requires a value"; PORT="$2"; shift 2 ;;
    --help)   print_help; exit 0 ;;
    *)        die "Unknown option: $1" ;;
  esac
done

# ------------------------------------------------------------
# Locate mpremote
# ------------------------------------------------------------

MPREMOTE=""
if command -v mpremote >/dev/null 2>&1; then
  MPREMOTE="mpremote"
elif [[ -x "$HOME/venvs/micropython/bin/mpremote" ]]; then
  MPREMOTE="$HOME/venvs/micropython/bin/mpremote"
else
  die "mpremote not found. Install it with: pip install mpremote  (or activate your micropython venv)"
fi

MPREMOTE_CMD=("$MPREMOTE" connect "$PORT")

# ------------------------------------------------------------
# Intro
# ------------------------------------------------------------

echo
echo "==> AirBuddy Pico Sync"
echo
echo "Uploads a lean Pico-only build:"
echo "  Excluded: ESP32 HAL, GPS/compass/nav/turtle/servo screens,"
echo "  device/battery/selfdestruct screens, unused drivers."
echo "  Recovers ~200 KB of flash vs a full sync."
echo

# ------------------------------------------------------------
# Connect & verify board is a Pico
# ------------------------------------------------------------

msg "Connecting to board"
"${MPREMOTE_CMD[@]}" exec "print('connected')" >/dev/null 2>&1 \
  || die "No MicroPython board found on port '$PORT'. Check the connection and try again."

PLATFORM="$("${MPREMOTE_CMD[@]}" exec "import sys; print(sys.platform)" 2>/dev/null | tail -n1 | tr -d '\r')"
[[ -n "$PLATFORM" ]] || die "Could not read sys.platform from the board."

if [[ "$PLATFORM" != "rp2" ]]; then
  die "This script is for Raspberry Pi Pico only (sys.platform=rp2). Detected: $PLATFORM"
fi
echo "Board: Pico (rp2) — OK"

# ------------------------------------------------------------
# Choose mode
# ------------------------------------------------------------

MODE=""
if [[ "$FRESH" -eq 1 ]]; then
  MODE="reset"
else
  echo
  echo "How would you like to sync?"
  echo "  1) Hard reset — wipe all files from the Pico, then upload a clean set"
  echo "  2) Sync       — upload files and remove any excluded leftovers (keeps unrelated files)"
  echo
  while true; do
    read -r -p "Enter 1 or 2: " reply
    case "$reply" in
      1) MODE="reset"; break ;;
      2) MODE="sync";  break ;;
      *) echo "  (Please enter 1 or 2.)" ;;
    esac
  done
fi

echo "Mode: $MODE"

# ------------------------------------------------------------
# Stage a Pico-only copy of device/
# ------------------------------------------------------------

msg "Staging Pico-only firmware"

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

rsync -a \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '.gitignore' \
  \
  `# ── ESP32 HAL (not needed on Pico) ──────────────────────────` \
  --exclude 'src/hal/board_esp32.py' \
  --exclude 'src/hal/board_esp32_s3.py' \
  --exclude 'src/hal/board_xiao_esp32_s3.py' \
  \
  `# ── Drivers not wired on base Pico build ─────────────────────` \
  --exclude 'src/drivers/as5600.py' \
  --exclude 'src/drivers/hmc5883l_qmc5883l.py' \
  --exclude 'src/drivers/ina219.py' \
  --exclude 'src/drivers/scd4x.py' \
  --exclude 'src/drivers/servo.py' \
  \
  `# ── Sensor modules for disabled/absent hardware ───────────────` \
  --exclude 'src/sensors/ublox6gps.py' \
  \
  `# ── Screens not used on Pico ─────────────────────────────────` \
  --exclude 'src/ui/screens/battery.py' \
  --exclude 'src/ui/screens/co2.py' \
  --exclude 'src/ui/screens/compass.py' \
  --exclude 'src/ui/screens/destination.py' \
  --exclude 'src/ui/screens/device.py' \
  --exclude 'src/ui/screens/frowny.py' \
  --exclude 'src/ui/screens/gps.py' \
  --exclude 'src/ui/screens/sailpoint.py' \
  --exclude 'src/ui/screens/servo.py' \
  --exclude 'src/ui/screens/summary.py' \
  --exclude 'src/ui/screens/temp2.py' \
  --exclude 'src/ui/screens/turtle_waiting.py' \
  \
  "$DEVICE_DIR/" "$STAGE_DIR/"

# Overwrite config.json with the Pico base config
cp "$SCRIPTS_DIR/basic_config.json" "$STAGE_DIR/config.json"
echo "Config: scripts/basic_config.json → config.json"

# Apply Pico-specific file overrides.
# _pico.py files are trimmed versions that save ~7-10 KB heap by removing
# dead code paths (turtle/compass/servo/GPS flows, unused glyphs).
# They are copied over the originals in the staging dir, then deleted
# so the device only receives the trimmed versions under the original names.
msg "Applying Pico-specific file overrides"
_pico_ok=1
cp "$STAGE_DIR/src/ui/flows_pico.py"  "$STAGE_DIR/src/ui/flows.py"  || { warn "flows_pico.py not found"; _pico_ok=0; }
cp "$STAGE_DIR/src/ui/glyphs_pico.py" "$STAGE_DIR/src/ui/glyphs.py" || { warn "glyphs_pico.py not found"; _pico_ok=0; }
cp "$STAGE_DIR/src/app/main_pico.py"  "$STAGE_DIR/src/app/main.py"  || { warn "main_pico.py not found"; _pico_ok=0; }
rm -f "$STAGE_DIR/src/ui/flows_pico.py" \
      "$STAGE_DIR/src/ui/glyphs_pico.py" \
      "$STAGE_DIR/src/app/main_pico.py"
[[ "$_pico_ok" -eq 1 ]] && echo "Pico overrides applied (flows, glyphs, app/main)." \
                        || warn "One or more overrides failed — check _pico.py files exist."

# ------------------------------------------------------------
# Hard reset: wipe board then upload
# ------------------------------------------------------------

if [[ "$MODE" == "reset" ]]; then
  msg "Hard reset: wiping all files from the Pico"

  "${MPREMOTE_CMD[@]}" exec "
import os

def is_dir(p):
    try:
        return bool(os.stat(p)[0] & 0x4000)
    except:
        return False

def rm_tree(p):
    try:
        if is_dir(p):
            for n in os.listdir(p):
                rm_tree(p + '/' + n)
            os.rmdir(p)
        else:
            os.remove(p)
    except Exception as e:
        print('warn:', p, repr(e))

for name in os.listdir('/'):
    rm_tree('/' + name)

print('Board cleared.')
" || warn "Some files could not be removed — continuing anyway."

  msg "Uploading Pico AirBuddy firmware"
  "${MPREMOTE_CMD[@]}" fs cp -r "$STAGE_DIR/." : \
    || die "Upload failed. Try reconnecting the board and running again."
  echo "Upload complete."
fi

# ------------------------------------------------------------
# RTC clock setup (hard reset only, skipped when --fresh)
# ------------------------------------------------------------

if [[ "$MODE" == "reset" && "$FRESH" -eq 0 ]]; then
  echo
  echo "Set the RTC (DS3231) clock?  The RTC always stores UTC."
  echo "  1) Use current system time (UTC)"
  echo "  2) Enter a custom UTC time"
  echo "  3) Leave as-is"
  echo
  while true; do
    read -r -p "Enter 1, 2, or 3: " rtc_reply
    case "$rtc_reply" in
      1|2|3) RTC_CHOICE="$rtc_reply"; break ;;
      *) echo "  (Please enter 1, 2, or 3.)" ;;
    esac
  done

  if [[ "$RTC_CHOICE" == "1" ]]; then
    # %u = weekday 1=Mon..7=Sun — matches DS3231 convention
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
    # Derive weekday (1=Mon..7=Sun) via host Python 3 so we don't need date -j
    RTC_WEEKDAY=$(python3 -c \
      "import datetime; print(datetime.date($((10#${RTC_YEAR:-2026})),$((10#${RTC_MONTH:-1})),$((10#${RTC_DAY:-1}))).isoweekday())" \
      2>/dev/null) || true
    [[ -z "$RTC_WEEKDAY" ]] && RTC_WEEKDAY=1
  fi

  if [[ "$RTC_CHOICE" == "1" || "$RTC_CHOICE" == "2" ]]; then
    # Strip leading zeros — prevents Python octal interpretation (e.g. 08 is invalid octal)
    RTC_YEAR=$((10#${RTC_YEAR}));     RTC_MONTH=$((10#${RTC_MONTH}))
    RTC_DAY=$((10#${RTC_DAY}));       RTC_WEEKDAY=$((10#${RTC_WEEKDAY}))
    RTC_HOUR=$((10#${RTC_HOUR}));     RTC_MIN=$((10#${RTC_MIN}))
    RTC_SEC=$((10#${RTC_SEC}))

    msg "Setting RTC — $(printf '%04d-%02d-%02d %02d:%02d:%02d' \
      $RTC_YEAR $RTC_MONTH $RTC_DAY $RTC_HOUR $RTC_MIN $RTC_SEC) UTC (weekday ${RTC_WEEKDAY}, 1=Mon..7=Sun)"

    "${MPREMOTE_CMD[@]}" exec "
import sys
sys.path.insert(0, '/src/lib')
sys.path.insert(0, '/src')
from machine import Pin, I2C
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
try:
    from src.drivers.ds3231 import DS3231
    ds = DS3231(i2c)
    ds.datetime(($RTC_YEAR, $RTC_MONTH, $RTC_DAY, $RTC_WEEKDAY, $RTC_HOUR, $RTC_MIN, $RTC_SEC))
    ds.clear_lost_power()
    yr, mo, dy, wd, hh, mm, ss = ds.datetime()
    print('RTC confirmed: {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC (wd={})'.format(yr,mo,dy,hh,mm,ss,wd))
except Exception as e:
    print('RTC ERROR:', repr(e))
" || warn "RTC step had errors — set the clock manually via REPL later."

  else
    echo "RTC left as-is."
  fi
fi

# ------------------------------------------------------------
# Sync: upload then remove excluded leftovers
# ------------------------------------------------------------

if [[ "$MODE" == "sync" ]]; then
  msg "Uploading Pico AirBuddy firmware"
  "${MPREMOTE_CMD[@]}" fs cp -r "$STAGE_DIR/." : \
    || die "Upload failed. Try reconnecting the board and running again."
  echo "Upload complete."

  msg "Removing excluded files (if present from a prior full install)"

  "${MPREMOTE_CMD[@]}" exec "
import os

EXCLUDED = [
    # ESP32 HAL
    '/src/hal/board_esp32.py',
    '/src/hal/board_esp32_s3.py',
    '/src/hal/board_xiao_esp32_s3.py',
    # Drivers not on base Pico
    '/src/drivers/as5600.py',
    '/src/drivers/hmc5883l_qmc5883l.py',
    '/src/drivers/ina219.py',
    '/src/drivers/scd4x.py',
    '/src/drivers/servo.py',
    # Sensors for disabled hardware
    '/src/sensors/ublox6gps.py',
    # Screens not used on Pico
    '/src/ui/screens/battery.py',
    '/src/ui/screens/co2.py',
    '/src/ui/screens/compass.py',
    '/src/ui/screens/destination.py',
    '/src/ui/screens/device.py',
    '/src/ui/screens/frowny.py',
    '/src/ui/screens/gps.py',
    '/src/ui/screens/sailpoint.py',
    '/src/ui/screens/servo.py',
    '/src/ui/screens/summary.py',
    '/src/ui/screens/temp2.py',
    '/src/ui/screens/turtle_waiting.py',
    '/.gitignore',
    # Pico override sources — never installed on device
    '/src/ui/flows_pico.py',
    '/src/ui/glyphs_pico.py',
    '/src/app/main_pico.py',
]

removed = 0
for path in EXCLUDED:
    try:
        os.remove(path)
        print('removed:', path)
        removed += 1
    except OSError:
        pass  # not present — fine

print('Cleanup done. Removed', removed, 'excluded file(s).')
" || warn "Cleanup step had errors — continuing."
fi

# ------------------------------------------------------------
# Free space report
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
# Reset
# ------------------------------------------------------------

msg "Resetting board"
"${MPREMOTE_CMD[@]}" reset || true

# ------------------------------------------------------------
# Done
# ------------------------------------------------------------

echo
echo "  Done!  AirBuddy Pico build is installed."
echo
echo "Watch it boot:"
echo "  $MPREMOTE connect $PORT repl"
echo
echo "Check free space:"
echo "  $MPREMOTE connect $PORT exec \"import uos; s=uos.statvfs('/'); print('Free:', s[0]*s[3]//1024, 'KB')\""
echo
