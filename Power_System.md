# Turtle Power System

The Hope Turtle uses **two separate battery systems**:

1. A **1S system battery** for TurtleShell, the XIAO ESP32-S3, sensors, GPS, display, telemetry and other electronics.
2. A dedicated **2S servo battery pack** for the MG996R sail servo.

This is a major upgrade from the earlier architecture, where one 3.7V battery powered both the electronics and the servo. Separating the two power domains keeps servo current spikes and electrical noise away from the XIAO and sensor buses.

---

## Power Architecture Overview

```text
SYSTEM POWER

Solar panel
    │
    ▼
Solar charger
    │
    ▼
INA219 VIN- side
    │
    ├── Main power switch ──> XIAO BAT+
    │
System Battery + ──> INA219 VIN+
System Battery - ───────────> System GND
```

```text
SERVO POWER

2 × 18650 cells in series
        │
        ▼
   2S pack (~7.4V nominal)
        │
        ▼
   LM2596 buck regulator
        │
        ▼
   ~6V regulated output
        │
        ▼
      MG996R servo
```

---

## System Power

The TurtleShell electronics are powered by a single **21700 Li-ion cell**, approximately **3.7V nominal / 4200mAh**.

The battery connects to the XIAO ESP32-S3 through its dedicated underside **BAT+ / BAT- pads**. The XIAO therefore runs directly from the 1S battery rather than from a boosted 5V rail.

```text
Battery + → INA219 → Main switch → XIAO BAT+
Battery - ───────────────────────→ XIAO BAT-
```

The main power switch disconnects the XIAO and TurtleShell electronics while leaving the solar charger connected to the battery. USB-C can still power the XIAO independently when plugged in.

---

## INA219 Battery Monitoring

The INA219 is positioned at the boundary of the system battery so TurtleOS can measure **net battery current** — both discharge and charge.

```text
Battery +
   │
   ▼
INA219 VIN+
   │
 [shunt]
   │
INA219 VIN-
   │
   ├── Solar charger BATT+
   └── Main switch → XIAO BAT+
```

Battery negative is shared between the battery, XIAO, INA219 and solar charger.

The INA219 convention is:

```text
VIN+ → VIN- = positive current
VIN- → VIN+ = negative current
```

So TurtleOS can interpret readings as:

```text
Positive current = battery discharging
Negative current = battery charging
```

Because both the solar charger and the XIAO connect to the **VIN- side**, the INA219 measures the net result of charging and system consumption.

Example:

```text
Solar provides:        300mA
System consumes:       120mA
Net battery charge:    180mA
INA219 reading:       -180mA
```

The INA219 is part of the **onboard/system I²C bus**, along with the OLED, RTC and future onboard sensors.

---

## Solar Charging

The 1S system battery is solar charged independently of whether TurtleShell is switched on.

```text
Solar panel → Solar charger → INA219 VIN- → INA219 → Battery
```

This keeps solar charging active while the XIAO is powered down and allows TurtleOS to detect charging current through the INA219.

The planned panel is the **Voltaic P126**, approximately **2W**, with a nominal operating voltage above 6V.

The current Robotistan **CN3065 Mini Solar Li-Po Charger** is specified for roughly **4.4–6V input**, while the Voltaic P126 can exceed 6V and has a substantially higher open-circuit voltage.

**This interface is not yet finalized.** Before deployment, either:

- use a solar charger rated for the P126's full voltage range,
- add suitable input regulation/clamping,
- or use a lower-voltage panel compatible with the CN3065.

USB-C charging through the XIAO remains available as a separate service/shore-charging method.

---

## Servo Power

The MG996R servo now uses its own dedicated **2S battery pack** made from two 3.7V 2000mAh 18650 cells in series.

```text
2 × 3.7V 2000mAh cells in series
        │
        ▼
7.4V nominal / 8.4V fully charged
2000mAh pack capacity
        │
        ▼
LM2596 buck regulator
        │
        ▼
~6V servo supply
        │
        ▼
MG996R
```

Because the cells are in series, the voltage doubles but the amp-hour capacity remains **2000mAh**.

The LM2596 steps the 2S pack down to the servo supply voltage. The regulator should be adjusted with a multimeter before connecting the servo; approximately **6.0V** is the current target.

The XIAO supplies only the servo PWM control signal:

| Servo wire | Connection |
|---|---|
| Red | LM2596 regulated output + |
| Brown / black | Servo power ground |
| Yellow / orange | XIAO servo PWM GPIO |

The servo must **not** be powered from the XIAO 3V3, 5V, BAT+ pad, or the main 1S system battery.

---

## Grounding Between Power Domains

The system and servo positive rails remain separate, but the XIAO and servo need a common reference for the PWM signal.

```text
XIAO PWM ─────────────────> Servo signal
XIAO GND ─────────────────> Servo regulator / servo GND

1S system battery +   ≠   2S servo battery +
```

This gives the servo and XIAO a shared signal ground without routing servo operating current through TurtleShell.

---

## Servo Battery Charging

For initial deployments, the 2S servo pack will be **fully charged before launch** and sized to last the planned voyage.

An onboard **2S balance charger / BMS** may be added later as an optional upgrade. Any charger used must support:

- 2S Li-ion chemistry
- 8.4V full-charge voltage
- cell balancing
- over-charge protection
- over-discharge protection

A 1S charger such as the CN3065 must never be used to charge the 2S servo pack.

---

## Power Domain Summary

| Domain | Battery | Regulation / Charging | Loads |
|---|---|---|---|
| System | 1 × 21700, 3.7V / 4200mAh | Solar charger + XIAO USB charging | XIAO, TurtleShell, sensors, GPS, OLED, telemetry |
| Servo | 2 × 18650, 2S, 7.4V / 2000mAh | LM2596 buck to ~6V | MG996R servo |

The design deliberately separates **energy for computation** from **energy for movement**. The system battery keeps TurtleOS alive and can be replenished from solar energy, while the independent 2S pack absorbs the servo's high-current demands without destabilizing the navigation electronics.
