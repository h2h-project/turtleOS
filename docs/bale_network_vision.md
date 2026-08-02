# Turtle Bale LoRa Mesh — Vision and Plan

**Status: planning only. No hardware selected, no code written.**
Nothing in this document is committed to. It exists so that the decisions
that are expensive to reverse — the wire format, the mesh semantics, the
hardware topology — get made deliberately rather than discovered halfway
through an implementation.

This document sits alongside `docs/navigation_roadmap.md` and follows its
two-stream convention: an **aboard stream** (turtleOS firmware) and an
**ashore stream** (hopeturtles.org). Each section opens with why the work
matters, then lands in concrete tasks and file locations.

---

## 1. The vision

A turtle is cheap. It is open source, built from wood and locally
available materials, and carries perhaps forty dollars of electronics. That
economics has a consequence that the current single-turtle architecture does
not yet exploit: **turtles will be deployed in numbers, and they will drift
in loose company.**

Today each turtle is an individual floating alone in the sea. It records to its own memory and waits for a
shore AP that, on a real mission, most likely will never come until it has successfully arrived. Records survive — the queue is
durable — but they arrive only when a human retrieves the hardware.  Alas when a
turtle is lost at sea takes every reading it ever took with it.

A **bale** changes the unit of survival from the turtle to the fleet.

Whereas there are flocks of sheeps, swarms of wasps... there are bales of turtles!

The proposition is simple: in the same way that real turtles can communicate to each other through the ocean to coordinate migrations and "arribadas" (mass arrivals that swamp predators), if our turtles can talk to each other, then

- a reading taken by one turtle can be **carried home by another**;
- a turtle that finds the wind can **tell its neighbours what it learned**;
- a turtle that still has shore connectivity becomes a **gateway for the
  whole bale**;
- and a purpose-built **other turtle** — the same hull, with added storage
  and no other job — can shadow the bale as a flying-recorder, so that a
  voyage produces evidence even when nothing makes landfall.

The loss model inverts. Today, losing an individual turtle loses its data. In a bale,
losing a turtle loses a *node* — and its data has already been somewhere
else for days.

This matters most for the project's actual purpose. Per the AOELL revision (Act, Observe, Evaluate, Log, Learn),
turtleOS is not a finished navigation controller but an instrumented sailing
experiment: every voyage, especially every failure, must produce evidence
that improves the next turtle. A bale multiplies the evidence yield of a
deployment by roughly the number of hulls in it, and — more importantly —
lets that evidence survive the hulls.

---

## 2. The technical opportunity: why the sea is unusually good for this

Mesh networking has a reputation for being fussy and disappointing. That
reputation was earned on land. Almost every reason it disappoints ashore is
absent at sea, and several factors actively work in our favour.

**Unobstructed line of sight.** LoRa's range budget is destroyed by
buildings, terrain, and foliage. Open water has none. Published amateur
results over water regularly reach tens of kilometres with modest antennas;
the constraint becomes the horizon rather than the link budget. For a
low-mounted antenna on a small hull the radio horizon is on the order of
5–10 km, and turtle-to-turtle geometry is symmetric, so both ends benefit.

**A quiet spectrum.** The 863–870 MHz and 902–928 MHz ISM bands are
congested in any city. Mid-ocean, the noise floor is close to thermal. The
same radio that manages 2 km in an urban environment is a different
instrument out there.

**Drift correlates.** This is the non-obvious one. Turtles released together
are moved by the same currents and the same wind field. They disperse, but
they disperse *slowly and coherently* — neighbours tend to stay neighbours
for days or weeks, and the bale behaves less like a random scatter than like
a slowly expanding raft. Mesh topology on land changes by the second; a bale's
topology changes by the hour. That makes routing dramatically easier: a
neighbour table refreshed a few times a day is a good description of reality.

**Nobody is in a hurry.** A voyage lasts weeks. A telemetry record is just as
useful arriving three days late as three seconds late. This removes the
single hardest constraint in mesh design. We do not need low latency, high
throughput, or reliable delivery — we need **eventual delivery**, which is a
vastly cheaper problem. Store-and-forward with generous timers is not a
compromise here; it is the correct design.

