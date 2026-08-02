# Navigation Roadmap — Road to the Full Olive Turtle Autonomy Stack

This document tracks what remains between the **minimal SAIL-NAV system**
now running in `device/src/nav/` and full unattended autonomy.

The governing idea is the **AOELL learning architecture**
(`docs/AOELL nav revision.md`): turtleOS is not a finished navigation
controller but an *instrumented sailing experiment*, and every voyage —
especially every failure — must produce evidence that improves the next
decision, the next policy version, and the next turtle.

The roadmap runs in **three development streams**:

- **Aboard** (turtleOS firmware) — the turtle acts, observes, evaluates,
  logs and learns.
- **Ashore** (hopeturtles.org, culminating in the `mission_review` panel)
  — the team and, later, models interpret those records and send back
  versioned policy adjustments.
- **Bale mesh** (`docs/bale_network_vision.md`) — turtles talk to each
  other, so lessons and records propagate between hulls at sea and reach
  shore through whichever hull finds connectivity first.

They are built in parallel, meet at defined integration milestones, and
complement each other completely: the turtle's logs are only as useful as
the shore system that can make sense of them; the shore system is only as
useful as the causal records the turtle keeps; and both are limited by how
much of that evidence actually survives the sea — which is what the mesh
exists to fix.

It is written to be readable by assistant engineers as well as the core
team: each section opens with a plain-language summary of *why* the work
matters, followed by concrete tasks and where each lands in the codebase.

For the team-wide, non-technical overview (with diagrams and animations),
see `docs/nav_system/index.html`.

---

## How the turtle actually sails — the ground rules

Everything in this roadmap follows from seven facts about the hull. They
are stated up front because each one shapes the order of the work:

1. **The rudder is fixed. Only the sail rotates.** There is no steering
   servo. "Sail servo" is the only servo. The rudder, hull, and keel are
   passive.
2. **Sail trim is not direct steering.** Moving the sail changes the
   aerodynamic force on the boat; the fixed rudder, hull, and keel then
   convert that force into some combination of forward movement and yaw.
   **Whether that relationship is consistent enough for dependable
   navigation is the central open question** — establishing it
   experimentally is what Phases P and T exist for.
3. **Controllers come after evidence.** A heading controller assumes the
   actuator has a predictable effect on the controlled variable. Until a
   10° sail change is shown to produce a repeatable heading response,
   wrapping a controller around it automates confusion rather than
   navigation. `nav/pid.py` stays in the tree but is **gated behind the
   turning-influence experiments** (Phase T).
4. **Sensing is fast; acting is slow.** The IMU can *detect* rotation at
   20–50 Hz, but the sail is never commanded at that frequency. Detection
   is a sensing task; response is an experiment (see the cadence table).
5. **The ladder is fixed:** demonstrate luff detection → demonstrate
   useful propulsion → demonstrate repeatable turning influence → only
   then attempt a deliberate tack.
6. **Wind estimate accuracy is to be established experimentally.** No
   accuracy figure is claimed until it is measured on water.
7. **ARRIVAL does not hold position.** A sail-only turtle with a fixed
   rudder cannot be assumed to station-keep. ARRIVAL means: inside the
   arrival radius → feather the sail, report arrival, and keep reporting
   position while drifting.

## The AOELL learning architecture — learn twice

The AOELL revision reframes what the firmware *is*. Every onboard cycle is
a **causal episode**, not merely a software loop:

> **Act → Observe → Evaluate → Log → Learn** (aboard)
> **→ Share → Visualize + interpret → Learn** (ashore) **→ Adjust → Act again**

- **Act** — every cycle begins with an explicit, intentional action, even
  when the sail does not move (`SENSE_AND_BASELINE`, `HOLD_SAIL`,
  `SET_SAIL_ANGLE`, `EXPLORATORY_SAIL_STEP`, `LUFF_SWEEP`,
  `RESTORE_PREVIOUS_ANGLE`, `FEATHER_SAIL`, `ENTER_SAFE_MODE`,
  `SLEEP_OR_REDUCE_SAMPLING`). Recording `HOLD_SAIL` matters most:
  hold periods are the control cases every intervention is compared
  against.
- **Observe** — concentrated around the action: a pre-action baseline plus
  post-action measurements (GPS/SOG/COG, fused heading, sail commanded vs.
  actual, wind estimate, VMG, cross-track, battery, servo behaviour, all
  with confidence). Short high-rate buffers around sweeps, sail moves,
  spins, and stalls; summary statistics for routine periods.
- **Evaluate** — at multiple configurable horizons (initial trial values
  30 s / 120 s / 300 s), judged primarily by **course over ground and
  VMG**, not compass heading — a fixed-rudder boat can point one way and
  travel another. Outcomes: `SUCCESS`, `MIXED`, `FAILURE`,
  `NO_MEASURABLE_EFFECT`, `NOT_EVALUABLE` — and `NOT_EVALUABLE` is *not*
  failure; a lost fix must never become a false training label.
- **Log** — the turtle's *reasoning*, not just its readings: what it
  believed, why it chose the action, what it predicted, what it observed,
  how it scored the result, what it updated, and the firmware / policy /
  model / config versions in force. Each cycle carries a device-generated
  `cycle_id` linking back to the prior cycle and learned state.
- **Learn aboard** — modest and inspectable: confidence updates, a small
  empirical response table, bounded state inside a *versioned* policy.
  No silent self-retraining at sea; every change logged and reversible.
- **Share / Visualize / Learn ashore / Adjust** — the records travel to
  hopeturtles.org, where the `mission_review` panel lets the team (and
  later, models) replay the voyage, compare intention with reality,
  annotate, assemble training data, and send back *versioned, signed,
  reviewable* policy adjustments.

This makes turtleOS and hopeturtles.org **two parts of one learning
system**. The governing principle:

> **Act deliberately, observe carefully, evaluate honestly, log
> completely, and learn twice: once aboard for the next decision, and
> again ashore so the whole team — and eventually the whole turtle
> fleet — can improve.**

Full specifications (event contract fields, batch/ack protocol, database
tables, API surface, ML stages, statistical safeguards, and the
model/policy lifecycle) live in `docs/AOELL nav revision.md`; this roadmap
sequences that work.

## The bale — the third column

Plain-language why: AOELL assumes the evidence gets home. Today it only
does if the hull does. A turtle records to its own flash and waits for a
shore access point that, on a real mission, most likely never comes until
it arrives — and a turtle lost at sea takes every reading it ever took
with it. That is a learning system with a single point of failure per
hull.

Turtles are cheap, open source, and built to be deployed in numbers, and
they drift in loose company. A **bale** — the collective noun we are
claiming for turtles, alongside flocks of sheep and swarms of wasps —
changes the unit of survival from the turtle to the fleet. If turtles can
talk to each other over LoRa:

- a reading taken by one turtle can be **carried home by another**;
- a turtle that solves the wind can **tell its neighbours what it
  learned**, so a neighbour can skip or seed an expensive luff sweep;
- a turtle that finds shore connectivity becomes a **gateway for the whole
  bale**, flushing everything it carries, not just its own queue;
- a **mother turtle** — same hull, extra storage, no sailing mission — can
  shadow the bale as its flying recorder, so a voyage produces evidence
  even when nothing makes landfall.

The loss model inverts: losing a hull stops being a data loss and becomes
the loss of a node whose records have been somewhere else for days.

This is why the mesh is a *learning* feature rather than a comms feature,
and why it earns a column beside aboard and ashore. It multiplies both
directions of the loop — **at sea**, turtles learn from each other within
a voyage instead of each repeating the same discoveries alone; **ashore**,
the team receives evidence from hulls it will never recover. A bale is
also a genuinely better experiment than a scatter of isolated devices:
many hulls, same water, same weather, different policies and rigs, with a
shared clock and shared positions.

