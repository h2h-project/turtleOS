#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# airOS (airBuddy) XIAO Synker
# ------------------------------------------------------------
# Uploads the air-quality-only build of turtleOS (turtle_mode=false)
# to a connected XIAO ESP32-S3 (or generic ESP32-S3).
#
# Excluded: Pico HAL, Pico-only override sources
# (flows_pico.py, glyphs_pico.py, main_pico.py).
# All XIAO features are included; sail servo / compass / nav
# screens simply go unused in airOS mode.
#
# Usage:
#   ./scripts/sync_airOS.sh
#   ./scripts/sync_airOS.sh --fresh
#   ./scripts/sync_airOS.sh --port /dev/cu.usbmodem141301
#
# --fresh: non-interactive hard reset (wipe and re-upload)
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE_DIR="$ROOT_DIR/device"
SCRIPTS_DIR="$ROOT_DIR/scripts"
TMP_DIR="$ROOT_DIR/.tmp_airbuddy_sync"
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
airOS XIAO Synker — uploads the air-quality-only (airBuddy) XIAO ESP32-S3 build

Usage:
  ./scripts/sync_airOS.sh [options]

Options:
  --fresh         Non-interactive hard reset (wipe board, then upload)
  --port PORT     Serial port (default: auto)
  --help          Show this help

EOF
}

run_rtc_setup() {
  echo
  echo "Set the RTC (DS3231) clock?  The RTC always stores UTC."
  echo "  1) Leave as-is  [default]"
  echo "  2) Use current system time (UTC)"
  echo "  3) Enter a custom UTC time"
  echo
  local RTC_CHOICE RTC_YEAR RTC_MONTH RTC_DAY RTC_WEEKDAY RTC_HOUR RTC_MIN RTC_SEC rtc_reply
  while true; do
    read -r -p "Enter 1, 2, or 3: " rtc_reply
    rtc_reply="${rtc_reply:-1}"
    case "$rtc_reply" in
      1|2|3) RTC_CHOICE="$rtc_reply"; break ;;
      *) echo "  (Please enter 1, 2, or 3.)" ;;
    esac
  done

  if [[ "$RTC_CHOICE" == "2" ]]; then
    read -r RTC_YEAR RTC_MONTH RTC_DAY RTC_WEEKDAY RTC_HOUR RTC_MIN RTC_SEC \
      < <(date -u "+%Y %m %d %u %H %M %S")

  elif [[ "$RTC_CHOICE" == "3" ]]; then
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

  if [[ "$RTC_CHOICE" == "2" || "$RTC_CHOICE" == "3" ]]; then
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
" || warn "RTC step had errors — set the clock manually via REPL later."

  else
    echo "RTC left as-is."
  fi
}

