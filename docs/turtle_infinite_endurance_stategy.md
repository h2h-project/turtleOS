# Turtle infinite endurance strategy

Started 20 November 2026, turtleOS 2.6.6.

## A Voyager for the Mediterranean

Voyager 1 and 2 left Earth in 1977 on a five-year mission to Jupiter and
Saturn. Nearly fifty years later both are still sending data, from beyond
the edge of the solar system. They got there by design: margin in every
system, a power source that decays slowly and predictably, and graceful
degradation. As the power falls, engineers switch instruments off one by
one, so the probe keeps doing its most important job for as long as
possible.

A hope turtle, once released, can be the same kind of probe. A turtle we
can no longer reach keeps sailing, or drifting, and keeps a timestamped GPS
track of exactly where the Mediterranean took it, along with heading,
pitch and roll, air pressure, temperature and its own battery health. Most
of that record will be collected far from any WiFi. It doesn't need to be
uploaded the same day. If the turtle beaches, is picked up by a fishing
boat, drifts back into range of a network, or is found months or years
later, its log can be uploaded (WiFi, Bluetooth through Turtle Wrangler,
LoRa in future) and every point is still valid, because each record
carries its own time and position.

The Mediterranean's surface currents are well studied in outline (Atlantic
water entering at Gibraltar, running east along North Africa, gyres and
eddies through the eastern basin) but sparsely sampled in detail. Every
long, continuous track from a cheap, wind-driven, bottle-built vessel is a
direct measurement of where surface water and anything floating in it
actually goes. A fleet of turtles that survive for months or years could
help refine the drift models that predict where aid, debris and people
end up.

Our target journey is **up to 3 months**. The goal of this document is to
make sure nothing in the turtle sets a ceiling much lower than that, and
then to keep pushing the ceiling out towards "infinite".

## What "alive" means

**A turtle is alive for as long as it can create and keep telemetry
records**: a valid timestamp plus a GPS position, saved to flash, waiting
to be uploaded. That is the one criterion this document measures
everything against.

Sailing is not on the critical path. When the servo, sail or rudder wears
out, the turtle becomes a drifter. A drifter is still a current probe, and
arguably a cleaner one: its track is the water's movement plus windage,
without the autopilot's input.

The **life-critical chain** is therefore:

> hull flotation → dry enclosure → solar panel → charger → system battery →
> XIAO + flash → GNSS module → clock (RTC or GPS time)

If any link in that chain fails, the turtle stops recording. Everything
else (servo, sail mechanism, servo battery, OLED, compass, IMU, air
sensors) can fail and the turtle stays alive in this sense, just with less
data per record.

---

## Hardware longevity ceilings

Estimates are for a turtle at sea in the Mediterranean: strong UV, salt
spray, water temperatures of roughly 15–28 °C, and the inside of a sealed
bottle reaching 50 °C or more in summer sun. They are order-of-magnitude
planning numbers, not measurements. **None has been tested on a turtle at
sea yet.** The *Critical* column marks the life-critical chain.

