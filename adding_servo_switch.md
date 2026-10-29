# Adding the Servo Power Switch

The sail servo's power can be switched on and off by the XIAO. A **Pololu
Isolated Solid-State Relay/Switch, SPST, 30V 11A** ([#5421](https://www.pololu.com/product/5421))
sits in the servo battery's positive lead, ahead of the LM2596 buck regulator.
The XIAO controls it from the underside pad **MTDO / GPIO40**.

**Why.** The LM2596 draws about 5–10 mA whenever it is connected (chip quiescent
current, module LED, servo idle), even when the servo isn't moving. That is
roughly 120–240 mAh a day, which flattens the 2000 mAh servo pack within weeks.
With the switch, the regulator and servo are only powered while the servo is
being moved.

The fitted servo has enough internal friction to hold the sail in place with
its power off, so cutting the power between moves is safe.

See [Power_System.md](Power_System.md) for the overall two-battery architecture.

---

## Parts

| Part | Notes |
|---|---|
| Pololu #5421 isolated SSR, SPST, 30V 11A | 2.7–40 V control input, optically isolated from the load. The two load terminals are interchangeable. |
| Inline fuse holder + 5 A fuse *(optional, recommended)* | At the servo pack's + terminal. A shorted 18650 pack can deliver tens of amps. |

---

## Load side: switching the pack into the LM2596

The SSR is just an on/off switch placed in the **positive wire** between the
servo battery and the LM2596. Everything else is wired as before.

```
                    ┌─────────┐
PACK + ──[fuse]────►│   SSR   │────► LM2596 IN+
                    │ (load)  │
                    └─────────┘
PACK − ─────────────────────────────► LM2596 IN−  ──┐
                                                    ├── XIAO GND (common ground)
LM2596 OUT+ (~6.0 V) ───────────────► Servo red     │
LM2596 OUT− ────────────────────────► Servo brown ──┘
XIAO D8 / GPIO7 ────────────────────► Servo signal (yellow / orange)
```

Wire it in four steps:

1. **Cut the pack's + wire** between the pack (or fuse) and the LM2596 IN+.
2. **Put the SSR's two load terminals into that gap.** Either way round works.
3. **Leave the − wire alone.** Pack − still runs straight to LM2596 IN−, and the
   LM2596 ground still connects to XIAO GND.
4. **Leave the servo wiring alone.** Red and brown from the LM2596 output,
   signal from D8.

Three rules, and why:

| Rule | Why |
|---|---|
| Switch the **+** wire, never the − wire. | With the ground cut, the servo finds a return path through its signal wire into GPIO7. That can half-power the servo and damage the XIAO pin. |
| Switch the LM2596's **input**, not its 6 V output. | The regulator's own idle draw is what we're trying to save. Switching after it would leave it running. |
| Keep the **common ground** between the servo pack and the XIAO. | The servo's signal needs a shared reference. The SSR isolates its control pins from its load pins only; it doesn't replace the ground link. |

**Setting the LM2596 to ~6.0 V:** once the SSR is in, the regulator only gets
power when the switch is on. Set the voltage before fitting the SSR, or
temporarily bridge its two load terminals with a jumper while you adjust it.

**Ratings:** the 2S pack (8.4 V max) and the servo (~2.5 A at stall) are well
inside the SSR's 30 V / 11 A.

---

## Connecting the SSR to the XIAO

| SSR pin | Connects to | Why |
|---|---|---|
| **VIN** | XIAO **3V3** | Powers the SSR's internal driver (up to 10 mA when on), so the GPIO doesn't have to. |
| **GND** | XIAO **GND** | Control ground |
| **EN** | XIAO underside pad **MTDO / GPIO40** | HIGH = servo power on, LOW = off |

This is Pololu's three-wire hookup. Don't use their two-wire option (EN and VIN
tied together and driven from the GPIO): GPIO40 would then have to supply the
driver's 10 mA.

Check the silkscreen for the pin order on your board; don't assume it from this
table.

### The MTDO pad (GPIO40)

Every header pin is in use while the L76K GNSS module is stacked, so the switch
uses one of the four JTAG pads on the **underside** of the XIAO:

| Pad | GPIO | Use |
|---|---|---|
| MTCK | GPIO39 | free |
| **MTDO** | **GPIO40** | **servo power switch EN** |
| MTDI | GPIO41 | free |
| MTMS | GPIO42 | free |

GPIO40 is not a strapping pin, so it doesn't affect how the chip boots.

- **Solder the wire to MTDO before stacking the GNSS module.** You can't reach it
  afterwards.
- These pads lift easily. Strain-relieve the wire with a dab of hot glue or
  Kapton tape close to the pad.
- The button LED stays on **D1 / GPIO2**, unchanged.

---

## Bench test

1. Before connecting the servo, put a multimeter on LM2596 OUT. Power the turtle
   on: it should read 0 V, and stay at 0 V through the whole boot.
2. Make the servo move (for example, run a sweep from the Servo screen). The
   meter should jump to ~6.0 V.
3. About 30 s after the movement stops, the meter should drop back to 0 V.
4. Connect the servo and repeat. The servo should move during the sweep, then
   hold its position when the power drops.

---

## Building the switch with freehold parts

The Pololu SSR is a convenient ready-made module. On the next turtle_shell PCB
the same job can be done with a few small, cheap parts: a **P-channel MOSFET
high-side switch** with a small N-channel MOSFET to drive it from the XIAO's
3.3 V pin.

### How it works

- **Q1 (P-channel MOSFET)** is the actual switch. It sits in the + wire, exactly
  where the SSR's load terminals were. A P-channel MOSFET turns on when its gate
  is pulled several volts *below* its source (the pack +), and turns off when the
  gate is at the same voltage as the source.
- **R1** holds Q1's gate up at pack voltage, so Q1 is **off** by default.
- **Q2 (small N-channel MOSFET)** is the level shifter. The XIAO can only output
  3.3 V, which can't directly pull an 8.4 V gate high or low. When GPIO40 goes
  high, Q2 turns on and pulls Q1's gate down toward ground (through R2), which
  turns Q1 **on**.
- **R4** pulls Q2's gate to ground, so Q2 has a defined "off" state whenever
  the XIAO isn't driving GPIO40. Without it, the gate floats and Q1 can sit
  half-on, getting hot.
- **R1 + R2** form a divider so Q1's gate never sees the full pack voltage, and
  together with **C1** they make Q1 turn on gently over about a millisecond
  instead of instantly. That limits the inrush current into the LM2596's input
  capacitors.

### Schematic

```
PACK + ──[F1 fuse]──┬──────────── S ┐
                    │               │ Q1  (P-MOSFET)
                    ├─[R1 47k]──┐   │
                    │           ├── G
                    └─[C1 47n]──┤   │
                                │   D ┘──────────► LM2596 IN+
                             [R2 10k]
                                │
                                D ┐
GPIO40 ──[R3 220Ω]──┬────────── G │ Q2  (N-MOSFET)
                    │           S ┘
                 [R4 100k]      │
                    │           │
GND ────────────────┴───────────┴────────────────► LM2596 IN−
```

### Parts list

| Ref | Part | Example | Why this value |
|---|---|---|---|
| Q1 | P-channel MOSFET, SOT-23 | **AO3401A** (−30 V, −4 A, ≤ 52 mΩ at Vgs = −4.5 V, Vgs max ±12 V) | Rated well above the 8.4 V pack. Low resistance when on, so it barely warms at servo currents. |
| Q2 | N-channel logic-level MOSFET, SOT-23 | **BSS138** or **2N7002** | Turns fully on from the XIAO's 3.3 V. Only switches a fraction of a milliamp. |
| R1 | 47 kΩ | 0603 | Holds Q1 off. |
| R2 | 10 kΩ | 0603 | With R1, sets Q1's gate at about 82 % of pack voltage: −6.9 V on a full pack, −4.9 V on an empty one. Both turn Q1 fully on and stay inside its ±12 V limit. |
| C1 | 47 nF, ≥ 16 V | 0603 ceramic | Soft start: Q1 turns on over ~0.5 ms. |
| R3 | 220 Ω | 0603 | Limits the GPIO's current spike into Q2's gate. |
| R4 | 100 kΩ | 0603 | Pull-down on Q2's gate. |
| F1 | 3–5 A fuse *(optional)* | PTC or blade fuse | Protects against a shorted pack or wiring. |
| D1 | TVS diode, ~10–12 V *(optional)* | SMBJ10A, pack + to GND after the fuse | Clamps spikes from long battery leads or the servo motor. |

Total cost is well under a dollar, against roughly $10 for the Pololu module.

### PCB layout notes

- **Use wide traces** for the pack + → Q1 → LM2596 path and for the ground
  return. The servo can pull ~2.5 A at stall; aim for 1 mm (40 mil) or wider on
  1 oz copper.
- **Give Q1 a copper pour on its drain pad.** At 2.5 A it dissipates about
  0.3 W, which a SOT-23 handles with some copper around it. Stalls are brief.
  If the PCB will drive a bigger servo, step up to an SO-8 or DPAK P-MOSFET.
- **Place R4 right next to Q2,** so the gate can't pick up noise when the XIAO
  isn't driving it.
- **Keep the common ground**, as with the SSR. This design isn't isolated at
  all: Q2's source is the shared ground, so the pack − and XIAO GND must be
  joined (they already are).

### Compared with the Pololu SSR

| | Pololu SSR | Freehold circuit |
|---|---|---|
| Cost | ~$10 | < $1 |
| Isolation between XIAO and pack | Yes | No (not needed here; the grounds are joined anyway) |
| Extra wire to 3V3 | Yes (VIN) | No: only GPIO40 and GND |
| Current from the XIAO while on | ~10 mA from 3V3 | ~0.2 mA through R1/R2, plus ~33 µA through R4 |
| Draw while off | Near zero | Near zero (MOSFET leakage only) |
| Reverse-polarity protection | No | No (Q1's internal diode conducts if the pack is reversed; add a reverse-polarity MOSFET if this matters) |
| Soft start | Built in | C1 |

---

## Bonus Engineering Addition: extending the XIAO's USB-C port

Once the XIAO is buried in the control cap, its own USB-C port is hard to reach.
We extend it to the outside of the cap with a **RUNCCI-YUN waterproof USB-C
female panel-mount socket**
([Amazon UK B0CPLRH4W6](https://www.amazon.co.uk/dp/B0CPLRH4W6), sold in packs of 6).
Everything that works through the XIAO's own port then works through the panel
socket: uploading firmware, the serial console, and shore-charging the system
battery.

### The part

From the listing:

- A USB-C **female** socket on a moulded PVC panel-mount body, with a pigtail
  of **4 bare wires** for soldering.
- Rated **5 V, 3 A**.
- Sold as a "power cable" for chargers. It does have the four wires USB 2.0 data
  needs, but data isn't advertised, so test it (below) before building it in.
- The listing doesn't give the wire colours, the wire gauge or the panel hole
  size. Measure the hole size from the part.

The XIAO ESP32-S3 uses USB 2.0 full speed (12 Mbit/s), which only needs these
four signals:

| Signal | Typical colour | Notes |
|---|---|---|
| VBUS | red | +5 V from the laptop or charger |
| GND | black | Ground |
| D+ | green | Data + |
| D− | white | Data − |

**The colours aren't published for this part. Confirm each one before
soldering** (see the bench checks).

### How it connects

The XIAO doesn't break its USB data lines out to a header pin or pad (confirm
against the Seeed schematic for your revision), so the way in is through its
own USB-C socket, using a short **USB-C male plug pigtail**. Amazon pairs this
socket with a RUNCCI-YUN right-angle USB-C *male* pigtail (25 cm, 4 wires,
28 AWG, power + data), which suits this job, and its right angle keeps the plug
low against the XIAO. The turtle_shell PCB sits in the middle as a junction:

```
 RUNCCI-YUN socket          turtle_shell PCB                   XIAO
 (in the cap wall)
┌──────────┐  4 wires   ┌───────────────────────┐  4 wires   ┌──────────┐
│ USB-C    │───────────►│ J1 ──[ESD]── traces ──│───────────►│ USB-C    │
│ female   │            │                  J2   │  male      │ socket   │
└──────────┘            └───────────────────────┘  pigtail   └──────────┘
```

1. **Mount the socket** in the cap wall with its seal, and run its four wires to
   the PCB. Trim them so the run is no longer than needed.
2. **Crimp or solder the four wires into J1**, a 4-pin locking connector on the
   PCB (for example JST-GH or JST-PH), in the order printed on the silkscreen:
   VBUS, D+, D−, GND.
3. **On the PCB, J1 joins J2 pin for pin**, with the ESD chip at J1.
4. **Cut the male pigtail short**, put the matching 4-pin connector on its bare
   end, plug it into J2, and plug its USB-C end into the XIAO.

Routing it through the PCB, rather than soldering the socket's wires straight to
the plug's wires, gives the wires strain relief and somewhere to put ESD
protection. It also lets either cable be unplugged when the cap is opened.

### PCB design notes

- **Never swap D+ and D−.** USB won't connect if they're crossed. Label both
  connectors' pins on the silkscreen.
- **Route D+ and D− as a pair:** side by side, the same length, over an
  unbroken ground plane, with no vias if possible. At full speed this is
  forgiving, but keep the whole run (socket wires + PCB + pigtail) under about
  30 cm and twist the loose data wires together.
- **Add an ESD protection chip at J1**, for example a **USBLC6-2SC6** (SOT-23-6)
  across D+, D−, VBUS and GND. The panel socket is the one part of the
  electronics a person touches, often with wet or salty hands, and a static zap
  travels straight down the data lines into the ESP32.
- **Make VBUS and GND traces wider** (0.5 mm or more). They carry the charging
  current.
- **Keep VBUS separate from every other supply on the PCB.** Don't join it to
  the system battery, the servo pack, the solar charger or the 3V3 rail. It
  only goes from J1 to J2, and the XIAO handles it from there (including
  charging the system battery).
- **Join GND to the PCB's ground plane.** It's the same ground as everything
  else.

### Bench checks before building it in

Do these on one socket from the pack, before it goes into the cap.

1. **Identify the wires.** Plug a USB-C breakout board (or a cut cable with
   known colours) into the socket and use a multimeter's continuity beep to
   match each bare wire to VBUS, GND, D+ and D−. Write the colours down.
2. **Check that a USB-C to USB-C cable gives power.** A USB-C charger or laptop
   port only turns on 5 V once it sees a 5.1 kΩ resistor from the socket's CC
   pins to GND. A 4-wire socket can only provide that if the resistors are built
   into the socket itself. Plug a USB-C to USB-C cable from a phone charger into
   the socket and measure VBUS to GND on the bare wires:
   - **~5 V:** the resistors are there; any cable works.
   - **0 V:** they aren't. The socket then only works with **USB-A to USB-C**
     cables (USB-A ports always supply 5 V). Label the cap accordingly, or keep
     a USB-A to USB-C cable with the turtle.
3. **Check data.** Wire the socket to the male pigtail (temporarily, by hand or
   through the PCB), plug it into the XIAO and connect a laptop. The XIAO's
   serial port should appear and a firmware sync should run normally.

### In the field

- **Keep the socket capped** whenever the turtle is in the water. The PVC body
  seals the cap wall, but an open USB socket corrodes quickly in sea air.
- **The XIAO's own port stays occupied** by the pigtail plug. To use it
  directly, unplug the pigtail.
