# Navigation Roadmap — Road to the Full Olive Turtle Autonomy Stack

This document tracks what remains between the **minimal SAIL-NAV system**
now running in `device/src/nav/` and full unattended autonomy. It replaces
the earlier `pending_nav_dev.md` and incorporates two July 2026 revisions:

1. The **design review** that corrected several assumptions the original
   plan inherited from `docs/Olive_Turtle_Dev_Deploy.pdf` (fixed rudder,
   no direct steering, experiments before controllers).
2. The **AOELL revision** (`docs/AOELL nav revision.md`), which added the
   missing learning architecture: turtleOS is not a finished navigation
   controller but an *instrumented sailing experiment*, and every voyage —
   especially every failure — must produce evidence that improves the next
   decision, policy version, and turtle.

The roadmap therefore now runs in **two development streams**: the
**aboard stream** (turtleOS firmware) and the **ashore stream**
(hopeturtles.org — culminating in the `mission_review` panel). They are
built in parallel, meet at defined integration milestones, and must
complement each other completely: the turtle's logs are only as useful as
the shore system that can make sense of them, and the shore system is only
as useful as the causal records the turtle actually keeps.

It is written to be readable by assistant engineers as well as the core
team: each section opens with a plain-language summary of *why* the work
matters, followed by concrete tasks and where each lands in the codebase.

For the team-wide, non-technical overview (with diagrams and animations),
see `docs/nav_system/index.html`.

---

## The July 2026 corrections — read this first

The original plan quietly assumed the turtle steers like a normal boat.
It does not. These corrections reshape the whole roadmap:

1. **The rudder is fixed. Only the sail rotates.** There is no steering
   servo. Every reference to a "rudder/sail servo" in older notes means
   the **sail servo**, full stop. The fixed rudder, hull, and keel are
   passive.
2. **Sail trim is not direct steering.** Moving the sail changes the
   aerodynamic force on the boat; the fixed rudder, hull, and keel then
   convert that force into some combination of forward movement and yaw.
   **We do not yet know whether that relationship is consistent enough
   for dependable navigation.** Establishing it experimentally is the
   central open question of this roadmap.
3. **PID heading control is premature.** PID assumes the actuator has a
   reasonably predictable effect on the controlled variable. We have not
   yet established that moving the sail by 10° produces a predictable
   heading response. A PID controller wrapped around an unknown
   relationship is an elegant way to automate confusion. The PID code in
   `nav/pid.py` stays in the tree, but it is **gated behind the
   turning-influence experiments** (Phase T below) rather than tuned
   first.
4. **No 50 Hz "spin guard counters immediately."** The IMU can *detect*
   rotation at 20–50 Hz, but the sail must never be commanded at that
   frequency. We must first determine whether any sail movement reliably
   arrests — or accidentally worsens — a spin. Detection is a sensing
   task; response is an experiment.
5. **Tacking moves much later.** The ladder is: demonstrate luff
   detection → demonstrate useful propulsion → demonstrate repeatable
   turning influence → only then attempt a deliberate tack.
6. **Wind accuracy claims are retired.** The "~2°" figure came from host
   simulation with whole-degree servo quantization. The correct statement
   everywhere is: **wind estimate accuracy is to be established
   experimentally.**
7. **ARRIVAL does not "hold position."** A sail-only turtle with a fixed
   rudder cannot be assumed to station-keep. ARRIVAL means: inside the
   arrival radius → feather the sail, report arrival, and keep reporting
   position while drifting. Anything more is future work with no current
   evidence behind it.

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
system** — and gives the roadmap its two streams. The governing principle:

> **Act deliberately, observe carefully, evaluate honestly, log
> completely, and learn twice: once aboard for the next decision, and
> again ashore so the whole team — and eventually the whole turtle
> fleet — can improve.**

Full specifications (event contract fields, batch/ack protocol, database
tables, API surface, ML stages, statistical safeguards, and the
model/policy lifecycle) live in `docs/AOELL nav revision.md`; this roadmap
sequences that work.

## What remains strong

- **The state-machine architecture** (BOOT → ACQUIRE → SAIL_NAV →
  ARRIVAL, any → SAFE) is the right skeleton and stays.
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
  mapping (PID with placeholder gains — see correction #3; treat as
  scaffolding, not a tuned controller), great-circle bearing to waypoint,
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
motion sensor for the turtles, replacing the ICM-20948 that the original
PDF specified. Everywhere the PDF or older notes say "ICM-20948", read
"our IMU module" instead — the integration plan is the same.

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

### Wiring the module — and fixing the 0x68 address clash

The IMU speaks I2C, the same shared two-wire bus every other sensor uses.
The catch: the IMU's factory I2C address is **0x68 — the exact same
address our DS3231 clock chip already uses**. Two devices with the same
address on one bus is like two houses with the same street number: the
mail (data) goes to the wrong place. The bus cannot work until one of
them moves.

Fortunately the chip designers planned for this. The module has a pin
labeled **AD0** (sometimes printed ADO). It is an address-select switch:

- AD0 left unconnected or wired to GND → address **0x68** (clashes ❌)
- AD0 wired to 3.3 V → address **0x69** (free ✅)

