# Turtle Wrangler — development plan

Turtle Wrangler is an Android app that talks to a turtle directly over
Bluetooth Low Energy (BLE) from a few metres away — a full-screen
replacement for standing over the hull clicking through the OLED carousel
(`device/src/ui/flows.py`) one gesture at a time. It does **not** replace
the shore telemetry pipeline to `hopeturtles.org`; that keeps running
exactly as it does today. Wrangler is a near-field field-ops and
diagnostics tool, not a remote-control link — the turtle still closes its
own navigation loop onboard regardless of whether a phone is anywhere
nearby.

For the non-technical, stakeholder-facing version of this pitch (why it's
worth building, what it looks like, no acronyms), see the proposal
circulated separately to the team. This folder is the engineering plan
that sits underneath it.

The work splits into two independent streams that meet at one shared
contract:

- **[01_firmware_ble.md](01_firmware_ble.md)** — the turtleOS side: a new
  BLE peripheral role on the XIAO ESP32-S3, built in phases alongside the
  existing WiFi telemetry client rather than replacing anything.
- **[02_android_app.md](02_android_app.md)** — the Wrangler app itself:
  scan, connect and pair, then four bottom tabs (Dashboard, Navigate,
  GPS, Diagnostics) with Settings behind a gear icon in the top-right
  corner, plus a planned "turtles at sea" view behind a
  Buwana login.

