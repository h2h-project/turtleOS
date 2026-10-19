# turtleOS to-do

Open work that isn't scheduled yet. Newest first.

---

## Servo power switch (LM2596 rail cut-off)

**Problem.** The LM2596 buck that steps the 2S pack (6.0–8.4 V) down to 6 V for
the sail servo draws ~5–10 mA idle (chip quiescent + module LED + servo idle)
whenever it is connected, which is ~120–240 mAh a day. Over a long deployment that
flattens the pack.

**Hardware (decided 2026-10-16).** High-side P-MOSFET switch on the LM2596 input,
driven by a logic-level N-MOSFET from a spare GPIO. It fails off, so the rail is
off while the XIAO boots, resets or is dead.

```
 BATT+ (after INA219) ──┬──────── S  Q1 P-MOSFET  D ──► LM2596 IN+
                        R1 100k     G
                        ├───────────┘
                        R2 1k (optional; + 100 nF G–S to soften inrush)
                        D  Q2 2N7002 / BSS138
 GPIO ── R3 220Ω ────── G
             └ R4 100k ─ GND
                        S ── GND ─────────────────────────► LM2596 IN−
```

- Q1: FQP27P06 (TO-220, perfboard) or AO4407A (SO-8). Avoid the AO3401 (SOT-23)
  because it runs hot at ~2 A servo stall.
- Switch **after** the INA219 so battery monitoring keeps working.
- Never switch the ground. The servo signal would then back-feed through GPIO7.

**Pin.** Every header pin is taken while the L76K GNSS module is stacked.
- Not stacked (GPS wired by hand to D6/D7): **D10 / GPIO9** is free. Use it.
- Stacked: use the underside JTAG pad **MTDO / GPIO40** (solder the wire before
  stacking).
- Fallback: give up the button LED on D1 / GPIO2.

**Firmware to do:**
- [ ] `board_xiao_esp32_s3.py`: add `SERVO_PWR_PIN` + `servo_power_pin()` (None on
      other boards); re-export from `src/hal/board.py`. Update the GNSS
      "reserved" comment if D10 is used.
- [ ] Small driver/helper: `servo_power(on)`. Drive the pin as an output, default
      low (off). When turning off, stop servo PWM and drive GPIO7 low **first**,
      so the servo isn't back-powered through its signal line.
- [ ] Power the rail only while something needs the servo: NavController
      steering/feathering, luff sweeps (Servo screen wind-finder, state-screen
      sweep), Wrangler `SERVO_SET_ANGLE`. Allow a settle delay (~100 ms) after
      power-on before the first pulse.
- [ ] Config key (e.g. `servo_power_mode`: `"auto"` | `"on"` | `"off"`). Document it
      in CLAUDE.md's config table. Respect `servo_present` / `autopilot_sail`.
- [ ] Rail state on the Servo screen and in Wrangler Sail/Status (GATT contract
      change: update `docs/wrangler/README.md` first).
- [ ] Sleep screen: force the rail off on entering sleep.
- [ ] Bench test: measure pack drain with the rail off (expect µA) vs on, and check
      that the servo holds/re-centres correctly after a power cycle.