show_flash_usage() {
  "${MPREMOTE_CMD[@]}" exec "
import uos

s = uos.statvfs('/')
BLK = s[0]  # allocation block size (typically 4096 on ESP32 LittleFS)
total_kb = BLK * s[2] // 1024
free_kb  = BLK * s[3] // 1024
used_kb  = total_kb - free_kb

TELEM = ('/telemetry_queue.json', '/telemetry_last_sent.json')

def _sz(p):
    try:
        raw = uos.stat(p)[6]
        return ((raw + BLK - 1) // BLK) * BLK
    except: return 0

def _isdir(p):
    try: return bool(uos.stat(p)[0] & 0x4000)
    except: return False

def _walk(path):
    fw = tl = 0
    try:
        for name in uos.listdir(path):
            full = path.rstrip('/') + '/' + name
            if _isdir(full):
                a, b = _walk(full)
                fw += a; tl += b
            else:
                sz = _sz(full)
                if full in TELEM:
                    tl += sz
                else:
                    fw += sz
    except: pass
    return fw, tl

fw_b, tl_b = _walk('/')

q = 0
try:
    with open('/telemetry_queue.json') as f:
        for line in f:
            if line.strip(): q += 1
except: pass

print('Total: {} KB  Used: {} KB  Free: {} KB'.format(total_kb, used_kb, free_kb))
print('  airOS firmware    : {} KB'.format(fw_b // 1024))
if tl_b > 0:
    print('  telemetry logs    : {} KB ({} queued reading{})'.format(tl_b // 1024, q, 's' if q != 1 else ''))
else:
    print('  telemetry logs    : 0 KB  (no pending readings)')
" || true
}

do_upload_sync() {
  msg "Uploading airOS firmware"
  "${MPREMOTE_CMD[@]}" fs cp -r "$STAGE_DIR/." : \
    || die "Upload failed. Try reconnecting the board and running again."
  echo "Upload complete."

  msg "Removing excluded files (if present from a prior Pico install)"
  "${MPREMOTE_CMD[@]}" exec "
import os

EXCLUDED = [
    '/src/hal/board_pico.py',
    '/src/ui/flows_pico.py',
    '/src/ui/glyphs_pico.py',
    '/src/app/main_pico.py',
    '/.gitignore',
]

removed = 0
for path in EXCLUDED:
    try:
        os.remove(path)
        print('removed:', path)
        removed += 1
    except OSError:
        pass

print('Cleanup done. Removed', removed, 'excluded file(s).')
" || warn "Cleanup step had errors — continuing."
}

open_repl() {
  msg "Opening REPL"
  echo "  Press Ctrl-D inside the REPL to reboot and see the full boot log."
  echo "  Press Ctrl-] to exit back to the shell."
  echo
  "${MPREMOTE_CMD[@]}" repl
  echo
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
cat <<'BANNER'
            ███               ███████     █████████
           ░░░              ███░░░░░███  ███░░░░░███
  ██████   ████  ████████  ███     ░░███░███    ░░░
 ░░░░░███ ░░███ ░░███░░███░███      ░███░░█████████
  ███████  ░███  ░███ ░░░ ░███      ░███ ░░░░░░░░███
 ███░░███  ░███  ░███     ░░███     ███  ███    ░███
░░████████ █████ █████     ░░░███████░  ░░█████████
 ░░░░░░░░ ░░░░░ ░░░░░        ░░░░░░░     ░░░░░░░░░
 _____                  _____ _          _____ _
|  |  |___ ___ _ _ _   |_   _| |_ _ _   |  _  |_|___
|    -|   | . | | | |    | | |   | | |  |     | |  _|_
|__|__|_|_|___|_____|    |_| |_|_|_  |  |__|__|_|_| |_| []
                                 |___|
BANNER
echo
echo "Uploads the air-quality-only XIAO ESP32-S3 build:"
echo "  Included: air quality screens, connectivity, GPS (optional)."
echo "  Excluded: Pico HAL, Pico-only override sources."
echo

# ------------------------------------------------------------
# Connect & verify board is an ESP32
# ------------------------------------------------------------

msg "Connecting to board"
"${MPREMOTE_CMD[@]}" exec "print('connected')" >/dev/null 2>&1 \
  || die "No MicroPython board found on port '$PORT'. Check the connection and try again."

PLATFORM="$("${MPREMOTE_CMD[@]}" exec "import sys; print(sys.platform)" 2>/dev/null | tail -n1 | tr -d '\r')"
[[ -n "$PLATFORM" ]] || die "Could not read sys.platform from the board."

if [[ "$PLATFORM" != "esp32" ]]; then
  die "This script is for XIAO ESP32-S3 only (sys.platform=esp32). Detected: $PLATFORM"
fi

MACHINE="$("${MPREMOTE_CMD[@]}" exec "import uos; print(uos.uname().machine)" 2>/dev/null | tail -n1 | tr -d '\r')"
echo "Board: esp32 — $MACHINE"

MACHINE_LOWER="$(echo "$MACHINE" | tr '[:upper:]' '[:lower:]')"
if [[ "$MACHINE_LOWER" != *"xiao"* && "$MACHINE_LOWER" != *"esp32s3"* ]]; then
  warn "Machine string does not mention XIAO or ESP32S3. Detected: $MACHINE"
  warn "Proceeding anyway — verify you have the correct board connected."
fi

# ------------------------------------------------------------
# Main menu
# ------------------------------------------------------------

ACTION=""
if [[ "$FRESH" -eq 1 ]]; then
  ACTION="full"
else
  echo
  echo "What would you like to do?  (Hit return for option 1)"
  echo "  1. Reboot the device and see the full boot log.  [default]"
  echo "  2. Do a quick airBuddy sync"
  echo "  3. Do a more in depth sync with full options"
  echo
  while true; do
    read -r -p "Enter 1, 2, or 3: " reply
    reply="${reply:-1}"
    case "$reply" in
      1) ACTION="reboot"; break ;;
      2) ACTION="quick";  break ;;
      3) ACTION="full";   break ;;
      *) echo "  (Please enter 1, 2, or 3.)" ;;
    esac
  done
fi

# ============================================================
# Option 1 — Reboot and watch the boot log
# ============================================================

if [[ "$ACTION" == "reboot" ]]; then
  echo
  echo "  Rebooting device — watch for the boot log below."
  echo "  Press Ctrl-] to exit back to the shell."
  echo
  "${MPREMOTE_CMD[@]}" reset repl
  echo
  exit 0
fi

# ============================================================
# Staging (shared by quick and full)
# ============================================================

msg "Staging XIAO ESP32-S3 firmware"

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
  \
  `# ── Pico HAL (not needed on ESP32-S3) ───────────────────────` \
  --exclude 'src/hal/board_pico.py' \
  \
  `# ── Pico-only override sources (never installed on XIAO) ─────` \
  --exclude 'src/ui/flows_pico.py' \
  --exclude 'src/ui/glyphs_pico.py' \
  --exclude 'src/app/main_pico.py' \
  \
  "$DEVICE_DIR/" "$STAGE_DIR/"

cp "$SCRIPTS_DIR/airbuddy_config.json" "$STAGE_DIR/config.json"
echo "Config: scripts/airbuddy_config.json → config.json"

# ============================================================
# Option 2 — Quick sync
# ============================================================

if [[ "$ACTION" == "quick" ]]; then
  do_upload_sync

  msg "Flash usage (after sync)"
  show_flash_usage
  echo
  echo "  Done!  airOS is installed."

  open_repl
  exit 0
fi

# ============================================================
# Option 3 / --fresh — Full sync with options
# ============================================================

msg "Flash usage (before sync)"
show_flash_usage

# ── Choose wipe vs sync ──────────────────────────────────────────────────────

MODE=""
if [[ "$FRESH" -eq 1 ]]; then
  MODE="reset"
else
  echo
  echo "How would you like to sync?"
  echo "  1) Sync       — upload files and remove any excluded leftovers (keeps unrelated files)  [default]"
  echo "  2) Hard reset — wipe all files from the board, then upload a clean set"
  echo
  while true; do
    read -r -p "Enter 1 or 2: " reply
    reply="${reply:-1}"
    case "$reply" in
      1) MODE="sync";  break ;;
      2) MODE="reset"; break ;;
      *) echo "  (Please enter 1 or 2.)" ;;
    esac
  done