**The node density is self-solving.** We cannot afford to deploy a
purpose-built relay network. But we do not have to: the relays are the
mission. Every turtle launched to sail somewhere is simultaneously a node.
The network densifies for free as the project succeeds.

**Failure is graceful.** A turtle that hears nobody behaves exactly as
turtles behave today — it queues to flash and sails on. The mesh is strictly
additive. There is no mode in which adding LoRa makes a lone turtle worse,
which means the feature can be deployed incrementally across a mixed fleet
without a flag day.

The honest counterweight: **antenna height is brutal at these ranges.** The
radio horizon scales with the square root of height, and a turtle's antenna
sits perhaps 30 cm above the water, in swell that periodically puts it
*below* the wave crests. Real-world link availability will be intermittent
and sea-state dependent in a way that flat-water testing will not reveal.
The design must assume links appear and vanish on a timescale of seconds
even when the average topology is stable for days. This is an argument for
store-and-forward and against anything resembling a session.

---

## 3. What the bale makes possible

### 3.1 For the individual turtle

**Wind field sharing.** The luff sweep (`src/nav/luff.py`) is how a turtle
discovers the apparent wind angle, and it is expensive: it interrupts
steering, works the servo, and takes a battery hit each time. A neighbour ten
kilometres away is very likely in a similar wind field. If turtles broadcast
their solved wind angle and position, a turtle can **seed its own sweep** with
a neighbour's recent solution, or defer a scheduled re-sweep entirely when a
fresh neighbour result agrees with its own. Fewer sweeps means less power and
less time not steering.

**Hazard and dead-water reporting.** A turtle that has been in SAFE state, or
becalmed, or driven backwards for six hours has learned something about a
patch of ocean. Broadcasting "this area cost me a day" lets neighbours weight
their routing away from it. This is the beginning of a shared, empirical
current-and-wind map built by the bale flotilla.

**Policy propagation.** If a turtle carries a navigation policy version and
one turtle's policy is demonstrably outperforming another's over the same
water, that is a fact worth transmitting. Full over-the-air policy update is a
later and much harder step, but even *reporting* comparative performance
turns a bale into a controlled experiment with many replicates.

**Position rescue.** A turtle with a failed GPS is currently blind. A turtle
that can hear three neighbours with known positions can bound its own
location well enough to keep sailing a sensible heading. This is not
precision navigation, but the alternative is SAFE state and drift.

### 3.2 For the bale

**Data survival.** The headline benefit. Records replicate to neighbours, so
any single hull's loss stops being a data loss. A record taken on day 2
should have several independent copies by day 5.

**Opportunistic backhaul.** Some turtles will pass within range of shore, a
harbour, a vessel, or a moored gateway. Any turtle with connectivity becomes
the bale's uplink for as long as it has it, flushing not just its own queue
but everything it is carrying for others. `mission_connection_mode` already
anticipates exactly this split between *creating* records and *shipping*
them.

**Mother turtles.** A hull with no sailing mission, extra storage, and a
larger battery, deliberately deployed to sit in the middle of a bale and
listen. It never needs to reach a destination; its job is to be the black box.
Recovering one mother turtle recovers the bale's collective log. Cheaper and
far more reliable than recovering every hull.

**Fleet-scale science.** Many hulls, same water, same weather, different
policies and rigs — with a shared clock and shared positions. That is a
genuinely powerful experimental design, and it is not available to a fleet of
isolated devices no matter how well instrumented each one is.

---

## 4. Technical challenges

### 4.1 The pin budget forces a second MCU

This is settled by arithmetic, not preference.

With the L76K GNSS module stacked, `src/hal/board_xiao_esp32_s3.py` accounts
for every header pin on the XIAO except **D2/GPIO3** and **D9/GPIO8**:

