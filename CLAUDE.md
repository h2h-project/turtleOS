# turtleOS 2.x — Developer Guide

turtleOS is a MicroPython firmware for the **Seeed Studio XIAO ESP32-S3** — the **sole deployment target going forward** — that drives a sail-servo actuator, reads GPS and compass, monitors battery via INA219, and displays a live turtle animation on a 128×64 OLED. It is designed for marine and field navigation applications.

The XIAO's MicroPython build places the Python heap in its **8 MB Octal PSRAM** (~7,998 KB total; ~7,996 KB free after a full boot). Memory is **not** a practical constraint on this hardware — see [Memory headroom](#memory-headroom-xiao-esp32-s3). The Raspberry Pi Pico W and basic ESP32 are no longer deployment targets; their HAL files remain in the tree as legacy/portability artifacts only.

**airOS mode** (the original air-quality monitor) remains fully functional and is activated by setting `turtle_mode: false` in `config.json`. In that mode the device reads CO2, TVOC, temperature, and humidity from an ENS160 + AHT21 sensor pair and posts telemetry to a REST API at `http://air.earthen.io`. The Hardware Abstraction Layer keeps board-specific code isolated in `src/hal/` so the firmware could be ported to future hardware without rewriting application code — but only the XIAO HAL is actively maintained and tested.

---

## Operating modes

| Config key | Value | Active mode |
|---|---|---|
| `turtle_mode` | `true` (default) | **turtleOS** — turtle idle animation, servo/compass/sailpoint/battery screens |
| `turtle_mode` | `false` | **airOS** — "Know thy air…" idle screen, CO2/TVOC/temp/summary carousel |

The mode is read from `config.json` at boot and again at the top of every main-loop iteration. Changing the value on the device takes effect on the next power-cycle; there is no hot-reload.

**What changes between modes:**
- Idle / waiting screen (`TurtleWaitingScreen` vs `WaitingScreen`)
- Single-click carousel content (turtle navigation screens vs air quality screens)
- GPS configure_mode call (turtle tracking vs air-quality logging)

Everything else — WiFi, telemetry, connectivity carousel, time screen, button gestures, HAL — is shared by both modes.

---

## Repository layout

