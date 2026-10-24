# scripts/config_collection.sh — sourced by sync_turtleOS.sh and sync_airOS.sh
# ------------------------------------------------------------
# Builds the config.json a Synker uploads: the script's base config
# (xiao_config.json / airbuddy_config.json), overlaid with the WiFi network
# and device picked from scripts/config_collection.json when that file
# exists. Both files are git-ignored; they hold WiFi passwords and device keys.
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
# chosen 0-based index ("" if the section is empty). The entry matching the
# board's current config is the default, so hitting return keeps the board
# as it is.
pick_from_collection() {
  local section="$1" heading="$2" base_name="$3" lines def n reply i
  mapfile -t lines < <(python3 - "$COLLECTION_FILE" "$section" "$BOARD_CONFIG" <<'PY'
import json, sys
coll_path, section, board_path = sys.argv[1:4]
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
print(default if labels else 0)
for label in labels:
    print(label)
PY
  )
  [[ -n "${lines[0]:-}" ]] || die "Could not read scripts/config_collection.json (is it valid JSON?)."

  def="${lines[0]}"
  n=$(( ${#lines[@]} - 1 ))
  if (( n < 1 )); then
    PICK=""
    echo "config_collection.json has no \"$section\" entries — using scripts/$base_name."
    return
  fi
  echo
  echo "$heading  (Hit return for option $def)"
  for (( i = 1; i <= n; i++ )); do
    echo "  $i. ${lines[$i]}"
  done
  echo
  while true; do
    read -r -p "Enter 1-$n: " reply
    reply="${reply:-$def}"
    if [[ "$reply" =~ ^[0-9]+$ ]] && (( reply >= 1 && reply <= n )); then
      PICK=$(( reply - 1 ))
      return
    fi
    echo "  (Please enter a number from 1 to $n.)"
  done
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