The sea is unusually good ground for this. LoRa's range budget is
destroyed by buildings and terrain, of which open water has none; the ISM
bands are near-silent mid-ocean; turtles released together drift
coherently, so neighbours stay neighbours for days and a neighbour table
refreshed a few times a day describes reality; and nothing is urgent — a
record is as useful three days late as three seconds late, which reduces
the problem to **eventual delivery** and makes store-and-forward the
correct design rather than a compromise. The honest counterweight is
antenna height: 30 cm above the water, periodically below the wave crests,
so links appear and vanish in seconds even when average topology is stable
for days. That argues for store-and-forward and against anything
resembling a session.

**The mesh is strictly additive.** A turtle with no LoRa hardware, or with
LoRa that hears nobody, behaves exactly as turtles behave today: queue to
flash and sail on. There is no flag day, and a mixed fleet is the expected
configuration for the first several deployments.

Full vision, hardware analysis, wire-format sketch, and open questions are
in `docs/bale_network_vision.md`; Stream C below sequences that work.

## The foundations we build on

- **The state-machine architecture** (BOOT → ACQUIRE → SAIL_NAV →
  ARRIVAL, any → SAFE) is the skeleton, and it stays.
- **GPS logging is our essential ground truth**: position, course over
  ground, speed over ground, and progress toward or away from the
  waypoint. Every experiment below is judged against the GPS track.
- **The AS5600 encoder** gives *actual* sail position, not merely the
  commanded servo position — and the difference between them is itself a
  signal (stall, jam, slack rigging).
- **The IMU** provides heading, heel, turn rate, and detection of wave
  disturbance and unstable motion.
- **The existing telemetry schema already carries what we need**:
  `values_json`, `confidence_json`, `flags_json`, GPS coordinates, and
  separate device and server timestamps. Experiments log through it
  without schema changes.
- **Luff sensing is an excellent experiment** precisely because it avoids
  an exposed wind vane — but its accuracy and usefulness must be
  demonstrated rather than assumed.
- **Confidence scoring belongs at the heart of the system**, not merely
  in diagnostics. Every derived quantity (wind estimate, heading,
  position, sail→yaw model) should carry a confidence value that gates
  what the controller is allowed to do with it, and should ship home in
  `confidence_json`.

## What is already in place (June 2026)

In plain terms: the turtle can already boot up, figure out which way it
is pointing (compass), sweep its sail to feel for the wind, compute the
bearing to a GPS waypoint, and put itself into SAFE mode when something
goes wrong. What follows is about *validating* that its sail movements
actually produce the navigation we hope for — and then making the loop
more accurate, more robust, and eventually able to sail upwind.

- Five-state machine (`nav/state_machine.py`): BOOT → ACQUIRE → SAIL_NAV →
  ARRIVAL, any → SAFE, reboot → BOOT. Reported in every telemetry payload
  as `machine_state` and stored/displayed on hopeturtles.org.
- Luff-sweep wind detection (`nav/luff.py`): stepwise, non-blocking,
  moving-baseline threshold calibration, sweep A/B onset capture,
  circular-midpoint wind solve. Verified in host simulation; **on-water
  accuracy to be established experimentally.**
- Minimal autopilot (`nav/controller.py`): heading-error → sail-servo
  mapping (PID with placeholder gains — scaffolding, not a tuned
  controller; see ground rule 3), great-circle bearing to waypoint,
  forward-only waypoint sequencing with flash persistence
  (`/nav_state.json`), arrival → ARRIVAL, GPS-loss timer → SAFE, low
  battery → feather.
- Three-circle machine-state screen (`ui/screens/state.py`): heading
  running-ball, sail diameter line, wind arrow; double-click in ACQUIRE
  starts the first sweep; double-click in SAIL-NAV re-sweeps.
- Shared GPS fix cache (`nav/gpsfix.py`) so NavController and the
  telemetry scheduler don't starve each other on the single UART buffer.

---

## Control cadences — sense fast, act slowly

Plain-language why: the IMU can sense at tens of hertz, but a sailboat
answers over seconds, and a servo commanded constantly burns the battery.
The system's rhythm is therefore deliberately layered: sample fast, decide
at boat speed, move the sail rarely and only when it matters.

| Function | Suggested active cadence | Physical action |
|---|---:|---|
| IMU sampling and heading fusion | 20–50 Hz | None |
| Rotation/spin detection | 10–25 Hz | Trigger only after persistent excessive turn rate |
| Heading controller | 2–5 Hz | Compute a desired sail change |
| Servo command | At most every 1–3 seconds, when needed | Move only beyond a deadband |
| GPS position acquisition | 0.2–1 Hz | None |
| Route, bearing and cross-track calculation | Every 5–30 seconds | Update desired heading |
| Waypoint/geofence evaluation | Every 10–60 seconds | Change navigation mode if necessary |
| Wind re-acquisition sweep | Event-driven; perhaps every 30–60 minutes as backup | Full sweep only when confidence is low |
| Battery and hull health | Every 10–60 seconds | Immediate response to serious fault |
| Routine telemetry | Every 5–30 minutes | Transmit summary |
| Emergency telemetry | Immediate | Transmit fault/event |

Note what this table does **not** contain: any path where a fast sensing
loop commands the servo directly. Spin *detection* runs at 10–25 Hz;
whether and how to *respond* is decided by the slow layers, and only after
the response experiments (Phase T) tell us what a response should be.

---

## The official IMU: the "MPU6050 module" (MPU-9250 chip)

**Decision (July 2026):** the sourced and wired IMU module is the official
motion sensor for the turtles.

### One important naming clarification

The module is *sold* as an "MPU6050 module", but the supplier's own spec
sheet says the chip on the board is an **MPU-9250**. These are different
chips:

| | MPU6050 | **MPU-9250 (what we have)** |
|---|---|---|
| Gyroscope (turn rate) | yes | yes (±250/500/1000/2000 °/s) |
| Accelerometer (tilt/motion) | yes | yes (±2/4/8/16 g) |
| Magnetometer (compass) | **no** | **yes** (AK8963 inside, ±4800 µT) |
| Axes | 6 | 9 |

This is good news — the MPU-9250 is the more capable part, and its
built-in compass could eventually replace the separate GY-271 board. But
it means **the driver must be written for the MPU-9250, not the MPU6050**;
their register maps differ.

First job when a unit is on the bench: confirm which chip is really on the
board by reading the WHO_AM_I register (address `0x75`). Expected values:
`0x71` = MPU-9250 ✅, `0x70` = MPU-6500 (9250 without the compass),
`0x68` = genuine MPU6050. Cheap modules are sometimes mislabeled in *both*
directions, so verify every batch. `tests/i2c_scan.py` plus a two-line
register read does this in under a minute.

### Wiring — resolved, IMU lives at 0x69

The IMU's factory I2C address (0x68) clashed with the DS3231 clock chip,
which already sat there. **Fixed on the module itself**: AD0 is strapped
to the module's own VCC pin, which flips the address to **0x69** — free
on our bus. This is done; no further hardware work is needed here. The
module is powered from the XIAO's 3V3 pin (never 5 V — AD0 ties straight
to the 3.3 V-only sensor die).

### Updated I2C bus map with the IMU installed

| Device | Address | Notes |
|---|---|---|
| AK8963 compass (inside the MPU-9250) | 0x0C | visible once bypass mode is enabled; **current heading source as of Phase 0** |
| QMC5883L compass (GY-271) | 0x0D | retired from the runtime path as of Phase 0; no clash with 0x0C |
| OLED | 0x3C | |
| INA219 battery monitor | 0x40 | |
| DS3231 clock | 0x68 | keeps its address; IMU moves instead |
| **MPU-9250 IMU** | **0x69** | with AD0 strapped high |

The MPU-9250's internal AK8963 compass is normally hidden behind the
MPU-9250 itself; setting the "I2C bypass" bit (register `INT_PIN_CFG`,
`0x37`) exposes it directly on our bus at 0x0C. That address does **not**
collide with the GY-271 at 0x0D, so both compasses can coexist during the
changeover and be compared against each other on the bench.

---

# The phases — three streams, one learning loop

