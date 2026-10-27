# Adding the Servo Power Switch

The sail servo's power rail can now be switched off by turtleOS. A **Pololu
Isolated Solid-State Relay/Switch, SPST, 30V 11A** ([#5421](https://www.pololu.com/product/5421))
sits in the 2S servo pack's positive lead, ahead of the LM2596 buck regulator.
The XIAO turns it on and off from the underside pad **MTDO / GPIO40**.

**Why.** The LM2596 draws about 5–10 mA whenever it is connected (chip quiescent
current, module LED, servo idle), even when the servo isn't moving. That is
roughly 120–240 mAh a day, which flattens the 2000 mAh servo pack within weeks.
With the switch, the rail is only on while the servo is being driven.

See [Power_System.md](Power_System.md) for the overall two-battery architecture.

---

## Parts

| Part | Notes |
|---|---|
| Pololu #5421 isolated SSR, SPST, 30V 11A | 2.7–40 V control input, optically isolated from the load. Back-to-back MOSFETs, so the two load terminals are interchangeable. Ships without headers or terminal blocks. |
| 0.1″ header (3 pins) | For the control side: EN, VIN, GND |
| 5 mm (or 3.5 mm) screw terminal, 2-way | For the load side |
| Thin hook-up wire (30 AWG) | For the MTDO pad on the underside of the XIAO |
| 100 kΩ resistor | EN → GND pull-down, only if the board doesn't already have one (see below) |
| Inline fuse holder + 5 A fuse | Recommended, at the 2S pack's + terminal |
| 18–20 AWG wire | For the 2S pack → SSR → LM2596 run |

---

## The 2S servo pack

Two 3.7 V 2000 mAh 18650 cells **in series**: 7.4 V nominal, 8.4 V full,
6.0 V empty, 2000 mAh.

```
   Cell A                 Cell B
 [ −    + ]──────────── [ −    + ]
   │      (series link: A+ → B−)  │
   │                              │
 PACK −                         PACK +
```

- **Series link:** Cell A **+** → Cell B **−**.
- **Pack −** is Cell A's −, and **Pack +** is Cell B's +.
- If you fit a 2S BMS / protection board, wire it as its instructions say. Its
  **P+ / P−** outputs then become Pack + / Pack − below. This is optional; see
  "Servo Battery Charging" in `Power_System.md`.
- Put the **fuse** right at Pack +, before anything else. A shorted 18650 pack
  can deliver tens of amps.

This pack is **never** connected to the 1S system battery's positive rail, and it
never goes through the INA219. Only the grounds are shared.

---

## Load side: switching the pack into the LM2596

The SSR's two screw terminals go **in series with Pack +**, between the pack
and the LM2596 input:

```
PACK + ──[fuse 5A]──► SSR LOAD ──► SSR LOAD ──► LM2596 IN+
                       (either way round)

PACK − ───────────────────────────────────────► LM2596 IN−

LM2596 OUT+ (~6.0 V) ─────────────────────────► Servo red
LM2596 OUT− ──────────────────────────────────► Servo brown / black
XIAO D8 / GPIO7 ──────────────────────────────► Servo yellow / orange (signal)

XIAO GND ─────────────────────────────────────► LM2596 IN− / OUT− (common ground)
```

Rules:

- **Switch the positive lead, never the ground.** With the servo's ground cut,
  current finds its way back through the PWM signal wire into GPIO7. That can
  half-power the servo and stress the XIAO pin.
- **Switch the LM2596's input, not its 6 V output.** Cutting the input is what
  saves the regulator's own idle draw, which is the reason for the switch.
- **Keep the common ground.** The servo pack's − (LM2596 IN−/OUT−, which are
  joined on the common LM2596 modules) must connect to XIAO GND, or the PWM
  signal has no reference. The SSR's isolation is only between its control
  side and its load side; it doesn't remove the need for a common ground.
- **Set the LM2596 to ~6.0 V before connecting the servo.** With the SSR in
  place you'll need the rail on to measure it. Either set
  `"servo_power_mode": "on"` in `config.json`, or temporarily bridge the two
  SSR load terminals.
- **Ratings.** The 2S pack (8.4 V max) and MG996R (~2.5 A at stall) are well
  inside the SSR's 30 V / 11 A.

---

## Control side: XIAO → SSR

| SSR pin | Connects to | Why |
|---|---|---|
| **VIN** | XIAO **3V3** | Powers the SSR's isolated driver (up to 10 mA when on). Using the 3V3 rail keeps that load off the GPIO. |
| **GND** | XIAO **GND** | Control ground |
| **EN** | XIAO underside pad **MTDO / GPIO40** | HIGH = servo rail on, LOW = off |

This is Pololu's three-wire hookup. Their two-wire option (EN and VIN tied
together and driven by the GPIO) would make GPIO40 supply the driver's 10 mA,
so don't use it here.

