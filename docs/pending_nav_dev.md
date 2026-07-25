# Pending Navigation Development — Road to the Full Olive Turtle Autonomy Stack

This document tracks what remains between the **minimal SAIL-NAV system**
now running in `device/src/nav/` and the full vision described in
`docs/Olive_Turtle_Dev_Deploy.pdf`, plus robustness work the PDF does not
cover. It is written to be readable by assistant engineers as well as the
core team: each section opens with a plain-language summary of *why* the
work matters, followed by the concrete task checklist and where each item
lands in the codebase.

For the team-wide, non-technical overview (with diagrams and animations),
see `docs/nav_system/July_nav_system_current+proposed.html`.

## What is already in place (June 2026)

In plain terms: the turtle can already boot up, figure out which way it is
pointing (compass), find the wind by sweeping its sail and feeling for the
flutter, steer toward a GPS waypoint, and put itself into a SAFE mode when
something goes wrong. What follows in the later sections is about making
that loop *more accurate, more robust, and able to sail upwind*.

- Five-state machine (`nav/state_machine.py`): BOOT → ACQUIRE → SAIL_NAV →
  ARRIVAL, any → SAFE, reboot → BOOT. Reported in every telemetry payload
  as `machine_state` and stored/displayed on hopeturtles.org.
- Luff-sweep wind detection (`nav/luff.py`): stepwise, non-blocking,
  moving-baseline threshold calibration, sweep A/B onset capture,
  circular-midpoint wind solve. Verified in host simulation across wind
  angles (solves within ~2° with whole-degree servo quantization).
- Minimal autopilot (`nav/controller.py`): PID heading control (placeholder
  gains kp=1.0 ki=0 kd=0.2) → servo, great-circle bearing to waypoint,
  forward-only waypoint sequencing with flash persistence
  (`/nav_state.json`), arrival → ARRIVAL, GPS-loss timer → SAFE, low
  battery → feather.
- Three-circle machine-state screen (`ui/screens/state.py`): heading
  running-ball, sail diameter line, wind arrow; double-click in ACQUIRE
  starts the first sweep; double-click in SAIL-NAV re-sweeps.
- Shared GPS fix cache (`nav/gpsfix.py`) so NavController and the telemetry
  scheduler don't starve each other on the single UART buffer.

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

## PDF Phase 1 — Sensor fusion (MPU-9250)

Plain-language why: today the turtle's sense of direction comes from a
magnetometer alone. A compass is truthful on average but jittery
second-to-second, and it lies when the boat heels over. A gyroscope is
the opposite: silky-smooth over seconds but slowly drifts. Fusing the two
("complementary filter") gives a heading that is both smooth *and* true,
and the accelerometer tells us which way is down so we can un-tilt the
compass reading on a heeled boat.

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
  complementary filter from the PDF (every IMU sample):
  `heading = 0.98 × (heading + gyro_yaw_rate × dt) + 0.02 × mag_heading`
  The `HeadingSource` abstraction already exists so this drops in without
  touching NavController or any screen.
- [ ] **Tilt-compensated heading** (mag + accelerometer) — a heeled
  sailboat reads garbage from a flat-mounted magnetometer; this matters
  more at sea than the gyro fusion does.
- [ ] **Compass changeover decision**: run the AK8963 (inside the IMU) and
  the GY-271 side by side on the bench; if the AK8963 is as good or
  better, retire the GY-271 and free the board space. Keep the GY-271 as
  the fallback path in `heading.py` either way.
- [ ] **50 Hz inner yaw-rate loop** to arrest spin onset before the 300 ms
  outer loop sees it (PDF autopilot step 4). Needs a timer IRQ or a faster
  tick path than `_bg_tick` — design carefully against heap/IRQ rules.
- [ ] `is_stable()` gate for BOOT→ACQUIRE: heading drift < 2°/min over a
  bench window (PDF Phase 1 target).

## PDF Phase 4 — SAIL-NAV maturation

Plain-language why: the current autopilot can hold a rough course in easy
conditions. This phase makes it hold course *well* (gain tuning), trim the
sail properly instead of approximately, correct sideways drift, and —
biggest of all — sail toward an upwind destination by zig-zagging
(tacking), which no sailboat can avoid.

- [ ] **PID gain tuning** in tethered water trials (target: heading hold
  ±10° in calm water). Gains live in `nav/pid.py`; make them config keys
  (`pid_kp/pid_ki/pid_kd`) once tuning starts so trials don't need
  reflashes.
- [ ] **Encoder↔servo↔wind trim calibration.** The minimal loop steers
  around servo neutral (90°) and "feathers" by centering. Real trim needs
  the mapping between AS5600 encoder degrees (0–360, arbitrary zero),
  servo command degrees (0–180), and boat axis. Add a one-time calibration
  routine + config offsets; then CRUISE = wind_angle ± attack offset and
  FEATHER = sail edge-on to solved wind.
