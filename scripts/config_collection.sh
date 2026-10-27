# scripts/config_collection.sh — sourced by sync_turtleOS.sh and sync_airOS.sh
# ------------------------------------------------------------
# Builds the config.json a Synker uploads: the script's base config
# (xiao_config.json / airbuddy_config.json), overlaid with the WiFi network
# and device picked from scripts/config_collection.json when that file
# exists. Both files are git-ignored; they hold WiFi passwords and device keys.
# Each picker also offers "Enter a new …", which adds the entry to the
# collection (creating the file on first use).
#
#   {"wifi":       [{"wifi_ssid": "", "wifi_password": "", "timezone_offset_min": 60}],
#    "turtles":    [{"device_id": "", "device_key": "", "api_base": "http://hopeturtles.org"}],
#    "airbuddies": [{"device_id": "", "device_key": "", "api_base": "http://air2.earthen.io"}]}
#
# "wifi" is shared. Each Synker lists only its own device section, since a
# device key only works against its own server.
#
# The caller defines die() and sets COLLECTION_FILE and BOARD_CONFIG (the
# board's current config.json, read over mpremote; empty if none).

# pick_from_collection SECTION HEADING BASE_NAME
# Lists one section of the collection as a numbered menu and sets PICK to the
# chosen 0-based index ("" = keep BASE_NAME's values). The entry matching the
# board's current config is the default, so hitting return keeps the board
# as it is. The last option enters a new WiFi network / device, which is
# saved into config_collection.json (created if missing) and then picked.
pick_from_collection() {
  local section="$1" heading="$2" base_name="$3" lines def n reply i new_opt
  local noun="device"
  [[ "$section" == "wifi" ]] && noun="WiFi network"
  [[ "$section" == "turtles" ]] && noun="turtle"
  [[ "$section" == "airbuddies" ]] && noun="AirBuddy"
  mapfile -t lines < <(python3 - "$COLLECTION_FILE" "$section" "$BOARD_CONFIG" <<'PY'
import json, os, sys
coll_path, section, board_path = sys.argv[1:4]
entries = []
if os.path.exists(coll_path):
    with open(coll_path) as f:
        entries = json.load(f).get(section) or []
try:
    with open(board_path) as f:
        board = json.load(f)
except Exception:
    board = {}
if not isinstance(board, dict):
    board = {}
key = "wifi_ssid" if section == "wifi" else "device_id"
default = 1
labels = []
for i, e in enumerate(entries, 1):
    label = str(e.get(key, "?"))
    if section == "wifi" and e.get("timezone_offset_min") is not None:
        label += "  (UTC%+g)" % (int(e["timezone_offset_min"]) / 60.0)
    if section != "wifi" and e.get("api_base"):
        label += "  (%s)" % e["api_base"]
    if str(e.get(key, "")) and str(e.get(key)) == str(board.get(key, "")):
        label += "  <- on the board now"
        default = i
    labels.append(label)
print(default)
for label in labels:
    print(label)
PY
  )
  [[ -n "${lines[0]:-}" ]] || die "Could not read scripts/config_collection.json (is it valid JSON?)."

  def="${lines[0]}"
  n=$(( ${#lines[@]} - 1 ))
  echo
  echo "$heading  (Hit return for option $def)"
  if (( n < 1 )); then
    # Nothing saved yet: option 1 keeps the base config's values.
    echo "  1. Keep the one in scripts/$base_name"
    new_opt=2
  else
    for (( i = 1; i <= n; i++ )); do
      echo "  $i. ${lines[$i]}"
    done
    new_opt=$(( n + 1 ))
  fi
  echo "  $new_opt. Enter a new $noun…"
  echo
  while true; do
    read -r -p "Enter 1-$new_opt: " reply
    reply="${reply:-$def}"
    if [[ "$reply" =~ ^[0-9]+$ ]] && (( reply >= 1 && reply <= new_opt )); then
      break
    fi
    echo "  (Please enter a number from 1 to $new_opt.)"
  done
  if (( reply == new_opt )); then
    add_to_collection "$section" "$noun"
  elif (( n < 1 )); then
    PICK=""
  else
    PICK=$(( reply - 1 ))
  fi
}

# add_to_collection SECTION NOUN
# Prompts for a new WiFi network (SSID, password, UTC offset) or device
# (ID, key, API base), saves it into config_collection.json and sets PICK to
# its index. An entry with the same SSID / device_id is replaced, not doubled.
add_to_collection() {
  local section="$1" noun="$2" a="" b="" c="" api_default
  echo
  echo "New $noun (saved to scripts/config_collection.json):"
  if [[ "$section" == "wifi" ]]; then
    while [[ -z "$a" ]]; do read -r -p "  WiFi name (SSID): " a; done
    read -r -p "  WiFi password: " b
    while true; do
      read -r -p "  UTC offset in hours, e.g. 1, -5, 5.5 (blank = none): " c
      [[ -z "$c" || "$c" =~ ^[+-]?[0-9]+(\.[0-9]+)?$ ]] && break
      echo "  (Please enter a number of hours, or leave it blank.)"
    done
  else
    api_default="http://hopeturtles.org"
    [[ "$section" == "airbuddies" ]] && api_default="http://air2.earthen.io"
    while [[ -z "$a" ]]; do read -r -p "  Device ID: " a; done
    while [[ -z "$b" ]]; do read -r -p "  Device key: " b; done
    read -r -p "  API base [$api_default]: " c
    c="${c:-$api_default}"
  fi
  PICK="$(NEW_A="$a" NEW_B="$b" NEW_C="$c" python3 - "$COLLECTION_FILE" "$section" <<'PY'
import json, os, sys
coll_path, section = sys.argv[1:3]
a, b, c = os.environ["NEW_A"], os.environ["NEW_B"], os.environ["NEW_C"]
coll = {}
if os.path.exists(coll_path):
    with open(coll_path) as f:
        coll = json.load(f)
coll.setdefault("_comment", "Local only (git-ignored). WiFi networks and devices "
                "offered by sync_turtleOS.sh / sync_airOS.sh.")
if section == "wifi":
    key = "wifi_ssid"
    entry = {"wifi_ssid": a, "wifi_password": b}
    if c:
        entry["timezone_offset_min"] = int(round(float(c) * 60))
else:
    key = "device_id"
    entry = {"device_id": a, "device_key": b, "api_base": c}
entries = coll.setdefault(section, [])
for i, e in enumerate(entries):
    if str(e.get(key)) == a:
        entries[i] = entry
        break
else:
    entries.append(entry)
    i = len(entries) - 1
tmp = coll_path + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(coll, f, indent=2)
    f.write("\n")
os.replace(tmp, coll_path)
print(i)
PY
  )" || die "Could not save to scripts/config_collection.json."
  echo "  Saved $noun ${a} to scripts/config_collection.json."
}

# build_upload_config BASE_CONFIG DEVICE_SECTION DEVICE_NOUN WIFI_PICK DEVICE_PICK OUT KEEP_KEY...
# Writes OUT and prints a label for the "Config: … → config.json" line.
# Then some keys (KEEP_KEY…) are kept from the board because the device wrote
# them itself: device_name comes from the server on an online boot and the
# base config doesn't carry it, so uploading as-is wiped the name until the
# next online boot. A non-empty local value wins, and nothing is kept when
# the upload gives the board a different device_id: the old device's name
# doesn't belong to the new one.
# Exits 3 when all four credentials would be blank (never upload the blank
# example: it would wipe the board's WiFi and device credentials).
build_upload_config() {
  local base="$1" section="$2" noun="$3" wifi_pick="$4" device_pick="$5" out="$6"
  shift 6
  WIFI_PICK="$wifi_pick" DEVICE_PICK="$device_pick" python3 - \
      "$base" "$BOARD_CONFIG" "$COLLECTION_FILE" "$out" "$section" "$noun" "$@" <<'PY'
import json, os, sys
local_path, board_path, coll_path, out_path, device_section, noun = sys.argv[1:7]
keys = sys.argv[7:]
with open(local_path) as f:
    cfg = json.load(f)
try:
    with open(board_path) as f:
        board = json.load(f)
except Exception:
    board = {}
if not isinstance(board, dict):
    board = {}

label = "scripts/" + os.path.basename(local_path)
picks = []
for section, env in (("wifi", "WIFI_PICK"), (device_section, "DEVICE_PICK")):
    idx = os.environ.get(env, "")
    if idx == "":
        continue
    with open(coll_path) as f:
        entry = json.load(f)[section][int(idx)]
    cfg.update({k: v for k, v in entry.items() if not k.startswith("_")})
    picks.append(entry.get("wifi_ssid") if section == "wifi" else noun + " " + str(entry.get("device_id")))
if picks:
    label += " + " + " / ".join(picks)

same_device = str(cfg.get("device_id", "")) == str(board.get("device_id", ""))
for k in keys:
    if str(cfg.get(k) or "").strip() or not same_device:
        continue
    v = board.get(k)
    if str(v or "").strip():
        cfg[k] = v
        print("Keeping %s %r from the board" % (k, v), file=sys.stderr)

blank = [k for k in ("wifi_ssid", "wifi_password", "device_id", "device_key")
         if not str(cfg.get(k) or "").strip()]
if len(blank) == 4:
    print("BLANK", file=sys.stderr)
    sys.exit(3)
if blank:
    print("WARNING: config.json has empty: %s — the board will get blank values." % " ".join(blank),
          file=sys.stderr)

with open(out_path, "w") as f:
    json.dump(cfg, f, indent=2)
print(label)
PY
}

# ------------------------------------------------------------
# Option 4: "go over all the config parameters"
# ------------------------------------------------------------
# review_config_params MODE CONFIG
# Asks the same questions as install_turtleOS.sh (MODE=turtle) or
# install_airOS.sh (MODE=air), defaulting each one to the value in CONFIG
# (the config.json about to be uploaded), so hitting return keeps it. Shows
# what changed and rewrites CONFIG in place once confirmed. The operating
# mode is not asked: each Synker uploads its own mode.

_cfg_yn() {   # _cfg_yn PROMPT DEFAULT(y|n) -> y|n
  local prompt="$1" def="$2" shown reply
  [[ "$def" == "y" ]] && shown="Y/n" || shown="y/N"
  while true; do
    read -r -p "$prompt [$shown]: " reply
    reply="${reply:-$def}"
    case "$(echo "$reply" | tr '[:upper:]' '[:lower:]')" in
      y|yes) echo y; return ;;
      n|no)  echo n; return ;;
      *)     echo "  (Please answer y or n.)" >&2 ;;
    esac
  done
}

