# Pending Navigation Development — Road to the Full Olive Turtle Autonomy Stack

This document tracks what remains between the **minimal SAIL-NAV implementation**
now in `device/src/nav/` and the full vision in
`docs/Olive_Turtle_Dev_Deploy.pdf`, plus robustness work the PDF does not
cover. Each item lists where it lands in the codebase.

## What is already in place (June 2026)

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

## PDF Phase 1 — Sensor fusion (ICM-20948)

The PDF specifies an ICM-20948 9-axis IMU; the boat currently carries a
QMC5883L magnetometer only, isolated behind `nav/heading.py:HeadingSource`.

- [ ] **Hardware**: ICM-20948 default I2C address 0x68 **collides with the
  DS3231 RTC** on the shared bus. Strap AD0 high (→ 0x69) or move the RTC.
- [ ] `Icm20948HeadingSource` in `nav/heading.py` implementing the
  complementary filter from the PDF (every IMU sample):
  `heading = 0.98 x (heading + gyro_yaw_rate x dt) + 0.02 x mag_heading`
- [ ] Tilt-compensated heading (mag + accelerometer) — a heeled sailboat
  reads garbage from a flat-mounted magnetometer; this matters more at sea
  than the gyro fusion does.
- [ ] 50 Hz inner yaw-rate loop to arrest spin onset before the 300 ms
  outer loop sees it (PDF autopilot step 4). Needs a timer IRQ or a faster
  tick path than `_bg_tick` — design carefully against heap/IRQ rules.
- [ ] `is_stable()` gate for BOOT→ACQUIRE: heading drift < 2°/min over a
  bench window (PDF Phase 1 target).

## PDF Phase 4 — SAIL-NAV maturation

- [ ] **PID gain tuning** in tethered water trials (target: heading hold
  ±10° in calm water). Gains live in `nav/pid.py`; make them config keys
  (`pid_kp/pid_ki/pid_kd`) once tuning starts so trials don't need reflashes.
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

- [ ] **Dead-reckoning** during GPS dropouts (gyro + accel integration)
  before the SAFE timer fires; the PDF allows a short-duration estimate.
  Blocked on Phase 1 (needs the IMU).
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

- [ ] **Magnetometer hard/soft-iron calibration**: rotate-the-boat routine
  storing offsets/scales in config; an on-device calibration screen.
  Compass error is the dominant navigation error source right now.
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
