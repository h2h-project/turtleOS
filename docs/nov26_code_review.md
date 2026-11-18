# turtleOS code review — November 2026

Reviewed at turtleOS 2.6.5 (commit `1265dcc` plus the lint fixes), 16 November 2026.

Everything here comes from reading the code on the laptop. Nothing was run on a
board, so each bug is marked:

- **Confirmed** — the code path is unambiguous.
- **Check on hardware** — the reading is clear but the effect depends on the
  board or the MicroPython build.

## What was reviewed

| Depth | Files |
|---|---|
| Read in full | `config.py`, `src/app/main.py`, `actions.py`, `journey.py`, `telemetry_scheduler.py`, `src/input/button.py`, all of `src/nav/` except `luff.py`, `src/net/background_process.py`, `telemetry_client.py`, `device_client.py`, `src/drivers/servo.py`, `servo_power.py` |
| Read in part | `device/main.py` (boot steps, crash handling), `src/ui/flows.py` (time flow), `src/net/ble_commands.py` (runner and first handlers), `sleep.py`, `togglemode.py`, `xiao_gnss.py`, `ina219.py`, `hmc5883l_qmc5883l.py`, `luff.py` (start conditions) |
| Not reviewed | `src/net/ble_service.py`, `ble_telemetry.py`, `wifi_manager.py`, `src/sensors/air.py`, `rtc_sync.py`, `clock.py`, most screens, the glyph and font files, the shell scripts |

Tools: `scripts/de_lint_please.sh` (clean), a wider ruff sweep for leads, and
the server's `controllers/deviceApiController.js` for the batch limit.

The parts not reviewed are roughly half of the firmware by line count, most of
it screens. A second pass should
start with `ble_service.py`, `wifi_manager.py` and `air.py`.

---

## 1. Bugs, in priority order

Priority is judged for an unattended turtle at sea first, bench use second.

### B1. A crash halts the turtle until someone power-cycles it — Confirmed, fixed in 2.6.6

> **Fixed (turtleOS 2.6.6, `src/app/watchdog.py`).** A hardware watchdog
> resets a hung turtle after `watchdog_timeout_s` (60 s), and a crash logs its
> traceback to `/crash.txt` and resets. Host-tested; the on-board checks
> (`tests/wdt_spike.py`, `wdt_hang.py`, `wdt_crash.py`) are still to run.
> B3 is now the next priority: after a reset the turtle does not resume
> sailing.

`device/main.py:1953-1961`. If `run()` raises, the handler writes `repr(e)` to
`crash.txt`, prints it and re-raises, which drops to the REPL. There is no
`machine.reset()` and no `machine.WDT` anywhere in the tree. A hang (a stuck
I2C transfer, a blocked socket) is never recovered either.

The "HAL missing" branch a few lines below does reset after 30 s, so the
pattern exists; the main path just doesn't use it.

**Fix:** reset after writing the crash file, and start a watchdog fed from
`_bg_tick`. Skip both when debug mode is active. Cap consecutive crash-resets
(a counter file) so a boot loop ends in the REPL, not in flash wear. Write the
traceback (`sys.print_exception(e, file)`) and append, not overwrite.

### B2. The offline queue stops draining for good once it passes 1,000 readings — Confirmed, fixed in 2.6.6

> **Fixed (turtleOS 2.6.6, `src/net/telemetry_client.py`).** `flush_queue()`
> now posts `FLUSH_CHUNK` (100) readings per batch, walking the file with a
> byte cursor, and cuts the delivered head off the file in one rewrite at the
> end of the round (also after a failed chunk), keeping anything appended
> meanwhile. A round stops at the first failure or after 90 s; the next send
> resumes. A reset mid-round resends chunks the server already has, which it
> ignores (`INSERT IGNORE` on turtle + `recorded_at`). At `QUEUE_MAX` the
> oldest 500 go in one rewrite. Queue counts on screen and over Bluetooth
> count down live during a drain. Host test: `cd device && python3
> ../tests/telemetry_queue_host.py`. Still to check on the board: a real
> multi-thousand-reading drain over WiFi.