fi

echo "Mode: $MODE"

# ── Hard reset: wipe then upload ────────────────────────────────────────────

if [[ "$MODE" == "reset" ]]; then
  msg "Hard reset: wiping all files from the board"

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

  msg "Uploading airOS firmware"
  "${MPREMOTE_CMD[@]}" fs cp -r "$STAGE_DIR/." : \
    || die "Upload failed. Try reconnecting the board and running again."
  echo "Upload complete."
fi

# ── RTC setup (reset mode, non-fresh only) ───────────────────────────────────

if [[ "$MODE" == "reset" && "$FRESH" -eq 0 ]]; then
  run_rtc_setup
fi

# ── Sync: upload, remove excluded, optional cleanup ─────────────────────────

if [[ "$MODE" == "sync" ]]; then
  do_upload_sync

  # Ask about clearing telemetry (No is default)
  echo
  echo "Clear the pending telemetry queue?"
  echo "  1) No — leave telemetry queue as-is  [default]"
  echo "  2) Yes — clear telemetry_queue.json"
  echo
  CLEAR_QUEUE="false"
  while true; do
    read -r -p "Enter 1 or 2: " reply
    reply="${reply:-1}"
    case "$reply" in
      1) CLEAR_QUEUE="false"; break ;;
      2) CLEAR_QUEUE="true";  break ;;
      *) echo "  (Please enter 1 or 2.)" ;;
    esac
  done

  if [[ "$CLEAR_QUEUE" == "true" ]]; then
    "${MPREMOTE_CMD[@]}" exec "
import os
try:
    sz = os.stat('/telemetry_queue.json')[6]
    os.remove('/telemetry_queue.json')
    print('Cleared telemetry_queue.json ({} bytes)'.format(sz))
except:
    print('telemetry_queue.json not present — nothing to clear.')
" || warn "Could not clear telemetry queue."
  fi

  run_rtc_setup
fi

# ── Final flash report and REPL ─────────────────────────────────────────────

msg "Flash usage (after sync)"
show_flash_usage
echo
echo "  Done!  airOS is installed."

open_repl
