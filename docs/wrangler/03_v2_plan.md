# Part 3 — v2: one data model for Bluetooth, telemetry and the app API

**Status: PLANNED (drafted 2026-10-14).** Nothing here is built. The v1
contract in [README.md](README.md) stays the source of truth until a phase
below lands and moves its part into the README.

## Why

The turtle describes itself in three places that grew up separately:

| Surface | Who reads it | Today |
|---|---|---|
| **GATT contract** | Wrangler, nearby | v1: binary characteristics + commands ([README.md](README.md)) |
| **Telemetry JSON** | hopeturtles.org ingest | `POST /api/v1/telemetry/batch`, device-key auth; `recorded_at`, `values`, `flags`, `lat`/`lon`, `machine_state` (`telemetry_scheduler._build_full_payload`) |
| **Web API** | the hopeturtles.org site | `/api/telemetry/...`, session (Buwana web login) auth; `GET /api/telemetry/:turtle_id/route` already returns each point's `journey_id` |
| **App API** | Wrangler, anywhere | not built: `/api/app/...` is Phase 8 of the app plan |

The journey log on the phone (Wrangler 0.2.7) shows the cost. A journey's
departure lives in `journey_state.json`, its arrival in the config key
`set_arrival` (latest only), its end time nowhere, and its track in
`telemetry_queue.json` until it's sent, then only on the server. Bluetooth
carries just `journey_id`. So the app has to guess at what the turtle
already knows.

**Goal:** Wrangler shows a journey (departure, destination, route,
arrival, start and end times) from **either** source and says which:
- **Bluetooth**, straight off the turtle: for field tests with no WiFi,
  where the unsent queue *is* the track;
- **hopeturtles.org**, after a Buwana login: for turtles out of range,
  and for the parts of a track the turtle already sent (sent readings
  leave the queue).

Both sources describe the same journey in the same words, so the app
keeps one journey model and can merge them by `journey_id`.

### What the server already has (hopeTurtles.org, 2026-10-14)

- **Ingest** (`controllers/deviceApiController.js`): `POST /api/v1/telemetry`
  and `/telemetry/batch`, device-key auth. `flags` goes into
  `telemetry_tb.raw_data` verbatim; `machine_state` has its own column.
  A reading with an empty `values` object is **rejected**, and one with
  all-zero values is ignored.
- **Journey legs on the map:** `telemetryModel.getRouteForTurtle` pulls
  `$.flags.journey_id` out of `raw_data` for each point, and the my-turtle
  map highlights each leg. So the server can already draw a journey's
  track; it has no journey record (no end, no arrival per journey).
- **Latest departure/arrival only:** `turtles_tb.set_departure_*` /
  `set_arrival_*` (migration `20260907_turtle_set_fields.sql`), pushed by
  the firmware's `PATCH /v1/device`. Each new journey overwrites them.
- **Route API for people exists, but for the website:** `routes/api/telemetry.js`
  is behind the web session (`ensureAuth`), not a token an app can hold.

## Conflicts settled (2026-10-14)

- **Battery current sign.** `ina_current_ma` / `current_mA` is the
  INA219's raw reading, **negative = charging**, as the waiting screen
  shows it (bolt when `current_ma <= 0`). The server had read it as
  positive = charging since 2026-08-18 (SoC, battery KPIs, daily Wh, the
  current chart). Fixed in hopeTurtles.org: `utils/batteryCurrent.js`
  `chargeMaFromIna()` converts at every read, `raw_data` stays raw.
  Stored `battery_soc_pct` from before the fix is unreliable.
- **One copy of these docs.** This folder is it. hopeTurtles.org's
  `docs/wrangler/` and `public/wrangler/` now point here; only
  `vision.html` (the stakeholder proposal) stays on the server.

## What "v2" means here

Three things are versioned, and they move together as one milestone,
**turtle data model v2**:

- **Data dictionary (new).** One table of every shared field: name, unit,
  type, "no data" value, and where it comes from on the turtle. The GATT
  contract, the telemetry JSON and the app API all cite it. Nobody names
  a field outside it.
- **GATT contract v2.** `contract_version` = 2 in `0101`. Bumped because
  v2 reshapes the journey data (see Phase C), not for its own sake. Rules
  that don't need to change (wire conventions, bonding, command framing)
  carry over unchanged.
- **Telemetry JSON v2.** Payloads carry `"schema": 2`. The server accepts
  both: a payload without `schema` is v1. Additive where it can be
  (`flags` is stored verbatim, so new flags need no ingest change).
- **App API.** Doesn't exist yet, so it's born on the v2 dictionary:
  `/api/app/v2/...`. No v1 app API is ever built.