| Pin | Owner |
|---|---|
| D0/GPIO1 | GPS_WAKEUP (reserved by GNSS module) |
| D1/GPIO2 | Button LED |
| D2/GPIO3 | **free** |
| D3/GPIO4 | Button |
| D4/GPIO5 | I2C SDA |
| D5/GPIO6 | I2C SCL |
| D6/GPIO43 | GPS UART TX |
| D7/GPIO44 | GPS UART RX |
| D8/GPIO7 | Servo PWM |
| D9/GPIO8 | **free** |
| D10/GPIO9 | GPS_RESET (reserved by GNSS module) |

An SX1262-class LoRa radio needs seven: SPI SCK/MOSI/MISO, NSS, RESET, BUSY,
DIO1. **Two free versus seven required.** There is no rearrangement that
fits — the GNSS module's reservations and the I2C bus are both immovable.

Three ways out, in order of preference:

1. **Second MCU as a LoRa co-processor**, talking to the XIAO over I2C
   (already wired, addresses free) or a shared UART. The radio MCU owns SPI,
   duty-cycle accounting, and the mesh protocol; turtleOS sees a simple
   "give me packets / take these packets" interface. Strong isolation: a mesh
   bug cannot stall the sailing loop, which matters because the nav
   controller has a 200–500 ms cycle and LoRa TX blocks for seconds.
   Costs a few dollars and some current.
2. **A LoRa-integrated board** (e.g. Heltec/TTGO ESP32+SX1262) as the *only*
   MCU, absorbing turtleOS. Cleanest hardware, but a HAL port and a
   re-verification of every driver — and it abandons the XIAO form factor the
   hulls are built around.
3. **Drop the GNSS module** to free D0/D10 and use a bare GPS on the freed
   pins. Recovers pins but not enough, and GPS is not optional.

**Recommendation: option 1.** It preserves the existing hull, HAL, and
driver stack; it isolates a hard-real-time radio task from the nav loop; and
it makes LoRa an *add-on* that a mixed fleet can adopt incrementally — which
matches the "strictly additive" property that makes this safe to roll out.

The I2C bus already carries six devices at 400 kHz; adding a seventh is
routine. A free address must be chosen clear of 0x0D, 0x38, 0x3C, 0x40, 0x53,
0x62, 0x68.

### 4.2 The wire format cannot be JSON

A telemetry payload today is JSON with named keys — `recorded_at`, nested
`values{}`, `flags{}`, `lat`, `lon`, `machine_state`, `confidence` — roughly
200–400 bytes. At SF10–12 the entire packet budget is **51 bytes**.

A packed binary record fits comfortably:

| Field | Type | Bytes |
|---|---|---|
| origin device id | uint16 | 2 |
| record sequence | uint16 | 2 |
| recorded_at | uint32 (epoch s) | 4 |
| lat | int32 (1e-5 deg, ~1 m) | 4 |
| lon | int32 (1e-5 deg, ~1 m) | 4 |
| machine_state | uint4 | ) 1 |
| flags (manual/auto, …) | uint4 | ) |
| battery | uint8 (0.1 V or %) | 1 |
| heading / COG | uint8 (2 deg) | 1 |
| wind angle solution | int8 | 1 |
| **subtotal** | | **20** |

Twenty bytes leaves room for a hop count, a short MAC, and either a second
record or a handful of optional sensor fields in one 51-byte packet.

This format is the **most expensive thing in the project to change later**,
because the shore ingest, the device firmware, and every stored record must
agree on it. It should be specified and version-tagged (a 4-bit format
version in byte 0) before any radio is purchased.

**The JSON path stays for WiFi.** Nothing about this replaces the existing
HTTP telemetry; LoRa gets a packed encoder and hopeturtles.org unpacks. A
turtle with WiFi should keep sending rich records.

### 4.3 Duty cycle and power

EU868 imposes a legal **1% duty cycle**: after a ~2.5 s SF12 transmission,
that sub-band must stay silent for roughly 250 s. That is a hard ceiling in
the low tens of messages per hour — *shared across everything the node
sends, including traffic it relays for others.*

This is the constraint that most shapes the mesh design. A naive
flood-everything relay will exhaust its duty-cycle budget carrying other
turtles' traffic and never send its own. The protocol needs an explicit
**transmit budget allocator** with a policy for how a node splits its
allowance between own-traffic and relayed traffic.