```
device/               ← everything deployed to the microcontroller
├── boot.py           ← MicroPython stage 1: sys.path setup
├── main.py           ← MicroPython stage 2: full boot pipeline
├── config.py         ← config manager (reads/writes config.json)
├── config.json       ← runtime config (not committed; device-specific)
└── src/
    ├── app/
    │   ├── main.py               ← main event loop (run())
    │   ├── main_pico.py          ← Pico-only override (legacy)
    │   ├── booter.py             ← animated boot progress bar
    │   ├── boot_guard.py         ← debug-mode REPL gate
    │   ├── rtc_sync.py
    │   ├── telemetry_scheduler.py
    │   └── telemetry_state.py
    ├── hal/
    │   ├── platform.py           ← detects "pico", "esp32", "esp32s3", "xiao_esp32s3"
    │   ├── board.py              ← facade: delegates to the correct board module
    │   ├── board_pico.py         ← Pico pin constants + init helpers
    │   ├── board_esp32.py        ← ESP32 pin constants + init helpers
    │   ├── board_esp32_s3.py     ← ESP32-S3 pin constants + init helpers
    │   └── board_xiao_esp32_s3.py ← XIAO ESP32-S3 pin map (current primary target)
    ├── input/
    │   └── button.py             ← AirBuddyButton (debounce, multi-click, hold)
    ├── ui/
    │   ├── oled.py               ← OLED wrapper (SSD1306/SH1106, font helpers)
    │   ├── flows.py              ← screen carousel orchestration
    │   ├── flows_pico.py         ← Pico-only carousel variant (legacy)
    │   ├── clicks.py             ← low-level click/dwell helpers used by flows
    │   ├── connection_header.py  ← GPS/API/WiFi icon cluster (top-right)
    │   ├── toggle.py             ← vertical toggle switch widget
    │   ├── glyphs.py             ← pixel-art icons (wifi, gps, api, gear, °, circle)
    │   ├── glyphs_pico.py        ← Pico-only glyph variant (legacy)
    │   ├── thermobar.py          ← horizontal thermometer bar widget
    │   ├── waiting.py            ← airOS idle screen ("Know thy air…")
    │   ├── logo_airbuddy.py      ← airOS splash logo
    │   └── screens/
    │       ├── — turtleOS screens —
    │       ├── turtle_waiting.py ← animated turtle idle screen (turtle_mode=true)
    │       ├── servo.py          ← sail servo status + test sweep
    │       ├── compass.py        ← live heading from QMC5883L
    │       ├── sailpoint.py      ← sail-angle overlay on compass reading
    │       ├── destination.py    ← destination/waypoint screen
    │       ├── battery.py        ← INA219 voltage/current/charge screen
    │       ├── — airOS screens (turtle_mode=false) —
    │       ├── co2.py            ← raw CO2 reading (ENS160)
    │       ├── eco2.py           ← eCO2 reading (alternate layout)
    │       ├── tvoc.py           ← TVOC reading
    │       ├── temp.py           ← temperature + humidity
    │       ├── temp2.py          ← alternate temp layout
    │       ├── summary.py        ← combined air quality summary
    │       ├── — shared screens —
    │       ├── time.py           ← local time / UTC / date
    │       ├── wifi.py           ← WiFi toggle screen
    │       ├── online.py         ← API/telemetry toggle screen
    │       ├── logging.py        ← telemetry rate screen
    │       ├── device.py         ← device info from API
    │       ├── gps.py            ← GPS fix status + manual telemetry logging
    │       ├── sleep.py          ← low-power screen
    │       ├── selfdestruct.py   ← factory reset / wipe (joke_mode gate)
    │       └── frowny.py         ← error / sad-face screen
    ├── fonts/
    │   ├── arvo16.py, arvo20.py, arvo24.py
    │   ├── mulish14.py
    │   └── ezFBfont_PTSansNarrow_07_ascii_11.py
    ├── drivers/
    │   ├── ds3231.py             ← RTC driver
    │   ├── aht10.py              ← temp/humidity driver
    │   ├── servo.py              ← MG996R sail servo driver (PWM, 50 Hz)
    │   ├── hmc5883l_qmc5883l.py  ← compass driver (GY-271 QMC5883L clone)
    │   ├── ina219.py             ← battery / power monitor
    │   ├── as5600.py             ← magnetic angle encoder
    │   ├── scd4x.py              ← SCD41 CO2 sensor (alternate)
    │   ├── bme280.py             ← BME280 temp/pressure/humidity (alternate)
    │   └── ezFBfont.py           ← font renderer
    ├── sensors/
    │   ├── air.py                ← AirSensor + AirReading (ENS160 + AHT21)
    │   ├── co2_test.py
    │   └── ublox6gps.py          ← u-blox NEO-6M GPS parser
    ├── net/
    │   ├── wifi_manager.py       ← STA connect/disconnect wrapper
    │   ├── wifi_manager_null.py  ← no-op stub for no-WiFi builds
    │   ├── telemetry_client.py   ← POST telemetry readings
    │   └── net_caps.py           ← wifi_supported() probe
    └── lib/
        └── urequests.py          ← lightweight HTTP (no SSL by default)

docs/                 ← development notes
scripts/              ← host-side deploy helpers
  ├── xiao_synker.sh  ← primary deploy script for XIAO ESP32-S3
  └── xiao_config.json ← base config installed by xiao_synker.sh
tests/                ← hardware/integration scripts (not unit tests)
backups/              ← archived experiment files
```

---

## How the device boots

MicroPython runs `boot.py` then `main.py` automatically on power-on.

### Stage 1 — `boot.py`
- Adds `/src` and `/src/lib` to `sys.path` so all imports work without prefixes.
- On ESP32 only: calls `esp.osdebug(None)` to suppress C-level log noise.

### Stage 2 — `main.py` (boot pipeline)
Six sequential steps run inside an animated `Booter` progress bar on the OLED. Each step holds for 500 ms so errors are readable:

| # | Step | What it does |
|---|------|-------------|
| 1 | **Loading config** | Reads `config.json` via `config.load_config()`. Applies defaults and migrates legacy keys. |
| 2 | **WiFi connect** | Probes `net_caps.wifi_supported()`. If supported, connects with a 4 s timeout and 0 retries (fast-fail). **Must run before AirSensor on ESP32** — see Gotchas. |
| 3 | **Device API check** | GET `/api/v1/device?compact=1` with `X-Device-Id` / `X-Device-Key` headers. Fetches device name, home, room, and community for the Device screen. Skipped if WiFi failed. |
| 4 | **RTC clock** | Reads DS3231 (I2C 0x68). Syncs `machine.RTC()` to UTC. DS3231 is always kept in UTC. |
| 5 | **Sensor warmup** | Scans I2C for ENS160 (0x53) / AHT21 (0x38). Creates `AirSensor` and calls `begin_sampling()`. Warmup default is 4 s (configurable via `warmup_seconds`). Skipped in turtle_mode if sensors are absent. |
| 6 | **GPS check** | If `gps_enabled`, opens UART and listens 1.2 s for NMEA bytes to confirm hardware is present. |