`src/net/telemetry_client.py:476-498`. `flush_queue()` posts the whole queue in
one request. The queue holds up to `QUEUE_MAX = 5000`, but the server rejects
more than `MAX_BATCH = 1000` with `batch_too_large`
(`hopeTurtles.org/controllers/deviceApiController.js:12, 237`). A failed flush
keeps the queue, so every later flush fails the same way and the queue only
grows.

1,000 readings is 33 hours offline at the default 120 s, or under 3 hours with
`journey_log_every_s: 10`.

Two related problems in the same code:

- The whole queue is serialised into one string and sent with an 8 s timeout.
  Even under the limit, a few hundred readings over weak WiFi can time out.
- At the 5,000 cap, every `enqueue()` loads, trims and rewrites the entire
  file (about 2 MB) for one new reading.

**Fix:** drain in chunks of 100-200, removing each chunk from the file only
after its 2xx. Trim the cap in blocks (drop the oldest 500 at once).

### B3. After any reboot the turtle never sails again on its own — Confirmed, fixed in 2.6.6

> **Fixed (turtleOS 2.6.6, `src/nav/controller.py`).** Whenever the turtle is
> in ACQUIRE with a journey open, NavController runs three resume stages in
> order: battery ≥ `resume_batt_pct` (new, default 40), then a fresh GPS fix
> (no SAFE timeout while waiting), then one luff sweep, then SAIL_NAV. A
> reboot mid-journey therefore resumes by itself; boot logs `[NAV] journey
> <id> open at boot: resuming`. ACQUIRE does not start a sweep with no
> journey open. The stage shows on the State screen, in Bluetooth Nav `0111`
> (`resume_stage`, appended) and in telemetry (`flags.resume_stage`). Host
> test: `tests/nav_sim_host.py` (`test_resume`). Still to check on the
> board: start a journey, `machine.reset()`, watch the stages in the log.

`src/app/main.py:908-915`, `src/nav/controller.py:253-264`. Boot sets the state
to ACQUIRE. From there the only ways into SAIL_NAV are a human gesture: a
journey start (`actions._enter_sail_nav`) or a luff sweep started from the
State screen or Wrangler (`actions.nav_luff_sweep`). In ACQUIRE the controller
steps a sweep only if one is already running; it never starts one.

`journey_state.json` and `nav_state.json` both survive a reboot, and the header
of `waypoints.py` says the index persists "so a watchdog reboot resumes
mid-mission", but nothing reads the open journey at boot to resume steering.
A brownout at sea leaves the turtle tagging telemetry with a journey id while
drifting.

This matters more once B1 is fixed, because resets become routine.

**Fix:** at the end of boot, if a journey is open, enter SAIL_NAV (through
ACQUIRE). Decide separately whether ACQUIRE should start its own sweep.

### B4. SAFE and ARRIVAL are dead ends; a journey start from them reports OK but does nothing — Confirmed, fixed in 2.6.6

> **Fixed (turtleOS 2.6.6).** A journey start puts the turtle in ACQUIRE from
> any state (`actions._enter_acquire`) and the B3 resume stages take it to
> SAIL_NAV; it returns WRONG_STATE if the state machine ever refuses.
> Ending a journey drops SAIL_NAV, ARRIVAL or SAFE to ACQUIRE. SAFE clears
> to ACQUIRE by itself after 30 s once a fresh fix and a target are back
> (`NavController._safe_cycle`), and with a journey open the resume stages
> follow.

`src/nav/state_machine.py:23-29`, `src/app/actions.py:142-164`.

- `_enter_sail_nav()` asks for SAIL_NAV directly. From ARRIVAL or SAFE the
  state machine blocks that (`_VALID` allows only ACQUIRE), prints
  `blocked transition` and `journey_start()` still returns OK. The journey
  opens and tags telemetry; the turtle does not steer.
- `_stand_down()` only acts in SAIL_NAV, so ending a journey never leaves
  ARRIVAL or SAFE.
- Nothing else calls `set_state(ACQUIRE)`. The comment says leaving SAFE
  "requires a manual reset → ACQUIRE"; no such reset exists short of a reboot.