| Component | Critical | What limits its life | Estimated life at sea |
|---|---|---|---|
| **Telemetry queue on the XIAO's flash** (`telemetry_queue.json`) | **Yes — the key ceiling** | Capped at `QUEUE_MAX` = **5,000 readings** (~400 B each, ~2 MB). Past the cap the **oldest** 500 are dropped. Unsent history is lost, starting with the beginning of the voyage. | **~7 days** at the default 120 s logging rate. ~35 days at 10 min, ~104 days at 30 min. See [the telemetry ceiling](#the-telemetry-ceiling) |
| XIAO flash wear | Yes | NOR flash: ~100,000 erase cycles per sector. LittleFS spreads writes across the filesystem (several MB). Appends plus a 2 MB trim every 500 readings is a few MB written a day. | Decades or more at current rates. Not a ceiling, but recheck if logging becomes very frequent |
| XIAO ESP32-S3 (MCU, PSRAM) | Yes | Silicon itself lasts decades. Limited by moisture and corrosion on the board and its solder pads, and by heat. | Decades if dry; weeks to months once water gets in |
| PET bottle hull | Yes (flotation) | UV photodegradation: yellowing, then embrittlement and cracking, especially at stressed points (threads, cut edges, Ecojoiner ports). PET itself persists in the sea for decades. | **~2 years** before embrittlement threatens joints and seals (conservative) |
| Seals: cast silicone rings, axle O-ring | Yes (dry enclosure) | Silicone resists UV well. The cast-from-caulk seals are not validated yet. The rotary O-ring on the sail shaft wears with every sail movement. | 1–3 years for static seals; the rotary seal may be months under constant steering. **Unvalidated** |
| Solar panel (Voltaic P126, ETFE laminate) | Yes | ETFE laminate yellowing and delamination, salt crust, and **biofouling** (algae, barnacles) covering the cells. Fouling, not panel ageing, is the likely first limit. | Laminate ~5 years. Output may be badly reduced by fouling within weeks to months unless waves keep it clean |
| CN3065 solar charger | Yes | No wear-out mechanism. Corrosion. **Today's P126 panel runs above the CN3065's 4.4–6 V input range** (see `Power_System.md`), which can damage it. | Decades if dry and within its input range; **at risk now** |
| System battery (1 × 21700 Li-ion, 4200 mAh) | Yes | Cycle wear (~500–1,000 full cycles to 80%; many more for shallow daily solar cycles) and **calendar ageing**, which heat and sitting at full charge (4.2 V float) accelerate badly. | **~2–4 years**, mostly set by heat and high state of charge, not cycles |
| L76K GNSS module | Yes | Electronics last decades. Needs a clear view of the sky: water or fouling over the antenna blocks fixes. | Decades if dry; fixes degrade as fouling builds |
| DS3231 RTC + coin cell | Partly | The coin cell only matters when main power is lost completely. Drift ~±2 ppm, about 1 min a year. If the clock is lost, `clock.py` resets it from the first GPS fix. Its calendar ends in 2099. | Coin cell 5–10 years as backup only. Not a ceiling, because GPS time covers it |
| INA219 battery monitor | No | Corrosion. | Decades if dry |
| Servo battery (2 × 18650, 2S, 2000 mAh) | No (sailing only) | **Not charged on board**: it is a fixed 14.8 Wh energy budget. Drained by servo moves and self-discharge (~2–3% a month). The SSR keeps the LM2596's 5–10 mA idle draw off it. | Weeks to a few months of active steering. **Unmeasured.** Sailing stops when it is flat; recording carries on |
| MG996R / DS-R001 sail servo | No | Gear and potentiometer wear from steering corrections; the analog MG996R's pot is the weak point. Water ingress. | Months of continuous steering (cut by the B9 deadband work). **Unmeasured** |
| LM2596 buck + Pololu SSR | No | Corrosion; electrolytic capacitor ageing in heat. | Years |
| AS5600 encoder + magnet | No | Contactless, so no wear. The neodymium magnet corrodes in salt water unless coated. | Decades dry; magnet months if wet |
| GY-273 compass, GY-521 IMU | No | Corrosion. The compass also drifts as the hull's own magnetism changes. | Decades if dry |
| AHT20 + BMP280 module | No | Humidity sensors drift in constant high humidity; the BMP280 needs an air path. | 1–3 years of useful accuracy |
| OLED (SSD1306 / SH1106) | No | Organic emitters dim with on-time (~10,000–50,000 hours). | Years if it sleeps at sea; irrelevant once launched |
| 608 bearings (steel) | No | Salt-water corrosion seizes them. | Months unless stainless or ceramic |
| GT2 belt and pulleys | No | Rubber ages in UV and salt; the fibreglass cords lose strength once wet. | 1–2 years |
| Steel sail shaft (Ø5 rod) | No | Corrosion, unless 316 stainless. | Months (mild steel) to decades (316) |
| 3D-printed parts (control cap, cage) | Partly (the cap holds the seal) | PLA softens and creeps above ~50 °C, absorbs water and goes brittle in UV. PETG and ASA last much longer. | PLA months to a year; ASA years |
| Wooden frame and fins (Ecojoiner) | Partly (holds the bottles together) | Rot and **marine borers** (shipworm) attack untreated wood in warm seas. | 6–18 months untreated; years with treatment |
| Wiring, JST connectors, solder joints | Yes | Corrosion from condensation inside the enclosure. | Years if the enclosure stays dry |
| Biofouling (whole turtle) | Yes | Not a component, but it hits several at once: covers the panel and GPS antenna, adds mass and drag, jams the sail pivot. | First growth in weeks; serious in months in warm water |