- [ ] **Cross-track error bias** (PDF autopilot step 6): bearing bias
  proportional to lateral offset from the track line between the previous
  and active waypoint. `nav/bearing.py` needs a `cross_track_m()` helper.
- [ ] **No-go-zone tack sequence**: if the destination lies within 45° of
  upwind, alternate close-hauled legs instead of pointing into the zone.
  This is the largest remaining navigation feature — a `nav/tack.py` state
  within SAIL_NAV.
- [x] **Periodic re-sweep cadence** (fixed interval): implemented —
  `luff_resweep_s` config (default 600 s); NavController auto-starts a
  sweep in SAIL_NAV when the timer expires and re-arms after every sweep;
  countdown shown bottom-right on the turtle waiting screen.
- [ ] **Event-driven re-sweep**: sooner after a tack or heading change
  > 30° (PDF spec). Needs a heading-delta tracker in `NavController`.
- [ ] **Light-wind adaptation**: scale `luff_threshold_mult` down and
  `luff_sweep_dps` down when solved-wind confidence is low / flutter
  amplitude is small.

## PDF Phase 5 — Reliability

Plain-language why: a turtle at sea is on its own. This phase is about
noticing when something is wrong (GPS gone quiet, position that doesn't
match the compass, drifted outside the allowed area, water inside the
hull) and reacting safely instead of sailing off confidently in the wrong
direction.

- [ ] **Dead-reckoning** during GPS dropouts (gyro + accel integration)
  before the SAFE timer fires; the PDF allows a short-duration estimate.
  Was blocked on the IMU — **unblocked now that the MPU-9250 is wired**;
  still depends on the Phase 1 driver work.
- [ ] **GPS spoofing detection**: compare RMC COG (already parsed into
  `nav/gpsfix.py:cog_deg()`) against compass heading; sustained
  disagreement beyond leeway → SAFE.
- [ ] **Geofence**: polygon or radius bound; breach → SAFE. Config schema
  + point-in-area check in `nav/bearing.py`.
- [ ] **Thermal / moisture sensors** → SAFE + distress telemetry packet.
- [ ] **SAFE manual reset gesture**: SAFE→ACQUIRE transition exists in the
  state machine but no UI triggers it yet. Decide the gesture (e.g.
  long-hold on the state screen) and implement.

## PDF Phase 6 — Watchdog + persistence

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
  unattended recovery (PDF: resume "without operator intervention"), add a
  config flag (`auto_resume: true`) that auto-starts the sweep in ACQUIRE
  when a persisted mission index exists.

## Beyond the PDF — robustness suggestions

Plain-language why: field lessons and known weak spots — mostly about the
compass being our dominant error source, the servo being our dominant
power drain, and making failures observable from shore.

- [ ] **Magnetometer hard/soft-iron calibration**: rotate-the-boat routine
  storing offsets/scales in config; an on-device calibration screen.
  Compass error is the dominant navigation error source right now. Applies
  to the MPU-9250's AK8963 exactly as it did to the GY-271 — the new
  hardware does not remove the need to calibrate.
- [ ] **Sweep failure handling**: `LuffSweep` fails cleanly today
  (`no luff (A)/(B)`) but nothing retries. Policy: retry with slower speed
  and lower threshold; N consecutive failures → SAFE. Known edge: wind
  sitting exactly at `sail_min_deg` inflates the moving-baseline threshold
  (calibration overlaps flutter) — detect via abnormally high calibration
  peak and restart from the opposite stop.
- [ ] **Servo slew-rate limiting + stall detection**: limit deg/s commanded
  to the MG996R to cut current spikes; flag a stall when AS5600 angle
  stops tracking the command (rigging jam, weed).
- [ ] **PID output low-pass / deadband** to stop micro-corrections from
  burning servo power on a multi-day crossing.
- [ ] **Nav internals in telemetry**: add `nav_err` (heading error),
  `nav_wind`, `nav_sail_cmd`, `nav_wp` to `values` so shore-side tuning can
  replay behaviour from the hopeturtles.org packet log.
- [ ] **Bench simulation harness**: the host-side fakes used to verify the
  sweep/controller (FakeServo/FakeEnc/clock shim) should be committed under
  `tests/` so regressions are catchable without hardware.
- [ ] **Pre-compile `src/nav/` to `.mpy`** to cut flash + import RAM
  (also suggested in `docs/features_to_add.md`).
- [ ] **Power budget for the sweep**: a full sweep is ~20–25 s of servo
  motion; gate automatic re-sweeps on battery percentage.

## Verification gates (carry from the PDF)

| Gate | Target |
|---|---|
| Phase 1 bench | heading drift < 2°/min, no gimbal-lock artefacts at 30° roll |
| Phase 2 bench | manual sail excitation jitter ≥ 5× calm baseline |
| Phase 4 water | heading hold ±10° calm water; WP1→WP2 advance at radius |
| Phase 5 bench | SAFE ≤ 60 s after GPS loss; feather ≤ 1 s; resume after restore |
| Phase 6 bench | reboot mid-mission resumes from persisted waypoint |