A third, smaller stream is **planned but not scheduled**: a user-facing
read-only API on hopeturtles.org (and a Wrangler app registration on
Buwana), so the app can show turtles that are out of Bluetooth range but
reporting over WiFi. See [Turtles at sea](#turtles-at-sea-shore-data-via-buwana-login--planned)
below and Phase 8 of the app plan.

Both streams build against the GATT contract below. **Treat it as the
interface boundary** — either side can iterate on its own implementation
freely as long as the wire format doesn't drift out from under the other.
If a characteristic's shape needs to change, update it here first.

---

## The GATT contract — v1

**Contract version: 1.** Every rule in this section is part of v1. Rules
for how the version changes are under [Versioning](#versioning).

### Wire conventions

- **UUIDs.** One custom 128-bit base, randomly generated:
  `26d0XXXX-5890-45f2-b0be-090d35436a95`. Each service and characteristic
  replaces `XXXX` with the 16-bit short ID in the tables below. For
  example, short ID `0101` is `26d00101-5890-45f2-b0be-090d35436a95`.
  (The previous draft's `…-0000-1000-8000-00805f9b34fb` base is the
  Bluetooth SIG base UUID. That base is reserved for SIG-assigned UUIDs
  and must not be used for custom ones.)
- **Byte order and packing.** All multi-byte fields are little-endian and
  packed with no padding. That matches MicroPython `struct` with a `<`
  format string, which is what every format below is written in. Android
  must use `ByteBuffer.order(ByteOrder.LITTLE_ENDIAN)`.
- **Scaled integers.** `×10` means tenths (a heading of 247.3° is sent as
  `2473`). `_e7` means degrees × 10⁷ as `int32`, which gives about 1 cm
  resolution with no floats on the wire.
- **"No data" sentinels.** Many values are routinely missing: no GPS fix,
  no IMU, no AS5600, RTC not synced. Every field that can be missing has a
  sentinel, and the app must render the sentinel as "—" and never as a
  number.

  | Field type | Sentinel |
  |---|---|
  | `int32` coordinate (`_e7`) | `0x7FFFFFFF` |
  | `uint16` | `0xFFFF` |
  | `int16` | `0x7FFF` |
  | `uint32` | `0xFFFFFFFF` (epoch fields use `0` = never, noted per field) |
  | `uint8` percent/count/enum | `0xFF` |

- **Forward-compatible lengths.** Within v1, firmware may **append**
  fields to the end of any characteristic. The app must accept a value
  longer than it expects and ignore the extra bytes. It must reject a
  value **shorter** than the v1 length.
- **Size.** Every read and notify value is ≤ 20 bytes, so it fits the
  default 23-byte ATT MTU. Only one command (`WIFI_SET_CREDENTIALS`)
  needs a larger MTU. See [Command protocol](#command-protocol).

### Advertising and identity

- **Advertising packet:** flags plus the complete list of 128-bit service
  UUIDs, containing only the Turtle service (`0001`). That takes 21 of
  the 31 available bytes. The app's scanner filters on this UUID.
- **Scan response:** complete local name = **`device_name`**, truncated to
  29 bytes UTF-8. The full name is always readable from the Turtle name
  characteristic (`0102`).
- **`device_name`** is a new config key. On every online boot,
  `step_api()` persists it from the top-level `device_name` field of
  `GET /v1/device`. That field is the human-readable turtle name,
  `turtles_tb.name` (`VARCHAR(50) NOT NULL`), which
  `deviceApiController.js:385` sends as `device_name: info.name`. The
  key name `device_name` is inherited from the airOS API shape (there is
  no `device_name` column). The config key uses the same name, so the
  API field, the config key and the Device screen all say `device_name`.
  This works the same way `mission_destination` is persisted today. Today the name
  is fetched for the Device screen and then thrown away, so an offline
  boot has no name to advertise. When `device_name` is empty (a device
  that has never been online), firmware advertises `Turtle-` plus the
  last 4 characters of `device_id`.

### Turtle service — `0001` (advertised; read + notify)

All characteristics are readable. Those marked **N** also notify.
Notifications are sent on change, and at most at the rate shown. The app
reads everything once on connect, then relies on notifications.

| Short ID | Name | Props | Format | Bytes | Max rate |
|---|---|---|---|---|---|
| `0101` | Contract info | R | `<BB>` | 2 | — |
| `0102` | Turtle name | R | UTF-8, ≤ 64 bytes | ≤ 64 | — |
| `0110` | Position | R N | `<iiBBH>` | 12 | 1 Hz (one per GPS epoch) |
| `0111` | Nav | R N | `<HHIhBBB>` | 13 | 1 Hz |
| `0112` | Targets | R N | `<iiiiB>` | 17 | on change |
| `0113` | Sail | R N | `<HHBBHB>` | 9 (6 before 2026-10-05) | 2 Hz while sweeping, else 1 Hz |
| `0114` | Power | R N | `<HhB>` | 5 | 0.2 Hz, or immediately on ≥ 1 % SoC change |
| `0115` | IMU / baro | R N | `<hhH>` | 6 | 1 Hz |
| `0116` | Status | R N | `<IBBHHH>` | 12 | on change |
| `0117` | Shore sync / journey | R N | `<IIIIB>` | 17 (12 before 2026-10-08) | on change, + 1/min for the clock |
| `0118` | Environment *(added 2026-10-04)* | R N | `<hHhhH>` | 10 | 0.2 Hz, on change |

**`0101` Contract info** — `contract_version u8`, `turtle_mode u8`
- `contract_version` = **1**. The app reads this before anything else.
  See [Versioning](#versioning).
- `turtle_mode`: 1 = turtleOS. BLE is turtleOS-only in v1, so this is
  always 1. The field is there so that an airOS BLE service added later
  doesn't need a new version.

**`0110` Position** — `lat_e7 i32`, `lon_e7 i32`, `sats u8`,
`fix_quality u8`, `fix_age_s u16`
- Source: `src/nav/gpsfix.py`. **Firmware work:** `gpsfix.update()` has
  to start caching the GGA satellite count and fix quality. It stores
  only lat/lon/COG today.
- `fix_quality`: the GGA field 6 value (0 = none, 1 = GPS, 2 = DGPS, …),
  or `0xFF` if unknown.
- `fix_age_s`: seconds since the cached fix (`gpsfix.get()` already
  returns the age), or `0xFFFF` if there has never been a fix. The app
  should grey out a position older than about 10 s.

**`0111` Nav** — `heading_x10 u16`, `bearing_to_target_x10 u16`,
`dist_to_target_m u32`, `xte_m i16`, `nav_state u8`, `fault u8`,
`trim u8`
- Source: `NavController.snapshot()` and `heading_source()`.
- `bearing_to_target` and `dist_to_target_m` are computed from the cached
  fix to `WaypointSequencer.current()` (`bearing.initial_bearing` /
  `distance_m`). `snapshot()` already computes the distance.
- `xte_m`: cross-track error in metres, where + means right of track.
  **Nothing computes XTE today.** Firmware sends `0x7FFF` until a
  cross-track function is added to `bearing.py`. The app shows "—".
- `nav_state`: 0 BOOT · 1 ACQUIRE · 2 SAIL_NAV · 3 ARRIVAL · 4 SAFE
  (`src/nav/state_machine.py`).
- `fault` (reason for SAFE): 0 none · 1 GPS never acquired · 2 GPS lost ·
  3 no waypoints configured · `0xFF` other (see the serial log).
- `trim`: 0 CRUISE · 1 FEATHER (low battery) · `0xFF` n/a.

**`0112` Targets** — `set_lat_e7 i32`, `set_lon_e7 i32`,
`mission_lat_e7 i32`, `mission_lon_e7 i32`, `active_source u8`
- `set_destination` and `mission_destination` from config. The sentinel
  means that target is not set.
- `active_source` is what `WaypointSequencer` resolved to:
  0 none · 1 `set_waypoints` · 2 `set_destination` ·
  3 `mission_waypoints` · 4 `mission_destination`.
- This lets the Navigate screen show both targets and mark the active one
  without a separate command.

**`0113` Sail** — `sail_angle_x10 u16`, `wind_angle_x10 u16`,
`sweep_state u8`, `confidence_pct u8`, then *(appended within v1,
2026-10-05; older firmware sends only the first 6 bytes)*
`servo_pos_x10 u16`, `manual_hold_s u8`
- `sail_angle`: AS5600 angle via `snapshot()["sail"]`.
- `wind_angle`: the latest result from whichever sweep ran last (nav
  sweep: `NavController.wind_angle()`; bench sweep: the `WindFinder`
  result).
- `sweep_state`: 0 idle · 1 nav luff sweep running · 2 servo bench sweep
  running.
- `confidence_pct`: comes from the bench sweep's jitter score. The nav
  sweep (`luff.py`) produces no confidence, so it reports `0xFF`.
- `servo_pos`: the last servo position **commanded**, by the
  autopilot or by hand, on the `SERVO_SET_ANGLE` scale: 0–180 spans the
  servo's full travel, `servo_min_us` → `servo_max_us`.
  `NavController.servo_position()`; `0xFFFF` until the servo has been
  written. It is the command, not a measurement; `sail_angle` is the
  measurement.
- `manual_hold_s`: seconds of hand control left (0 = the autopilot
  owns the servo). `NavController.manual_hold_s()`, capped at 254.

**`0114` Power** — `voltage_mV u16`, `current_mA i16`, `soc_pct u8`
- Source: the shared `ina_dev` (INA219). `soc_pct` is the estimate from
  `NavController._battery_pct()` and must not be computed a second time.
  `current_mA` is the INA219's raw reading: **negative = charging**
  (current flowing into the pack), positive = discharging. That's the
  same sign the OLED's battery overlay interprets (`turtle_waiting.py`).

**`0115` IMU / baro** — `pitch_x10 i16`, `roll_x10 i16`,
`pressure_hPa_x10 u16`
- Source: the single `_rt_imu` (`GY87`) instance. It is never re-probed
  (gotcha 18).

**`0116` Status** — `flags u32`, `telemetry_mode u8`,
`connection_mode u8`, `telemetry_interval_s u16`, `queue_count u16`,
`stamps_session u16`

| Bit | Flag | Bit | Flag |
|---|---|---|---|
| 0 | wifi_connected | 11 | as5600_present |
| 1 | api_ok | 12 | servo_present (config) |
| 2 | gps_fixed | 13 | rtc_synced (epoch is valid) |
| 3 | journey_active | 14 | link_bonded (this connection is encrypted and bonded) |
| 4 | wifi_enabled | 15 | ble_require_bond (config) |
| 5 | gps_enabled | 16 | command_in_progress |
| 6 | gps_hw_present | 17 | wifi_credentials_set |
| 7 | imu_present | 18 | rtc_battery_fault *(2026-10-08)* |
| 8 | mag_present | 19 | secure_mode *(2026-10-08)* |
| 9 | ina219_present | 20 | secure_mode_blocked *(2026-10-08)* |
| 10 | baro_present | 21–31 | reserved, sent as 0 |

- `telemetry_mode`: 0 off · 1 auto · 2 manual (the authoritative config
  key).
- `connection_mode`: 0 `wifi_auto` · 1 `wifi_manual` · 2 `lora`.
- `queue_count`: unsent readings in the flash queue. This is the same
  number the Online screen shows.
- `stamps_session`: manual stamps taken this boot. This is the same
  number as the GPS screen's `Logged: N`.
- The presence bits drive the app's sensor-health checklist.
- Bits 18–20 are explained in [Clock and secure mode](#clock-and-secure-mode).
  Older firmware sends them as 0.

**`0117` Shore sync / journey** — `last_shore_sync u32`,
`journey_id u32`, `device_now u32`, then *(appended within v1,
2026-10-08; older firmware sends only the first 12 bytes)*
`device_clock u32`, `clock_source u8`
- `last_shore_sync`: unix seconds of the last successful POST
  (`TelemetryState.get_last_sent()`). 0 means never.
- `journey_id`: the open journey (`journey.active_id()`), which is also
  its start time. 0 means no journey is open.
- `device_now`: the turtle's RTC in unix seconds, or 0 when not synced.
  The app can show clock drift against the phone's clock.
- `device_clock`: the same system clock **whatever it says**, even when
  it isn't synced (year before 2020, e.g. a DS3231 whose coin cell is
  flat restarts at 2000-01-01). Lets the app show a wrong clock instead
  of nothing. 0 only when the clock can't be read. `rtc_synced` (Status
  bit 13) still says whether it is trusted; fix it with `TIME_SET`.
- `clock_source`: what last set the clock this boot. 0 not set · 1 RTC
  chip (DS3231) · 2 NTP · 3 GPS · 4 phone (`TIME_SET`). See
  [Clock and secure mode](#clock-and-secure-mode).

**`0118` Environment** — `aht_temp_x10 i16`, `aht_rh_x10 u16`,
`bmp_temp_x10 i16`, `rtc_temp_x10 i16`, `pressure_hPa_x10 u16`
- Conditions **inside the control bottle**, for spotting overheating and
  (through humidity) a leak.
- **AHT21:** air temperature and relative humidity, the headline
  "bottle" reading, via `AirSensor.aht_now()`.
- **BMP180:** temperature and pressure, from the shared `GY87`. Pressure
  is also in `0115`, repeated here so this one characteristic is the
  whole picture.
- **DS3231:** the RTC's die temperature, i.e. the board temperature,
  from `run()`'s `rtc` dict (refreshed about every 70 s).
- Each missing sensor reads as its sentinel.
- **Added within v1** (a new characteristic is not a breaking change).
  An app must treat it as optional: a turtle on older firmware doesn't
  have it.

### Command protocol

The earlier draft had one write characteristic per command. v1 replaces
that with **one command characteristic and one result characteristic**.
The main reason is that failures must come back to the app. A write
acknowledgement only means "bytes received", not "journey started".
With this design every command, current and future, reports its outcome
the same way, and adding a command doesn't add a characteristic.

#### Command service — `0002`

| Short ID | Name | Props | Format |
|---|---|---|---|
| `0201` | Command | Write (with response) | `<BB>` `opcode, seq` + payload |
| `0202` | Result | R N | `<BBB>` `opcode, seq, code` + payload |

**Rules:**
1. The app subscribes to Result **before** writing any command.
2. `seq` is an app-chosen `u8` that increments per command. Firmware
   echoes it, so the app matches each result to the command that caused
   it.
3. **Every command produces exactly one final result.** A command that
   takes time (a sweep, a WiFi connect, an API handshake) first sends
   `IN_PROGRESS` with the same `seq`, then its final result. For
   everything else the first result is the final one.
4. **Only one command runs at a time.** A command that arrives while
   another is in progress gets `ERR_BUSY` immediately. Status bit 16
   shows that a command is running.
5. The BLE IRQ handler only **queues** the command. The command executes
   in `ble.tick()` on the main loop, never in IRQ context. File I/O and
   `save_config()` from scheduler context is not safe alongside the
   telemetry `_thread`.
6. The app times out a command it has heard nothing about in 5 s. Once
   `IN_PROGRESS` has been received, the timeout is 180 s (a bench sweep
   can take minutes).
7. Result is also readable: a read returns the last result, so the app
   can recover after a reconnect.
8. Firmware sizes the Command attribute buffer at 128 bytes
   (`gatts_set_buffer`). Firmware accepts an MTU exchange up to 185, and
   the app requests 185 on connect.

#### Result codes

| Code | Name | Meaning / what the app tells the user |
|---|---|---|
| `0x00` | OK | Done. |
| `0x01` | IN_PROGRESS | Accepted and running; a final result will follow. |
| `0x10` | ERR_UNKNOWN_OPCODE | The firmware doesn't know this command (the app is newer than the firmware). |
| `0x11` | ERR_BAD_LENGTH | The payload is the wrong size for this opcode. |
| `0x12` | ERR_BAD_VALUE | A value is out of range (e.g. interval < 10 s, lat > 90). |
| `0x13` | ERR_BUSY | Another command is running. |
| `0x14` | ERR_NOT_BONDED | The link isn't bonded. Pair from the turtle's Bluetooth screen. |
| `0x15` | ERR_WRONG_STATE | Not valid right now; the payload byte says why (see per-opcode notes). |
| `0x20` | ERR_NO_GPS_FIX | The turtle has no GPS fix. |
| `0x21` | ERR_RTC_NOT_SYNCED | The turtle's clock isn't set, so no valid timestamp. |
| `0x22` | ERR_NO_HARDWARE | Required hardware is absent (servo, GPS module, IMU). |
| `0x23` | ERR_NO_TARGET | No mission target is stored on the turtle. |
| `0x30` | ERR_WIFI_FAILED | The WiFi connect attempt failed. |
| `0x31` | ERR_API_FAILED | The API handshake failed. |
| `0x32` | ERR_NOT_STAMPED | No telemetry payload could be built; the payload byte gives the reason. |
| `0x7F` | ERR_INTERNAL | An exception was caught. Firmware logs `[BLE]` details to serial. |

Unknown codes: the app treats any code ≥ `0x10` it doesn't recognise as a
generic failure and shows the hex value.

#### Opcodes

"OLED" names the gesture a command mirrors. **App-only** commands have no
OLED equivalent. Unless a note says otherwise, the result payload is
empty.

**Journey & destination**

| Op | Name | Payload | OLED equivalent | Result |
|---|---|---|---|---|
| `0x01` | JOURNEY_START | — | Journey screen, double-click (off → on) | OK `<I>` journey_id · NO_GPS_FIX · RTC_NOT_SYNCED · WRONG_STATE (already open) |
| `0x02` | JOURNEY_END | — | Journey screen, double-click (on → off) | OK `<B>` arrival_stamped (0 if there was no fix; the journey still ends) · WRONG_STATE (none open) |
| `0x03` | DEST_SET_HERE | — | Destination menu, 1× "target = here" | OK `<ii>` stamped lat/lon · NO_GPS_FIX |
| `0x04` | DEST_SET_MISSION | — | Destination menu, 2× "target = mission" | OK · NO_TARGET |
| `0x05` | DEST_SET_COORDS | `<ii>` lat_e7, lon_e7 | **App-only** (map picker) | OK · BAD_VALUE |
| `0x06` | DEST_CLEAR | — | **App-only** | OK |

- **"Here" always means the turtle's own GPS fix, never the phone's
  location.** DEST_SET_HERE and JOURNEY_START/END take no coordinates
  from the app. The UI must say this plainly, e.g. "Set to the turtle's
  current position".
- All destination opcodes go through the same function the Destination
  screen uses. That function writes `set_destination` (+ `SET` /
  `User Set` names), which triggers the `PATCH /v1/device` mirror and the
  `nav_state.json` route-signature reset. BLE handlers must not write
  config keys directly.
- The OLED hand-off from DEST_SET_HERE to the Journey screen has no BLE
  equivalent. The app shows a "Start journey?" prompt instead.

**Sail**

| Op | Name | Payload | OLED equivalent | Result |
|---|---|---|---|---|
| `0x10` | NAV_LUFF_SWEEP | — | Machine-state screen, double-click (`NavController.begin_luff_sweep()`) | IN_PROGRESS → OK `<H>` wind_x10 (`0xFFFF` = the sweep ended without a wind angle) · WRONG_STATE `<B>` nav_state (only valid in ACQUIRE / SAIL_NAV) · BUSY (sweep already running) |
| `0x11` | SERVO_BENCH_SWEEP | — | Servo screen, double-click (`WindFinder` full luff sweep) | IN_PROGRESS → OK `<HHB>` wind_x10, alt_wind_x10, confidence_pct (WindFinder's jitter ratio vs calm: 1× = 0 %, ≥ 5× = 100 %) · NO_HARDWARE (`servo_present` false, or no AS5600) |
| `0x12` | SERVO_SET_ANGLE *(added 2026-10-05)* | `<B>` 0–180 | **App-only** (Navigate servo dial) | OK `<H>` pulse written (µs) · BAD_VALUE (> 180) · BUSY (a luff sweep is moving the servo) · NO_HARDWARE (no NavController or `servo_present` false) |

`SERVO_SET_ANGLE` maps 0–180 linearly onto `servo_min_us` →
`servo_max_us`, the servo's own end stops (config), **not** onto the
autopilot's `sail_min_deg` / `sail_max_deg` limits. Each command pauses
the autopilot's servo writes (steering, feathering, the scheduled
re-sweep) for 60 s (`actions.SERVO_MANUAL_HOLD_MS`). After that,
navigation takes the servo back on its own. The app sends one command
at a time while the dial is dragged, always with the latest angle.

- The two sweeps are different operations and both exist on the OLED.
  NAV_LUFF_SWEEP is the non-blocking autonomous sweep and is what the app
  should normally offer. SERVO_BENCH_SWEEP is the bench/diagnostic
  sweep. It **blocks the main loop** for its whole run (it owns the servo,
  and nav is deliberately not ticked). During that time no notifications
  go out, but NimBLE keeps the link alive. The app should put up a
  modal "Bench sweep running…" and wait for the final result.

**GPS & logging**

| Op | Name | Payload | OLED equivalent | Result |
|---|---|---|---|---|
| `0x20` | GPS_STAMP | — | GPS screen, single-click in manual mode (`TelemetryState.send_manual`) | OK `<BHB>` committed_to (0 flash queue · 1 background sender), stamps_session, has_position (0 = recorded without a fix, sensor values and time only) · WRONG_STATE (`telemetry_mode` isn't manual) · NOT_STAMPED `<B>` reason (1 RTC not synced · 2 GPS disabled and no sensor values · 3 no fix and no sensor values · `0xFF` other, e.g. a sensor sample in flight) |
| `0x21` | GPS_SET_ENABLED | `<B>` 0/1 | GPS screen, double-click (auto/off) or triple-click (manual) | OK · NO_HARDWARE |
| `0x22` | TELEMETRY_SET_MODE | `<B>` 0 off · 1 auto · 2 manual | Logging screen, double-click (cycles off → auto → manual) | OK · BAD_VALUE |
| `0x23` | TELEMETRY_SET_INTERVAL | `<H>` seconds | **App-only** (config key, no OLED writer) | OK · BAD_VALUE (< 10) |
| `0x24` | API_HANDSHAKE | — | Online screen, double-click on (fresh API handshake) | IN_PROGRESS → OK · API_FAILED · WRONG_STATE (WiFi down) |

- GPS_STAMP is the **most important command in the contract**. It behaves
  exactly like the OLED stamp: it commits the payload before returning
  and never blocks on the network. A stamp taken without a fix is still
  a valid record when the turtle has sensor values to save;
  `has_position = 0` tells the app to show "Stamped — no position" instead
  of implying a GPS point was logged. A later shore delivery shows up as
  `queue_count` falling and `last_shore_sync` advancing in the
  notifications. No second result is sent.
- The Online screen's on/off toggle is covered by TELEMETRY_SET_MODE (it
  writes `auto`/`off`) plus API_HANDSHAKE (the handshake it kicks off).
  The app's GPS screen needs both the mode switch and the stamp button,
  so switching to manual and stamping never requires the OLED.

**Connectivity**

| Op | Name | Payload | OLED equivalent | Result |
|---|---|---|---|---|
| `0x30` | WIFI_SET_ENABLED | `<B>` 0/1 | WiFi screen, double-click | on: IN_PROGRESS → OK once the turtle has an IP · WIFI_FAILED after 25 s; off: OK |
| `0x31` | WIFI_SET_CREDENTIALS | `<B>` ssid_len, ssid bytes, password bytes (rest of the payload) | **App-only** | IN_PROGRESS → OK · WIFI_FAILED (saved, but the connect failed) · BAD_LENGTH (SSID > 32 or password > 63 bytes, or the MTU is too small) |
| `0x32` | CONNECTION_MODE_SET | `<B>` 0 wifi_auto · 1 wifi_manual | **App-only** | OK · BAD_VALUE (2 = lora is reserved) |
| `0x33` | BLE_SET_ENABLED | `<B>` 0 | Bluetooth screen, double-click | OK, then firmware disconnects and stops the radio |

- WIFI_SET_CREDENTIALS replaces the earlier SSID/password/apply triple.
  It saves both values atomically, then reconnects if `wifi_enabled`.
  Saving the credentials never depends on the connect succeeding. The
  connect result is reported separately from the save.
- BLE_SET_ENABLED only accepts 0. Once Bluetooth is off, no phone can
  turn it back on; only the Bluetooth screen can (see
  [Wrangle window](#wrangle-window-and-bluetooth-config)).

**System**

| Op | Name | Payload | OLED equivalent | Result |
|---|---|---|---|---|
| `0x40` | SLEEP | — | Hold flow → Sleep screen, double-click | OK, then the turtle sleeps and BLE shuts down |
| `0x41` | SET_TURTLE_MODE | `<B>` 0 = airOS | Quint-click on the waiting screen / triple-click on the Version screen | `0`: OK, then the turtle reboots into airOS (no BLE service) · `1`: OK, nothing to do (already turtleOS) |
| `0x42` | REBOOT | — | **App-only** (the OLED equivalent is a power cycle) | OK, then `machine.reset()` |
| `0x43` | COMPASS_SET_OFFSET | `<h>` degrees, −180…180 | **App-only** (`compass_offset_deg` has no OLED writer) | OK · BAD_VALUE |
| `0x44` | TIME_SET *(added 2026-10-08)* | `<I>` unix seconds, UTC | **App-only** (Diagnostics "Set turtle clock to phone time") | OK `<B>` rtc_chip_written (1 = the DS3231 was set too · 0 = system clock only, lost at the next boot) · BAD_VALUE (before 2020 or after 2099) |
| `0x45` | SECURE_MODE_SET *(added 2026-10-08)* | `<B>` 0 off · 1 on | **App-only** (Diagnostics "Secure mode") | OK · BAD_VALUE (> 1) · WRONG_STATE `<B>` 1 (turning on refused: RTC battery fault, or no RTC chip) |

- The app confirms 0x40–0x42 with the user before sending, because each
  one ends the connection.
- TIME_SET is for a turtle whose clock was never synced (no WiFi for NTP
  or the API, and a DS3231 that lost power), which otherwise refuses
  every stamp and journey with RTC_NOT_SYNCED. It sets `machine.RTC()`
  immediately and writes the DS3231 (clearing its oscillator-stop flag)
  so the time survives a reboot while the board stays powered, or
  always once the coin cell is replaced. The time comes from the phone's
  clock; a later NTP or API sync simply overwrites it.
- SECURE_MODE_SET saves `secure_mode` to config and takes effect at
  once. Turning it **off** always works over a bonded link.

**Deliberately not in the contract:**
- Carousel navigation (single-click advance, hold to enter the flow).
  These move between OLED screens; the app has its own tabs.
- The self-destruct / quad-click screen. It is a `joke_mode` animation
  and changes nothing.
- `timezone_offset_min`. It is written only from the server during the
  boot API step, never by a gesture.
- The Destination menu's "3× cancel" is UI-only.

### Security: bonding with an on-screen passkey

The command service can redirect a turtle, turn its radio off, or hand it
WiFi credentials, so **every command requires a bonded, encrypted
link**. Reads and notifications stay open, because telemetry is harmless
and useful to any nearby phone.

- **Pairing method:** LE Secure Connections with MITM protection. The
  turtle's IO capability is `DisplayOnly`: it shows a 6-digit passkey on
  the OLED and the phone's user types it in. Seeing the passkey requires
  standing at the hull, which is exactly the trust boundary wanted.
- **Pairing is only accepted while the Bluetooth screen is showing.**
  The passkey is drawn there, so no other screen needs a pairing
  overlay. A pairing attempt at any other time is rejected. The app then
  tells the user: "Open the Bluetooth screen on the turtle (triple-click,
  then single-click three times) to pair".
- **Bond storage:** `/ble_bonds.json`, device-owned like
  `journey_state.json`. The sync/install scripts must exclude it, or a
  re-upload would un-pair every phone. Firmware implements
  `_IRQ_GET_SECRET` / `_IRQ_SET_SECRET` against it (or uses aioble's
  `security` module).
- **An unbonded command** gets `ERR_NOT_BONDED`. Firmware checks this
  itself rather than relying on attribute permissions, so the app always
  gets a readable reason.
- **`ble_require_bond`** (config, default `true`) can be set to `false`
  for bench development before bonding is implemented. Status bit 15
  reports it, and the app shows a warning banner when it's off. It must
  never be `false` on a deployed turtle.

Because WiFi credentials depend on bonding, **bonding moves ahead of the
WiFi commands in the firmware build order**. It is no longer a Phase 6
hardening item. See 01_firmware_ble.md.

### Wrangle window and Bluetooth config

Bluetooth is off, or only briefly discoverable, unless someone at the
hull asks for it. Three new config keys:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ble_enabled` | bool | `true` | Master switch. `false` means the radio is **never activated**: no advertising, no connections, no power draw. This is the setting for a real mission voyage. |
| `ble_window_min` | int | `10` | After boot, after waking from sleep, and each time the Bluetooth screen is opened, the turtle advertises for this many minutes, then stops. `0` means advertise continuously while enabled. |
| `ble_require_bond` | bool | `true` | See [Security](#security-bonding-with-an-on-screen-passkey). |

- **Closing the window never drops an open connection.** It only stops
  advertising. After a disconnect, advertising resumes only if the
  window is still open.
- **Sleep shuts BLE down** completely (`BLE().active(False)`). Waking
  reopens a fresh window.
- **Bluetooth screen:** new, in the connectivity carousel after WiFi:
  Online → Logging → WiFi → **Bluetooth** → Device. It uses the same
  skeleton as the WiFi screen (title, right-hand toggle, double-click
  flips `ble_enabled`). Body text:
  - off: "Off"
  - on and advertising: "Open 9:42" (countdown)
  - on with a phone connected: "Connected" plus a bonded marker
  - pairing: the 6-digit passkey, large
  - Opening the screen reopens the window, since the operator standing
    there is the intent.
  - Triple-click: "Forget phones" (clears `/ble_bonds.json`) behind a
    confirm.
- **Connection header:** a BLE glyph is shown in the icon cluster while a
  phone is connected, so someone at the hull can tell.
- **Mission lockdown:** set `ble_enabled: false` on the Bluetooth screen,
  or from the app with BLE_SET_ENABLED 0, before release. The only way
  back is the button, which is intended.

### Clock and secure mode

Every record, stamp and journey is timed by the turtle's clock, so the
firmware cares where that time came from. One config key *(added
2026-10-08, turtleOS 2.5.1)*:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `secure_mode` | bool | `false` | For missions where GPS may be spoofed. The turtle never takes its time from GPS. Further precautions will hang off this key later. It **can't turn on while the RTC battery is faulty** (see below). |

**Where the time comes from at boot,** in order:
1. **The RTC chip (DS3231).** Accepted when it reads 2020 or later.
2. **NTP,** when WiFi is up and the chip's time is missing or its
   battery is faulty.
3. **GPS,** when neither gave a trustworthy time and `secure_mode` is
   off: the first valid GPS fix (`$G?RMC` status `A`, good checksum,
   2026–2099, since a receiver's week-number rollover lands earlier) sets the system clock and writes the DS3231. It happens
   once per boot, whenever the fix arrives, not during the boot screen.
4. **The phone** (`TIME_SET`), at any time.

`clock_source` in Shore says which one it was.

**RTC battery fault** (Status bit 18). The DS3231's oscillator-stop
flag (OSF) means the chip lost all power, i.e. its coin cell is flat or
missing. The firmware logs `[RTC] battery fault` and the boot screen
shows `[BAT LOW]`. Setting the time clears OSF, so the fault is also
kept in a flash marker (`/rtc_battery_fault`). The marker clears only on
a **power-on** boot where OSF is clear and the chip still holds a valid
time, which proves the coin cell kept it through a power-off. After a
new coin cell: set the clock, then power-cycle the turtle once. With no
DS3231 detected, the battery counts as faulty too.

**Secure mode** (Status bit 19 = on and in force). If config asks for
`secure_mode` but the RTC battery is faulty, secure mode is **blocked**
(bit 20): it doesn't count as on, but the turtle still refuses GPS time
(it fails closed), and the app tells the user to replace the coin cell.
On a real mission the RTC has to be sound, because with secure mode on
it is the only time source at sea.

### Device Information service — `0x180A` (standard, read-only)

This is the standard Bluetooth SIG service, so any generic scanner can
read it without knowing turtleOS:
- Manufacturer Name String: `Hope Turtles`
- Model Number String: `turtleShell` (with `platform_tag()` appended)
- Firmware Revision String: `VERSION_NUM` from `src/app/booter.py` (e.g.
  `2.5.1`)
- Serial Number String: `device_id`

### Versioning

- The app reads `0101` first. If `contract_version` is **higher** than
  the app understands, the app shows the turtle read-only (it can still
  decode v1 prefixes of every value) and disables commands, with an
  "Update Wrangler" notice. If the version is **lower**, the app hides
  features the turtle can't do.
- **Bump the version only for breaking changes:** a field removed,
  resized or reinterpreted; an opcode's payload or meaning changed; a
  UUID moved. Appending a field or adding a new opcode or result code is
  **not** breaking. Old apps ignore trailing bytes, and old firmware
  answers new opcodes with `ERR_UNKNOWN_OPCODE`.
- Any change goes into this README first, with the version it lands in.

### Turtles at sea: shore data via Buwana login — planned

This is not part of the BLE contract. It is recorded here so both
streams leave room for it.

When the app can't find a turtle over Bluetooth, the "No turtles nearby"
screen asks:

> Sorry, we can't find any turtles to connect to via Bluetooth :-(
> Would you like to login and connect to your other turtles that are
> swimming in WiFi?

**Why this needs new server work.** Every `/v1` route on
hopeturtles.org is behind `deviceAuth` (`routes/api/v1/index.js:15`).
It authenticates with a turtle's own `X-Device-Id` / `X-Device-Key`,
so the API only lets a *turtle* talk about itself. There is no route a
*person* can use to read a turtle's data from an app. Shipping device
keys inside the app would let anyone with the APK post telemetry as
that turtle, so that is not an option.

**The plan (app Phase 8):**
- **Login:** the user logs in with their **Buwana** account through
  OIDC with PKCE. This is the same identity provider the hopeturtles.org
  website uses, with Wrangler registered as its own Buwana app.
- **Which turtles:** the user sees the turtles they manage
  (`turtles_tb.buwana_id`), or all turtles for admins. This is the same
  access rule as the website's `my-turtle` page.
- **Server API:** a new read-only API at `/api/app/v1/...` verifies the
  Buwana token and serves turtle lists, latest readings and tracks.
- **Read-only:** shore data is view-only. A turtle at sea listens for
  nothing inbound, so commands stay Bluetooth-only. Shore data is
  always labelled "via hopeturtles.org · last report HH:MM" so it can't
  be mistaken for a live Bluetooth reading.

**Until then (v1):** the app caches the last Bluetooth snapshot of each
turtle it has connected to and shows it as "last seen HH:MM". The Log in
button on the "No turtles nearby" screen is present but marked "coming
soon".

---

## Visual identity

Wrangler's palette and type are pulled directly from `hopeturtles.org`
(`public/css/main.css`'s `:root` block and `views/partials/header.ejs`'s
font `<link>`), not invented fresh — the app should read as the same
project as the shore dashboard, not a separate product. `wrangler.html`
in this folder is the reference implementation; treat it as the source of
truth once Part 2 gets to a real Android theme, not this table in
isolation.

| Role | Token | Hex | Notes |
|---|---|---|---|
| Primary | `--color-primary` | `#017919` | Buttons, links, active states |
| Accent | `--color-accent` | `#23b053` | Secondary green, lighter emphasis |
| Dark | `--color-dark` | `#1f3b22` | Hero/header backgrounds, heading text |
| Light | `--color-light` | `#c0e3cb` | Tints on dark backgrounds |
| Background | `--color-background` | `#f2f9f3` | Alternating section backgrounds |
| Text | `--color-text` | `#1f2521` | Body copy |
| Text muted | `--color-text-muted` | `#6b7280` | Grey — captions, secondary copy |
| Pink | `--color-pink` | `#ec8fc0` | Ghost-button borders/text, single points of emphasis |
| Pink (dark) | `--color-pink-dark` | `#b8508e` | Text on pink-tinted backgrounds |
| Pink (tint) | `--color-pink-tint` | `#fdf1f8` | Light pink fills — pills, badges, ghost-button fills |

**Pink is a spotlight, not a workhorse color** — this mirrors how
`hopeturtles.org` itself uses its fuchsia accent: the site's `:root`
palette is overwhelmingly green, and its one bright fuchsia value
(`#ff00ff`) appears exactly once, as the hover state on the commission
page's single master call-to-action button (`main.css`,
`[data-commission-submit]:hover`). Wrangler uses a softer, lighter pink
throughout rather than that literal hex — easier to read as body text and
borders — but keeps the same restraint: at most one pink element per
screen, and on the app mockups pink never appears as a solid filled
object. It's always a ghost button (white/transparent fill, pink border
and text) or a light tint fill, never a saturated block — the ink should
read as an accent outline, not a shout. If a screen has more than one
pink element, or a pink element with a solid saturated fill, that's a
signal something drifted from this convention, not a reason to add more.

**Typography**: `Aleo` (headings) paired with `Mulish` (body), loaded
from the same Google Fonts request the main site uses:
```
https://fonts.googleapis.com/css2?family=Aleo:wght@400;700&family=Mulish:wght@300;400;500;600&display=swap
```
Fallback stacks match the site's own tokens —
`"Aleo", Georgia, "Times New Roman", serif` for headings,
`"Mulish", "Helvetica Neue", Arial, sans-serif` for body — so a slow or
missing font load degrades the same way on Wrangler as it does on
hopeturtles.org.

When Part 2 reaches a real Android theme (Compose `MaterialTheme` /
`colors.xml`), this table is what gets ported over — the six-color roles
map cleanly onto Material's primary/secondary/background/surface/on-*
slots, with fuchsia reserved for a single accent color, used the same
sparingly.
