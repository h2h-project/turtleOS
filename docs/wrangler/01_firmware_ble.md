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

1. **BLE stack on the deployed build.** *Checked on the desk:* the
   image `resources/ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin`
   contains the `bluetooth` module on NimBLE, including `gap_pair`,
   `gap_passkey` and `gatts_set_buffer`, so pairing and large writes are
   compiled in. `aioble` is not in the image. Still to confirm on the
   device: `import bluetooth` works on the flashed board. Then decide
   between vendoring `aioble` into
   `src/lib/` (the way `urequests.py` is vendored) and writing directly
   against `bluetooth`. aioble's `security` module handles bond
   persistence, which counts in its favour.
2. **WiFi/BLE coexistence and power management.** ESP-IDF requires
   WiFi modem sleep while BLE is active. On ESP32, `wifi_manager.py`
   sets `PM_PERFORMANCE` (`WIFI_PS_MIN_MODEM`, which is modem sleep, so
   it is compatible). The CYW43 value `0xA11140` is guarded to Pico
   only. The one risk is the `PM_NONE` fallback (line 97), which only
   runs if `PM_PERFORMANCE` is missing. v1.28 has it, but the spike
   should confirm the constant exists and that BLE survives a WiFi
   connect. Then measure telemetry POST latency and offline-queue drain
   rate with and without a phone connected.
3. **Passkey pairing.** Confirm the build supports
   `ble.config(io=DISPLAY_ONLY, mitm=True, bond=True, le_secure=True)`
   and delivers `_IRQ_PASSKEY_ACTION` for display. Confirm a bond
   survives a reboot through `_IRQ_GET_SECRET` / `_IRQ_SET_SECRET`.
4. **Large writes.** Confirm that MTU exchange up to 185 and
   `gatts_set_buffer(handle, 128)` accept a ~100-byte
   WIFI_SET_CREDENTIALS write in one piece.

Deliverable: a short go/no-go note appended to this file, not code.

---

### Phase 0 results (2026-09-27, turtle 18, MicroPython 1.28.0)

Scripts: `tests/ble_spike.py` (tests A + B) and `tests/ble_coex.py`
(test C).

| Check | Result |
|---|---|
| `bluetooth` on the deployed build | **Pass.** NimBLE; `bond`, `le_secure`, `mitm`, `io=DisplayOnly` and `mtu=185` all accepted before/after `active(True)`. |
| Advertise + scan-response name | **Pass.** 21 B adv (flags + 128-bit UUID), name in scan response. Found by nRF Connect. |
| Read / 1 Hz notify | **Pass.** |
| 100-byte write (WIFI_SET_CREDENTIALS size) | **Pass** at both MTU 185 (phone requested it) and MTU 23 (no request, so Android used a long write). WIFI_SET_CREDENTIALS works even on a phone that never raises the MTU. The earlier 0-byte result was an empty paste. |
| Passkey pairing (DisplayOnly, OLED code) | **Pass.** LE Secure, authenticated, bonded, 16-byte key. A failed first attempt ended the connection (`encrypted=0`, then disconnect). |
| Bond survives a hard reset | **Pass.** Secrets reloaded from the JSON store. The phone reconnected from a *new* resolvable private address and was still recognised (IRK resolution works). Encrypted, bonded, no passkey. |
| Stale bond | Observed both ways. (1) Phone still bonded, turtle has no keys: the phone is dropped within seconds (see app Phase 7). (2) Phone forgot the turtle, turtle still has keys: on re-pairing NimBLE deleted the old keys itself (`secret deleted`) and offered a fresh passkey. A pairing that isn't completed leaves the link up but unencrypted (`encrypted=0`), so command writes must return `ERR_NOT_BONDED`, which is the contract's design. |
| WiFi PM with BLE active | **Pass.** `PM_PERFORMANCE` = 1 exists; `pm` stays 1 throughout. No abort or reset. |
| `GET /api/v1/device` latency: BLE off / advertising | Median 870 / 845 ms, 20/20 each. **No measurable cost from advertising.** |
| Latency with a phone connected + 5 Hz notify | **Pass.** 20/20, median 993 ms against 841 ms with BLE off in the same run (about +150 ms, ~18%). 5 Hz is a deliberate stress rate, several times the contract's real notify rates, so no throttling is needed for v1. Max latency stayed about 2.2–2.5 s in every phase, including BLE off. |
| BLE off again with WiFi up | **Pass.** WiFi stayed connected, 20/20. Median 933 / 936 ms in two runs, about 90 ms above the BLE-off baseline both times. Small, but repeatable; recheck in Phase 7 whether a BLE on→off cycle leaves the radio slightly slower. |
| Current draw | *Not measured.* On USB power the INA219 reads ~0 mA. Deferred to a battery-powered bench run; it only tunes the `ble_window_min` default and blocks nothing. |

