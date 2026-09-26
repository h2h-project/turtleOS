# Part 1 — turtleOS firmware: BLE peripheral

This is the turtle-side half of Turtle Wrangler. It adds a BLE GATT
peripheral role to turtleOS: the XIAO ESP32-S3 advertises, a phone
connects, telemetry goes out as notifications, and commands come in as
writes, each of which returns a result. The WiFi telemetry pipeline
(`src/net/telemetry_client.py`, `src/app/telemetry_scheduler.py`) does
not change. BLE is an additional, independent interface, not a
replacement.

The contract this plan builds against is **GATT contract v1** in
[README.md](README.md). That README is the source of truth for UUIDs,
formats, opcodes, result codes, security and the wrangle window. This
document is the build order.

There is currently **no Bluetooth code in the tree**. `device/src/net/`
holds only outbound HTTP clients, and nothing listens for inbound
connections.

---

## Phase 0 — feasibility spike (no app-visible output)

Confirm these on real hardware before building. If any of them fails,
the plan changes.

1. **BLE stack on the deployed build.** Confirm that
   `resources/ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin` has the
   `bluetooth` module (NimBLE). Decide between vendoring `aioble` into
   `src/lib/` (the way `urequests.py` is vendored) and writing directly
   against `bluetooth`. aioble's `security` module handles bond
   persistence, which counts in its favour.
2. **WiFi/BLE coexistence and power management.** `wifi_manager.py`
   sets `pm=0xA11140` (line 63) and falls back to `PM_NONE` (line 97).
   ESP-IDF requires WiFi modem sleep while BLE is active, and it can
   abort with *"Should enable WiFi modem sleep when both WiFi and
   Bluetooth are enabled"*. Test the turtle with BLE active plus a WiFi
   connect, a telemetry POST and an offline-queue drain, and find out
   whether the PM setting has to change while BLE is up. Then measure
   POST latency and queue-drain rate with a phone connected.
3. **Passkey pairing.** Confirm the build supports
   `ble.config(io=DISPLAY_ONLY, mitm=True, bond=True, le_secure=True)`
   and delivers `_IRQ_PASSKEY_ACTION` for display. Confirm a bond
   survives a reboot through `_IRQ_GET_SECRET` / `_IRQ_SET_SECRET`.
4. **Large writes.** Confirm that MTU exchange up to 185 and
   `gatts_set_buffer(handle, 128)` accept a ~100-byte
   WIFI_SET_CREDENTIALS write in one piece.

Deliverable: a short go/no-go note appended to this file, not code.

---

## Phase 1 — groundwork in existing code (no BLE yet)

These changes are needed before BLE can be built cleanly. Each is small
and independently useful.

- **One action layer for OLED and BLE.** Add `src/app/actions.py`. It
  pulls the side-effecting parts of each gesture out of the screens into
  plain functions that return `(result_code, payload)`, using the
  contract's result codes: `journey_start`, `journey_end`,
  `dest_set_here`, `dest_set_mission`, `gps_stamp`, `gps_set_enabled`,
  `telemetry_set_mode`, `wifi_set_enabled`, and so on. The screens call
  these and draw the outcome. The BLE handler calls the same functions
  and notifies the outcome. This is what keeps "every OLED command is in
  the contract" true over time: there is one code path, and the
  mirroring to `PATCH /v1/device` and the `nav_state.json` reset come
  along automatically.
- **Safe config writes.** Add `config.update_config(**changes)`, which
  loads, merges and saves atomically, and move screen writers to it.
  Today `logging.py`, `wifi.py`, `online.py` and `gps.py` save a `cfg`
  dict they loaded earlier, so a BLE write that lands while one of those
  screens is open would be silently overwritten when the screen saves.
- **`device_name`.** `step_api()` persists the API's top-level
  `device_name` (which is `turtles_tb.name`) into config on every online boot, next to the existing
  `mission_destination` persistence. Add the default and fallback to
  `config.py`.
- **GPS fix detail.** Extend `src/nav/gpsfix.py` to cache GGA satellite
  count and fix quality alongside lat/lon/COG. The Position
  characteristic needs both, and the GPS screen can use them too.
- **Config keys.** Add `ble_enabled` (true), `ble_window_min` (10) and
  `ble_require_bond` (true) to `config.py` defaults.
- **Sync scripts.** Exclude `/ble_bonds.json` from the rsync stage of
  all four sync/install scripts, the same way `telemetry_queue.json` is
  excluded.

---

## Phase 2 — peripheral skeleton, wrangle window, Bluetooth screen

Get a turtle advertising under the operator's control, with no data yet.

