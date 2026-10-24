# AirBuddy app roadmap: Bluetooth in airOS

**Status: PLANNING (drafted 2026-10-24).** Nothing below is built except
Phase 0. This is the AirBuddy counterpart of the Wrangler plans in
[wrangler/](wrangler/): the decisions to make and the work to do before an
AirBuddy Android app can talk to an AirBuddy over Bluetooth and to
air2.earthen.io (airbuddy-online) over the internet.

## Goal

An **AirBuddy app**, separate from Turtle Wrangler, that:

- finds AirBuddies nearby over Bluetooth, shows their live air readings,
  and sets them up (WiFi, clock, logging) without the install script;
- after a Buwana login, shows the same readings for the user's homes and
  rooms from airbuddy-online, for devices out of range;
- describes a reading in the same words from either source, the way
  Wrangler merges turtle and hopeturtles.org data.

The device side is airOS: the turtleOS firmware with `turtle_mode: false`.
So the AirBuddy's Bluetooth service is built from the same code as the
Turtle service, not a fork.

## Where things stand

**Shared with turtleOS already (no work):**

- Telemetry JSON v2: `schema`, `recorded_at`, `values`, `flags`,
  `confidence`, `lat`/`lon`, posted to `/api/v1/telemetry` and `/batch`.
- The data dictionary, which now lists the air-quality fields
  ([wrangler/README.md, Air quality](wrangler/README.md#air-quality-airos)).
- The BLE plumbing in `src/net/`: radio lifecycle, wrangle window,
  passkey bonding, `/ble_bonds.json` (already excluded by
  `sync_airOS.sh`), the Command/Result channel, Device Information
  `0x180A`, and the header "+" indicator.
- `src/app/actions.py`: one code path per action for the OLED and
  Bluetooth. WiFi, telemetry mode, GPS stamp, clock, sleep and reboot
  already work the same in both modes.

**Phase 0, done 2026-10-24** (airOS and air2 aligned with v2):

- airOS takes `timezone_offset_min` from air2's `GET /api/v1/device`
  again (boot step and Time screen). turtleOS still ignores the server's
  zone. The Time screen's device URL now handles an `api_base` without
  `/api`, which had been sending it to a 404 on air2.
- airOS no longer sends `machine_state` (always `BOOT` there).
- airbuddy-spa's "+ charging" current chart converts the raw INA219 sign
  (`app/lib/batteryCurrent.ts`).
- airbuddy-online stores `schema` in `telemetry_readings_tb.schema_version`
  (migration `005_telemetry_schema.sql`, run it before deploying).
- Air fields added to the data dictionary.

**Turtle-only today, and why it matters:**

| What | Where | Blocks |
|---|---|---|
| `BleService` is built only in turtle mode | `device/main.py` `step_init_runtime()` | everything |
| The Bluetooth screen leads the hold flow only in turtle mode | `src/ui/flows.py` `sleep_flow` | pairing: the passkey is only accepted while that screen shows |
| One service, `0001` Turtle, with nav/sail/journey characteristics | `ble_service.py`, `ble_telemetry.py` | an AirBuddy needs air characteristics, and Wrangler must not list it |
| `0101` Contract info says `turtle_mode` = 1 always | README | was reserved for exactly this; needs a defined meaning |
| `SET_TURTLE_MODE 0` documents "reboots into airOS (no BLE service)" | `ble_commands.py`, README | becomes "reboots into airOS (AirBuddy service)" |
| airbuddy-online has session-cookie routes only | `src/routes/dashboard.js` | the app's server half |

---

## Decisions to make

Each has a recommendation. They are ordered by what blocks what: D1–D3
fix the wire, so they come before any firmware.

### D1. A distinct service, on the same UUID base?

The app should never see a turtle, and Wrangler should never list an
AirBuddy. So the AirBuddy advertises **its own service UUID**, not `0001`.

- **(a) Same base, new short ID** (recommended): AirBuddy service
  `26d00003-5890-45f2-b0be-090d35436a95`, advertised instead of `0001`.
  The Command service `0002` and Device Information `0x180A` are shared
  unchanged. One `_UUID_FMT` in the firmware, one UUID table in a shared
  Android module, and the scanners still filter cleanly by service.
- **(b) A new UUID base** for everything AirBuddy, Command service
  included. Cleanest separation, but a second copy of the command service
  under another UUID buys nothing: its framing would be identical.

Decide also whether the AirBuddy service gets its own advertised name
pattern (`AirBuddy-` + last 4 of `device_id` when `device_name` is empty,
as turtles use `Turtle-`).

### D2. One contract document or two?

- **(a) One contract, two profiles** (recommended). Split
  `wrangler/README.md` into: shared rules (wire conventions, sentinels,
  bonding, command framing, result codes, Device Information); the Turtle
  service profile; the AirBuddy service profile. The data dictionary stays
  one table for both. The opcode table marks each opcode Turtle, AirBuddy
  or both.
- **(b) A separate AirBuddy contract** that copies the shared rules. Two
  copies drift.

With (a), the docs folder probably moves from `docs/wrangler/` to
something like `docs/ble/`, with Wrangler's app plans staying under
`wrangler/`. hopeTurtles.org's pointer to this folder must follow.

**Versioning:** each service has its own Contract info characteristic
whose first byte is that service's version. The AirBuddy service starts
at 1. `0101`'s `turtle_mode` byte keeps its meaning (always 1 on the
Turtle service); the AirBuddy's says 0.

### D3. Which characteristics?

Rule (recommended): **a value both devices have keeps the Turtle
layout byte for byte**, so one Android parser reads both. Only
air-specific values get new layouts. A first proposal, short IDs `03xx`:

| Short ID | Name | Layout | Source |
|---|---|---|---|
| `0301` | Contract info | `<BB>` version, device_mode (0 = airOS) | as `0101` |
| `0302` | Name | UTF-8 ≤ 64 | as `0102` |
| `0310` | Air | new: `ens_eco2` u16 ppm, `ens_tvoc` u16 ppb, `ens_aqi` u8, `sensor_confidence` u8, `scd_co2` u16 ppm, `reading_age_s` u16 | `AirSensor` last reading |
| `0311` | Climate | new: `aht_temp_x10` i16, `aht_rh_x10` u16, `bme_temp_x10` i16, `bme_rh_x10` u16, `bme_pressure_hPa_x10` u16, `rtc_temp_x10` i16 | `AirSensor`, RTC |
| `0314` | Power | **same as `0114`** `<HhB>` | INA219 |
| `0316` | Status | same as `0116`, nav bits unused | |
| `0317` | Shore sync | same as `0117`, `journey_id` always 0 | |
| `0320` | Position | same as `0110`, only if the AirBuddy has GPS | `gpsfix` |

Questions inside D3:

- `0118` Environment (`aht_*`, `bmp_*`, `rtc_temp`) already exists on the
  Turtle service. Reuse it as the Climate characteristic, extended with
  `bme_*` by appending (allowed within v1), or keep Climate separate?
  Reuse is fewer layouts; separate keeps the turtle's "control bottle"
  meaning apart from a room's.
- Is Position wanted at all on an indoor device? AirBuddies with GPS
  exist (the GPS screen is in airOS's hold flow), so probably yes, as an
  optional characteristic.
- Absent sensors use the contract's sentinels, never 0.

### D4. Which commands, and any new ones?

Shared opcodes that already make sense in airOS: `0x20`–`0x24` (GPS
stamp, GPS on/off, telemetry mode/interval, API handshake), `0x30`–`0x34`
(WiFi), `0x33` (Bluetooth off), the clock opcodes, `0x40` SLEEP, `0x41`
SET_TURTLE_MODE (here `1` = switch to turtleOS), `0x42` REBOOT.

Turtle-only (`0x01`–`0x08` journeys/destinations, `0x10`–`0x12` sail): an
AirBuddy answers `ERR_UNKNOWN_OPCODE`, or a new `ERR_NOT_THIS_DEVICE`.
Decide which. `UNKNOWN_OPCODE` already means "the app is newer than the
firmware", which is the wrong message.

Candidate AirBuddy opcodes (`0x50`–`0x5F`, reserved for AirBuddy):

- `SAMPLE_NOW`: take one full ENS160/AHT sample (`air.finish_sampling()`,
  as the single-click carousel does) and notify `0310`. Long-running
  (IN_PROGRESS → OK).
- `DEVICE_SET_CREDENTIALS`: see D6.
- `READINGS_PAGE`: page through unsent readings in
  `telemetry_queue.json`, like `JOURNEY_TRACK`, so the app can chart a
  device that hasn't been online. Optional; see D9.

### D5. Time zone: server or phone?

Phase 0 made airOS take its zone from air2 (the owner's account). An app
with `TIMEZONE_SET` adds a second writer. Options:

- **(a) The server wins while the device is claimed** (recommended): the
  boot sync overwrites; `TIMEZONE_SET` is for unclaimed or never-online
  devices. Simple, and an AirBuddy rarely moves.
- **(b) The phone wins, as on turtles**: then the boot sync must stop
  overwriting once a phone has set it (needs a "set by phone" flag).
- **(c) Push it the other way**: the app sets the zone on the room/home
  in airbuddy-online and the device keeps following the server.

### D6. Setting up a new AirBuddy from the phone

The biggest win over the install script. Today a device key is made by
`POST /api/devices/register` on the website, shown once, and typed into
`airbuddy_config.json`. The app could do the whole claim:

1. The app pairs with the AirBuddy (passkey on the OLED).
2. The app calls a new app-API route to register the device and gets the
   key once.
3. The app writes `device_id` + `device_key` over the bonded link with
   `DEVICE_SET_CREDENTIALS`, then WiFi with `WIFI_SET_CREDENTIALS`.

To decide: write-only key (never readable back, like the WiFi password);
accepted only while the Bluetooth screen shows, like pairing; what
happens to the old key (`reset-key` revokes it server-side). The
alternative, the device generating its own key and the app relaying
it, needs a server change in `deviceAuth` and gains little.

### D7. Wrangle window and lockdown indoors

Turtles advertise for `ble_window_min` (10) minutes after boot, wake and
each Bluetooth-screen visit; `ble_enabled: false` is mission lockdown.
An AirBuddy sits in a home. Options: same defaults (recommended, the
user opens the Bluetooth screen to connect); or advertise always
(`ble_window_min: 0`) for "open the app and it's there", at the cost of
being visible to everyone nearby. Bonding stays required either way.

### D8. The app: its own repo, sharing a module?

- **(a) A separate app** (`airbuddy_app`, Kotlin + Compose like
  Wrangler) **plus a shared library module** (recommended): scanner,
  bonding, command channel, result codes, sentinels, and the shared
  parsers (Power, Shore, Status, Position, Device Information,
  `Format.charge()`). Wrangler moves those into the module first.
- **(b) One app with two flavours.** Less duplication, but every release
  of one ships the other, and the Play listings differ.

Also decide: a separate Buwana client for the AirBuddy app
(`client_id`, custom-scheme redirect such as
`org.earthen.airbuddy://oauth2redirect`), as Wrangler has its own.

### D9. Offline history over Bluetooth?

A turtle has `JOURNEY_TRACK` because a field test has no WiFi. An
AirBuddy at home is usually online, so the app can read history from
airbuddy-online. Bluetooth history (`READINGS_PAGE`) is then only for
devices without WiFi. Recommend deferring it until someone needs it.

### D10. The server's app API

airbuddy-online needs what hopeTurtles.org built in `/api/app/v2`
(see [wrangler/03_v2_plan.md](wrangler/03_v2_plan.md), Phase D):

| Call | Notes |
|---|---|
| `POST /session`, `DELETE /session`, `GET /me` | Buwana ID token → bearer token; `app_sessions_tb` stores its SHA-256 |
| `GET /devices` | the user's devices through `home_memberships_tb`, with home and room |
| `GET /devices/:id/latest` | `{ device, reading }`; `reading` in the dictionary's shape: `recorded_at`, `received_at`, `values`, `flags`, `lat`/`lon` |
| `GET /devices/:id/readings?hours=` | trend data; raw values, the app converts (e.g. current sign) |
| `POST /devices/register` | for D6; unlike Wrangler's API this one writes |

To decide: copy hopeTurtles.org's `appAuth` and session code, or pull it
into a package both servers use. Both are Express on Node 22 and both use
Buwana.

---

## Work, in order

### Phase 1: contract (docs only)

Settle D1–D4 and write the AirBuddy profile into the contract (D2's
shape). Update `tests/ble_decode.py` for the new characteristics.

**Done when** the AirBuddy service table, its opcodes and the shared /
Turtle / AirBuddy split are in the contract.

### Phase 2: firmware, Bluetooth in airOS

- Build `BleService` in `step_init_runtime()` in both modes; pass it to
  `run()` in airOS too.
- Profile the service by mode: `ble_service.py` registers `0001` or
  `0003`; `ble_telemetry.py` splits into shared packers (Power, Status,
  Shore, Position) and per-profile ones (nav/sail/journey vs air/climate).
- Opcode table per mode in `ble_commands.py`, with D4's answer for the
  turtle-only opcodes. New AirBuddy opcodes go through `actions.py`.
- The Bluetooth screen leads airOS's hold flow too. The header "+" and
  `BleWatch` already work on every screen; airOS's waiting screen
  (`src/ui/waiting.py`) needs its own indicator, the turtle's rune lives
  in `turtle_waiting.py`.
- Air values notify from the last `AirSensor` reading. Bluetooth never
  starts a sample except through `SAMPLE_NOW`: the ENS160 sampling cycle
  is owned by the scheduler and the carousel.
- Boot log and version: the `[BLE]` lines and Device Information model
  string say AirBuddy/airOS.
- `tests/ble_fuzz_host.py` runs against both profiles.

**Done when** nRF Connect on a phone sees an AirBuddy advertising `0003`,
pairs with the OLED passkey, reads `0310`/`0311`/`0314`, and runs a WiFi
command.

### Phase 3: hardware checks on a real AirBuddy

The Phase 0 spikes (`tests/ble_spike.py`, `ble_coex.py`) ran on a turtle.
Re-run on an AirBuddy, where WiFi is up most of the time and the
ENS160/AHT21 sit on the same I2C bus as the OLED:

- coexistence with WiFi always connected, and through a batch upload;
- BLE during ENS160 warmup (4 s) and a full sample;
- boot time with and without the BLE build.

### Phase 4: server

- D10's app API on airbuddy-online, the Buwana client, and
  `POST /devices/register` for the app.
- Optional: a `GET /devices/:id/readings` that answers in the
  dictionary's names only (no legacy aliases), so the app never sees
  `eco2_ppm`.

#### Server schema

**Bluetooth itself changes nothing on the server.** The radio runs
between the phone and the AirBuddy. Readings still reach airbuddy-online
over WiFi through the same ingest, and bonds live on the device
(`/ble_bonds.json`). What changes the schema is the app: its login, and
setting up devices from the phone. Migrations go in
`airbuddy-online/db/migrations/` and are run by hand before the code that
uses them, as with `005_telemetry_schema.sql` (already done:
`telemetry_readings_tb.schema_version`).

| Change | For | Needed? |
|---|---|---|
| **`app_sessions_tb`** (new): `token_hash` CHAR(64) unique, the user, `client`, `created_at`, `last_used_at`, sliding `expires_at`, `revoked_at`. Copy hopeTurtles.org's `sql/migrations/20261015_app_sessions.sql`, with the foreign key pointing at airbuddy's `users_tb` | D10: the app's bearer tokens (only their SHA-256 is stored) | **Yes**, for anything behind the login |
| `device_keys_tb.created_via` ENUM('web','app'), and maybe `devices_tb.claimed_at` | D6: knowing which keys the app made, and when a device was claimed | Probably. The existing columns work without them, so the audit trail is the only gain |
| A pending-claim table (`claim_code_hash`, device, user, `expires_at`) | D6, only if the claim goes through a short-lived code instead of a direct registration | Only for that option |
| Where a reading came from: a `via` column, or `flags.via` | D9, only if the app uploads readings it pulled over Bluetooth | No if it goes in `flags`, which is already stored verbatim |

**Columns that exist but nothing writes:**
- `devices_tb.firmware_version` is never written.
- `devices_tb.device_type` still defaults to `'pico_w'`.

The app can fill both from Device Information (`0x180A`), or the device
can send them at boot (a `PATCH /api/v1/device` like hopeturtles.org's).
Either way it needs a writer, not a new column.

**No change needed:**
- **Time zone (D5):** `homes_tb.time_zone` already exists. Sending the
  home's zone instead of the owner's from `GET /api/v1/device` is a
  query change in `src/routes/v1/device.js`.
- **Advertised name:** the device advertises `devices_tb.device_name`,
  which `GET /api/v1/device` already returns.

### Phase 5: the app

- The shared module out of Wrangler (D8), then the AirBuddy app: scan →
  pair → live readings → set up (WiFi, D6 claim, clock, logging).
- Server source after login: devices by home and room.
- One reading model fed by both sources, by `recorded_at`.

### Order

```
Phase 0 (done) ─► D1–D4 ─► Phase 1 contract ─► Phase 2 firmware ─► Phase 3 hardware ─┐
                                                                                     ├─► Phase 5 app
                  D5, D6, D10 ───────────────► Phase 4 server ───────────────────────┘
                  D8 ────────────────────────► shared module out of Wrangler ────────┘
```

Phases 2 and 4 can run in parallel once Phase 1 is written.

## Open questions

- Should the data dictionary be renamed from "Turtle data dictionary" to
  a device-neutral name when the docs move (D2)? The anchor is linked
  from many places; rename once, with the move.
- Will an AirBuddy ever be a turtle's passenger (air sensors on a
  turtle)? The dictionary already allows it: a turtle sending
  `ens_eco2` uses the same key.