**Verdict: GO.** Build as planned, with no architecture changes:
- use the raw `bluetooth` module with our own JSON bond store (`/ble_bonds.json`), not aioble;
- keep the existing WiFi power-management code as it is;
- no coexistence throttling is needed for v1.

Carry forward:
- recheck the small post-BLE latency offset in Phase 7;
- measure current on battery before fixing the `ble_window_min` default.

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

### Phase 1 status — DONE (2026-09-28, commits 5bd36b1, 96ae214)

Tested on turtle 18 (Applemore), including a field walk for Journey,
Destination and GPS stamps.

- `src/app/actions.py` holds every gesture's side effects and returns
  contract result codes:
  - journey: `journey_start/end`
  - destination: `dest_set_here/mission/coords/clear`
  - GPS: `gps_stamp`, `gps_set_enabled`
  - telemetry: `telemetry_set_mode/interval`, `api_handshake` + `api_outcome`
  - WiFi: `wifi_set_enabled/credentials`, `connection_mode_set`
  - sail and compass: `nav_luff_sweep`, `compass_set_offset`
  - system: `set_turtle_mode`
  - Bluetooth (Phase 2): `ble_set_enabled`, `ble_open_window`
- **Live config mirror.** `actions.write_config()` = `update_config()`
  plus a mirror into `run()`'s `_cfg_cell[0]` (bound via
  `bind_cfg_cell`). A change takes effect on the next background tick.
  No screen calls `save_config()` any more.
- **Deferred to Phase 6**, because the screen logic is too entangled to
  separate yet: `SERVO_BENCH_SWEEP` (WindFinder) and `SLEEP`.
- **Found:** `GnssModule` has no power control, so "GPS off" has only ever
  been a config flag; the module keeps running. `gps_set_enabled`
  reports `power_control`. Real L76K standby (a command, or the
  `GPS_WAKEUP` pin on D0) is a separate battery task, not in this plan.
- `device_name` is persisted by `step_api()`. `gpsfix` caches GGA fix
  quality and satellite count (`note_gga()` / `gga()`).
  `journey_state.json`, `nav_state.json` and `ble_bonds.json` are
  excluded from uploads.

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

### Phase 2 status — DONE (2026-09-29)

Tested on turtle 18 with nRF Connect: advertises as **Applemore**,
connect/disconnect, both identity characteristics, on/off persists
across reboot, sleep shuts the radio down, wake restarts it, and window
expiry.

- `src/net/ble_service.py` (`init()` / `instance()`) is built in
  `step_init_runtime()` (turtle mode only), passed to `run(ble=...)`,
  and ticked from `_bg_tick()`. The IRQ handler only records events.
  Advertising interval 0.5 s. Serves `0101` Contract info and `0102`
  Turtle name.
- **Bluetooth screen** (`screens/bluetooth.py`) after WiFi in the
  connectivity carousel. Arvo 20 title like Online/WiFi; states Off /
  Open m:ss / Open / Connected / Hidden. Opening it reopens the window.