_cfg_text() {   # _cfg_text PROMPT DEFAULT [secret] -> value (required)
  local prompt="$1" def="$2" secret="${3:-}" shown reply
  shown="$def"
  [[ -n "$secret" && -n "$def" ]] && shown="keep current"
  while true; do
    if [[ -n "$def" ]]; then
      read -r -p "$prompt [default: $shown]: " reply
      reply="${reply:-$def}"
    else
      read -r -p "$prompt: " reply
    fi
    if [[ -n "$reply" ]]; then echo "$reply"; return; fi
    echo "  (This field is required — please enter a value.)" >&2
  done
}

_cfg_num() {   # _cfg_num PROMPT DEFAULT -> number
  local prompt="$1" def="$2" reply
  while true; do
    read -r -p "$prompt [default: $def]: " reply
    reply="${reply:-$def}"
    if [[ "$reply" =~ ^[+-]?[0-9]+(\.[0-9]+)?$ ]]; then echo "$reply"; return; fi
    echo "  (Please enter a number.)" >&2
  done
}

review_config_params() {
  local mode="$1" cfg="$2" dash reply def changes
  dash="hopeturtles.org"
  [[ "$mode" == "air" ]] && dash="air2.earthen.io"

  # Current values (installer defaults where a key is missing) -> CFGR_* vars.
  eval "$(python3 - "$cfg" <<'PY'
import json, shlex, sys
with open(sys.argv[1]) as f:
    c = json.load(f)
yn = lambda v: "y" if v else "n"
num = lambda v: ("%g" % v) if isinstance(v, float) else str(v)
tz = c.get("timezone_offset_min")
out = {
    "WIFI_ENABLED": yn(c.get("wifi_enabled", True)),
    "WIFI_SSID": c.get("wifi_ssid") or "",
    "WIFI_PASSWORD": c.get("wifi_password") or "",
    "TELEMETRY_MODE": c.get("telemetry_mode") or "auto",
    "TELEMETRY_POST_EVERY_S": num(c.get("telemetry_post_every_s", 120)),
    "MISSION_CONNECTION_MODE": c.get("mission_connection_mode") or "wifi_auto",
    "DEVICE_ID": c.get("device_id") or "",
    "DEVICE_KEY": c.get("device_key") or "",
    "TZ_HOURS": "none" if tz is None else "%g" % (int(tz) / 60.0),
    "GPS_ENABLED": yn(c.get("gps_enabled", True)),
    "SERVO_PRESENT": yn(c.get("servo_present", True)),
    "COMPASS_OFFSET_DEG": num(c.get("compass_offset_deg", 0)),
    "BLE_ENABLED": yn(c.get("ble_enabled", True)),
    "BLE_WINDOW_MIN": num(c.get("ble_window_min", 10)),
    "OLED_COL_OFFSET": num(c.get("oled_col_offset", 0)),
}
for k, d in (("arrival_radius_m", 300), ("gps_loss_safe_s", 120), ("low_batt_pct", 20),
             ("sail_min_deg", 10), ("sail_max_deg", 170), ("luff_sweep_dps", 8),
             ("luff_resweep_s", 600), ("luff_threshold_mult", 5.0), ("nav_cycle_ms", 300)):
    out[k.upper()] = num(c.get(k, d))
for k, v in out.items():
    print("CFGR_%s=%s" % (k, shlex.quote(str(v))))
PY
  )" || die "Could not read the config to review (is it valid JSON?)."

  echo
  echo "Hit return on any question to keep the value shown."

  echo
  echo "--- Network ---"
  CFGR_WIFI_ENABLED="$(_cfg_yn "Enable WiFi? (required for telemetry uploads)" "$CFGR_WIFI_ENABLED")"
  if [[ "$CFGR_WIFI_ENABLED" == "y" ]]; then
    CFGR_WIFI_SSID="$(_cfg_text "WiFi network name (SSID)" "$CFGR_WIFI_SSID")"
    CFGR_WIFI_PASSWORD="$(_cfg_text "WiFi password" "$CFGR_WIFI_PASSWORD" secret)"

    case "$CFGR_TELEMETRY_MODE" in manual) def=2 ;; off) def=3 ;; *) def=1 ;; esac
    echo
    echo "How should readings be logged to your $dash dashboard?"
    echo "  1) auto   — post a reading on a fixed interval"
    echo "  2) manual — only when you stamp a position by hand on the GPS screen"
    echo "  3) off    — never log"
    while true; do
      read -r -p "Telemetry mode [default: $def]: " reply
      case "${reply:-$def}" in
        1) CFGR_TELEMETRY_MODE="auto";   break ;;
        2) CFGR_TELEMETRY_MODE="manual"; break ;;
        3) CFGR_TELEMETRY_MODE="off";    break ;;
        *) echo "  (Please enter 1, 2, or 3.)" ;;
      esac
    done
    if [[ "$CFGR_TELEMETRY_MODE" == "auto" ]]; then
      CFGR_TELEMETRY_POST_EVERY_S="$(_cfg_num "How often to upload readings (seconds)" "$CFGR_TELEMETRY_POST_EVERY_S")"
    fi

    if [[ "$mode" == "turtle" ]]; then
      [[ "$CFGR_MISSION_CONNECTION_MODE" == "wifi_manual" ]] && def=2 || def=1
      echo
      echo "When should the turtle try to connect to WiFi to upload stored readings?"
      echo "  1) automatically — retry on a backoff (15 min, growing to 12 h)"
      echo "  2) manually      — only when you triple-click into the connectivity screens."
      echo "                     Best for a launched turtle: no home WiFi at sea, so"
      echo "                     automatic scans just waste battery."
      while true; do
        read -r -p "Connection mode [default: $def]: " reply
        case "${reply:-$def}" in
          1) CFGR_MISSION_CONNECTION_MODE="wifi_auto";   break ;;
          2) CFGR_MISSION_CONNECTION_MODE="wifi_manual"; break ;;
          *) echo "  (Please enter 1 or 2.)" ;;
        esac
      done
    fi
  else
    # Like the installer: no WiFi, no logging. The credentials are kept.
    CFGR_TELEMETRY_MODE="off"
  fi

  echo
  echo "--- Device registration ---"
  echo "Your device ID and key come from your $dash account."
  CFGR_DEVICE_ID="$(_cfg_text "Device ID" "$CFGR_DEVICE_ID")"
  CFGR_DEVICE_KEY="$(_cfg_text "Device key" "$CFGR_DEVICE_KEY" secret)"

  echo
  echo "--- Time zone ---"
  echo "How many hours ahead of (or behind) UTC is your local time?"
  echo "  Examples:  +8 = Hong Kong/Manila,  +7 = Jakarta,  +5.5 = India,  +3 = Istanbul,  0 = UTC/London"
  echo "  Behind UTC: -5 = New York,  -6 = Chicago,  -8 = Los Angeles"
  echo "  Enter \"none\" to leave it unset."
  while true; do
    read -r -p "Hours offset from UTC [default: $CFGR_TZ_HOURS]: " reply
    reply="${reply:-$CFGR_TZ_HOURS}"
    if [[ "$reply" == "none" || "$reply" =~ ^[+-]?[0-9]+(\.[0-9]+)?$ ]]; then
      CFGR_TZ_HOURS="$reply"; break
    fi
    echo "  (Please enter a number of hours, or \"none\".)"
  done

  echo
  echo "--- Sensors and hardware ---"
  CFGR_GPS_ENABLED="$(_cfg_yn "Is a GPS module attached (the stacked L76K GNSS board, or one on D6/D7)?" "$CFGR_GPS_ENABLED")"

  if [[ "$mode" == "turtle" ]]; then
    CFGR_SERVO_PRESENT="$(_cfg_yn "Is the MG996R sail servo wired to pin D8 / GPIO7?" "$CFGR_SERVO_PRESENT")"

    echo
    echo "--- Compass calibration ---"
    echo "Point the turtle North, note the raw heading shown on the Compass screen,"
    echo "then enter the negative of that number here to zero the offset."
    echo "Example: if the raw reading is 80 when pointing North, enter -80."
    CFGR_COMPASS_OFFSET_DEG="$(_cfg_num "Compass offset (degrees)" "$CFGR_COMPASS_OFFSET_DEG")"

    echo
    echo "--- Bluetooth (Turtle Wrangler app) ---"
    echo "Bluetooth lets the Turtle Wrangler phone app read and steer the turtle"
    echo "up close.  Turn it off for a mission voyage — it can only be turned back"
    echo "on from the Bluetooth screen on the turtle itself."
    CFGR_BLE_ENABLED="$(_cfg_yn "Enable Bluetooth?" "$CFGR_BLE_ENABLED")"
    if [[ "$CFGR_BLE_ENABLED" == "y" ]]; then
      echo "The turtle advertises for a few minutes after boot, after waking, and"
      echo "each time you open the Bluetooth screen.  0 = advertise all the time."
      CFGR_BLE_WINDOW_MIN="$(_cfg_num "Bluetooth pairing window (minutes, 0-240)" "$CFGR_BLE_WINDOW_MIN")"
    fi
  fi

  echo
  echo "--- Display ---"
  echo "Most OLED modules need no offset. If your display is slightly misaligned"
  echo "horizontally (SH1106 variant), try an offset of 2."
  CFGR_OLED_COL_OFFSET="$(_cfg_num "OLED column offset" "$CFGR_OLED_COL_OFFSET")"

  if [[ "$mode" == "turtle" ]]; then
    echo
    echo "--- Navigation tuning ---"
    echo "These control autopilot behaviour."
    echo
    CFGR_ARRIVAL_RADIUS_M="$(_cfg_num "Arrival radius — distance that counts as 'arrived' (metres, 20-5000)" "$CFGR_ARRIVAL_RADIUS_M")"
    CFGR_GPS_LOSS_SAFE_S="$(_cfg_num "GPS loss timeout — feather sail after this many seconds without a fix" "$CFGR_GPS_LOSS_SAFE_S")"
    CFGR_LOW_BATT_PCT="$(_cfg_num "Low-battery threshold — feather sail below this charge level (0-90 %)" "$CFGR_LOW_BATT_PCT")"
    CFGR_SAIL_MIN_DEG="$(_cfg_num "Sail servo minimum angle — lower physical stop (degrees)" "$CFGR_SAIL_MIN_DEG")"
    CFGR_SAIL_MAX_DEG="$(_cfg_num "Sail servo maximum angle — upper physical stop (degrees)" "$CFGR_SAIL_MAX_DEG")"
    CFGR_LUFF_SWEEP_DPS="$(_cfg_num "Luff sweep speed (degrees/second, 2-30)" "$CFGR_LUFF_SWEEP_DPS")"
    CFGR_LUFF_RESWEEP_S="$(_cfg_num "Luff re-sweep interval in SAIL-NAV mode (seconds)" "$CFGR_LUFF_RESWEEP_S")"
    CFGR_LUFF_THRESHOLD_MULT="$(_cfg_num "Luff detection sensitivity multiplier (1.5-20.0)" "$CFGR_LUFF_THRESHOLD_MULT")"
    CFGR_NAV_CYCLE_MS="$(_cfg_num "Autopilot cycle interval (milliseconds, 100-2000)" "$CFGR_NAV_CYCLE_MS")"
  fi

  # Merge the answers into a copy of CONFIG and list what changed.
  changes="$( (export "${!CFGR_@}"; python3 - "$cfg" "$cfg.review" "$mode") <<'PY'