- **`device/src/net/ble_service.py`.** Owns the radio lifecycle:
  activate/deactivate, advertise (Turtle service UUID) plus scan
  response (`device_name`), and connection tracking. It exposes a
  non-blocking `tick()`.
- **Where `tick()` runs.** Call `ble.tick()` from **`_bg_tick()`**
  (`src/app/main.py`), not only from the top-level loop. Screens own the
  loop while they are showing and call `tick_fn=_bg_tick`, so a tick
  that only runs at the top level would stall whenever a carousel is
  open. Paths that never call `tick_fn` (the bench sweep, the boot
  pipeline, the sleep screen) simply pause BLE work. NimBLE keeps the
  link up on its own task.
- **Build it in `step_init_runtime()`** and pass it to `run()` as a
  kwarg, like `imu` (gotcha 18: inject, don't re-probe). When
  `ble_enabled` is false it is not constructed and the radio is never
  activated.
- **Wrangle window.** Advertise for `ble_window_min` after boot, after
  waking from sleep, and whenever the Bluetooth screen opens. Stop
  advertising when the window closes, but never drop an open
  connection. Re-advertise after a disconnect only while the window is
  still open. `0` means advertise continuously.
- **Sleep.** The sleep screen deactivates BLE before sleeping and
  reopens a window on wake.
- **Bluetooth screen** (`src/ui/screens/bluetooth.py`) in the
  connectivity carousel after WiFi: Online → Logging → WiFi →
  **Bluetooth** → Device. It uses the WiFi screen's skeleton (title,
  right-hand `ToggleSwitch`, double-click flips `ble_enabled`). Body
  states: Off / "Open m:ss" / "Connected". Opening the screen reopens
  the window. Wire it into `flows.py` and `get_screen()`, and add it to
  `_preload_screens()` per the "adding a new screen" checklist.
- **Connection header.** Add a BLE glyph to `glyphs.py`, and show it in
  `connection_header.draw()` while a phone is connected.
- **Validation** with a generic scanner (nRF Connect): the turtle
  appears under its name, advertising stops when the window closes,
  toggling off on the screen removes it, and sleep removes it.

---

## Phase 3 — Turtle service (read + notify)

Wire the Turtle service characteristics in the contract to the runtime
objects that already hold the data. Nothing here computes anything new,
except bearing to target.

| Characteristic | Reads from |
|---|---|
| Contract info | constant `CONTRACT_VERSION = 1` in `ble_service.py` |
| Turtle name | `cfg["device_name"]` (+ fallback) |
| Position | `gpsfix.get()` + the new GGA fields |
| Nav | `NavController.snapshot()`, `heading_source()`, `bearing.initial_bearing()` to `WaypointSequencer.current()`. XTE sends `0x7FFF` until a cross-track function exists |
| Targets | `set_destination`, `mission_destination`, and the sequencer's resolved source |
| Sail | `snapshot()["sail"]`, `NavController.wind_angle()` / the last `WindFinder` result |
| Power | the shared `ina_dev` + `NavController._battery_pct()` |
| IMU / baro | the single `_rt_imu` (`GY87`) instance, never re-probed |
| Status | the `status` dict in `run()`, config, the presence of each runtime object, `TelemetryState.get_queue_size()`, the stamp session count |
| Shore sync / journey | `TelemetryState.get_last_sent()`, `journey.active_id()`, the RTC |

Pack with `struct` using the contract's format strings exactly. Encode
missing data with the contract's sentinels, never with `0`. Notify on
change, capped at the contract's per-characteristic rate. Exact
thresholds are a tuning pass and don't block the phase.

---

## Phase 4 — command/result plumbing and bonding

Build the command channel and its security together, before any real
command exists. That way no command ever ships on an open link.

- **Command and Result characteristics.** The IRQ handler validates
  only framing: at least 2 bytes, otherwise `ERR_BAD_LENGTH` with the
  opcode and seq it could parse. It then **queues** `(opcode, seq,
  payload)`. `ble.tick()` pops the queue and executes. Anything else in
  flight gets `ERR_BUSY`. Every path, including exceptions
  (`ERR_INTERNAL`, logged as `[BLE]`), ends in exactly one final
  result.
- **Dispatch table** from opcode to `(expected_len, handler)` in
  `ble_service.py`, where each handler calls into `src/app/actions.py`.
  A length mismatch returns `ERR_BAD_LENGTH` before the handler runs. An
  unknown opcode returns `ERR_UNKNOWN_OPCODE`.