- **Indicators** (the design changed during testing; the earlier "rune
  in the header while connected" was replaced):
  - they read state from the service on every draw
    (`connection_header.ble_on()` / `ble_visible()`), so they can't go
    stale;
  - shown whenever Bluetooth is on: blinking with a 1 s phase while
    advertising, steady when a phone is connected or the window is
    closed;
  - every screen: a 3x3 "+" in the WiFi icon's empty lower-right corner,
    without moving the WiFi icon;
  - waiting screen only: a 5x7 rune left of the nav flèche. The flèche
    is now 7x8, pointing up, cap-height with its tip in the spare row
    above the heading text;
  - static screens (Destination, Device, Servo) redraw for the blink via
    `connection_header.BleWatch`.
- Also fixed: the Servo screen never passed `gps_state` to the header.

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

### Phase 3 status — DONE (2026-09-30)

Tested on turtle 18 with nRF Connect: all ten Turtle-service
characteristics are present and live. Decoded with `tests/ble_decode.py`
(a host helper: `python3 tests/ble_decode.py 0116 F3-BF-02-...`).

- `src/net/ble_telemetry.py` (`TurtleData`) packs `0110`–`0117` to the
  contract formats, using sentinels for missing data. The schedule is in
  `SCHEDULE`: Position, Nav, Sail and IMU at 1 s, with Sail at 0.5 s
  while sweeping; Targets and Status at 2 s; Power and Shore at 5 s.
  Notify happens on change only. Shore also forces one notify a minute
  for its clock.
- Values are packed **only while a phone is connected**. A new
  connection resets the schedule, so the first reads are fresh and every
  characteristic notifies once.
- Sources are getters handed in by `run()` via `ble.attach_sources()`.
  The queue count and last-successful-sync time are read from flash at
  most every 10 s.
- Epoch: `rtc_unix()` reuses `TelemetryScheduler._dt_to_unix()`, because
  `time.mktime()` counts from 2000 on ESP32.
- **Nav bug fixed along the way:** `WaypointSequencer` resolved its route
  only when `NavController` was built, so a destination set at runtime
  wasn't steered to until the next reboot. `WaypointSequencer.sync(cfg)`
  now runs from `NavController.tick()` at most once a second; a changed
  route restarts at waypoint 0. The sequencer also reports which config
  key the route came from (Targets `active_source`). `NavController`
  gained `target()`, `route_source()`, `battery_pct()` and
  `sail_encoder_present()`.
- Still sentinel by design: `xte_m` (no cross-track function) and Sail
  `confidence_pct` (the nav sweep produces none; the bench sweep
  arrives in Phase 6).

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

### Phase 4 status — DONE (2026-10-02)

Tested on turtle 18 with nRF Connect:
- an unbonded command returns `ERR_NOT_BONDED`;
- pairing off-screen is refused and the link dropped;
- on the Bluetooth screen the passkey shows on the OLED, and the link
  comes up `encrypted=1 authenticated=1 bonded=1`;
- the bond survives a reboot (re-encrypts with no passkey);
- REBOOT and BLE_SET_ENABLED 0 act after their OK;
- "Forget phones" clears every bond.

- `src/net/ble_commands.py` (`CommandRunner`, `OPCODES`) does framing,
  length and bond checks, sends one final result per command, refuses a
  second command with `ERR_BUSY`, and polls `IN_PROGRESS` work.
  Followups are `("after", ms, fn)` (act once the result has gone out:
  reboot, radio off) and `("poll", fn, timeout_ms)`. Adding a command is
  one `OPCODES` entry plus a handler that calls `src/app/actions.py`.
  First commands: `REBOOT` (0x42) and `BLE_SET_ENABLED 0` (0x33).
- `ble_service.py`:
  - Command service `0002` (`0201` write, 128-byte buffer; `0202`
    Result, read + notify; the last result stays readable).
  - Security: `bond`, `le_secure`, `mitm`, `io=DisplayOnly`, set before
    `active(True)`.
  - The bond store `/ble_bonds.json` is answered synchronously in
    `_irq()`. This is the one exception to the IRQ-records-only rule.
  - The passkey is generated with `os.urandom`. The pairing gate is
    `set_pairing_allowed()`, opened only while the Bluetooth screen
    shows; otherwise the link is dropped.
  - `link_bonded()` requires encrypted, authenticated and bonded.
- Bluetooth screen: "Pair code" with large digits while pairing;
  "Connected / Paired - <name>"; triple-click "Forget phones?" (double
  confirms, shows the bond count).
- Status bits `link_bonded` (14) and `command_in_progress` (16) are live.
- `tests/ble_decode.py` also decodes `0202` Results.

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

### Phase 5 status — BUILT; device testing deferred to the app (2026-10-02)

The 11 commands are in `ble_commands.OPCODES`:
- journey: `0x01`/`0x02`
- destination: `0x03`–`0x06`
- GPS: `GPS_STAMP` `0x20`, `GPS_SET_ENABLED` `0x21`
- telemetry: `0x22` mode, `0x23` interval
- `API_HANDSHAKE` `0x24`: `IN_PROGRESS`, then `OK` / `ERR_API_FAILED`,
  with a 30 s timeout

Each handler calls the same `src/app/actions.py` function as its OLED
gesture, and only packs the contract's result payload. The full path
(raw bytes → runner → real `actions`/`config`/`gpsfix` → result bytes)
was verified on the host for all 22 cases, including every error code.
Testing these by hand in nRF Connect was judged too tedious; they will
be exercised from the Wrangler app instead.

**Deferred device-test checklist (run from the app):**
- [ ] `GPS_STAMP` refused in auto (WRONG_STATE), accepted in manual, and the
      OLED GPS screen's count agrees with `stamps_session`
- [ ] `JOURNEY_START` with and without a fix; `JOURNEY_END` with
      `arrival_stamped` 1 and 0; the journey appears on hopeturtles.org
- [ ] `DEST_SET_HERE` / `MISSION` / `COORDS` / `CLEAR` each change the
      Targets characteristic and log `[NAV] route changed`
- [ ] `TELEMETRY_SET_MODE` / `INTERVAL` reflected on the Logging screen and in
      Status
- [ ] `GPS_SET_ENABLED` 0/1 reflected in Status (flag only — GnssModule has
      no power control)
- [ ] `API_HANDSHAKE` reaches OK online and `ERR_API_FAILED` with WiFi off

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

### Phase 6 status — BUILT; device testing deferred (2026-10-02)

All 21 contract opcodes are now in `ble_commands.OPCODES`. Phase 6 added:
- sail: `0x10` NAV_LUFF_SWEEP (IN_PROGRESS, polls `nav.sweeping()`) and
  `0x11` SERVO_BENCH_SWEEP;
- connectivity: `0x30` WIFI_SET_ENABLED, `0x31` WIFI_SET_CREDENTIALS,
  `0x32` CONNECTION_MODE_SET;
- system: `0x40` SLEEP, `0x41` SET_TURTLE_MODE, `0x43` COMPASS_SET_OFFSET
  (applied live via `HeadingSource.set_offset`).

How the harder ones work:
- **Bench sweep.** It calls the new `ServoScreen.run_bench_sweep()` (the
  sweep blocks the main loop by design, with progress on the OLED). It
  starts one tick after IN_PROGRESS so that notification goes out first.
  Every bench result (OLED or BLE) is recorded via
  `actions.note_bench_result()`. The Sail characteristic now reports its
  confidence: jitter ratio 1× = 0 %, ≥ 5× = 100 %.
- **WiFi.** It never touches the radio from the main loop when the
  background thread exists: `request_wifi_check(cfg, force=True)`, then
  "connected" means `wlan.ifconfig()` shows an IP (25 s timeout).
  Credentials save first, then disconnect, then reconnect. If WiFi was off
  at boot (`NullWiFiManager`), it connects inline like the WiFi screen
  (12 s).
- **Sleep.** It reuses the new `SleepScreen.go_to_sleep()` through run()'s
  `enter_sleep` source, nested in the BLE tick. BLE is suspended while
  asleep.

Verified on the host for 17 cases (raw bytes → runner → real
actions/config → result bytes). Not yet run on the turtle.

**Deferred device-test checklist:**
- [ ] **OLED regression (do first on the next sync):** Sleep-screen
      double-click still goes Good night → sleep → Good morning; the Servo
      screen's double-click sweep still shows its result.
- [ ] `1101` bench sweep: progress on the OLED, `11-01-01` then
      `11-01-00-…`; the link survives the blocking sweep; Sail
      `confidence_pct` is set afterwards.
- [ ] `4002` SLEEP: OK, then Good night; the phone disconnects; wake with a
      click; advertising resumes.
- [ ] `31…` WIFI_SET_CREDENTIALS re-joining the current network: IN_PROGRESS
      then OK within ~10 s (the WiFi/BLE coexistence case from Phase 0).
- [ ] `30..00` / `30..01` WiFi off/on; `0x10` nav sweep from ACQUIRE;
      `0x41 1` no-op; `0x43` compass offset visible on the Compass screen.

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

### Phase 7 status — host work DONE; hardware checks deferred (2026-10-02)

- **Device Information `0x180A`** is registered as the third GATT service:
  Manufacturer `Hope Turtles`, Model `turtleShell <platform_tag>`,
  Firmware `VERSION_NUM` (`src/app/booter.py`), Serial `device_id`.
  Verified on the host that all three services register with the right
  values.
- **Malformed input:** `tests/ble_fuzz_host.py` drives the real
  `CommandRunner` + `actions` + `config` with random writes: known and
  unknown opcodes; empty, short, exact and over-long payloads; random
  bytes; random nav states. It checks that the runner never raises, that
  there is exactly one final result echoing opcode + seq, that nothing
  stays busy, and that `ERR_BUSY` comes back during `IN_PROGRESS`.
  **33,000 writes over 4 seeds: 0 problems.** Rerun after any change to
  `ble_commands.py` or `actions.py`:
  `python3 tests/ble_fuzz_host.py 10000 <seed>`.
- **Reconnection:** Result `0202` is readable after a reconnect (since
  Phase 4). A dropped link cancels the in-flight poll, but a queued
  reboot / radio-off / sleep still happens.
- **Docs:** `CLAUDE.md` now has a Bluetooth section (architecture,
  rules, test tools), gotcha 19 (IRQ records only, except the bond
  store), and the new files in the layout and key-file tables.

**Deferred hardware checks (with the Phase 5/6 checklists, from the app):**
- [ ] nRF Connect / the app reads `0x180A`: Hope Turtles · turtleShell
      xiao_esp32s3 · 2.4.1 · 18
- [ ] Drop the link mid-command (e.g. during a bench sweep); after
      reconnecting, reading `0202` returns that command's final result
- [ ] Coexistence under real load: the app connected with every
      characteristic subscribed while telemetry posts on schedule; compare
      POST latency with the Phase 0 numbers
- [ ] Recheck the ~90 ms post-BLE latency offset seen in Phase 0
- [ ] Current draw on battery (BLE off / advertising / connected) to set
      the `ble_window_min` default

**Firmware side complete.** All 21 contract opcodes and every
characteristic are implemented; what remains is on-device testing,
best done from the Wrangler app (`02_android_app.md`).

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