import json, os, sys
src, dst, mode = sys.argv[1:4]
with open(src) as f:
    c = json.load(f)
E = lambda k: os.environ["CFGR_" + k]
yn = lambda k: E(k) == "y"
to_int = lambda k: int(round(float(E(k))))
new = {
    "wifi_enabled": yn("WIFI_ENABLED"),
    "wifi_ssid": E("WIFI_SSID"),
    "wifi_password": E("WIFI_PASSWORD"),
    "telemetry_mode": E("TELEMETRY_MODE"),
    "telemetry_enabled": E("TELEMETRY_MODE") != "off",
    "telemetry_post_every_s": to_int("TELEMETRY_POST_EVERY_S"),
    "device_id": E("DEVICE_ID"),
    "device_key": E("DEVICE_KEY"),
    "timezone_offset_min": None if E("TZ_HOURS") == "none" else int(round(float(E("TZ_HOURS")) * 60)),
    "gps_enabled": yn("GPS_ENABLED"),
    "oled_col_offset": to_int("OLED_COL_OFFSET"),
}
if mode == "turtle":
    new.update({
        "mission_connection_mode": E("MISSION_CONNECTION_MODE"),
        "servo_present": yn("SERVO_PRESENT"),
        "compass_offset_deg": to_int("COMPASS_OFFSET_DEG"),
        "ble_enabled": yn("BLE_ENABLED"),
        "ble_window_min": to_int("BLE_WINDOW_MIN"),
        "luff_threshold_mult": float(E("LUFF_THRESHOLD_MULT")),
    })
    for k in ("arrival_radius_m", "gps_loss_safe_s", "low_batt_pct", "sail_min_deg",
              "sail_max_deg", "luff_sweep_dps", "luff_resweep_s", "nav_cycle_ms"):
        new[k] = to_int(k.upper())
SECRET = ("wifi_password", "device_key")
for k, v in new.items():
    if c.get(k) != v:
        old = c.get(k)
        if k in SECRET:
            old, v = "(old)", "(changed)"
        print("  %-24s %s -> %s" % (k, json.dumps(old), json.dumps(v)))
c.update(new)
with open(dst, "w") as f:
    json.dump(c, f, indent=2)
PY
  )" || die "Could not apply the reviewed settings."

  echo
  if [[ -z "$changes" ]]; then
    echo "No changes — uploading the config as it was."
    rm -f "$cfg.review"
    return 0
  fi
  echo "--------- changed settings ---------"
  echo "$changes"
  echo "------------------------------------"
  if [[ "$(_cfg_yn "Use these settings?" y)" == "y" ]]; then
    mv "$cfg.review" "$cfg"
    echo "Settings updated."
  else
    rm -f "$cfg.review"
    echo "Changes discarded — uploading the config as it was."
  fi
}