The roadmap runs as an **experimental ladder in three streams**:

- **Stream A (aboard — turtleOS):** **Phase 0 (IMU bring-up) runs
  immediately, ahead of everything else** — it is a hardware-integration
  task, not an experiment, and every later phase (fusion, luff validation,
  turning influence) depends on the MPU-9250 being read reliably. AOELL
  foundations (A) follow, then the remaining sensing phase (S: fusion
  proper), then L; propulsion (P) and turning influence (T) must be
  demonstrated before any closed-loop heading control (H); tacking (K)
  comes only after all of the above. Reliability (R) and
  watchdog/persistence (W) proceed in parallel where they don't depend on
  the ladder.
- **Stream B (ashore — hopeturtles.org):** the data model, ingestion, and
  the `mission_review` panel — the major later-phase destination that
  every aboard phase is working toward connecting to. Without it, the
  turtle's experiments produce data nobody can interpret; with it, every
  failure becomes a lesson.
- **Stream C (bale mesh — turtle to turtle):** the LoRa store-and-forward
  network that makes evidence survive the hull that gathered it and lets
  turtles learn from each other mid-voyage. It is deliberately sequenced
  *behind* Stream A's foundations — a mesh that carries records defined by
  an unfinished contract would have to be rebuilt — but its Phase 0 paper
  work can and should start immediately, because the wire format is the
  most expensive decision in the project to reverse.

The streams are developed in parallel and **rejoin at the integration
milestones (J1–J5)** defined after the phase lists. None can finish alone:
the experiment phases (L, P, T) generate their evidence *through* the
AOELL pipeline, the shore-side models and policies feed adjusted behaviour
*back* to the turtle, and the mesh decides how much of that evidence ever
arrives. Failing, learning, failing, learning — knot by knot, inch by
inch, toward a robust and potent turtle navigation system.

---

# Stream A — aboard (turtleOS)

## Phase 0 — IMU bring-up (immediate, blocks everything else)

Plain-language why: the MPU-9250 is sourced, wired, and strapped to a
free address — the only thing standing between it and the rest of the
roadmap is code. This is deliberately **not** bundled into Phase A or
Phase S: it is a short, mechanical hardware-integration task, and every
later phase (AOELL, fusion, luff validation, turning influence) implicitly
assumes it is already done. Do this first, before AOELL foundations, and
before continuing the old GY-271/compass-only path.

- [ ] **WHO_AM_I bench check**: read register `0x75` at address `0x69`;
  expect `0x71` (MPU-9250). `tests/i2c_scan.py` plus `tests/mpu9250_bench.py`
  (new — plain register pokes, no driver dependency) confirms the chip
  identity, and also settles the warm-up and axis-convention questions
  below. **Still needs to be run on real hardware** — not yet confirmed.
- [x] **`src/drivers/mpu9250.py` driver**: init, gyro/accel raw reads,
  bypass-mode enable (`INT_PIN_CFG`, `0x37`) so the AK8963 magnetometer
  answers at `0x0C`. Written; `heading()` matches the existing
  `QMC5883L`/`HMC5883L` contract so screens/`nav/heading.py` didn't need
  to change their call sites. **Axis convention (`atan2(y, x)`) is a
  starting assumption, not yet bench-verified on this breakout** — see
  `tests/mpu9250_bench.py`.
- [x] **Boot-time detection and registration**: `main.py`'s `step_mpu9250()`
  scans for `0x69`, imports the driver, wakes the chip, enables bypass,
  and reports a found/not-found/bypass-failed tri-state — same pattern as
  `step_as5600()` — replacing the old `step_compass()`. `I2C_ADDR_MPU9250`/
  `I2C_ADDR_AK8963` are in the generic `[BOOT] I2C scan: [...]` name table
  too. **Not yet confirmed on hardware.**
- [ ] **Warm-up question**: does the MPU-9250 need a settle period before
  its first reading is trustworthy, the way the ENS160/AHT21 pair does
  (`warmup_seconds`)? The datasheet suggests gyro/accel data is valid
  within tens of milliseconds of power-up with no analogous "burn-in";
  `tests/mpu9250_bench.py` compares an immediate AK8963 read against one
  taken 1.5 s later to settle this rather than assuming it. If a delay
  turns out to be needed, it's a short hardcoded `time.sleep_ms()` inside
  `AK8963.__init__()`, not a `cfg["warmup_seconds"]`-style knob — this is
  a much smaller ask than the ENS160/AHT21's multi-second thermal warmup.
