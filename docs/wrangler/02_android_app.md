# Part 2 — Turtle Wrangler Android app

This is the phone-side half of Turtle Wrangler: a new Android app, built
from scratch. It finds a turtle over BLE and presents four bottom tabs:
**Dashboard, Navigate, GPS, Diagnostics**. **Settings** is not a tab: a
gear icon in the top-right corner of the top bar, next to the connection
status, opens it on every screen, including the "No turtles nearby"
screen, where it holds app-level settings and, after Phase 8, the
account.

When no turtle is in Bluetooth range, a planned later phase lets the user log in with
their Buwana account and view turtles they manage that are reporting
over WiFi.

The app builds against **GATT contract v1** in [README.md](README.md).
Early phases can proceed in parallel with the firmware. Phase 1 only
needs *a* peripheral advertising the Turtle service UUID. A small
`bleak` script on a laptop or an nRF Connect server profile works as a
stand-in until firmware Phase 2 lands.

Recommended stack: **Kotlin + Jetpack Compose**, MVVM. A
`StateFlow<TurtleTelemetry>` feeds the composables, and a
`CommandClient` returns `suspend` results. Screens never touch
`BluetoothGatt` directly.

---

## Phase 0 — decisions before scaffolding

- **Map provider** for the Navigate screen: Google Maps SDK (needs Play
  Services) or MapLibre (open source, no Play Services). This has to be
  decided before Phase 3.
- **Distribution:** internal only. A sideloaded APK or Play Console's
  internal track, not a public listing. This affects the signing setup.
- **`minSdk = 31`** (Android 12). This skips the pre-12
  location-permission model for BLE (`BLUETOOTH_SCAN` with
  `neverForLocation`, plus `BLUETOOTH_CONNECT`). Revisit only if a
  specific older phone must be supported.
- **Reserve the app's OAuth redirect scheme now**
  (`org.hopeturtles.wrangler:/oauth2redirect`), even though login lands
  in Phase 8. Changing an app ID or scheme after distribution is
  painful.

---

## Phase 1 — scaffold, BLE core, command client

The foundation every later screen relies on.

- `TurtleUuids`: the contract's base `26d0XXXX-5890-45f2-b0be-090d35436a95`
  plus the short IDs.
- `TurtleScanner`: a `BluetoothLeScanner` filtered on the Turtle service
  UUID (`0001`). Turtles are listed by the scan-response name, which is
  `device_name` (the turtle's name, `turtles_tb.name`).
- `TurtleConnection`: a `BluetoothGatt` wrapper exposing
  `StateFlow<ConnectionState>` and `StateFlow<TurtleTelemetry>`.
  - A **serial GATT operation queue**, because Android allows one
    outstanding GATT operation at a time. A second call before the
    previous callback fires fails silently.
  - `requestMtu(185)` on connect.
  - On connect: **read Contract info (`0101`) first**. A higher version
    than the app knows means read-only mode plus an "Update Wrangler"
    notice. After that, read every characteristic once, subscribe to
    the notifying ones, and **subscribe to Result before any command**.
- **Decoders** for every characteristic: little-endian `ByteBuffer`.
  Accept values *longer* than the v1 length (ignore the tail) and
  reject *shorter* ones. Map the contract's sentinels to `null`, never
  to 0.
- **`CommandClient`.** It owns `seq`, writes `opcode, seq, payload`,
  and suspends until the matching final Result arrives. It handles
  `IN_PROGRESS` by exposing a progress state and continuing to wait.
  Timeouts are 5 s before any response and 180 s after `IN_PROGRESS`.
  It maps every result code to a **human-readable message** in one
  place, for example `ERR_NO_GPS_FIX` → "The turtle doesn't have a GPS
  fix yet. Try again in the open." An unknown code shows as "Failed
  (0x…)". Every button that sends a command shows its outcome; nothing
  fails silently.
- **Pairing UX.** The first command to a new turtle can return
  `ERR_NOT_BONDED`. Show: "To pair, open the Bluetooth screen on the
  turtle (triple-click, then single-click three times) and enter the
  6-digit code it shows." Then trigger bonding
  (`createBond()` / encrypted access), which brings up Android's system
  passkey dialog. If Status bit 15 (`ble_require_bond`) is off, show a
  persistent "Unsecured — development turtle" banner.
- **Connect screen:** turtle list, connection state (scanning →
  connecting → connected → disconnected), and manual retry.
- **"No turtles nearby" screen.** After a scan finds nothing:

  > Sorry, we can't find any turtles to connect to via Bluetooth :-(
  > Would you like to login and connect to your other turtles that are
  > swimming in WiFi?

  In v1 the screen offers **Scan again** and **Last seen**, which is the
  cached snapshot of any turtle this phone has connected to before. The
  **Log in** button is present but disabled, labelled "coming soon",
  until Phase 8. It includes the hint "Is the turtle's Bluetooth
  window open? Visit its Bluetooth screen to reopen it."
- **Local snapshot cache:** the last `TurtleTelemetry` per turtle, saved
  on disconnect and labelled "last seen HH:MM" wherever it is shown.

Deliverable: the app finds a turtle, connects, pairs, verifies the
contract version, and round-trips a `REBOOT` or harmless test command
with its result displayed.

---

## Phase 2 — Dashboard

- Heading, bearing to target, distance to target, battery (V / mA /
  SoC), sail angle, GPS position with satellite count and fix age, and
  nav state.
- **SAFE state is prominent**, with the fault reason in plain words
  ("GPS lost", "No waypoints configured").
- Values with sentinels render as "—". Fix age over about 10 s greys
  out the position.
- When disconnected, show the cached snapshot labelled "last seen", or
  after Phase 8, shore data labelled "via hopeturtles.org". **Live,
  cached and shore data must never look the same.**

---

## Phase 3 — Navigate

- **Destination card** showing both targets from `0112 Targets`, with
  the active one marked.
  - **"Set to turtle's current position"** → `DEST_SET_HERE`. The label
    and the confirm dialog say plainly that this uses *the turtle's*
    GPS and not the phone's.
  - "Use mission target" → `DEST_SET_MISSION`, disabled when no mission
    target is stored.
  - "Pick on map" → `DEST_SET_COORDS`; "Clear" → `DEST_CLEAR`.
  - After a successful set-here, offer **"Start journey now?"**. This
    mirrors the OLED's hand-off to the Journey screen.
- **Journey card:** start/end → `JOURNEY_START` / `JOURNEY_END`. State
  comes from Status bit 3, with the start time from `journey_id`. A
  failed start shows the reason (no GPS fix / clock not set). An end
  with no fix says "Journey ended — arrival point not recorded (no
  GPS)".