### Reading the table

- **The first ceiling we hit is software, not hardware**: the 5,000-reading
  queue fills in about a week at the default logging rate and then starts
  throwing away the start of the voyage.
- After that, the life-critical chain is limited by **heat and charge state
  in the system battery (2–4 years)**, **UV on the PET hull (~2 years)**,
  **unvalidated seals**, and **biofouling on the solar panel** (possibly
  months).
- The **servo battery has no recharge path**, so active sailing has a fixed
  budget. Today that, not the servo itself, is most likely what turns a
  sailing turtle into a drifter.

---

## The telemetry ceiling

This is the ceiling that matters most, and the one entirely in our hands.

### Today

| Logging interval | Days of history 5,000 readings hold |
|---|---|
| 120 s (default `telemetry_post_every_s`) | 6.9 |
| 5 min | 17 |
| 10 min | 35 |
| 30 min | 104 |
| 1 hour | 208 |
| 3 hours | 625 |

A 3-month journey with no upload needs a reading every **26 minutes or
slower** to keep the whole track. Past the cap, `enqueue()` drops the oldest
500 readings, so a turtle at sea loses its departure first. For a Voyager,
the departure leg is the part we most need: it ties the track to a known
release point.

Each queued reading is a full JSON telemetry payload of ~400 bytes. The
only fields a current probe strictly needs are time, latitude and longitude
(12 bytes in binary). The rest are useful but not essential.

### Ways to raise it, roughly in order of effort

1. **Pick the logging rate for the mission.** Set `telemetry_post_every_s`
   (or `journey_log_every_s`) to suit the journey length before launch, and
   show the predicted coverage on the Logging screen and in Wrangler. No
   code change needed to start.
2. **Thin instead of truncate.** When the queue is full, drop every second
   reading from the older half instead of the oldest 500. The whole voyage
   is kept at decreasing resolution: the track since departure is always
   complete, just coarser further back. Repeated halving means the queue
   **never runs out of time span**, only resolution. This is the single
   change that makes the record "infinite".
3. **Compact records.** Store a binary or minimal-key record (time, lat,
   lon, battery, heading, pressure: ~20–24 bytes) for readings that can't be
   sent, and rebuild the JSON payload at upload time. That is roughly 15–20×
   more readings in the same space: about 80,000 at 24 bytes in the 2 MB the
   queue uses now, or several hundred thousand if the queue may use most of
   the filesystem.
4. **Give the queue more of the flash.** Size `QUEUE_MAX` from
   `os.statvfs()` free space at boot instead of a fixed number, keeping a
   reserve for config, journey state and crash logs.
5. **Adaptive logging rate.** Log more often while the position is changing
   fast or a journey has just started, less often when drifting slowly or
   when the battery is low.
6. **External storage** (an SD card or SPI flash on the TurtleShell PCB)
   for gigabytes, if the flash above ever becomes the limit.

With 2 + 3 + 4 together, a turtle logging every 30 minutes could keep a
multi-year track with the newest months at full resolution.