**So: do we wire AD0 to the VCC pin on the IMU's own board, or to the
XIAO's 3V3 pin?** Answer: **either works — they are the same wire.** The
module's VCC pin is fed from the XIAO's 3V3 pin, so both points carry the
same 3.3 V. The tidiest option is a short jumper (or a solder bridge)
right on the IMU board from **AD0 to the module's VCC pin**, because it
keeps the fix on the module itself — any board wired that way is
plug-in-safe no matter who connects it later.

**One hard rule: power the module from the XIAO's 3V3 pin, never from
5 V.** The spec sheet says "3–5 V supply", but that tolerance is for the
VCC pin only (some boards have a regulator behind it). The AD0 pin
connects straight to the sensor chip, which is a 3.3 V part — putting 5 V
on AD0 can damage it. Powering everything at 3.3 V makes the whole
question moot: every point is 3.3 V and AD0-to-VCC is safe by
construction.

Full hookup (4 wires + the AD0 strap):

| IMU module pin | Connects to | Why |
|---|---|---|
| VCC | XIAO **3V3** | power (3.3 V only — see above) |
| GND | XIAO GND | ground |
| SCL | XIAO D5 / GPIO6 | shared I2C clock |
| SDA | XIAO D4 / GPIO5 | shared I2C bus data |
| **AD0** | **module VCC** (short jumper on the board) | moves address 0x68 → 0x69 |
| INT, FSYNC, others | leave unconnected | not used |

### Updated I2C bus map with the IMU installed

| Device | Address | Notes |
|---|---|---|
| AK8963 compass (inside the MPU-9250) | 0x0C | visible once bypass mode is enabled |
| QMC5883L compass (GY-271) | 0x0D | current heading source; no clash with 0x0C |
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

# The phases — two streams, one learning loop

The roadmap runs as an **experimental ladder in two streams**:

- **Stream A (aboard — turtleOS):** AOELL foundations (A) first, then the
  sensing phases (S, L); propulsion (P) and turning influence (T) must be
  demonstrated before any closed-loop heading control (H); tacking (K)
  comes only after all of the above. Reliability (R) and
  watchdog/persistence (W) proceed in parallel where they don't depend on
  the ladder.
- **Stream B (ashore — hopeturtles.org):** the data model, ingestion, and
  the `mission_review` panel — the major later-phase destination that
  every aboard phase is working toward connecting to. Without it, the
  turtle's experiments produce data nobody can interpret; with it, every
  failure becomes a lesson.

The streams are developed in parallel and **rejoin at the integration
milestones (J1–J4)** defined after the phase lists. Neither stream can
finish alone: the experiment phases (L, P, T) generate their evidence
*through* the AOELL pipeline, and the shore-side models and policies feed
adjusted behaviour *back* to the turtle. Failing, learning, failing,
learning — knot by knot, inch by inch, toward a robust and potent turtle
navigation system.

---

# Stream A — aboard (turtleOS)

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
  `tests/i2c_scan.py` showing 0x69 (and 0x0C once bypass is enabled)
  alongside the existing devices.
- [ ] **`src/drivers/mpu9250.py` driver**: init, gyro/accel/mag reads at
  the ranges we need (±250 °/s and ±2 g are plenty for a sailboat),
  bypass-mode enable for the AK8963.
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
  SOG) before the SAFE timer fires — a short-duration estimate only. Was
  blocked on the IMU — **unblocked now that the MPU-9250 is wired**;
  still depends on the Phase S driver work.
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

# Integration milestones — where the streams rejoin

The two streams exist to meet. Each milestone requires both sides and is
the only honest measure that the system — not just one half of it — works.

| Milestone | Aboard requires | Ashore requires | Proof |
|---|---|---|---|
| **J1 — First replayed voyage** | Phase A (contract + queue) | M1–M3 | A real voyage scrubbed end-to-end in `mission_review`; any cycle explains belief → action → prediction → outcome |
| **J2 — Trustworthy training data** | AOELL-instrumented L/P/T experiments | M4 | A reviewed, versioned dataset with confounded cycles excluded |
| **J3 — Shadow mode at sea** | Policy runtime hooks reporting shadow recommendations | M5 | Candidate vs. incumbent compared over real missions |
| **J4 — Closed learning loop** | Policy receive path (verify, ack, activate, rollback) | M6 | A signed adjustment deployed, monitored, and rollback-able — AOELL → Share → Learn → Adjust → AOELL, complete |

Closed-loop waypoint navigation (Phase H) graduates from "tuned once" to
"continuously improved" only after J4; tacking (Phase K) remains the last
sailing feature either way.

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
- [ ] **Pre-compile `src/nav/` to `.mpy`** to cut flash + import RAM
  (also suggested in `docs/features_to_add.md`).
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

**Joint (the streams rejoined):** J1 first replayed voyage → J2
trustworthy training data → J3 shadow mode at sea → J4 closed learning
loop. J4 is the roadmap's definition of done for the learning
architecture; the sailing ladder (through K) is its definition of done
for navigation. The turtle is finished when both are.