- **Bonding.** LE Secure Connections, `DisplayOnly`, MITM. Pairing is
  accepted **only while the Bluetooth screen is showing**, and the
  screen draws the 6-digit passkey large. Bonds persist in
  `/ble_bonds.json`. Triple-click on the Bluetooth screen offers
  "Forget phones".
- **Enforcement.** Every command checks the link's encryption/bond
  state and returns `ERR_NOT_BONDED` when needed, unless
  `ble_require_bond` is false (bench development only). Status bit 15
  reports that flag.
- **First commands**, to prove the plumbing: `REBOOT` (0x42) and
  `BLE_SET_ENABLED` (0x33).

---

## Phase 5 — commands: journey, destination, GPS & logging

These are the field-critical commands, and `GPS_STAMP` comes first.

- `GPS_STAMP` (0x20) calls `actions.gps_stamp()`, which is the
  `TelemetryState.send_manual` path. It returns `WRONG_STATE` unless
  `telemetry_mode` is manual, and `NOT_STAMPED` with a reason when no
  payload could be built. On success the payload reports where the
  reading was committed and the session count.
- `TELEMETRY_SET_MODE` (0x22), `TELEMETRY_SET_INTERVAL` (0x23),
  `GPS_SET_ENABLED` (0x21), `API_HANDSHAKE` (0x24, sends
  `IN_PROGRESS` first).
- `JOURNEY_START` / `JOURNEY_END` (0x01/0x02) return
  `ERR_NO_GPS_FIX` / `ERR_RTC_NOT_SYNCED` / `ERR_WRONG_STATE` as the
  contract specifies. `journey.start()` returning `None` maps to
  `ERR_RTC_NOT_SYNCED`.
- `DEST_SET_HERE` (0x03, uses the turtle's own fix), `DEST_SET_MISSION`
  (0x04), `DEST_SET_COORDS` (0x05), `DEST_CLEAR` (0x06).

---

## Phase 6 — commands: sail, connectivity, system

- `NAV_LUFF_SWEEP` (0x10) calls `NavController.begin_luff_sweep()`,
  sends `IN_PROGRESS`, and sends the final result when `sweeping()`
  goes false. It returns `WRONG_STATE` outside ACQUIRE/SAIL_NAV.
- `SERVO_BENCH_SWEEP` (0x11) runs `WindFinder` through `ServoScreen`'s
  raw-PWM callbacks. It **blocks the main loop** for the whole sweep
  (nav deliberately isn't ticked). Send `IN_PROGRESS` *before*
  starting, then the final result. Gate it on `servo_present`.
- `WIFI_SET_ENABLED` (0x30) and `WIFI_SET_CREDENTIALS` (0x31). Save
  first, then connect, and report the connect result separately from
  the save. Deliberately test pushing credentials while WiFi is
  mid-reconnect. This is where the Phase 0 coexistence findings matter
  most.
- `CONNECTION_MODE_SET` (0x32), `COMPASS_SET_OFFSET` (0x43).
- `SLEEP` (0x40) and `SET_TURTLE_MODE` (0x41). Send the OK result,
  give it about 200 ms to go out, then act.

---

## Phase 7 — Device Information service and hardening

- The standard `0x180A` service: manufacturer `Hope Turtles`, model
  `turtleShell` + `platform_tag()`, firmware `VERSION_NUM`
  (`src/app/booter.py`), serial `device_id`.
- **Malformed input.** Fuzz every opcode with short, long and
  out-of-range payloads. Each must produce a result code and none may
  crash the handler.
- **Reconnection.** Drop and restore the link mid-command. After
  reconnecting, a read of Result must return the last result.
- **Coexistence under real load.** Re-run the Phase 0 test with the
  full characteristic set notifying and a phone actively commanding.
- **Docs.** Fold `ble_service.py`, `actions.py`, the Bluetooth screen,
  the new config keys and `/ble_bonds.json` into `CLAUDE.md`
  (repository layout, config table, connectivity carousel, a gotcha
  for the IRQ-queues-only rule).

---

## Out of scope for this plan

- **Turtle-to-turtle BLE.** That's the bale mesh vision in
  `docs/bale_network_vision.md`: a different radio problem at a
  different range.
- **Over-the-air firmware updates via BLE.** The `mpremote` / sync
  script path already works.
- **Commands over WiFi.** The app's planned "turtles at sea" view
  ([02_android_app.md](02_android_app.md), Phase 8) is **read-only via
  hopeturtles.org**. The turtle still only makes outbound requests and
  listens for nothing. Remote commands would mean relaying them through
  the telemetry response, which is a separate design.
- Any change to the WiFi telemetry pipeline. Wrangler is additive.