After the pipeline, `main.py`:
1. Draws the **waiting screen** (turtle animation or airOS idle, depending on `turtle_mode`).
2. Checks HAL for `btn_pin()` — if missing, shows an error and waits 30 s then auto-resets.
3. Calls `src.app.main.run(...)`, which is the permanent event loop.

### Debug mode gate (`boot_guard.py`)
Hold the button **at power-on for 2 seconds** → boot halts and drops to the MicroPython REPL instead of running the app. A file named `debug_mode` on the flash also triggers this. To exit: `import os, machine; os.remove('debug_mode'); machine.reset()`.

---

## Board pin maps

All board-specific code lives in `src/hal/`. Never hardcode pins outside these files. **The XIAO ESP32-S3 is the only deployment target**; the Pico, ESP32, and generic ESP32-S3 columns below are legacy reference for code that still exists in the tree — do not spend effort keeping them working.

| | Raspberry Pi Pico | ESP32 | ESP32-S3-N16-R8 | **XIAO ESP32-S3** (primary) |
|---|---|---|---|---|
| `sys.platform` | `"rp2"` | `"esp32"` | `"esp32"` | `"esp32"` |
| `platform_tag()` | `"pico"` | `"esp32"` | `"esp32s3"` | **`"xiao_esp32s3"`** |
| HAL file | `board_pico.py` | `board_esp32.py` | `board_esp32_s3.py` | **`board_xiao_esp32_s3.py`** |
| Button GPIO | GP15 | GPIO4 | GPIO4 | GPIO4 (D3) |
| Button LED | GP18 | GPIO18 | GPIO48 | GPIO2 (D1) — moved off D0, see GNSS note |
| I2C bus | I2C(0) SCL=GP1, SDA=GP0 | I2C(0) SCL=22, SDA=21 | I2C(0) SCL=6, SDA=5, 400 kHz | I2C(0) SCL=6 (D5), SDA=5 (D4), 400 kHz |
| GPS UART | UART(1) TX=GP8, RX=GP9 | UART(2) TX=17, RX=16 | UART(1) TX=43, RX=44 | UART(1) TX=43 (D6), RX=44 (D7) |
| Servo PWM | — | — | — | **GPIO7 (D8) — MG996R sail actuator** |
| WiFi | Pico W only (via `net_caps`) | Built-in | Built-in | Built-in |
| USB power detect | GP24 (VBUS) | board-specific | Returns `False` | Returns `False` |
| Heap concern | Moderate | High | High — WiFi PHY fragmentation risk | **None — 8 MB PSRAM heap** (see Memory headroom) |

**Stacked L76K GNSS module (Seeed SKU 109100021):** The XIAO GNSS module stacks onto the board and is wired exactly as the HAL expects — GPS_RXD on D6/GPIO43 and GPS_TXD on D7/GPIO44, default baud 9600. It also **reserves two extra pins**: `D0/GPIO1 → GPS_WAKEUP` and `D10/GPIO9 → GPS_RESET`. Because the module claims D0, the external button LED was relocated from D0/GPIO1 to **D1/GPIO2** (`BTN_LED_PIN = 2`). Do not reuse D0 or D10 for anything else while the module is stacked. All other header pins (D2, D3, D4/SDA, D5/SCL, D8/servo, D9) pass straight through and are unaffected — the I2C bus is untouched.