Consequences: after reaching a pond target (ARRIVAL), the next test needs a
reboot. At sea, 120 s without a GPS fix (`gps_loss_safe_s`) puts the turtle in
SAFE permanently, even when the fix returns a minute later.

**Fix:** have `_enter_sail_nav()` go through ACQUIRE from ARRIVAL and SAFE and
return `ERR_WRONG_STATE` if the transition is refused. Let SAFE recover on its
own when its cause clears (fix regained) after a hold-off.

### B5. A GPS fix older than about 6.2 days reads as fresh — Confirmed (period per the ESP32 port)

`src/nav/gpsfix.py:54-58`. The age is `ticks_diff(ticks_ms(), fix_ticks)`.
`ticks_ms()` wraps every 2^30 ms (12.4 days) and `ticks_diff` is only valid
within half of that. Once the last fix is more than ~6.2 days old the age goes
negative, so every `age < N` test passes:

- telemetry attaches the old position as current (`_read_gps_fix`),
- the GPS icon shows a fix (`_probe_gps`),
- `actions.current_fix()` returns it for "target = here" and journey stamps.

It needs an uptime of weeks with a long GPS outage, which is the mission
profile. The same pattern affects any `ticks_ms` stamp compared after 6 days:
`api_state["last_ok_ms"]`, the WiFi backoff's `_last_wifi_attempt_ms`.

**Fix:** store the fix's RTC unix time alongside the ticks, or mark the cache
expired from a periodic tick once it passes a hard limit.

### B6. A power cut during a config save can wipe the WiFi and device credentials — Confirmed

`device/config.py:264-275` and `197-261`.

- `save_config()` writes `config.json.tmp`, **removes** `config.json`, then
  renames. A reset between the remove and the rename leaves no config file.
  On LittleFS `os.rename` replaces the target atomically, so the remove only
  adds the window. (`journey.py`, `waypoints.py` and `telemetry_client.py`
  already rename without removing.)
- `load_config()` treats a missing or unparsable file as `{}`, fills in the
  defaults and **saves them**. The blank `wifi_ssid`, `device_id` and
  `device_key` are now on flash, and the leftover `.tmp` is never looked at.

Config is written on every Bluetooth setting, gesture toggle and online boot,
so the window is hit often.

**Fix:** drop the `os.remove`. On a failed read, try `config.json.tmp` before
falling back, and never write defaults over a file that exists but failed to
parse.

### B7. Five quick clicks switch the turtle to airOS and reboot it, with no confirmation — Confirmed

`src/app/main.py:1092-1099`, `src/ui/screens/togglemode.py`. A `quint` from the
waiting screen flips `turtle_mode`, saves it and calls `machine.reset()`. In
airOS mode there is no NavController. `button.py` fires quint on the fifth
release with no window, so a bouncing or wet button can do it. The gesture is
not in the developer guide.

**Fix:** require a confirming gesture on the toggle screen, or remove the
gesture and leave mode changes to Wrangler's `SET_TURTLE_MODE` and the Synker.

### B8. NMEA sentences are used without checking their checksum — Confirmed

`src/sensors/xiao_gnss.py:178-214`, `src/nav/gpsfix.py:14-45`. Any line that
starts `$GP`/`$GN` is parsed; the `*hh` checksum is ignored. The GPS UART
shares pins with the console (gotcha 16) and `read_nmea()` truncates its buffer
mid-stream when it overflows, so damaged lines are plausible. A corrupted digit
gives a valid-looking position kilometres off.

The 1° jump filter exists only in the telemetry scheduler's own drain
(`_drain_gps_to_cache`). In turtle mode NavController does the draining and
publishes whatever parses.

**Fix:** verify the checksum in `read_nmea()` and drop failures. Move the jump
filter into `gpsfix.update()` so every reader gets it.

### B9. In SAIL_NAV the servo rail never switches off, and the low-battery feather chatters — Confirmed, chatter fixed in 2.6.6

> **Chatter fixed (turtleOS 2.6.6).** The battery is now checked first in
> SAIL_NAV; below `low_batt_pct` the turtle feathers and drops to ACQUIRE,
> and the B3 resume waits for `resume_batt_pct` (default 40) before sailing
> again. **The servo write on every cycle is still open.**

