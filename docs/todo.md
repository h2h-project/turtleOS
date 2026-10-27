# turtleOS to-do

Open work that isn't scheduled yet. Newest first.

---

## Servo power switch (LM2596 rail cut-off)

**Problem.** The LM2596 buck that steps the 2S servo pack (6.0–8.4 V) down to 6 V
for the sail servo draws ~5–10 mA idle (chip quiescent + module LED + servo idle)
whenever it is connected, which is ~120–240 mAh a day. Over a long deployment that
flattens the pack.

**Hardware (revised 2026-10-27).** A Pololu #5421 isolated solid-state relay
(SPST, 30V 11A) in the 2S pack's + lead, ahead of the LM2596 input. It replaces
the P-MOSFET + N-MOSFET circuit decided on 2026-10-16. The servo pack is separate
from the 1S system battery and never passes through the INA219 (see
`Power_System.md`). Control: VIN → 3V3, GND → GND, EN → underside pad
**MTDO / GPIO40**, so the button LED keeps D1 / GPIO2. Full wiring:
[`adding_servo_switch.md`](../adding_servo_switch.md).

**Done (firmware, 2026-10-27):**
- [x] `board_xiao_esp32_s3.py`: `SERVO_PWR_PIN = 40` + `servo_power_pin()`;
      re-exported from `src/hal/board.py` (None on other boards).
- [x] `src/drivers/servo_power.py`: `request()` / `off()` / `tick()`. Turning off
      stops servo PWM and drives GPIO7 low first. 100 ms settle on power-up.
- [x] Rail powered on demand by every servo write (`drivers/servo.py`, the Servo
      screen's wind-finder), cut after `servo_power_idle_s` from `_bg_tick`.
- [x] Config `servo_power_mode` (`"auto"` | `"on"` | `"off"`) +
      `servo_power_idle_s`; `servo_present: false` holds the rail off.
- [x] EN driven low right after the boot-guard sample in `device/main.py`.
- [x] Sleep screen cuts the rail on entering sleep.

**Still open:**
- [ ] Solder the MTDO wire and fit the SSR; check whether EN has an internal
      pull-down, else add 100 kΩ EN → GND.
- [ ] Bench test: measure pack drain with the rail off (expect µA) vs on, and check
      that the servo holds/re-centres correctly after a power cycle.
- [ ] Decide whether an unpowered (free) sail is OK between autopilot writes, or
      whether turtles at sea should run `servo_power_mode: "on"`.
- [ ] The first write after power-up blocks 100 ms, including Wrangler's
      `SERVO_SET_ANGLE` fast path (once per power-up). Make it non-blocking if
      that's noticeable on the dial.
- [ ] Rail state on the Servo screen and in Wrangler Sail/Status (GATT contract
      change: update `docs/wrangler/README.md` first).