Power is the sibling constraint. SF12 TX draws on the order of 120 mA for
over a second per message; a relaying node can plausibly spend more radio
energy than the WiFi scanning recently removed from the send path. RX is much
cheaper but only under a duty-cycled receive schedule, which requires loose
time sync across the bale.

**Action before design work:** measure real device draw with the INA219
already on the bus (0x40). Idle, sailing, and a synthetic radio event. Every
power claim in this document is an estimate until that measurement exists.

### 4.4 Regulatory

Band plan is region-specific — EU868 in Europe, US915 in North America, with
different rules (US915 uses dwell time and frequency hopping rather than duty
cycle, and is more permissive for this use case). A vessel in international
waters is genuinely unclear territory. **This needs a real answer from
someone who knows maritime radio law before deployment**, not an assumption.
It changes the message budget substantially and therefore the protocol.

### 4.5 Mesh semantics — where the real complexity lives

Point-to-point LoRa is a weekend. The mesh is the project.

- **Dedup.** `(origin_device_id, sequence)` as a globally unique record key.
  Every node keeps a seen-set. The set must be bounded and survive reboots.
- **Hop limit.** A TTL decremented per relay, low (3–4). Without it, a bale
  that reconverges after partition will broadcast-storm itself into
  duty-cycle exhaustion.
- **Provenance.** A relayed record **must** keep its originator's device id.
  A record that arrives claiming the wrong origin is worse than a lost one —
  it is a false position report that corrupts the shared map.
- **Queue ownership changes meaning.** `device/telemetry_queue.json` is today
  "my unsent readings." It becomes "readings I am responsible for," including
  foreign ones. That needs a size cap, an eviction policy (oldest first? own
  records last?), and a decision on whether foreign records survive a reboot.
  Note this file is deliberately device-owned and excluded from the sync
  scripts — that stays true and becomes more important.
- **Partition and reconvergence.** Two halves of a bale drift apart for a
  week and meet again. Both carry thousands of records the other lacks. The
  reconnection must not produce an unbounded exchange; it needs a digest
  mechanism (e.g. exchange sequence ranges per origin, request only gaps).

### 4.6 Security

`X-Device-Id` / `X-Device-Key` headers are meaningless in a 20-byte frame. A
mesh that accepts and relays foreign records is a mesh that can be injected
into — with false positions, false wind data, or a duty-cycle exhaustion
flood. A short truncated MAC over the packed record with a per-device key is
the likely answer, with shore-side verification as the authority. Worth
noting the threat model is low (nobody is attacking a turtle bale today) but
the cost of designing it in now is also low, and retrofitting authentication
into a deployed wire format is not.

### 4.7 Time

Dedup, ordering, and duty-cycled RX windows all need consistent time. The
fleet is well placed here: DS3231 is already kept in UTC (a documented
invariant) and GPS provides an independent source. A turtle whose RTC has
failed should be able to take time from the mesh — which is another reason
records carry absolute epoch timestamps rather than relative ones.

---

## 5. Roadmap

Phases are sequenced so each produces something useful even if the next never
happens. Nothing here should start before the **Phase 0** decisions are
written down.

### Phase 0 — Paper only (no hardware, no code)

The cheapest and highest-leverage work. All of it is documents.

| Task | Output |
|---|---|
| Specify the packed wire format, version-tagged | `docs/bale_wire_format.md` |
| Specify mesh semantics: dedup, TTL, provenance, queue eviction, partition digest | `docs/bale_protocol.md` |
| Resolve the regulatory question for the intended operating area | decision recorded here |
| Measure real power draw (idle / sailing / synthetic radio event) with the INA219 | numbers recorded here |
| Choose the co-processor and confirm the I2C address is clear | decision recorded here |

**Exit criterion:** the wire format is stable enough that shore and device
teams could implement against it independently.

### Phase 1 — Aboard: transport abstraction

Only once Phase 0 is settled and a radio is actually on the bench. Doing this
earlier risks designing the wrong abstraction — the real requirements
(blocking behaviour, failure surfacing, duty-cycle accounting placement) are
not knowable without hardware.