`src/nav/controller.py:371-380`, `343-350`.

- `_set_sail()` writes the servo every nav cycle (300 ms) even when the angle
  is unchanged. Each write calls `servo_power.request()`, which resets the idle
  timer, so `servo_power_idle_s` never elapses while steering. The switch's
  saving only applies outside SAIL_NAV.
- The battery failsafe compares `pct < low_batt_pct` with no hysteresis. Around
  the threshold the sail flips between centre and the PID angle every cycle,
  which costs the most servo current exactly when the battery is low.

**Fix:** write only when the target moves by more than a deadband (2-3°).
Resume from feather at `low_batt_pct + 5`.

### B10. A journey-started SAIL_NAV never schedules the periodic wind re-sweep — Confirmed, fixed in 2.6.6

> **Fixed (turtleOS 2.6.6).** Only `NavController._enter_sail_nav()` enters
> SAIL_NAV now, and it always arms the re-sweep. A journey start runs a
> sweep before steering (B3).

`src/nav/controller.py:263, 269, 293-297`. `_next_sweep_ms` is armed only when
a sweep finishes. A journey start enters SAIL_NAV with no sweep, so
`luff_resweep_s` never takes effect and `wind_angle()` stays `None` for the
whole journey. The effect is small today because steering doesn't use the wind
angle yet (`90 + PID`, marked as a placeholder), and grows when it does.

**Fix:** arm the re-sweep whenever SAIL_NAV is entered.

### B11. The GPS icon on the turtle waiting screen is only refreshed on entry — Confirmed

`src/app/main.py:940-953`. `_probe_gps()` and `set_gps_state()` run at the top
of the main loop, which is only reached between screens.
`TurtleWaitingScreen` then reads `get_gps_state()` for as long as it shows, so
a fix gained or lost while idle isn't shown until the next gesture.

**Fix:** call `_probe_gps()` from `_bg_tick` (it is a cache read).

### B12. The send LED blink and Morse probably never run on the XIAO — Check on hardware