- **Cross-track gauge** from `0111 Nav`. It shows "—" until the firmware
  computes XTE (the contract sends `0x7FFF` meanwhile).

---

## Phase 4 — GPS (manual position stamps)

**This is the most important screen for field work.** It replaces the
OLED hold-flow GPS logger.

- **Logging mode selector:** Off · Auto · Manual →
  `TELEMETRY_SET_MODE`, reflecting `telemetry_mode` from Status.
- **Stamp button.** It is large, easy to hit, and one tap is one
  `GPS_STAMP`. It is enabled only in Manual mode. In Auto/Off it is
  replaced by "Switch to manual to take stamps" (one tap, with
  confirmation).
- The current fix is shown above the button (position, satellites, fix
  age). Stamping without a fix is allowed, and the turtle's answer is
  shown.
- **Session log on the phone:** each stamp with time, coordinates and
  outcome. Successes show "Stamped" plus the turtle's session count.
  Failures show the reason, e.g. "Not stamped — turtle clock not set".
  The log persists on the phone per turtle and journey, so a field
  session can be reviewed afterwards.
- A **shore delivery** strip showing `queue_count` ("3 waiting to
  send") and `last_shore_sync`. When the queue drains, stamps move to
  "Sent", as on the OLED.
- A GPS on/off toggle → `GPS_SET_ENABLED`.

---

## Phase 5 — Diagnostics

- **Wind-finder:** "Run nav sweep" → `NAV_LUFF_SWEEP`, with progress
  from `IN_PROGRESS` and `sweep_state`. The result is the wind angle.
  `WRONG_STATE` explains which nav state it needs.
- **Bench sweep** → `SERVO_BENCH_SWEEP`, behind an "advanced" disclosure.
  It shows a modal "Bench sweep running…", because the turtle stops
  sending updates until it finishes. The result is wind + alternate +
  confidence.
- **Sensor health:** from the presence bits in `0116 Status` (compass,
  IMU, baro, battery monitor, sail encoder, GPS module, servo, RTC).
- **Nav state and fault**, plus an **API handshake** test →
  `API_HANDSHAKE`.
- **Compass offset** → `COMPASS_SET_OFFSET`.

---

## Phase 6 — Settings (gear icon, top-right)

This is a full-screen page opened from the top-bar gear. The gear is
highlighted while Settings is open, the bottom tab bar stays visible
with no tab highlighted, and tapping a tab or using Android's back
gesture leaves Settings.

- **Shore WiFi:** enabled toggle → `WIFI_SET_ENABLED`; SSID + password →
  `WIFI_SET_CREDENTIALS`. Report separately that the credentials were
  saved and whether the connect succeeded.
- **Connection mode** (WiFi auto / WiFi manual) → `CONNECTION_MODE_SET`.
- **Telemetry interval** (≥ 10 s) → `TELEMETRY_SET_INTERVAL`.
- **Bluetooth:** wrangle-window status. **"Turn off Bluetooth for the
  voyage"** → `BLE_SET_ENABLED 0`, with a confirm that states plainly
  that only the turtle's button can turn it back on.
- **Device:** name, firmware, serial from DIS `0x180A`, and the turtle
  clock versus the phone clock (`device_now`).
- **Danger zone:** Sleep, Reboot, Switch to airOS. Each is confirmed,
  and each ends the connection.

---

## Phase 7 — resilience and polish

- Auto-reconnect with backoff after an unexpected disconnect. A
  user-initiated disconnect does not retry.
- Empty and error states: connection dropped mid-command (after
  reconnecting, read Result to learn the outcome), a turtle whose
  window has closed, a contract version mismatch.
- **Stale bond** (seen in Phase 0). If the turtle has lost its bond
  keys ("Forget phones", or a wiped `/ble_bonds.json`) while the phone
  still holds its own, Android tries to encrypt with the old key, fails,
  and drops the link within seconds of connecting. The app must detect
  "connected, then dropped before any read completed" twice in a row,
  and tell the user to forget the turtle in Android's Bluetooth
  settings and pair again (optionally calling `removeBond()` itself).
- Branding pass per the README's visual identity (one pink element per
  screen, ghost style only).