Check the silkscreen for the pin order on your board; don't assume it from this
table.

### The MTDO pad (GPIO40)

Every header pin is in use while the L76K GNSS module is stacked, so the switch
uses one of the four JTAG pads on the **underside** of the XIAO:

| Pad | GPIO | Use |
|---|---|---|
| MTCK | GPIO39 | free |
| **MTDO** | **GPIO40** | **servo power switch EN** |
| MTDI | GPIO41 | free (D12; Sense board's mic data) |
| MTMS | GPIO42 | free (D11; Sense board's mic clock) |

GPIO40 is not a strapping pin, nothing else in turtleOS uses it, and the Sense
board's microphone doesn't use it. The JTAG function isn't needed: on the
ESP32-S3, debugging runs over USB.

- **Solder the wire to MTDO before stacking the GNSS module.** You can't reach it
  afterwards.
- These pads lift easily. Strain-relieve the wire with a dab of hot glue or
  Kapton tape close to the pad.
- The button LED stays on **D1 / GPIO2**, unchanged.

### Boot safety (fail-off)

GPIO40 floats from power-on until `device/main.py` drives it low (right after the
boot-guard button check, well before the boot bar). The servo rail must stay off
through that window, through a reset, and while the XIAO is dead:

- Check the Pololu product page or schematic for an internal pull-down on EN.
- **If there isn't one, fit 100 kΩ from EN to GND.** It costs nothing and makes
  the rail fail off.

---

## Firmware

| Piece | Where |
|---|---|
| Pin | `SERVO_PWR_PIN = 40` / `servo_power_pin()` in `device/src/hal/board_xiao_esp32_s3.py`, re-exported by `src/hal/board.py` (`None` on other boards) |
| Driver | `device/src/drivers/servo_power.py` |
| Boot | `device/main.py` drives EN low right after the boot-guard sample |
| Idle cut-off / mode | `servo_power.tick()` runs from `run()`'s `_bg_tick` |
| Sleep | the Sleep screen cuts the rail on entering sleep |

Every servo write powers the rail first: the autopilot, luff sweeps, Wrangler hand
control (all through `drivers/servo.py`) and the Servo screen's wind-finder sweep.
The first write after the rail was off waits **100 ms** for the LM2596 output to
come up. Later writes don't wait.

When the rail goes off, the firmware first stops the servo PWM and drives GPIO7
low, then drops EN. That way the unpowered servo is never fed through its signal
line.

### Config

| Key | Default | Meaning |
|---|---|---|
| `servo_power_mode` | `"auto"` | `"auto"`: on when the servo is driven, off after `servo_power_idle_s` with no servo write. `"on"`: always on (the behaviour before the switch). `"off"`: always off, and servo writes are skipped. |
| `servo_power_idle_s` | `30` | Idle time before `auto` cuts the rail. Clamped 5–3600. |

`servo_present: false` also holds the rail off.

**Unpowered means the sail is free.** A hobby servo with no power doesn't hold
its position. In `auto`, the rail stays on while the autopilot is steering, since
it writes every cycle. Once nothing has moved the servo for `servo_power_idle_s`
(for example after feathering, with `autopilot_sail: false`, or after a Wrangler
hand move), the sail can be pushed around by wind until the next write. If the
sail must hold against wind, use `"on"`.

The serial log shows `[SERVO] power on` and `[SERVO] power off (idle 30 s)`,
`(sleep)`, `(mode off)` or `(no servo)`.

---

## Bench test

1. Before connecting the servo, put a multimeter on LM2596 OUT. Boot the turtle:
   it should read 0 V.
2. Open the Servo screen and double-click to run a sweep. The log prints
   `[SERVO] power on`, and the meter should read ~6.0 V. Adjust the LM2596 now if
   needed.
3. Wait `servo_power_idle_s` after the sweep finishes. The log prints
   `[SERVO] power off (idle 30 s)`, and the meter drops back to 0 V.
4. Connect the servo and repeat. The servo should move during the sweep and go
   limp after the idle cut-off.
5. Hold the button at power-on to drop into debug mode. The rail must stay off
   the whole time. While the button is held, only the EN pull-down keeps it off;
   after that the firmware drives EN low.