**Turtles in the field update slowly.** For a long time there will be v1
and v2 turtles side by side, so:
- the **app** understands v1 and v2 turtles, and ships **before** any v2
  firmware (an app that only knows v1 shows a v2 turtle read-only,
  README [Versioning](README.md#versioning));
- the **server** accepts v1 and v2 payloads;
- v1 is retired only when no turtle in service reports it.

---

## Phase A — the data dictionary

**Status: DRAFTED (2026-10-14)** as [Turtle data dictionary](README.md#turtle-data-dictionary)
in README.md: every v1 field on Bluetooth and in the telemetry JSON,
under one name, with the gaps listed. Next: review, then Phase B.

What it covers:

- **Time:** unix seconds, UTC, from the turtle's clock (`recorded_at`,
  `journey_id`, `ended_at`). The turtle's clock is the authority: the app
  and server never restamp.
- **Position:** degrees in JSON (`lat`, `lon`), `i32` × 1e7 on the wire
  (as v1's `lat_e7`/`lon_e7`). "No fix" = absent in JSON, sentinel on the
  wire.
- **Journey:** `journey_id` (start second), `departure` [lat, lon],
  `destination` [lat, lon] and its source (set / mission, as `0112`),
  `ended_at`, `arrival` [lat, lon] or none (no fix at the end).
- **Track point:** `recorded_at`, `lat`, `lon`, `machine_state`, plus
  `journey_id` when tagged. This is the subset of a telemetry reading the
  map needs.
- **Naming:** JSON names are the canonical ones. Wire fields use the same
  names (with `_e7` / `_x10` suffixes for scaled integers).
- A mapping table: each field → telemetry JSON path → GATT characteristic
  and offset → app API field.

**Done when:** the dictionary is in README.md and v1's fields are mapped
into it (unchanged ones marked "same in v1").

## Phase B — firmware: journeys the turtle remembers

**Status: BUILT, host-tested, not yet on a turtle (2026-10-14).**
`src/app/journey.py` keeps the open journey, a `history` of the last 20
and a queue of start/end `events` in `journey_state.json`; the scheduler
sends each event on the next reading (`flags.journey_event` +
`flags.journey`), every payload carries `"schema": 2`, and
`journey_log_every_s` speeds up logging during a journey. Host test:
`cd device && python3 ../tests/journey_host.py`. Done with it: telemetry
takes its position from the shared `gpsfix` cache (the fix Bluetooth
sends), and battery percent has one estimator, `ina219.pct_from_volts`.
Server note for Phase D: ingest keeps only `values`/`flags`/`confidence`/
`alt_m` in `raw_data`, so `schema` is dropped today (harmless).

The turtle keeps the facts it currently throws away, and tells the server.

- `journey.end()` keeps a **last journey** record in `journey_state.json`
  (`id`, `departure`, `destination`, `ended_at`, `arrival`) instead of
  clearing everything. The open journey stays as it is.
- A small **journey history** (the last N records, same shape) so a
  phone that was away for several journeys gets them all. N is a few
  dozen; one record is under 100 bytes.
- **Journey events in the telemetry stream:** at start and end the
  scheduler queues one reading with `flags.journey_event` = `"start"` /
  `"end"` and the journey's fields. The server learns the end time and
  arrival without a new route. Today's ingest rejects a reading with
  empty `values`, so either the event reading carries the usual sensor
  values, or v2 ingest allows an empty `values` when `flags.journey_event`
  is set (Phase D).
- **Track resolution:** while a journey is open, an optional faster
  logging interval (`journey_log_every_s`) so a short field test isn't
  one point every 2 minutes. Defaults to `telemetry_post_every_s`.
- Telemetry payloads gain `"schema": 2`.

**Flash:** this adds a few small writes per journey. The track itself
needs no new storage: it's already in `telemetry_queue.json` (JSONL,
appended, up to 5000 readings).

## Phase C — GATT contract v2: journeys over Bluetooth

**Status: BUILT in turtleOS 2.6.0, host-tested, not yet on a turtle
(2026-10-14).** In the README contract: `0119` Journey (`<IiiI>`: open
journey_id, its departure, newest ended journey id), `JOURNEY_LIST`
(0x07) and `JOURNEY_TRACK` (0x08, `<IiiB>` points, pages sized to the
MTU), Result buffer 64 → 180 bytes. `ble_fuzz_host.py`, `ble_decode.py`
and `tests/journey_host.py` cover it.

**Decision: it stays contract v1.** Everything below turned out to be
addable without breaking anything: Shore `0117` keeps `journey_id`, and
the new characteristic and opcodes are additions, which the Versioning
rules say don't need a bump. Bumping anyway would make every Wrangler
build that knows only v1 show 2.6.0 turtles read-only. The "v2" is the
data model; `contract_version` goes to 2 with the first real break.
The "sent points" count below was dropped: the turtle can't know it
(sent readings are deleted); the app uses `last_shore_sync` instead.

As planned:

- **Journey characteristic (replaces the journey part of Shore `0117`):**
  the open journey's `journey_id`, `departure`, `destination`, and the
  last journey's `journey_id`, `ended_at`, `arrival`. Read + notify on
  change.
- **Journey list and track, by command** (paged, so no transfer holds
  the command channel for long):
  - `JOURNEY_LIST page` → journey records from the history, newest first;
  - `JOURNEY_TRACK journey_id, page` → that journey's track points.
    *Built in 2.6.0 as a line-by-line pass over `telemetry_queue.json`;
    that took seconds inside the button loop, so 2.6.1 appends each
    point to a per-journey file `/journey_<id>.trk` instead and pages
    out of it with a seek.*
  Each answer says whether more pages follow. A dropped link resumes from
  the last page received.
- **Encoding of a track point:** fixed binary `<Iii B>` (`recorded_at`,
  `lat_e7`, `lon_e7`, `machine_state`), 13 bytes, against a few hundred
  for a JSON line. A full 5000-point queue is then ~65 kB.
- **What Bluetooth can't give** (2.6.0 only): readings already sent to
  the server had left the queue. From 2.6.1 the track file keeps every
  point with a fix, sent or not, up to 1000 per journey (thinned past
  that).
- Readable bonded-only, like the commands (tracks are location history).

**Done when:** README.md has the v2 contract, `ble_fuzz_host.py` covers
the new commands, and `tests/ble_decode.py` decodes the new values.

## Phase D — server: journeys as data, and the app API

Folds in app Phase 8 (Buwana login), rebuilt on the dictionary.

- **Ingest:** accept `"schema": 2`; record `flags.journey_event` into a
  journeys table (`journey_id`, turtle, departure, destination,
  `ended_at`, arrival), so a journey is a row, not something inferred
  from tagged readings. Allow an empty `values` on an event reading.
- **Backfill** journeys for v1 turtles from tagged readings already in
  `telemetry_tb` (start = first tagged point, end unknown), the same way
  `getRouteForTurtle` groups them today.
- **App API, `/api/app/v2`,** Buwana token (OIDC + PKCE, Wrangler as its
  own Buwana app), read-only:
  - `GET /turtles` — the user's turtles (`turtles_tb.buwana_id`; all for admins)
  - `GET /turtles/:id/latest`
  - `GET /turtles/:id/journeys` — journey records, newest first
  - `GET /turtles/:id/journeys/:journey_id/track` — track points
  Same field names and shapes as Phase C, in JSON. The track query reuses
  `getRouteForTurtle`'s filters (no `(0, 0)` boot fixes), keyed on
  `journey_id` instead of a time window.
- The website's session-auth routes stay as they are; the app API is a
  separate router with its own token check, like `/api/v1` has `deviceAuth`.
- Older readings tagged with `flags.journey_id` (v1 turtles) still give a
  track; the journey record is just less complete (no end, no arrival).

## Phase E — Wrangler: one journey view, two sources

**Status: Bluetooth half BUILT in Wrangler 0.2.7, unit-tested, not yet
tried against a 2.6.0 turtle (2026-10-14).** The app reads `0119`, and
on connect (and whenever `last_journey_id` changes) pulls new ended
journeys with `JOURNEY_LIST` and their tracks with `JOURNEY_TRACK`, plus
the open journey's track so far. `JourneyRecord.withTurtle` /
`withTurtlePoints` merge by `journey_id`: the turtle's departure, end,
arrival and destination win; the route is the time-ordered union of the
turtle's points and the phone's (route points now carry times). The
review screen says where each part came from. The sync is quiet and
retries a minute after a failure (e.g. not paired yet). Instead of a
`JourneySource` interface, the turtle source is the merge functions plus
the sync; the server source (after Phase D) adds a third input to the
same merge. Waiting on Phase D: the server source and the login.

As planned:

- A `JourneySource` interface with three implementations: the phone's
  own log (today's `JourneyStore`), Bluetooth (Phase C) and
  hopeturtles.org (Phase D).
- **Merge by `journey_id`.** Turtle-stamped facts (departure, end,
  arrival) beat the phone's guesses. A track is the union of the points
  from every source, ordered by `recorded_at`.
- The review screen labels where each part came from: "from the turtle",
  "via hopeturtles.org · last report HH:MM", "seen by this phone".
- Login (Phase 8's "No turtles nearby → log in" flow) opens the server
  source for turtles out of range: read-only, as planned.
- The app keeps supporting v1 turtles with today's phone-side log.

## Phase F — rollout

1. App with v1 + v2 support (and the server source behind login) ships first.
2. Server accepts `"schema": 2` and serves `/api/app/v2`.
3. Firmware v2 goes to the test turtles, then the fleet.
4. v1 retires when no turtle in service reports it.

## Order and dependencies

```
A dictionary ──► B firmware journeys ──► C GATT v2 ──► E app (Bluetooth source)
            └──────────────────────────► D server  ──► E app (server source, login)
```

B serves both branches. C and D can then proceed in parallel: C first if
WiFi-less field tests come first, D first if remote viewing does.

## Open questions

- **Journey history size (N)** and what happens to its oldest records.
- **Sent readings:** should the turtle keep the open journey's sent
  points too, so Bluetooth alone gives the whole track? That costs flash;
  the server already has them.
- **Destination changes mid-journey:** keep only the final destination,
  or a list with times?
- **App upload:** may the app forward a track it pulled over Bluetooth to
  the server for a turtle that never reaches WiFi? It would need a
  write route on the app API, which this plan keeps read-only.
- **Retiring v1:** how to know when no turtle in service still reports it.