- No background foreground-service in v1. Near-field use doesn't need
  it.

---

## Phase 8 — turtles at sea: Buwana login (planned, not in v1)

This is the answer to the "No turtles nearby" prompt: view turtles the
user manages that are out of Bluetooth range but reporting to
hopeturtles.org over WiFi. It is **read-only**, because the turtle
listens for nothing inbound.

It needs work in **three places**:

**Buwana** (`buwana.ecobricks.org`)
- Register Wrangler as its own Buwana app (`apps_tb`): a public client
  (no secret), PKCE, redirect `org.hopeturtles.wrangler:/oauth2redirect`,
  scopes `openid buwana:basic`.
- Open question: does Buwana's `/authorize.php` accept a custom-scheme
  redirect URI? If not, use an https App Link on hopeturtles.org that
  hands off to the app.

**hopeturtles.org:** a new user-facing, read-only API, separate from
the device-key `/v1` routes (`deviceAuth`):
- `POST /api/app/v1/session`. The app sends its Buwana ID token. The
  server verifies it with the existing JWKS path
  (`authController.validateIdToken`, extended to accept Wrangler's
  `client_id` as an audience), upserts the user as the web login does,
  and returns a revocable app token.
- `GET /api/app/v1/turtles` returns the turtles the user may see: those
  where `turtles_tb.buwana_id` is the user, or all for admins. This is
  the same rule `dashboardController.renderMyTurtle` applies.
- `GET /api/app/v1/turtles/:id/latest` returns the newest telemetry
  (position, battery, heading, flags incl. `journey_id`), mission
  target and `set_*` fields, and last-seen time.
- `GET /api/app/v1/turtles/:id/telemetry?since=` returns the track for
  a map trail.

**App**
- AppAuth-Android (Custom Tab + PKCE). Tokens go in
  `EncryptedSharedPreferences`.
- "My turtles" list, then a read-only Dashboard and a map trail for the
  selected turtle. Every value is labelled **"via hopeturtles.org · last
  report HH:MM"** so it is never mistaken for a live Bluetooth reading.
  Command buttons are hidden.
- Entry points: the "No turtles nearby" screen's **Log in** button, and
  an account menu on the Connect screen.

---

## Phase 9 — field testing and release

- End-to-end on real hardware: pairing, the full command set with every
  failure path provoked (no fix, clock unset, sweep in the wrong state,
  a bad WiFi password), window expiry, and sleep.
- Internal distribution per Phase 0.
- Route field issues to firmware or app. Contract changes go through
  README.md first, with a version bump if they are breaking.

---

## Out of scope for v1

- iOS. Core Bluetooth differs enough that it would be close to a
  separate app.
- Ubuntu Touch. UBports' BLE support is thinner, and nothing drives the
  need.
- Offline map tile caching (follow-up once the app is in the field).
- A multi-turtle live Bluetooth view. The app connects to one turtle at
  a time over BLE. Phase 8's "My turtles" list is the fleet view, via
  shore data.
- Commands to turtles at sea over WiFi (see 01_firmware_ble.md, out of
  scope).