`src/net/background_process.py:318, 409` use `Timer(-1)`. The comment at
`device/main.py:1787` says the ESP32-S3 raises on `Timer(-1)`, and both call
sites swallow the exception. `drivers/servo.py:16` says the opposite ("the LED
blink uses a virtual one"). If `Timer(-1)` does work on the installed build, it
maps to a hardware timer and should be checked against the servo glide's
`Timer(2)`.

**Fix:** pick an explicit timer id for each user and record them in one place.

### B13. A failed dashboard PATCH is never retried — Confirmed

`src/net/device_client.py:7` and `actions.save_set_fields` both say the next
online boot re-pushes the `set_*` fields. `step_api()` has no such push, and
`patch_set_fields` has one caller. A target set offline never reaches the
dashboard.

The PATCH also runs on the main thread with a 6 s timeout, so each
destination or journey action can freeze the screen and Bluetooth for that
long when the server is slow. It calls `WLAN.isconnected()` there too, which
the rest of the code avoids while the background thread exists.

**Fix:** queue the PATCH to the background process and re-push at boot.

### B14. Smaller confirmed defects

| # | Where | Problem |
|---|---|---|
| a | `actions.gps_stamp` vs `telemetry_scheduler._read_gps_fix` | The stamp reports "no position" when the fix is over 5 s old, but the payload may still carry a fix up to 5 minutes old (`_GPS_FALLBACK_MS`), with nothing in it saying how old. |
| b | `telemetry_scheduler.tick` (background path, line 884) | `write_last_sent()` pairs the *new* reading's timestamp with the *previous* send's result, and writes flash on every reading. |
| c | `telemetry_scheduler._build_full_payload` | The journey event is marked reported when the reading is built. If the hand-off and the flash queue both fail, the event is lost. |
| d | `telemetry_scheduler._build_payload_parts` | A temperature or humidity of exactly 0.0 is dropped as "no reading". |
| e | `telemetry_scheduler.tick` | The next send time is set before the payload is built, so a reading skipped for "sampling in progress" or "rtc not epoch" waits a full interval. |
| f | `controller._sail_nav_cycle` | `_last_nav_ms` isn't cleared after a re-sweep or a feather, so the first PID step afterwards uses a `dt` of many seconds. Harmless while `ki = 0`. |
| g | `src/app/main.py` click routing | `screens.clear()` on every gesture throws away all cached screens. The quad click then builds a second `TurtleWaitingScreen` beside the one from boot, and screen state is lost. |
| h | `src/app/main.py:1078` | `selfdestruct_flow`'s return value is ignored, so a hold on that screen is lost. |
| i | `actions.current_fix` | Its listen loop drains GGA sentences without calling `gpsfix.note_gga()`. |
| j | `background_process.request_wifi_check` | Called on every return to the waiting screen; each one sets `sending` and starts the blink, so the API icon shows "sending" for 3 s with nothing sent. |
| k | `config._normalize_types` | `morse_bless` is not normalised; `sailpoint_offset_deg` is read in `main.py:637` but has no default. |

### B15. The developer guide disagrees with the code

| `CLAUDE.md` says | The code does |
|---|---|
| `gps_enabled` and `wifi_enabled` default to `false` | Both default to `True` (`config.py:26, 33`) |
| `config.py` strips `https://` to `http://` | It keeps the value as entered; `urequests` then tries `ussl` |
| Click window 500 ms, debounce 50 ms | `run()` builds the button with 800 ms and 45 ms |
| Double click (turtleOS) opens the machine-state screen | It opens the Time screen; a single click there goes on to State |
| Five clicks: not mentioned | Toggles `turtle_mode` and reboots (B7) |
| A reading is sent only with `eco2 > 0`, `tvoc > 0`… | Turtle mode sends battery, IMU and GPS with no air values |
| `device_client.py` re-pushes on boot | It doesn't (B13) |

---

## 2. Obsolete code

About 2,500 lines can go without touching the XIAO's behaviour.

| What | Size | Note |
|---|---|---|
| `src/app/main_pico.py`, `src/ui/flows_pico.py`, `src/ui/glyphs_pico.py` | 1,488 lines | Nothing imports them on the XIAO; the Synker already excludes them. |
| `src/hal/board_pico.py`, `board_esp32.py`, `board_esp32_s3.py` and their branches in `board.py` | 341 lines | Not deployment targets. |
| `_is_pico` / `_is_esp32` branches and `_preload_screens()` in `device/main.py` | ~300 lines | The XIAO skips the preload. |
| Inline send path in `TelemetryScheduler.tick()` plus `_morse_blink`, `_get_led`, `_may_retry_wifi` | ~200 lines | Only runs if `_thread` is missing, which it isn't on the XIAO. Duplicates the background process. |
| `src/sensors/ublox6gps.py` and 13 `LEGACY-NEO6M` blocks | small | Marked for deletion in their own comments. |
| `src/sensors/co2_test.py` | 34 lines | Nothing references it; it is a test script inside the firmware. |
| `run_screen()` in `src/app/main.py:494-534` | 40 lines | Never called. |
| `boot_state.txt` write in `device/main.py:1903-1913` | — | Debug leftover; one flash write per boot. |
| Fallback builders in `run()` (I2C, INA219, GPS, WiFi, waiting screen) | ~120 lines | Only reached on the Pico, where `step_init_runtime()` is skipped. |
| `_now_ms` / `_ticks_add` / `_ticks_diff` wrappers and the `gc is None` / `requests is None` guards | — | Guard against things that can't be missing on MicroPython. |
| Low-memory scaffolding: lazy `_json()` importers, `TelemetryState`'s lazy scheduler, `gc.collect()` around most calls | — | The 8 MB heap makes them unnecessary; the main loop already dropped them from `_bg_tick` for speed. |
| 51 blocks of commented-out code | — | Listed by `ruff check --select ERA001 device`. |
| `backups/` (11 tracked files) | — | Tracked although `.gitignore` lists the folder. `git rm -r --cached backups`. |
| `docs/xiao_setup_old.md`, `scripts/xiao_config_england.json` | — | Superseded by the installers and the config collection. |
| File headers reading "AirBuddy 2.1", "Pico-friendly", "LOW-MEM" | — | Misleading on a turtleOS 2.6 XIAO build. |

---

## 3. Improvements, in order of benefit

### I1. Make the turtle recover by itself

Watchdog, reset on crash, a crash log with tracebacks and a boot counter (B1),
then resume an open journey at boot (B3) and let SAFE clear itself (B4). These
three together are what turns a reset from mission-ending into a few seconds of
drift. Report the reset reason (`machine.reset_cause()`) and the last crash in
telemetry so they are visible from shore.

### I2. Make the offline queue trustworthy

Chunked drain (B2), block trimming at the cap, and one owner for the queue
file. Today two `TelemetryClient` objects (scheduler and background thread)
each cache their own `_queue_len`. Add a host test that fills the queue past
1,000 and drains it against a stub server.

### I3. Run the host tests and the linter automatically

`tests/*_host.py` already cover journeys, BLE fuzzing, the beacon, the nav
simulator and the wind finder, but each is run by hand. Add a
`scripts/run_host_tests.sh` and a GitHub Action that runs it with
`de_lint_please.sh` on every push. B2, B4, B5 and B6 are all testable on the
laptop.

### I4. Real power saving

- The Sleep screen turns off the OLED, servo rail and Bluetooth, but the CPU
  keeps running a 25 ms loop at full speed with WiFi up. Use
  `machine.lightsleep()` between nav ticks, with the button as a wake pin.
- In `wifi_manual` mode, deactivate the WiFi interface completely.
- Servo write deadband (B9).
- Measure each step with the INA219, as `ble_power_test.py` does for Bluetooth.

### I5. Better heading

The heading is `atan2(y, x)` plus one offset: no hard-iron or soft-iron
calibration and no tilt compensation, on a hull that rolls. The MPU6050's pitch
and roll are already read for telemetry. Add a calibration routine (rotate the
hull, store min/max per axis in config) and tilt-compensate. `heading.py`
already sketches the complementary filter.

### I6. Harden GPS handling in one place

Checksum, jump filter and satellite/quality gating inside `gpsfix` (B8), a
wall-clock age (B5), and `gps_age_s` in the telemetry payload so the server can
tell a live fix from a carried one (B14a).

### I7. Delete the legacy code

Section 2. It is the cheapest item here, and it makes every later change
easier: `device/main.py` drops below 1,500 lines and the two Pico "variant"
files stop drifting from their XIAO originals.

### I8. One copy of shared helpers

| Helper | Copies today |
|---|---|
| RTC → unix seconds | `telemetry_scheduler._now_unix_seconds`, `journey._now_unix_seconds` |
| `[lat, lon]` validation | `config._valid_pair`, `actions._valid_pair`, `waypoints._clean_pairs` |
| GATT result codes | `actions.py`, `ble_commands.py` |
| WiFi backoff constants and logic | `telemetry_scheduler.py`, `background_process.py` |
| LED pin lookup | `telemetry_scheduler._get_led`, `background_process._start_blink`, `_start_morse_blink` |
| API endpoint normalisation | `telemetry_client.py`, `device_client.py` |

### I9. Stop hiding errors

There are 516 `except Exception: pass` blocks in `device/`. Most are sensible
on a device that must not stop, but they also hide real faults (B12 is one).
Route them through a `log_once(tag, e)` helper that prints the first
occurrence of each, and count them in a diagnostics value that telemetry can
report.

### I10. Split the two largest files

`device/main.py` (1,967 lines) mixes the boot steps, an HTTP client for the
device lookup, the preload and the launch code. `src/ui/flows.py` (1,279
lines) holds every carousel. Moving the boot steps to `src/app/boot_steps.py`
and one carousel per module would make both reviewable.

### I11. Keep the screen cache

Drop `screens.clear()` (B14g) and build each screen once. Reuse the boot-built
`TurtleWaitingScreen` for the quad click.

### I12. Bring the developer guide back in line

Fix the rows in B15 and add a short "timers in use" and "files the turtle owns"
table (`crash.txt`, `boot_state.txt`, `journey_<id>.trk` and `config.json.tmp`
are not listed anywhere, and the Synker's exclude list doesn't name the `.trk`
files).