> **Note:** GPIO43/44 are also the ESP32-S3 UART0 console pins (`U0TXD`/`U0RXD`). A device continuously driving GPIO44 can, on some firmware builds, leak bytes into the REPL — see [Critical gotcha #16](#16-gps-uart-shares-gpio4344-with-the-esp32-s3-uart0-console-pins).

**I2C devices on the shared bus** (addresses the same across all ESP32-S3 variants):

| Device | I2C Address | Mode |
|--------|------------|------|
| QMC5883L (compass — GY-271 clone) | 0x0D | turtleOS |
| AHT10 / AHT21 (temp/humidity) | 0x38 | airOS |
| OLED (SSD1306/SH1106) | 0x3C | shared |
| INA219 (battery/current monitor) | 0x40 | turtleOS |
| ENS160 (CO2/TVOC) | 0x53 | airOS |
| SCD41 (CO2 — alternate sensor) | 0x62 | airOS |
| DS3231 (RTC) | 0x68 | shared |

**Platform detection** (`src/hal/platform.py`):
```python
from src.hal.platform import platform_tag
tag = platform_tag()   # "pico" | "esp32" | "esp32s3" | "xiao_esp32s3" | "unknown"
```

> **Note:** `sys.platform` returns `"esp32"` for all ESP32 variants. `platform.py` resolves this via `uos.uname().machine` in priority order: `"xiao"` in the machine string → `"xiao_esp32s3"`; `"ESP32S3"` → `"esp32s3"`; `"esp32"` → `"esp32"`. The XIAO check must precede the generic S3 check because XIAO firmware may include both strings.

**HAL facade** (`src/hal/board.py`): imports the right board module at runtime and re-exports `btn_pin()`, `btn_led_pin()`, `init_i2c()`, `i2c_pins()`, `gps_pins()`, `usb_power_present()`, `servo_pin()`, `servo_pwm_config()`. Always import from `src.hal.board`, never from platform-specific files directly. `servo_pin()` and `servo_pwm_config()` return `None` on boards without a servo wired (Pico, ESP32, ESP32-S3).

---

## User interaction — the button

One physical button wired active-low (pulled up internally). `AirBuddyButton` in `src/input/button.py` is non-blocking; call `btn.poll_action()` in a tight loop.

### Click actions

| Gesture | turtleOS action | airOS action |
|---------|-----------------|--------------|
| **Single click** | turtle navigation carousel | air quality carousel |
| **Double click** | Machine-state screen (three circles; double-click again starts the luff sweep) | Time screen |
| **Triple click** | Connectivity carousel | Connectivity carousel |
| **Quad click** | Show turtle waiting screen (or selfdestruct if `joke_mode`) | Self-destruct flow (factory reset) |
| **Hold 2 s** | GPS → Battery → Sleep → Version screens | GPS → Battery → Sleep → Version screens |

**How clicks work internally:**
- Button is sampled in every loop iteration (non-blocking).
- 50 ms debounce on edges.
- Clicks counted within a **500 ms window** after the first press. After the window expires, `poll_action()` returns the count as a string (`"single"`, `"double"`, etc.).
- Quad fires immediately on the 4th release (no window wait).
- A hold of ≥ 2 s while pressed returns `"sleep"` immediately.
- `btn.reset()` clears all pending state — call it at the start of any interactive screen.

### Double-click on toggle screens
Screens with a toggle switch (`wifi.py`, `online.py`, `logging.py`) use double-click to flip the enabled state:
- **WiFi**: toggles `wifi_enabled`, attempts connect or disconnect immediately.
- **Online**: toggles `telemetry_enabled`; turning on kicks off a fresh API handshake + connecting animation.
- **Telemetry**: toggles `telemetry_enabled` via `_apply_toggle()`.

---

## Single-click carousels

### turtleOS sensor carousel (turtle_mode=true)

Single-click enters `sensor_carousel()` configured for navigation screens:
1. **Sailpoint** screen — sail-angle overlay on heading.
2. **Servo** screen — sail servo status; double-click triggers a gentle, low-power 90°→80°→100°→90° test sweep (small ±10° range + slow ramp to avoid a battery rail sag that makes the servo stutter).
3. **Destination** screen — active waypoint.

The **GPS screen is deliberately not here** (nor in the connectivity carousel) — it
leads the hold flow instead, so hand-taken position stamps are one hold away from
the waiting screen. See [Manual GPS logging](#manual-gps-logging-hold-flow).

### airOS sensor carousel (turtle_mode=false)

Single-click enters `sensor_carousel()` configured for air quality:
1. Calls `air.finish_sampling()` for one full reading.
2. **CO2** screen (static, timed dwell).
3. **TVOC** screen (static, timed dwell).
4. **Temp** screen via `show_live()` — live-updating, exits on single click.
5. **Summary** screen via `show_live()`.

Any non-single click during a dwell exits the carousel early.

---

## Connectivity carousel in detail (`src/ui/flows.py`)

Triple-click enters `connectivity_carousel()` — same in both modes:

```
Waiting → Online screen
            ↓ single click (always advances)
         Logging screen
            ↓ single click (always advances)
         WiFi screen
            ↓ single click
         Device screen
            ↓ single click
         Waiting
```

**Key rules:**
- Online screen leads and is always shown, including offline — it renders "Offline" plus the pending unsent-telemetry count, which is how you check the queue without a connection.
- A single click **always** advances from Online to Logging (the `api_ok` gate was intentionally removed — the scheduler's `api_ok` flag lags the live handshake).
- The GPS screen is **not** in this carousel; it leads the hold flow. See [Manual GPS logging](#manual-gps-logging-hold-flow).
- Quad click at any step triggers `selfdestruct_flow`.
- `_entry_settle(btn)` drains tail bounces of the triggering triple-click at carousel entry. `_post_screen_flush(btn, ms=120)` drains between screens. Neither calls `btn.reset()` (which would eat real clicks).

---

## Manual GPS logging (hold flow)

`telemetry_mode: "manual"` (set on the Logging screen: off → auto → manual) turns
the GPS screen into a field logger. It is the **first screen of the hold flow**, so
recording a position is: hold 2 s, then click.

```
Waiting --hold 2s--> GPS screen --double click--> Battery --> Sleep --> Version --> Waiting
                       ↑ single click = record one reading
                       ↑ triple click = toggle gps_enabled
                       ↑ hold         = leave the flow
```

In `manual` mode the GPS screen inverts the usual gestures — **single click stamps a
reading, double click advances** — because stamping is the action repeated dozens of
times in a session while advancing happens once. In `auto`/`off` mode the legacy
mapping stands (single advances, double toggles GPS).

**What a stamp does** (`GPSScreen._manual_send` → `TelemetryState.send_manual`):
1. Arms `scheduler._manual_flag`, which punches through the `manual` gate in `tick()`
   and marks the payload `{"auto_log": false, "manual_registry": true}`.
2. `tick()` builds the payload and **commits it before returning** — handed to the
   background sender, or written straight to the flash queue when it can't be.
3. The screen reports "Stamped" immediately and never blocks; if a send outcome
   arrives within 6 s, `_poll_send_outcome()` upgrades the line to "Sent".
4. The registry line then shows `Logged: N` for the session. `Not stamped` means no
   payload could be built — check serial for the reason (`skip (rtc not epoch)` is
   the usual one: no DS3231 time, and a record with no timestamp is unusable).

**Invariants worth preserving:**
- A manual stamp is valid with **no sensor values at all** — position plus time is a
  complete record. `_build_full_payload()` reads GPS before the "no values" gate for
  exactly this reason; a turtle carrying no air sensor is the normal case.
- Whoever drains the GPS UART **must** publish to `src/nav/gpsfix.py`. The GPS screen
  and `NavController` both consume the same single UART, and the telemetry scheduler
  falls back to that cache when its own drain comes up empty. A screen that reads
  NMEA without publishing silently strips lat/lon from every stamp taken while it is
  open.
- `device/telemetry_queue.json` is **device-owned**. The sync/install scripts exclude
  it (along with `telemetry_last_sent.json`) from the rsync stage — staging it would
  overwrite the board's unsent readings on the next upload.

---

## The main event loop (`src/app/main.py`)

`run()` is an infinite loop. It:
1. Reads `config.json` at the top of each iteration; updates `_cfg_cell[0]` so background ticks see the latest config.
2. Maintains a `status` dict (`wifi_ok`, `api_ok`, `api_sending`, `gps_on`) updated by the telemetry scheduler.
3. Selects idle screen: `TurtleWaitingScreen` if `turtle_mode=true`, `WaitingScreen` otherwise.
4. Maintains a `screens` dict cache — screen objects are instantiated lazily and cached. A failed instantiation is **not** cached as `None`; next access retries.
5. Polls `btn.poll_action()` each iteration and dispatches to the appropriate flow function.
6. Calls `telemetry_scheduler.tick(...)` on every iteration to handle background posting without blocking.

**Screen cache pattern:**
```python
def get_screen(name):
    if name in screens and screens[name] is not None:
        return screens[name]
    # ... import, instantiate, cache
    screens[name] = instance
    return instance
```

---

## Telemetry scheduler (`src/app/telemetry_scheduler.py`)

Runs as a cooperative tick (called from the main loop, never blocking). Posts to `POST /api/v1/telemetry` every `telemetry_post_every_s` seconds (minimum 10 s, default 120 s).

**Gating — a reading is only sent if:**
- `telemetry_enabled` is `True` in config.
- WiFi is connected.
- The reading has real sensor data: `eco2 > 0`, `tvoc > 0`, temp in a plausible range, `0 ≤ rh ≤ 100`.
- Sensor warmup is complete.

**Time source:** derives UTC epoch seconds from `machine.RTC().datetime()`, not `time.time()` (which starts at epoch 0 on cold boot until synced).

**Auth:** sends `X-Device-Id` and `X-Device-Key` headers on every request.

---

## Configuration (`config.py` / `config.json`)

`load_config()` reads `config.json`, applies defaults, and migrates legacy keys. `save_config(cfg)` writes back atomically.

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `turtle_mode` | bool | `true` | **Primary mode switch** — `true` = turtleOS, `false` = airOS |
| `wifi_enabled` | bool | `false` | |
| `wifi_ssid` | str | `""` | |
| `wifi_password` | str | `""` | |
| `telemetry_mode` | str | `"auto"` | **authoritative**: `"auto"` posts on the interval, `"manual"` only on hand-taken GPS stamps, `"off"` never |
| `telemetry_enabled` | bool | `true` | derived mirror of `telemetry_mode != "off"`, rebuilt on every load; writers must set `telemetry_mode` |
| `telemetry_post_every_s` | int | `120` | min 10 |
| `api_base` | str | `"http://air.earthen.io"` | always HTTP — `https://` is stripped |
| `device_id` | str | `""` | |
| `device_key` | str | `""` | |
| `gps_enabled` | bool | `false` | |
| `timezone_offset_min` | int | `null` | UTC offset in minutes, −720 to +840 |
| `compass_offset_deg` | int | `0` | magnetic declination correction |
| `servo_present` | bool | `false` | authoritative flag for physical servo wiring |
| `joke_mode` | bool | `false` | quad-click shows selfdestruct instead of turtle screen |
| `oled_col_offset` | int | `0` | pixel offset for SH1106 column alignment |
| `board_type` | str | `""` | override for HAL if platform detection is unreliable |

Legacy key migration handled automatically: `"api-base"` → `"api_base"`, boolean strings normalized.

---

## OLED and fonts

`src/ui/oled.py` wraps SSD1306 / SH1106 (128×64). The `OLED` object exposes:

| Attribute | Font | Approx height |
|-----------|------|--------------|
| `f_small` | PTSansNarrow 7 | 7 px |
| `f_med` | Mulish 14 | 11 px |
| `f_large` | Arvo 24 | 20 px | **trimmed set: space · 0-9 · - · . · : · C · E · N · S · W only** |
| `f_arvo16` | Arvo 16 | 14 px |
| `f_arvo20` | Arvo 20 | 17 px |

Key helper methods:
- `oled.draw_centered(font, text, y)` — horizontally centers text.
- `oled._text_size(font, text)` → `(w, h)` in pixels.
- The raw framebuffer is `oled.oled` (SSD1306 object); call `oled.oled.show()` to flush.

**Screen title convention:** titles use `f_arvo20` left-aligned at `x=0, y=5`. Connectivity icons (`connection_header.draw()`) sit at `icon_y=1` on the right — they're right-aligned so they don't collide with left-aligned titles.

---

## Connection header (`src/ui/connection_header.py`)

Draws a right-aligned GPS / API / WiFi icon cluster at the top of any screen.

```python
from src.ui import connection_header as _ch
from src.ui.connection_header import GPS_NONE, GPS_INIT, GPS_FIXED

_ch.draw(
    fb,                    # raw framebuffer (oled.oled)
    oled_width,            # typically 128
    gps_state=GPS_NONE,    # GPS_NONE | GPS_INIT | GPS_FIXED
    wifi_ok=True,
    api_connected=True,
    api_sending=False,
    icon_y=1,              # top-y of icon strip
)
```

Icon cluster width is ~38 px (WiFi 9 + gap 4 + API 7 + gap 4 + GPS 14). Titles up to ~90 px wide won't collide.

---

## Toggle switch (`src/ui/toggle.py`)

Vertical pill-shaped toggle. All connectivity screens use the same geometry:

```python
self.toggle = ToggleSwitch(x=100, y=21, w=24, h=40)  # or h=43 for Online
self.toggle.draw(fb, on=bool_state)
```

Call `toggle.draw()` on every `_draw()` call — it re-renders from scratch each time.

---

## Adding a new screen

1. Create `device/src/ui/screens/yourscreen.py` with a class that has `show_live(self, btn)`.
2. Add a case in `src/app/main.py`'s `get_screen()` factory.
3. Import the connection header if the screen should show connectivity icons.
4. Pre-load the module in `main.py`'s `_preload_screens()` to avoid post-WiFi MemoryError on ESP32.
5. If the screen is turtle-mode-specific, document which carousel it appears in.

---

## Deploying to the XIAO ESP32-S3

The primary deploy script is `scripts/xiao_synker.sh`. It stages a XIAO-only copy (excludes Pico HAL and Pico-only overrides), then either hard-resets or syncs the board.

```bash
# Interactive mode (prompts for hard reset vs sync)
./scripts/xiao_synker.sh

# Non-interactive hard reset (wipe and re-upload)
./scripts/xiao_synker.sh --fresh

# Specify port explicitly
./scripts/xiao_synker.sh --port /dev/cu.usbmodem141301
```

The script also offers to set the DS3231 RTC from host system time (UTC) after a hard reset.

**Manual mpremote operations:**
```bash
# REPL
mpremote connect auto repl

# Run a single test script
mpremote connect auto run tests/blink.py

# Check free flash
mpremote connect auto exec "import uos; s=uos.statvfs('/'); print('Free:', s[0]*s[3]//1024, 'KB')"

# Reset the board
mpremote connect auto reset
```

---

## Running tests

There is no automated unit test suite. `tests/` contains hardware integration scripts deployed directly to the device.

```bash
mpremote connect auto run tests/i2c_scan.py
mpremote connect auto run tests/sensor_debug.py
```

---

## Memory headroom (XIAO ESP32-S3)

The XIAO's MicroPython heap lives in 8 MB PSRAM, so memory pressure is **not an active constraint** on the deployment target. Measured on hardware (June 2026):

| Metric | Value |
|---|---|
| Total Python heap | ~7,998 KB |
| Free after full boot (app + all preloads) | ~7,996 KB |
| Full `_preload_screens()` cost (all screens + nav stack) | ~91 KB |
| Entire `src/nav/` stack bytecode | ~30 KB (≈0.4% of heap) |
| Largest contiguous block allocatable after boot | 4 MB |

Check headroom anytime:

```bash
mpremote connect auto exec "import gc; gc.collect(); print('free:', gc.mem_free()//1024, 'KB')"
mpremote connect auto exec "import uos; s=uos.statvfs('/'); print('flash free:', s[0]*s[3]//1024, 'KB')"
```

The `[PRELOAD] start/done` lines printed at every boot show heap before/after module loading — if those numbers ever trend toward zero, that is the early warning.

---

## Critical gotchas

> Gotchas 1–5 below are **memory-discipline conventions inherited from the
> small-heap boards (Pico W ~70 KB free, non-PSRAM ESP32)**. On the XIAO's
> 8 MB heap they are not active constraints — `MemoryError`s from these
> paths do not occur in practice. Keep following them as cheap hygiene
> (they cost nothing and keep the code portable), but do not treat them as
> blocking design constraints when building new features.

### 1. WiFi MUST init before AirSensor on ESP32
ESP32's WiFi PHY allocates a large contiguous block. If AirSensor (also a large allocation) runs first, the heap becomes fragmented and WiFi init crashes with `MemoryError` on small-heap boards. The boot pipeline enforces this order; there is no reason to change it.

### 2. `gc.collect()` before every heavy allocation
Call `_gc()` before any `import`, sensor init, HTTP request, or JSON parse. MicroPython does not compact the heap; fragmentation is permanent until reset. (On the XIAO's 8 MB heap this is hygiene, not survival.)

### 3. Lazy imports everywhere
Do not add top-level imports to screen files or flow modules. Import inside functions / `try` blocks. Screen modules are pre-loaded at boot but class instances are created lazily.

### 4. Pre-load screen modules before WiFi (`_preload_screens`)
On small-heap boards, module bytecode imports can fail after WiFi fragments the heap, so `main.py` pre-loads all screen modules before `step_wifi()`. If you add a new screen used in any carousel, add it to the `_preload_screens()` list — it keeps boot deterministic and costs ~nothing.

### 5. Font pre-warming
Font writers have lazy internal caches. Calling `w.size("A")` once during boot warms those caches. `_preload_screens()` does this for all fonts.

### 6. `btn.reset()` vs `_post_screen_flush()`
- `btn.reset()` clears ALL pending click state. Use it at the start of interactive screens (e.g. `WiFiScreen.show_live`).
- `_post_screen_flush()` drains the click window for ~90–140 ms without resetting state. Use it *between* carousel screens to absorb bounce without eating the next real click. **Never call `btn.reset()` between screens in a carousel.**

### 7. Screen cache: failed instantiations are NOT cached as `None`
If `get_screen("foo")` raises an exception, the key is not written. The next call retries. This is intentional — a transient MemoryError should be retryable.

### 8. DS3231 is always stored in UTC
The `timezone_offset_min` config key is applied only at display time in `TimeScreen`. Never write local time to the RTC.

### 9. `api_ok` in status lags the Online screen handshake
`status["api_ok"]` is updated by the telemetry scheduler after a successful background POST, which may not have run yet when the user opens the Online screen. The Online screen's own `_connected` flag reflects the live handshake result — use that for the connection header icon on that screen.

### 10. HTTPS is not supported
`urequests.py` does not support TLS. `config.py` forcibly strips `https://` → `http://`. Do not add TLS without also swapping the HTTP library.

### 11. `telemetry_enabled` is shared between Online and Telemetry screens
Both screens read and write the same `cfg["telemetry_enabled"]` key. A double-click on either screen toggles the same setting. Reload config after toggling to stay in sync.

### 12. ESP32 platform detection via `uos.uname().machine`
`sys.platform` returns `"esp32"` on all ESP32 variants. `platform_tag()` resolves this in priority order: `"xiao"` in machine string → `"xiao_esp32s3"`; `"ESP32S3"` → `"esp32s3"`; `"esp32"` → `"esp32"`. The XIAO check must precede the generic S3 check because XIAO firmware may include both strings.

### 13. XIAO has no VBUS-detect GPIO
`usb_power_present()` in `board_xiao_esp32_s3.py` returns `False` by default. Battery monitoring is handled by the INA219 over I2C (0x40).

### 14. `servo_present` is the authoritative wiring flag
`PWM(Pin(n)).init()` always succeeds on ESP32-S3 regardless of whether a servo is physically wired, so software cannot detect physical connection. Always gate servo operations on `cfg["servo_present"]`.

### 15. `turtle_mode` is read fresh each main-loop iteration
The idle screen object (`turtle_waiting_scr`) is instantiated once at startup. Changing `turtle_mode` in config at runtime has no effect until the next power-cycle. Do not add hot-reload for this; the instantiation cost is too high mid-loop.

### 16. GPS UART shares GPIO43/44 with the ESP32-S3 UART0 console pins
The GPS UART is on `TX=43, RX=44` (D6/D7), which are **also the ESP32-S3's default UART0 console pins** (`U0TXD`/`U0RXD`). If a build leaves UART0 active as a secondary console with input enabled, a device that continuously drives GPIO44 (e.g. a GNSS module's TXD streaming NMEA at power-up) can inject bytes into the REPL's stdin — showing as garbage at the prompt, and a stray `0x03` reads as Ctrl-C (`KeyboardInterrupt`). This is a latent pin/console overlap to keep in mind when a "crash" correlates with the harness being wired up; note it has **not** been confirmed as the cause of any specific field issue, and `os.dupterm(None, 1)` does not address it (the console is at the IDF level, not a Python dupterm slot). If it is ever confirmed, the clean fix is to reflash MicroPython with the UART0 console disabled (console on USB-Serial-JTAG only), which frees GPIO43/44 entirely.

---

## Key file locations at a glance

| What | File |
|------|------|
| Boot pipeline | `device/main.py` |
| Main event loop | `device/src/app/main.py` |
| Config manager | `device/config.py` |
| Platform detection | `device/src/hal/platform.py` |
| HAL facade | `device/src/hal/board.py` |
| XIAO ESP32-S3 pin map (primary) | `device/src/hal/board_xiao_esp32_s3.py` |
| ESP32-S3 pin map | `device/src/hal/board_esp32_s3.py` |
| ESP32 pin map | `device/src/hal/board_esp32.py` |
| Pico pin map | `device/src/hal/board_pico.py` |
| Button handler | `device/src/input/button.py` |
| Screen carousels | `device/src/ui/flows.py` |
| OLED wrapper | `device/src/ui/oled.py` |
| Connection header | `device/src/ui/connection_header.py` |
| Turtle idle screen | `device/src/ui/screens/turtle_waiting.py` |
| Sail servo screen | `device/src/ui/screens/servo.py` |
| Compass screen | `device/src/ui/screens/compass.py` |
| Battery screen | `device/src/ui/screens/battery.py` |
| Telemetry scheduler | `device/src/app/telemetry_scheduler.py` |
| Air sensor + reading | `device/src/sensors/air.py` |
| GPS parser | `device/src/sensors/ublox6gps.py` |
| HTTP client | `device/src/lib/urequests.py` |
| XIAO deploy script | `scripts/xiao_synker.sh` |