- Extract a transport interface — `available()`, `send(record)`,
  `flush(queue)` — from `src/net/background_process.py`, whose `_send()`
  currently hardcodes WiFi association → HTTP POST → batch flush.
- Wire `mission_connection_mode` (already validated in `config.py`, already
  reserving `"lora"`) to select the transport.
- Implement the packed codec in `src/net/` with **host-side round-trip
  tests** — encode/decode is pure logic and needs no device.
- No behaviour change for WiFi-only turtles.

### Phase 2 — Aboard: point-to-point link

- Co-processor firmware: SPI to the radio, I2C to the XIAO, duty-cycle
  accounting owned entirely by the co-processor.
- `src/net/lora_transport.py` implementing the Phase 1 interface.
- Two turtles on a bench exchanging packed records. Then two turtles at
  opposite ends of a beach. **Then on water** — the sea-state effect on a
  30 cm antenna will not appear in any land test.

### Phase 3 — Aboard: store-and-forward mesh

- Seen-set, TTL, provenance preservation.
- Queue ownership change: foreign records, size cap, eviction policy.
- Transmit budget allocator splitting duty cycle between own and relayed
  traffic.
- Partition digest exchange.
- A new UI screen for bale state: neighbours heard, records carried,
  records relayed. Follow the conventions in CLAUDE.md — `f_arvo20` title at
  `x=0,y=5`, connection header at `icon_y=1`, and add it to
  `_preload_screens()`.

### Phase 4 — Ashore: hopeturtles.org

Runs in parallel from Phase 1; the device stream is useless without it.

- Ingest endpoint for packed records — distinct from the existing JSON
  telemetry endpoint, not a replacement.
- **Deduplication and provenance at ingest.** The same record will arrive
  many times by many paths, days apart, from turtles that did not take it.
  Ingest must key on `(origin_device_id, sequence)` and be idempotent.
- Model the relay graph: which turtle carried which record, and by what path.
  This is scientifically interesting in its own right — it is a measured map
  of bale connectivity over a voyage.
- Bale view: fleet positions, topology over time, per-turtle carried-record
  counts.
- Extend `mission_review` to reason about a bale rather than a hull —
  comparative policy performance across turtles in the same water is the
  payoff for the whole exercise.

### Phase 5 — Collective intelligence

Only meaningful once Phases 3 and 4 are real and a bale has actually flown.

- Wind-field sharing feeding `luff.py` re-sweep scheduling.
- Hazard/dead-water reporting weighting `waypoints.py` routing.
- Mother turtle build: same hull, added storage, no sailing mission.
- Position rescue from neighbour ranging for GPS-failed turtles.
- Over-the-air policy propagation — last, hardest, and only with a rollback
  story.

---

## 6. Open questions

These need answers from the team, not from the code:

1. **Which region and band?** Determines the entire message budget. Blocking
   for Phase 0.
2. **What is the realistic bale size** for the first deployment — 3 hulls or
   30? Below roughly 5 the mesh rarely has a relay path and the value is
   mostly data survival, not routing.
3. **Is a mother turtle in scope for the first bale**, or a later addition?
   It changes the storage requirement and the recovery plan.
4. **What is the acceptable added cost per hull?** A co-processor plus radio
   plus antenna against a hull that is deliberately cheap. If the mesh
   doubles the cost of a turtle, deploying twice as many isolated turtles may
   be the better experiment.
5. **What happens to a turtle that never meets another turtle?** Today's
   answer — queue to flash, hope for recovery — remains the fallback, and
   the design must not degrade it.

---

## 7. What this does not change

Worth stating plainly, because it bounds the risk.

- A turtle with no LoRa hardware behaves exactly as it does today.
- A turtle with LoRa hardware that hears nobody behaves exactly as it does
  today.
- The WiFi/HTTP telemetry path, the flash queue, and `mission_connection_mode`
  are unchanged in their existing modes.
- The mesh is strictly additive. There is no flag day, and a mixed fleet of
  mesh and non-mesh turtles is a supported configuration — indeed the
  expected one for the first several deployments.