---

## Software ceilings

Hardware is not the only thing that ages. These are the time limits found
in turtleOS so far.

| Limit | Effect | Status |
|---|---|---|
| `ticks_ms()` wraps every 12.4 days; `ticks_diff()` is only valid within ~6.2 days | A GPS fix more than ~6.2 days old read as fresh; SAFE could stop clearing; a week-old wind solution counted as current | **Fixed in 2.7 (review B5)**: `src/app/monotonic.py`, a millisecond clock that never wraps. Test: `tests/gpsfix_age_host.py` |
| Telemetry queue cap (5,000) | Oldest history dropped | See above; thinning recommended |
| Journey history (`HISTORY_MAX` = 20) | Only the last 20 journeys are kept on the turtle | Fine for now; telemetry events carry the records to the server |
| GPS week-number rollover | Receivers can report a date ~19.6 years in the past | Guarded: `clock.GPS_YEAR_MIN` rejects GPS dates before 2026 |
| DS3231 calendar (2000–2099) | Clock invalid after 2099 | Not a concern |
| Crash loop | From the 4th crash in a row, one reset every 10 minutes | Never halts (watchdog, 2.6.6) |

When adding code, any "how long ago" stamp that can be days old must use
`monotonic.ms()`, not `ticks_ms()` (developer guide, gotcha 21).

---

## Graceful degradation (the Voyager rule)

Voyager survives by shedding load in a planned order. A turtle should do
the same, keeping the recorder alive to the end:

1. **Full operation**: sailing, luff sweeps, all sensors, Bluetooth beacon.
2. **Servo battery flat or servo failed** → stop steering, drift. Keep
   logging.
3. **System battery low** → stop Bluetooth advertising and WiFi attempts,
   stop the IMU and air sensors, and log less often.
4. **System battery critical** → GPS fix plus one record per long interval,
   deep sleep in between, wake on a timer. The last thing to go is
   *time + position to flash*.
5. **Solar returns** → climb back up the ladder.

Steps 3–5 are not built yet. The current low-battery handling only feathers
the sail and pauses navigation.

---

## Priorities

What extends the life of the turtle most, for the least work:

1. **Queue thinning instead of truncation** (telemetry ceiling, item 2).
   Turns a 7-day record into an unbounded one.
2. **Fix the P126 / CN3065 voltage mismatch.** The only link in the
   life-critical chain that is at risk on day one.
3. **Battery charge ceiling.** Stop charging the 21700 at ~4.0–4.1 V instead
   of 4.2 V, and keep it out of direct sun. This is the largest single gain
   in battery calendar life.
4. **A degradation ladder** (above), with deep sleep between fixes at the
   bottom of it.
5. **Compact records and a flash-sized queue.**
6. **Recharge the servo pack from solar** (the optional 2S charger in
   `Power_System.md`), so sailing doesn't have a fixed budget.
7. **Materials**: UV-resistant print material (ASA/PETG) for the cap,
   316 stainless shaft, stainless or ceramic bearings, treated wood, coated
   magnet, a sealed GPS antenna window.
8. **Measure everything we've estimated.** Servo-pack drain per day of
   steering, system current at each degradation step (the INA219 can do
   this, as `ble_power_test.py` does for Bluetooth), and a sea or tank soak
   test of the seals.
9. **Recovery path.** A visible "if found" label with a contact, plus an
   easy Bluetooth or WiFi dump for whoever finds the turtle, so a log that
   survives actually reaches us.

## Open questions

- Actual LittleFS partition size on our XIAO build (`os.statvfs('/')`), to
  size a flash-based queue.
- Real system-battery drain at sea, by subsystem, and the solar margin in
  winter.
- How fast fouling covers the panel and antenna in the eastern
  Mediterranean, and whether the turtle's motion keeps them clear.
- Whether the server should accept compact records directly, or the turtle
  should always rebuild the full JSON before upload.