- [x] **Retire the GY-271 (QMC5883L) and treat this as the sole IMU**:
  `compass.py` and `nav/heading.py` (not `sailpoint.py` — that screen is
  100% AS5600 sail-angle and never touched the compass; this checklist
  item's original wording was imprecise) now read `mpu9250.py` instead of
  `hmc5883l_qmc5883l.py`. The old driver file is kept in the tree,
  unimported, with a comment marking it retired-from-runtime and reserved
  for the Phase S bench comparison — not deleted, per the "one deliberate
  exception" wording below.
- [x] **Wire it into the existing sensor screens**: `compass.py` and
  `nav/heading.py` (consumed by `nav/controller.py` → the three-circle
  state screen's heading circle) now show a heading sourced from the new
  driver with no other behavior change.
  *Gate: `[BOOT]` reports the IMU found; the compass screen and the
  three-circle state screen display a live heading read from the
  MPU-9250, with the old GY-271 path removed rather than left running in
  parallel. **Code is in place; the gate itself — actually seeing this
  work on a live board — still needs to be run and confirmed.***

## Phase A — AOELL foundations aboard

Plain-language why: before the turtle runs more experiments, it must be
able to *explain* them. If sail commands, sensor packets, and GPS points
are stored as unrelated time series, we will see where the turtle went but
never reliably know why. Phase A gives every action a causal record and a
guaranteed path to shore.

- [ ] **AOELL event contract** (`nav/aoell.py` or similar): define the
  cycle record — `cycle_id`, mission/turtle refs, prior cycle + learned
  state, action type (the nine initial types), trigger and decision rule,
  previous/commanded sail angles, intent class (observational /
  exploratory / corrective / safety), predicted direction + magnitude,
  planned evaluation horizons, confidence scores, battery/safety
  constraints, and rejected-candidate notes. First action after boot:
  `SENSE_AND_BASELINE`.
  *Gate: one cycle can be reconstructed unambiguously from local logs.*
- [ ] **Observation windows**: pre-action baseline + post-action capture;
  short high-rate buffers around sweeps / sail moves / spins / stalls;
  summary statistics for routine periods (storage- and battery-frugal).
- [ ] **Multi-horizon evaluation**: 30 s / 120 s / 300 s (config keys, to
  be tuned from trial data); outcomes `SUCCESS` / `MIXED` / `FAILURE` /
  `NO_MEASURABLE_EFFECT` / `NOT_EVALUABLE`; judged by COG/SOG/VMG and
  cross-track change against the preceding hold period, not by compass
  heading alone.
- [ ] **Local logging + resumable sharing**: append-only local queue
  (cycle records, evaluations, event buffers, faults, version metadata);
  batches carry `device_uid`, `mission_id`, `batch_id`, sequence range,
  device timestamps, record count, checksum, schema version; server acks
  highest contiguous sequence; retry until acknowledged; idempotent
  re-upload. Millisecond event timestamps + device-generated IDs (the
  server's whole-second `UNIQUE (device_id, recorded_at)` cannot
  distinguish multiple events in one second).
  *Gate: delayed and duplicate uploads produce one ordered server record.*
- [ ] **Learn aboard (bounded)**: wind-confidence updates, a small
  empirical response table, action-reliability estimates, exploration
  back-off on low battery / low confidence / unfamiliar conditions — all
  as bounded state inside a **versioned policy**, logged and reversible.
- [ ] **Policy receive path**: `GET /api/v1/policy/manifest` +
  `GET /api/v1/policy/:version` + ack; checksummed, versioned, rollback
  retained; never execute a partial or incompatible download.

## Phase S — Sensor fusion (MPU-9250)

Plain-language why: today the turtle's sense of direction comes from a
magnetometer alone. A compass is truthful on average but jittery
second-to-second, and it lies when the boat heels over. A gyroscope is
the opposite: silky-smooth over seconds but slowly drifts. Fusing the two
("complementary filter") gives a heading that is both smooth *and* true,
and the accelerometer tells us which way is down so we can un-tilt the
compass reading on a heeled boat. This phase is pure sensing — nothing in
it commands the servo.

- [x] **Hardware selected and wired**: MPU-9250 module (see section above).
  Default address 0x68 collides with the DS3231 RTC — resolved by
  strapping AD0 to VCC on the module (→ 0x69).
- [ ] **Bench verification**: WHO_AM_I check (expect `0x71`) and
  `tests/i2c_scan.py`/`tests/mpu9250_bench.py` showing 0x69 (and 0x0C once
  bypass is enabled) alongside the existing devices. Same item as Phase 0's
  bench check above — still needs to be run on real hardware.
- [x] **`src/drivers/mpu9250.py` driver**: init, gyro/accel/mag reads at
  the ranges we need (±250 °/s and ±2 g, the chip's power-on defaults —
  plenty for a sailboat), bypass-mode enable for the AK8963. Built in
  Phase 0 (`read_gyro()`/`read_accel()`/`heading()`); the raw-read methods
  exist now specifically so this phase's fusion work can consume the same
  driver instance without a rewrite.
- [ ] `Mpu9250HeadingSource` in `nav/heading.py` implementing the
  complementary filter, sampled at 20–50 Hz per the cadence table:
  `heading = 0.98 × (heading + gyro_yaw_rate × dt) + 0.02 × mag_heading`
  The `HeadingSource` abstraction already exists so this drops in without
  touching NavController or any screen.
- [ ] **Tilt-compensated heading** (mag + accelerometer) — a heeled
  sailboat reads garbage from a flat-mounted magnetometer; this matters
  more at sea than the gyro fusion does.
- [ ] **Heel and turn-rate outputs** exposed alongside heading, plus a
  **motion-disturbance metric** (accel variance) for detecting wave
  action and unstable motion. All feed `confidence_json`.
- [ ] **Rotation/spin detection** at 10–25 Hz: flag persistent excessive
  turn rate as an event (telemetry + state screen). **Detection only** —
  no automatic sail response until Phase T tells us whether one helps.
- [ ] **Compass changeover decision**: run the AK8963 (inside the IMU) and
  the GY-271 side by side on the bench; if the AK8963 is as good or
  better, retire the GY-271 and free the board space. Keep the GY-271 as
  the fallback path in `heading.py` either way.
- [ ] `is_stable()` gate for BOOT→ACQUIRE: heading drift < 2°/min over a
  bench window.

## Phase L — Luff sensing validation

Plain-language why: the luff sweep is our wind sensor, and it works in
simulation — but a simulation cannot flutter. Before anything downstream
depends on the wind estimate, we must measure how well the sweep finds
the wind on real water, in real chop, and attach an honest confidence
number to every solve.

- [ ] **On-water accuracy experiments**: repeated sweeps against a known
  reference wind (shore flag + handheld vane, or two turtles
  cross-checking); log every sweep's A/B onsets, solve, and the reference
  through `values_json`. Output: a measured accuracy figure to replace
  "to be established experimentally."
- [ ] **Wind-solve confidence score**: derived from flutter amplitude
  vs. baseline, A/B symmetry, and agreement with the previous solve.
  Ships in `confidence_json`; low confidence triggers a re-sweep and
  gates what SAIL_NAV is allowed to attempt.
- [ ] **Sweep failure handling**: `LuffSweep` fails cleanly today
  (`no luff (A)/(B)`) but nothing retries. Policy: retry with slower
  speed and lower threshold; N consecutive failures → SAFE. Known edge:
  wind sitting exactly at `sail_min_deg` inflates the moving-baseline
  threshold (calibration overlaps flutter) — detect via abnormally high
  calibration peak and restart from the opposite stop.
- [ ] **Light-wind adaptation**: scale `luff_threshold_mult` down and
  `luff_sweep_dps` down when solved-wind confidence is low / flutter
  amplitude is small.
- [x] **Periodic re-sweep cadence** (fixed interval): implemented —
  `luff_resweep_s` config (default 600 s); NavController auto-starts a
  sweep in SAIL_NAV when the timer expires and re-arms after every sweep;
  countdown shown bottom-right on the turtle waiting screen.
- [ ] **Event-driven re-sweep**: re-sweep when wind confidence drops or
  after a heading change > 30°, with the fixed interval relaxed toward a
  30–60 minute backup role per the cadence table. Needs a heading-delta
  tracker in `NavController`.
- [ ] **AOELL instrumentation**: every sweep is a `LUFF_SWEEP` cycle with
  its high-rate encoder buffer, A/B onsets, solve, and confidence logged —
  so `mission_review` can analyze sweep repeatability and outward/return
  disagreement across the whole record, not just the trials we watched.

## Phase P — Propulsion demonstration

Plain-language why: before asking "can we steer?", ask "can we go?". This
phase demonstrates that a chosen sail trim produces measurable forward
progress, judged against the GPS ground truth (position, course over
ground, speed over ground).

- [ ] **Encoder↔servo↔wind trim calibration.** The minimal loop steers
  around servo neutral (90°) and "feathers" by centering. Real trim needs
  the mapping between AS5600 encoder degrees (0–360, arbitrary zero),
  servo command degrees (0–180), and boat axis. Add a one-time
  calibration routine + config offsets.
- [ ] **Trim-vs-speed experiments**: for a set of wind angles, hold a
  series of sail trims and log SOG/COG per trim. Output: a coarse polar
  ("at wind angle X, trim Y moves us best") and demonstrated settings —
  CRUISE = wind_angle ± attack offset; FEATHER = sail edge-on to solved
  wind.
- [ ] **Progress metric in telemetry**: velocity-made-good toward the
  active waypoint, computed from GPS, logged in every routine packet —
  the single number that says whether the turtle is actually getting
  there.
- [ ] **AOELL instrumentation**: trims are `SET_SAIL_ANGLE` cycles
  bracketed by `HOLD_SAIL` control periods, evaluated at all three
  horizons — so the polar is assembled ashore from evaluable cycles, with
  confounded ones (`NOT_EVALUABLE`) honestly excluded.

## Phase T — Turning influence characterization

Plain-language why: this is the make-or-break experiment of the whole
roadmap. With a fixed rudder, sail trim changes the force on the boat and
the hull decides what that force does. Does easing or sheeting the sail
yaw the boat predictably? By how much, how fast, how repeatably? Until
these experiments produce a usable sail→heading-response model, no
closed-loop heading controller — PID or otherwise — is justified.

- [ ] **Step-response experiments**: from steady sailing, command a fixed
  bounded sail change (±5°, ±10°, ±20° as `EXPLORATORY_SAIL_STEP` cycles)
  and log fused heading, gyro turn rate, and GPS COG at the 30/120/300 s
  horizons. Repeat across wind angles and in both directions, always
  bracketed by `HOLD_SAIL` control periods — without the holds we cannot
  tell the sail's effect from current, waves, or a wind shift that would
  have happened anyway.
- [ ] **Repeatability analysis** (ashore, in `mission_review`): same
  input, same conditions — same response? Quantify the spread; compare
  clockwise vs. anticlockwise response probability, response by
  luff-relative wind angle, energy per correction. This number decides
  whether a heading controller is viable at all.
- [ ] **Spin-response experiments**: when the spin detector (Phase S)
  fires, does any sail action (feather? sheet in? ease?) reliably arrest
  the rotation — or accidentally worsen it? Until answered, spin
  detection remains report-only.
- [ ] **Sail→yaw model + confidence**: distill the experiments into a
  simple model (even a lookup table by wind angle) with a confidence
  score. This model is the *prerequisite artifact* for Phase H.

## Phase H — Heading control (only after Phase T)

Plain-language why: with a demonstrated sail→yaw relationship, a heading
controller becomes legitimate. Per the cadence table it computes at
2–5 Hz, commands the servo at most every 1–3 seconds, and only moves the
sail beyond a deadband — sailing is patient, and servo current is our
biggest power drain.

- [ ] **Controller structure decision**: keep PID (`nav/pid.py`) only if
  Phase T shows a roughly linear, low-lag response; otherwise a simpler
  rule-based / deadband controller derived from the sail→yaw table.
  Gains/parameters become config keys (`pid_kp/pid_ki/pid_kd` or
  equivalents) so water trials don't need reflashes.
- [ ] **Servo command discipline**: deadband on commanded change,
  slew-rate limiting (cuts MG996R current spikes), minimum 1–3 s between
  commands, and a low-pass on controller output so micro-corrections
  don't burn servo power on a multi-day crossing.
- [ ] **Stall detection**: flag a stall when the AS5600 angle stops
  tracking the command (rigging jam, weed).
- [ ] **Tethered water trials**: heading hold target ±10° in calm water —
  now a meaningful test, because the plant is characterized.
- [ ] **Cross-track error bias**: bearing bias proportional to lateral
  offset from the track line between the previous and active waypoint,
  recomputed every 5–30 s per the cadence table. `nav/bearing.py` needs a
  `cross_track_m()` helper.

## Phase K — Tacking (much later, deliberately)

Plain-language why: tacking is last among the sailing features on
purpose. It presumes everything before it: a trusted wind solve
(Phase L), demonstrated propulsion (Phase P), and repeatable turning
authority (Phases T + H). Attempting it earlier would be testing three
unproven systems at once.

- [ ] **No-go-zone tack sequence**: if the destination lies within ~45° of
  upwind, alternate close-hauled legs instead of pointing into the zone.
  A `nav/tack.py` state within SAIL_NAV.
- [ ] **Post-tack re-sweep**: a tack invalidates the wind solve's frame;
  trigger an event-driven re-sweep (Phase L) after every tack.
- [ ] **Tack success metric**: velocity-made-good upwind across a full
  zig-zag, from the GPS track.

## Phase R — Reliability

Plain-language why: a turtle at sea is on its own. This phase is about
noticing when something is wrong (GPS gone quiet, position that doesn't
match the compass, drifted outside the allowed area, water inside the
hull) and reacting safely instead of sailing off confidently in the wrong
direction.

- [ ] **Dead-reckoning** during GPS dropouts (fused heading + last known
  SOG) before the SAFE timer fires — a short-duration estimate only.
  Depends on the Phase S driver work.
- [ ] **GPS spoofing detection**: compare RMC COG (already parsed into
  `nav/gpsfix.py:cog_deg()`) against compass heading; sustained
  disagreement beyond leeway → SAFE.
- [ ] **Geofence**: polygon or radius bound, evaluated every 10–60 s per
  the cadence table; breach → SAFE. Config schema + point-in-area check
  in `nav/bearing.py`.
- [ ] **Thermal / moisture sensors** → SAFE + immediate emergency
  telemetry packet.
- [ ] **SAFE manual reset gesture**: SAFE→ACQUIRE transition exists in the
  state machine but no UI triggers it yet. Decide the gesture (e.g.
  long-hold on the state screen) and implement.
- [ ] **ARRIVAL behavior, honestly defined**: inside arrival radius →
  feather, report arrival, keep reporting position while drifting. If
  drift carries the turtle back outside the radius, re-enter SAIL_NAV
  toward the same waypoint. No station-keeping is claimed or attempted.

## Phase W — Watchdog + persistence

Plain-language why: over a multi-day crossing the software *will* hit a
condition nobody predicted. A watchdog restarts a hung turtle
automatically, and mission persistence means a restarted turtle picks up
where it left off instead of forgetting its trip.

- [ ] **Hardware watchdog**: deliberately deferred. Existing blocking flows
  (boot pipeline 500 ms holds, carousel dwells, `time_flow`) would trip a
  tight WDT. Requires an audit pass to thread `machine.WDT.feed()` through
  every loop, or a move to scheduled feeding. Do not enable before that.
- [ ] **Software heartbeat watchdog** monitoring main-loop stall.
- [x] Waypoint index persisted to `/nav_state.json` (done).
- [ ] **Mission resume hardening**: on reboot mid-mission, BOOT → ACQUIRE
  currently requires a manual double-click to re-sweep and resume. For
  unattended recovery, add a config flag (`auto_resume: true`) that
  auto-starts the sweep in ACQUIRE when a persisted mission index exists.

---

# Stream B — ashore (hopeturtles.org)

Plain-language why: the turtle can log perfect causal records and they are
still worthless if nobody can replay, question, and learn from them. The
ashore stream turns hopeturtles.org from a telemetry dashboard into the
other half of the learning system, culminating in the **`mission_review`
panel** — the destination every aboard phase is building toward. Stack:
Express + MySQL + EJS/Tailwind (+ client-side JS map/chart components;
Socket.io announces arrivals but the database is always the source of
truth).

## Phase M1 — Mission + AOELL data model

- [ ] New tables per the AOELL spec: `missions_tb`,
  `mission_waypoints_tb`, `aoell_cycles_tb`, `aoell_evaluations_tb`,
  `aoell_observations_tb`, `mission_annotations_tb`,
  `policy_versions_tb`, `model_versions_tb`,
  `device_policy_deployments_tb`, `upload_batches_tb`.
- [ ] `telemetry_readings_tb` keeps high-volume samples but gains
  millisecond timestamps (`DATETIME(3)`) and an optional mission id;
  AOELL tables reference telemetry time ranges rather than copying
  samples.
- [ ] Frequently filtered fields (mission, cycle, action type, timestamp,
  policy version, outcome, key nav metrics) as indexed columns — not
  buried in JSON.
  *Gate: one uploaded voyage can be queried by mission, cycle, and
  evaluation horizon.*

## Phase M2 — Ingestion + policy distribution

- [ ] Device-authenticated endpoints: `POST /api/v1/telemetry/batch`,
  `POST /api/v1/aoell/batch`, `GET /api/v1/policy/manifest`,
  `GET /api/v1/policy/:version`, `POST /api/v1/policy/:version/ack`.
- [ ] Idempotent batch ingestion: checksum verification, contiguous
  sequence acknowledgement, duplicate suppression via `upload_batches_tb`.
- [ ] Replay ordering by device event time; `received_at` kept separate so
  week-late telemetry appears in the week-old part of the voyage, never
  masquerading as live navigation.
  *Gate: delayed and duplicate uploads produce one ordered server record
  (the shore half of the Phase A gate).*

## Phase M3 — `mission_review` panel: replay

The core of the visualizer — an authenticated mission-analysis page.

- [ ] **Mission map** distinguishing four concepts: **Actual** (GPS
  track), **Intended** (route, active waypoint, corridor, geofence),
  **Predicted** (what turtleOS expected an action to do, as a point/line/
  uncertainty ellipse at a named horizon), **Evaluated** (what actually
  followed at each horizon) — so the team can tell apart a wrong
  objective, a bad prediction, and a good action defeated by conditions.
- [ ] Map layers: track coloured by time/speed/VMG/action/confidence;
  action markers (holds, steps, sweeps, feathers, safety events);
  prediction-error vectors; gaps and low-confidence periods; optional
  retrospective wind/wave/current context clearly labelled as shore-side
  external data.
- [ ] **Synchronized voyage timeline**: sail commanded vs. measured, wind
  estimate + confidence, heading vs. COG, speed + VMG, cross-track,
  turn rate/heel/stability, battery + action energy, nav state, policy
  changes, upload gaps — all aligned, all driven by one time scrubber
  that also drives the map.
- [ ] **AOELL cycle inspector**: click any marker → the full causal
  record: belief before, action + trigger, alternatives rejected,
  prediction, commanded vs. actual movement, observations, per-horizon
  evaluation, learning update, versions in force, raw telemetry excerpt.
  *Gate: a team member can explain what turtleOS believed, did,
  predicted, and observed at any point of a voyage.*

## Phase M4 — `mission_review` panel: interpretation

- [ ] **Annotations + review**: mark evaluations confirmed / questionable
  / mislabelled / not-evaluable; segment notes, external events
  (handling, launch, collision, weather), photos and field notes, fault
  flags. Machine output, human interpretation, and the approved label
  stay distinct; originals are never deleted.
- [ ] **Comparison and sense-making views**: response by starting angle
  and step size; CW vs. ACW probability; response by luff-relative wind
  angle; VMG/speed change by action; energy per successful correction;
  success rate by confidence band; prediction calibration (70%
  confidence should succeed ~70% of the time); sweep repeatability;
  hold vs. intervention periods; failure and `NOT_EVALUABLE` reasons;
  per-turtle / per-firmware / per-policy breakdowns.
- [ ] **Training-set assembly**: select cycles, exclude confounded ones,
  version the dataset selection rules.
  *Gate: the team can assemble a trustworthy training dataset.*

## Phase M5 — Models: descriptive → supervised → shadow

Machine learning grows in stages; a complex controller trained on sparse,
noisy, safety-critical data would be premature.

- [ ] **Stage 1 — descriptive**: response tables and visual summaries
  ("under this wind/motion/sail state, +10° turned the boat clockwise in
  64% of evaluable trials"). Tests whether a learnable relationship
  exists at all.
- [ ] **Stage 2 — supervised response models**: predict ΔCOG, Δspeed,
  ΔVMG, turn probability/direction, no-effect probability, energy cost,
  evaluability. Validate by holding out entire missions and preferably
  entire turtles — random splits of neighbouring points would flatter the
  model.
- [ ] **Stage 3 — shadow mode**: the candidate records what it *would*
  have done and predicted while the established policy keeps control;
  `mission_review` compares. Only models that beat simple baselines,
  stay calibrated, and abstain safely outside familiar conditions move
  on. A model must be allowed to say "I do not know."
  *Gate: shadow recommendations demonstrate benefit, calibration, and
  safe abstention.*

## Phase M6 — Policy/model lifecycle + fleet learning

- [ ] **Release pipeline**: versioned dataset → candidate → validation vs.
  baselines and held-out missions → error/safety inspection in
  `mission_review` → simulation/replay → shadow mode → human approval →
  signed, checksummed publication → selected-turtle deployment → ack +
  activation records → regression monitoring → one-step rollback.
- [ ] **Model registry**: dataset version + selection rules, features,
  training code version, artifact, metrics + calibration, applicability
  limits, reviewer, deployment history.
- [ ] **Stage 4 — bounded action selection** (contextual-bandit-style over
  a small permitted action set; exploration disabled near hazards / low
  battery; auditable; falls back to the known policy).
- [ ] **Stage 5 — anomaly + fleet learning**: servo degradation, sensor
  drift, unusual energy use, out-of-experience conditions; shared fleet
  evidence with per-turtle parameters — hulls differ enough that one
  global response model may perform poorly.
  *Gate: an approved adjustment outperforms the previous policy in
  controlled trials without exceeding energy or safety limits.*

---

# Stream C — the bale mesh network

Plain-language why: a lesson learned at sea is only worth what reaches
another turtle or the shore. This stream builds the turtle-to-turtle
LoRa link, then the store-and-forward mesh on top of it, so that records
replicate between hulls, any turtle that finds connectivity becomes the
bale's uplink, and neighbours can share what they have learned about the
wind and the water they are all sitting in. Full analysis in
`docs/bale_network_vision.md`.

## Phase B0 — Paper only (no hardware, no code)

The cheapest and highest-leverage work in the whole stream, and the only
part that should start before Stream A's Phase A contract is settled.
The wire format is the **most expensive thing in the project to change
later**, because device firmware, mesh relays, and shore ingest must all
agree on it forever.

- [ ] **Packed wire format, version-tagged** (`docs/bale_wire_format.md`).
  A JSON telemetry payload is 200–400 bytes; the SF10–12 packet budget is
  **51 bytes**. A packed record — origin device id, sequence, epoch,
  lat/lon at 1e-5°, machine state + flags, battery, heading, wind solution
  — fits in about 20 bytes, leaving room for hop count, a short MAC, and a
  second record. Reserve a 4-bit format version in byte 0.
  **The JSON/HTTP path stays for WiFi**; LoRa gets a packed encoder and
  hopeturtles.org unpacks. Nothing about this replaces existing telemetry.
- [ ] **Mesh semantics** (`docs/bale_protocol.md`): dedup key
  `(origin_device_id, sequence)` with a bounded, reboot-surviving seen-set;
  a low TTL (3–4) so a reconverging bale cannot broadcast-storm itself;
  **provenance preservation** (a relayed record keeps its originator's id —
  a record claiming the wrong origin is worse than a lost one); queue
  eviction policy; and a partition digest so two halves of a bale that meet
  after a week exchange only gaps, not everything.
- [ ] **Regulatory answer for the intended operating area.** EU868's 1%
  duty cycle vs. US915's dwell-time rules change the message budget
  substantially and therefore the protocol; a vessel in international
  waters is genuinely unclear. This needs an answer from someone who knows
  maritime radio law, not an assumption.
- [ ] **Measure real power draw** with the INA219 already on the bus
  (0x40): idle, sailing, and a synthetic radio event. Every power estimate
  in the vision document is unverified until this exists.
- [ ] **Choose the co-processor and confirm a free I2C address.** The pin
  budget settles this by arithmetic: with the L76K GNSS module stacked,
  only D2/GPIO3 and D9/GPIO8 are free, and an SX1262-class radio needs
  seven pins (SPI ×3, NSS, RESET, BUSY, DIO1). The recommended answer is a
  **second MCU as a LoRa co-processor** on the existing I2C bus — it
  preserves the hull, HAL, and driver stack, and it isolates a radio task
  that blocks for seconds from a nav loop that cycles in 200–500 ms.
  Address must be clear of 0x0D, 0x38, 0x3C, 0x40, 0x53, 0x62, 0x68.
  *Gate: shore and device teams could implement against the wire format
  independently.*

## Phase B1 — Transport abstraction aboard

Starts once B0 is settled and a radio is on the bench — earlier risks
designing the wrong abstraction, since blocking behaviour and duty-cycle
accounting placement are not knowable without hardware.

- [ ] Extract a transport interface — `available()`, `send(record)`,
  `flush(queue)` — from `src/net/background_process.py`, whose `_send()`
  currently hardcodes WiFi association → HTTP POST → batch flush.
- [ ] Wire `mission_connection_mode` (already validated in `config.py`,
  already reserving `"lora"`) to select the transport.
- [ ] Implement the packed codec in `src/net/` with **host-side round-trip
  tests** under `tests/` — encode/decode is pure logic and needs no device.
- [ ] No behaviour change for WiFi-only turtles.

## Phase B2 — Point-to-point link

- [ ] Co-processor firmware: SPI to the radio, I2C to the XIAO,
  duty-cycle accounting owned entirely by the co-processor.
- [ ] `src/net/lora_transport.py` implementing the Phase B1 interface.
- [ ] Two turtles on a bench exchanging packed records; then two turtles
  at opposite ends of a beach; **then on water** — the sea-state effect on
  a 30 cm antenna will not appear in any land test.
  *Gate: a record created on turtle A is decoded intact on turtle B, with
  measured link availability across a real sea state.*

## Phase B3 — Store-and-forward mesh

- [ ] Seen-set, TTL, and provenance preservation per the B0 protocol.
- [ ] **Queue ownership change**: `device/telemetry_queue.json` stops
  meaning "my unsent readings" and starts meaning "readings I am
  responsible for," including foreign ones — needs a size cap, an eviction
  policy, and a decision on whether foreign records survive a reboot. The
  file stays device-owned and excluded from the sync scripts; that
  invariant becomes more important, not less.
- [ ] **Transmit budget allocator** splitting the duty cycle between own
  and relayed traffic. Without one, a naive relay exhausts its allowance
  carrying other turtles' traffic and never sends its own.
- [ ] Partition digest exchange.
- [ ] **Bale screen** (`ui/screens/bale.py`): neighbours heard, records
  carried, records relayed. Follow the CLAUDE.md conventions — `f_arvo20`
  title at `x=0,y=5`, connection header at `icon_y=1` — and add it to
  `_preload_screens()`.
- [ ] **Mesh time**: dedup, ordering, and duty-cycled RX windows all need
  consistent time. DS3231 is already kept in UTC and GPS is an independent
  source; a turtle whose RTC has failed should be able to take time from
  the mesh, which is another reason records carry absolute epochs.
- [ ] **Authentication**: a short truncated MAC over the packed record
  with a per-device key, verified authoritatively ashore. The threat model
  is low today, but retrofitting authentication into a deployed wire
  format is not cheap and `X-Device-Id` headers are meaningless in a
  20-byte frame.
  *Gate: a record originated by turtle A, relayed by turtle B, arrives
  ashore attributed to A — once, no matter how many paths it took.*

## Phase B4 — Ashore: bale ingest and view

Runs in parallel with B1–B3; the device stream is useless without it.

- [ ] Ingest endpoint for packed records — distinct from the JSON
  telemetry endpoint, not a replacement.
- [ ] **Idempotent dedup and provenance at ingest**: the same record will
  arrive many times, by many paths, days apart, from turtles that did not
  take it. Key on `(origin_device_id, sequence)`.
- [ ] **Relay graph model**: which turtle carried which record, by what
  path — scientifically interesting in its own right, as a measured map of
  bale connectivity over a voyage.
- [ ] **Bale view**: fleet positions, topology over time, per-turtle
  carried-record counts.
- [ ] Extend `mission_review` to reason about a **bale** rather than a
  hull — comparative policy performance across turtles in the same water
  is the payoff for the whole exercise.

## Phase B5 — Collective intelligence

Only meaningful once B3 and B4 are real and a bale has actually flown.

- [ ] **Wind-field sharing** feeding `luff.py` re-sweep scheduling: a
  sweep costs battery and interrupts steering, and a neighbour ten km away
  is likely in a similar wind field. A turtle can seed its own sweep with
  a neighbour's recent solution, or defer a scheduled re-sweep when a
  fresh neighbour result agrees with its own.
- [ ] **Hazard and dead-water reporting** weighting `waypoints.py`
  routing: a turtle becalmed or driven backwards for six hours has learned
  something about a patch of ocean worth broadcasting. This is the
  beginning of an empirical current-and-wind map built by the bale itself.
- [ ] **Mother turtle build**: same hull, added storage, larger battery,
  no sailing mission — deployed to sit in the middle of a bale and listen.
  Recovering one mother turtle recovers the bale's collective log.
- [ ] **Position rescue**: a turtle with failed GPS that hears three
  neighbours with known positions can bound its own location well enough
  to keep sailing a sensible heading — better than SAFE and drift.
- [ ] **Comparative policy reporting**: turtles broadcast policy version
  and measured performance, turning a bale into a controlled experiment
  with many replicates. Full over-the-air policy propagation is last,
  hardest, and only with a rollback story.

## Open questions for the team (Stream C)

These need answers from people, not from the code:

1. **Which region and band?** Determines the entire message budget.
   Blocking for B0.
2. **Realistic bale size for the first deployment** — 3 hulls or 30?
   Below roughly 5, the mesh rarely has a relay path and the value is
   mostly data survival rather than routing.
3. **Is a mother turtle in scope for the first bale**, or a later
   addition? It changes the storage requirement and the recovery plan.
4. **What added cost per hull is acceptable?** If the mesh doubles the
   cost of a deliberately cheap turtle, deploying twice as many isolated
   turtles may be the better experiment.

---

# Integration milestones — where the streams rejoin

The streams exist to meet. Each milestone requires more than one side and
is the only honest measure that the system — not just one part of it —
works.

| Milestone | Aboard requires | Ashore requires | Bale requires | Proof |
|---|---|---|---|---|
| **J1 — First replayed voyage** | Phase A (contract + queue) | M1–M3 | — | A real voyage scrubbed end-to-end in `mission_review`; any cycle explains belief → action → prediction → outcome |
| **J2 — Trustworthy training data** | AOELL-instrumented L/P/T experiments | M4 | — | A reviewed, versioned dataset with confounded cycles excluded |
| **J3 — Shadow mode at sea** | Policy runtime hooks reporting shadow recommendations | M5 | — | Candidate vs. incumbent compared over real missions |
| **J4 — Closed learning loop** | Policy receive path (verify, ack, activate, rollback) | M6 | — | A signed adjustment deployed, monitored, and rollback-able — AOELL → Share → Learn → Adjust → AOELL, complete |
| **J5 — Evidence outlives the hull** | Phase A records + B1 transport | B4 ingest, dedup, relay graph | B2–B3 | A record originated by one turtle, relayed by another, ingested once ashore and attributed to its originator — from a voyage where the originating hull never made landfall |

Closed-loop waypoint navigation (Phase H) graduates from "tuned once" to
"continuously improved" only after J4; tacking (Phase K) remains the last
sailing feature either way. J5 is independent of J1–J4 in principle but
gains its full value after them: once records explain the turtle's
reasoning, carrying them home matters far more.

---

# Development timetable

This is the sequencing decision layered on top of the phase lists above —
what actually happens first, what runs in general terms after it, and
where hopeturtles.org (a separate repo) has to move in step. It resolves
to four buckets: an immediate, detailed **Phase 0**, then three
progressively less-detailed phases carrying the project from "it senses
correctly" through "it sails onshore," "it sails offshore unattended," and
finally "a bale of them talk to each other." Later phases will be broken
down with the same level of detail as Phase 0 once the phase before them
is actually done — sequencing three sprints of detail in advance for work
that depends on results not yet in hand would just be guessing twice.

## Phase 0 — IMU integration (now)

The only phase with no sailing dependency and no open experimental
question — it is bring-up work, and it blocks every later phase, so it
runs first regardless of anything else in flight.

**turtleOS (this repo):**
- Bench-verify the MPU-9250 at 0x69 (`WHO_AM_I` = `0x71`).
- Write `src/drivers/mpu9250.py` (init, raw gyro/accel reads, AK8963
  bypass).
- Add boot-time detection + logging (`[BOOT] MPU-9250 found`), same
  pattern as the existing ENS160/AHT21 announce.
- Settle the warm-up question empirically (read-immediately vs.
  read-after-delay comparison).
- Cut over `compass.py` / `sailpoint.py` to the new driver; retire the
  GY-271 read path rather than running both indefinitely.
- Full task list: [Phase 0 above](#phase-0--imu-bring-up-immediate-blocks-everything-else).

**hopeturtles.org:** none. Phase 0 is bench work; nothing ships home yet
that the server doesn't already handle.

## Phase 1 — Onshore: prove the turtle senses and moves correctly

General shape, to be broken into a Phase-0-level task list once Phase 0
lands. Everything here can be validated tethered or in a harbor/beach
setting — no open-water passage required.

**turtleOS:**
- AOELL foundations (Phase A): cycle contract, local queue, resumable
  upload.
- Sensor fusion proper (Phase S): complementary filter, tilt compensation,
  heel/turn-rate outputs, spin detection (report-only).
- Luff validation (Phase L) started onshore/near-shore against a
  reference wind source.

**hopeturtles.org:**
- Phase M1 (mission + AOELL data model) and Phase M2 (ingestion + policy
  distribution endpoints) — the server needs these tables and endpoints
  before there's anywhere for Phase A's records to go.
- Phase M3 begins: enough of `mission_review` to replay a single tethered
  session end to end (map + timeline), even before there's a real voyage
  to look at.

*Milestone: J1 — first replayed session, on the bench or in the harbor.*

## Phase 2 — Offshore: prove the turtle sails and decides safely, unattended

General shape. This is where the turtle actually leaves sight of shore,
so it is gated entirely on Phase 1's evidence, not on a calendar date.

**turtleOS:**
- Propulsion (Phase P) and turning influence (Phase T) — the make-or-break
  experiments, judged against GPS ground truth.
- Heading control (Phase H), only once T produces a usable model.
- Reliability (Phase R): dead reckoning, GPS spoofing check, geofence,
  hull sensors, honest ARRIVAL.
- Watchdog + persistence (Phase W): unattended reboot/resume.
- Tacking (Phase K) at the tail of this phase, not before.

**hopeturtles.org:**
- Phase M4 (annotation + comparison views) and Phase M5 (descriptive →
  supervised → shadow models) — the team needs to be able to mark up and
  learn from real offshore voyages as they start arriving.
- Phase M6 begins: the signed policy release pipeline, so a
  shadow-validated improvement has somewhere to be approved and shipped
  from.

*Milestones: J2 (trustworthy training data) → J3 (shadow mode at sea) → J4
(closed learning loop).*

## Phase 3 — The bale: full onshore + offshore + mesh protocol

General shape. Runs the mesh stream (C) to completion and lets Phase 1/2's
learning loop extend across a fleet rather than one hull.

**turtleOS:**
- Stream C in full: B0 paper decisions (wire format, regulatory answer,
  measured power draw) → B1 transport abstraction → B2 point-to-point
  link → B3 store-and-forward mesh (seen-set, TTL, provenance, bale
  screen) → B5 collective intelligence (shared wind, hazard reporting,
  position rescue).

**hopeturtles.org:**
- Phase B4: packed-record ingest endpoint (distinct from the JSON
  telemetry endpoint), idempotent dedup/provenance at ingest, the relay
  graph model, and a bale view (fleet positions, topology over time,
  per-turtle carried-record counts).
- Extend `mission_review` to reason about a bale of turtles rather than a
  single hull — comparative policy performance across turtles sharing the
  same water and weather.

*Milestone: J5 — a record originated by one turtle, relayed by another,
attributed correctly ashore, from a voyage where the originating hull
never made landfall.*

## Cross-cutting — confidence, telemetry, and tooling

Plain-language why: every experiment above is only as good as its data
trail, and confidence scoring is the connective tissue that lets a partly
proven system act safely on what it *does* know.

- [ ] **Confidence framework**: a small `nav/confidence.py` (or fields on
  existing objects) carrying the AOELL score set —
  `gps_position_confidence`, `gps_course_confidence`,
  `heading_confidence`, `sail_angle_confidence`, `luff_wind_confidence`,
  `motion_stability`, `action_execution_confidence`,
  `outcome_evaluation_confidence`, `model_applicability_confidence` —
  each 0–1. Controllers read confidence to decide what they may do (low
  wind confidence → no tack attempts; low model confidence → wider
  deadband). All scores ship in `confidence_json`, and `mission_review`
  must *show* these uncertainties rather than hiding them behind a single
  cheerful green number.
- [ ] **Nav internals in telemetry**: add `nav_err` (heading error),
  `nav_wind`, `nav_sail_cmd`, `nav_sail_actual` (AS5600), `nav_wp`, and
  velocity-made-good to `values_json`; event flags (spin detected, sweep
  failed, stall) in `flags_json` — so shore-side tuning can replay
  behaviour from the hopeturtles.org packet log.
- [ ] **Magnetometer hard/soft-iron calibration**: rotate-the-boat routine
  storing offsets/scales in config; an on-device calibration screen.
  Compass error is the dominant navigation error source right now.
  Applies to the MPU-9250's AK8963 exactly as it did to the GY-271 — the
  new hardware does not remove the need to calibrate.
- [ ] **Bench simulation harness**: the host-side fakes used to verify the
  sweep/controller (FakeServo/FakeEnc/clock shim) should be committed
  under `tests/` so regressions are catchable without hardware.
- [ ] **Pre-compile `src/nav/` to `.mpy`** to cut flash + import RAM.
- [ ] **Power budget for the sweep**: a full sweep is ~20–25 s of servo
  motion; gate automatic re-sweeps on battery percentage.
- [ ] **Statistical safeguards baked into the experiment design** (from
  the AOELL revision): confounding (current/wind/waves changing at the
  same time as an action), selection bias (only acting when already
  losing progress), serial dependence (one voyage ≠ independent trials),
  sensor uncertainty (poor low-speed GPS course must not become ground
  truth), execution failure (a stalled servo is not evidence against the
  chosen angle), data leakage (shore-side weather or future GPS must not
  feed an onboard model that won't have them at sea), model drift, and
  unsafe exploration. Countermeasures: controlled hold periods, sparse
  bounded interventions, cooldowns, confidence-aware labels.
- [ ] **Retrospective environmental context**: shore-side wind/wave/tide/
  current data stored as *external* context with source, resolution, and
  uncertainty — useful for explaining outcomes ashore, never confused
  with what the turtle actually knew when it decided.

## Verification gates

**Stream A (aboard):**

| Gate | Target |
|---|---|
| Phase A bench | one AOELL cycle reconstructable from local logs; delayed/duplicate uploads produce one ordered server record |
| Phase S bench | heading drift < 2°/min; correct at 30° heel; spin events detected and logged (no servo response) |
| Phase L water | measured wind-solve accuracy figure + calibrated confidence; sweep retry policy exercised; sweeps logged as AOELL cycles |
| Phase P water | demonstrated forward progress at chosen trims; VMG logged; trims bracketed by hold controls |
| Phase T water | quantified, repeatable COG response to bounded sail steps vs. hold baselines — the go/no-go for closed-loop control |
| Phase H water | heading hold ±10° calm water; WP1→WP2 advance at radius; servo duty within power budget |
| Phase K water | net upwind VMG > 0 across a full tack sequence |
| Phase R bench | SAFE ≤ 60 s after GPS loss; feather ≤ 1 s; resume after restore |
| Phase W bench | reboot mid-mission resumes from persisted waypoint |

**Stream B (ashore):**

| Gate | Target |
|---|---|
| Phase M1 | one uploaded voyage queryable by mission, cycle, and evaluation horizon |
| Phase M2 | idempotent ingestion: delayed + duplicate batches → one ordered record; late data displays as late |
| Phase M3 | `mission_review` replay: any point of a voyage explains belief → action → prediction → outcome |
| Phase M4 | a reviewed, versioned, trustworthy training dataset can be assembled |
| Phase M5 | a model beats naïve baselines on held-out missions; shadow mode shows benefit, calibration, safe abstention |
| Phase M6 | a signed policy adjustment deploys, monitors, and rolls back cleanly |

**Stream C (bale mesh):**

| Gate | Target |
|---|---|
| Phase B0 paper | wire format stable enough for shore and device teams to implement independently; band/regulatory answer recorded; measured power draw recorded |
| Phase B1 bench | host-side codec round-trips every field; WiFi-only turtles show no behaviour change |
| Phase B2 water | packed record decoded intact turtle-to-turtle, with measured link availability across a real sea state |
| Phase B3 water | a relayed record arrives with its originator's id preserved, exactly once, with the duty-cycle budget respected |
| Phase B4 | the same record arriving by many paths days apart ingests once; the relay graph reconstructs who carried what |
| Phase B5 water | a shared wind solution measurably reduces a neighbour's sweep count without degrading its navigation |

**Joint (the streams rejoined):** J1 first replayed voyage → J2
trustworthy training data → J3 shadow mode at sea → J4 closed learning
loop, with J5 evidence-outlives-the-hull carrying the bale stream in. J4
is the roadmap's definition of done for the learning architecture; the
sailing ladder (through K) is its definition of done for navigation; J5 is
its definition of done for evidence survival. The turtle is finished when
all three are.
