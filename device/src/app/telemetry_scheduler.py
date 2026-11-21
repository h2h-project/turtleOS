# src/app/telemetry_scheduler.py
# Telemetry scheduler: decides when a reading is due, builds the payload from
# local sensors and hands it to the background process
# (src/net/background_process.py), which owns WiFi, HTTP and the queue drain.

import time

LAST_SENT_FILE = "telemetry_last_sent.json"
QUEUE_FILE = "telemetry_queue.json"

def _json():
    """Lazy JSON import (prefer ujson)."""
    try:
        import ujson as _j
        return _j
    except Exception:
        import json as _j
        return _j


def _gc_collect():
    try:
        import gc
        gc.collect()
    except Exception:
        pass


class TelemetryScheduler:
    def __init__(self, air_sensor, rtc_info_getter=None, wifi_manager=None, gps=None, battery_sensor=None,
                 imu=None, heading_getter=None):
        self.air = air_sensor
        self.get_rtc = rtc_info_getter
        self.wifi = wifi_manager
        self.gps = gps
        self._ina = battery_sensor
        # GY-87 10DOF (src/drivers/gy87.py): BMP180 baro + MPU6050 pitch/roll.
        # heading_getter returns NavController's heading (compass_offset_deg
        # applied) so telemetry reports exactly what the compass screen shows.
        self._imu = imu
        self._heading_getter = heading_getter

        self._client = None
        self._next_send_ms = time.ticks_add(time.ticks_ms(), 30000)
        self._last_reading = None

        self._dbg_every_n = 1
        self._dbg_count = 0

        # Public state for UI (Online screen reads this instead of making its own HTTP)
        # last_ms stamps every send *attempt* (success or failure); last_ok_ms
        # stamps only a successful one, so "last sent" on the Online screen can
        # report actual connectivity instead of the most recent retry.
        # last_ms is ticks_ms (short "did a send just finish" checks only);
        # last_ok_ms is src/app/monotonic.ms(), because it can be days old.
        self.api_state = {"ok": None, "sending": False, "msg": "", "last_ms": None, "last_ok_ms": None}
        self._send_now = False
        self._next_event_try_ms = time.ticks_ms()   # see tick(): journey events

        # One-shot: set by send_manual() so the next payload is flagged as a
        # hand-taken registry stamp rather than an automatic sample.
        self._manual_flag = False

        # Background process — set via set_background_process() after start-up.
        # tick() hands every payload to it; until it is attached (or if its
        # thread is down) payloads go to the flash queue, which it drains.
        self._background_process = None

    def set_background_process(self, background_process):
        """
        Attach a TelemetryBackgroundProcess instance. After this call, tick()
        hands payloads off to the background thread instead of sending inline,
        so the caller is never blocked by WiFi or HTTP operations.
        """
        self._background_process = background_process

    def request_now(self):
        """Ask the scheduler to send at the next tick (called by Online screen)."""
        self._send_now = True
        self._next_send_ms = time.ticks_ms()
        self._dbg_print("[ONLINE] request_now: send queued")

    def send_manual(self):
        """
        Arm a hand-taken registry stamp. Punches through the "manual" mode gate
        in tick() and marks the resulting payload as manual, not auto-logged.
        """
        self._manual_flag = True
        self.request_now()

    def manual_pending(self):
        """
        True while an armed manual stamp has not yet produced a payload.
        _build_full_payload() consumes the flag only when it actually builds one,
        so a still-set flag means no record was created (RTC not yet synced,
        sampling in flight, or no values) — the caller must not report success.
        """
        return bool(self._manual_flag)

    def clear_manual(self):
        """Drop an unconsumed manual arm so it cannot leak into a later send."""
        self._manual_flag = False

    @staticmethod
    def read_last_sent():
        j = _json()
        try:
            with open(LAST_SENT_FILE, "r") as f:
                return j.load(f)
        except Exception:
            return None
        finally:
            _gc_collect()

    @staticmethod
    def write_last_sent(ts, ok=True):
        j = _json()
        try:
            with open(LAST_SENT_FILE, "w") as f:
                j.dump({"ts": int(ts), "ok": bool(ok)}, f)
        except Exception:
            pass
        finally:
            _gc_collect()

    @staticmethod
    def queue_size():
        # Shared with the sender, so a drain in progress counts down live.
        try:
            from src.net.telemetry_client import queue_count
            return queue_count()
        except Exception:
            return 0

    def _dbg_print(self, *parts):
        try:
            print(*parts)
        except Exception:
            pass

    def _ensure_client(self, cfg):
        if self._client is not None:
            return self._client

        from src.net.telemetry_client import TelemetryClient

        api_base = (cfg.get("api_base") or "").strip()
        device_id = (cfg.get("device_id") or "").strip()
        device_key = (cfg.get("device_key") or "").strip()

        self._client = TelemetryClient(
            api_base=api_base,
            device_id=device_id,
            device_key=device_key
        )
        return self._client

    @staticmethod
    def _dt_to_unix(y, mo, d, hh, mm, ss):
        # Direct UTC-components → Unix timestamp. Avoids time.mktime() which returns
        # Unix epoch on RP2040 but MicroPython epoch (2000-01-01) on ESP32/ESP32-S3,
        # making the offset addition wrong on one of the two platforms.
        _MDAYS = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        leap = (y % 4 == 0) and (y % 100 != 0 or y % 400 == 0)
        leaps = (y - 1) // 4 - (y - 1) // 100 + (y - 1) // 400 - 477
        days = (y - 1970) * 365 + leaps
        for m in range(1, mo):
            days += _MDAYS[m] + (1 if m == 2 and leap else 0)
        days += d - 1
        return days * 86400 + hh * 3600 + mm * 60 + ss

    def _now_unix_seconds(self):
        try:
            from machine import RTC
            y, mo, d, wd, hh, mm, ss, sub = RTC().datetime()
            if y < 2020:          # RTC not synced yet — return 0 so caller skips
                return 0
            return self._dt_to_unix(y, mo, d, hh, mm, ss)
        except Exception:
            try:
                # time.time() uses MicroPython epoch (2000-01-01) on all baremetal ports
                return int(time.time()) + 946_684_800
            except Exception:
                return 0

    def _sampling_in_progress(self):
        a = self.air
        if a is None:
            return False

        try:
            wu = getattr(a, "_warmup_until", None)
            if wu is not None:
                try:
                    if time.ticks_diff(time.ticks_ms(), wu) < 0:
                        return True
                except Exception:
                    return True
        except Exception:
            pass

        try:
            if hasattr(a, "is_ready") and callable(a.is_ready):
                if not a.is_ready():
                    return True
        except Exception:
            pass

        return False

    _GPS_FRESH_MS = 5000        # a cache this new is someone else's live drain: just use it
    _GPS_FALLBACK_MS = 300000   # oldest cached fix a reading may still carry

    def _read_gps_fix(self):
        """The turtle's position for this reading: (lat, lon) or (None, None).

        One fix, one source: the shared cache in src/nav/gpsfix.py, which is
        also what Bluetooth Position (0110) sends, so a reading and the
        Wrangler app always agree. In turtle mode NavController drains the
        UART every nav cycle and keeps the cache fresh, so this just reads it.
        Only when nobody has fed the cache lately (airOS, or nav not ticking)
        does this drain the UART itself, and then it publishes what it reads
        (RMC and GGA) back to the cache, like every other reader must.
        """
        try:
            from src.nav import gpsfix
        except Exception:
            return None, None
        lat, lon, age_ms = gpsfix.get()
        if lat is not None and age_ms is not None and age_ms < self._GPS_FRESH_MS:
            return lat, lon
        if self.gps is not None:
            self._drain_gps_to_cache(gpsfix)
        lat, lon, age_ms = gpsfix.get()
        if lat is not None and age_ms is not None and age_ms < self._GPS_FALLBACK_MS:
            return lat, lon
        return None, None

    def _drain_gps_to_cache(self, gpsfix):
        """Drain the UART to the end and publish every RMC (and any GGA) to
        gpsfix. read_nmea() has already dropped bad checksums and
        gpsfix.update() drops position jumps, for every reader (review B8)."""
        try:
            for _ in range(30):
                line = self.gps.read_nmea()
                if line is None:
                    break
                if "GGA" in line:
                    gpsfix.note_gga(line)
                    continue
                if "RMC" not in line:
                    continue
                lat, lon, cog = gpsfix.parse_rmc(line)
                if lat is not None:
                    gpsfix.update(lat, lon, cog)
        except Exception:
            pass

    def _build_payload_parts(self, reading, rtc_temp_c=None):
        values = {}
        confidence = None

        if reading is not None and (not isinstance(reading, dict)):
            try:
                # ENS160 fields — eco2 > 0 confirms ENS160 was active (tvoc can legitimately be 0 in clean air)
                eco2 = getattr(reading, "eco2_ppm", None)
                tvoc = getattr(reading, "tvoc_ppb", None)
                aqi = getattr(reading, "aqi", None)
                if eco2 is not None and int(eco2) > 0:
                    values["ens_eco2"] = int(eco2)
                    if aqi is not None:
                        try:
                            values["ens_aqi"] = int(aqi)
                        except Exception:
                            pass
                if tvoc is not None and int(tvoc) > 0:
                    values["ens_tvoc"] = int(tvoc)

                # BME280 fields (temp + humidity + pressure)
                bme_temp = getattr(reading, "bme280_temp_c", None)
                bme_rh = getattr(reading, "bme280_humidity", None)
                bme_pressure = getattr(reading, "bme280_pressure_hpa", None)
                if bme_temp is not None:
                    try:
                        v = float(bme_temp)
                        if v != 0.0:
                            values["bme_temp"] = round(v, 2)
                    except Exception:
                        pass
                if bme_rh is not None:
                    try:
                        v = float(bme_rh)
                        if v != 0.0:
                            values["bme_humidity"] = round(v, 1)
                    except Exception:
                        pass
                if bme_pressure is not None:
                    try:
                        v = float(bme_pressure)
                        if v > 0.0:
                            values["bme_pressure"] = round(v, 1)
                    except Exception:
                        pass

                # AHT fields — prefer AHT21, fall back to AHT10
                aht_temp = getattr(reading, "aht21_temp_c", None) or getattr(reading, "aht10_temp_c", None)
                aht_rh = getattr(reading, "aht21_humidity", None) or getattr(reading, "aht10_humidity", None)
                if aht_temp is not None:
                    try:
                        v = float(aht_temp)
                        if v != 0.0:
                            values["aht_temp"] = v
                    except Exception:
                        pass
                if aht_rh is not None:
                    try:
                        v = float(aht_rh)
                        if v != 0.0:
                            values["aht_humidity"] = v
                    except Exception:
                        pass

                # SCD41 fields
                scd_co2 = getattr(reading, "scd41_co2_ppm", None)
                scd_temp = getattr(reading, "scd41_temp_c", None)
                scd_rh = getattr(reading, "scd41_humidity", None)
                if scd_co2 is not None and int(scd_co2) > 0:
                    values["scd_co2"] = int(scd_co2)
                if scd_temp is not None:
                    try:
                        values["scd_temp"] = float(scd_temp)
                    except Exception:
                        pass
                if scd_rh is not None:
                    try:
                        values["scd_humidity"] = float(scd_rh)
                    except Exception:
                        pass

                conf = getattr(reading, "confidence", None)
                if conf is not None:
                    try:
                        confidence = {"sensor_confidence": int(conf)}
                    except Exception:
                        pass

            except Exception:
                pass

        if isinstance(reading, dict):
            def g(*keys):
                for k in keys:
                    try:
                        v = reading.get(k, None)
                    except Exception:
                        v = None
                    if v is not None:
                        return v
                return None

            # Accept both new and legacy key names from queued readings
            eco2 = g("ens_eco2", "eco2", "eCO2", "eco2_ppm", "co2_ppm", "co2")
            tvoc = g("ens_tvoc", "tvoc", "tvoc_ppb")
            aqi = g("ens_aqi", "aqi")
            temp = g("aht_temp", "temp_c", "temperature_c", "t_c")
            rh = g("aht_humidity", "rh_pct", "rh", "humidity", "humidity_rh")
            bme_t = g("bme_temp")
            bme_h = g("bme_humidity")
            bme_p = g("bme_pressure")

            if eco2 is not None:
                values["ens_eco2"] = eco2
            if tvoc is not None:
                values["ens_tvoc"] = tvoc
            if aqi is not None:
                try:
                    values["ens_aqi"] = int(aqi)
                except Exception:
                    pass
            if temp is not None:
                values["aht_temp"] = temp
            if rh is not None:
                values["aht_humidity"] = rh
            if bme_t is not None:
                values["bme_temp"] = bme_t
            if bme_h is not None:
                values["bme_humidity"] = bme_h
            if bme_p is not None:
                values["bme_pressure"] = bme_p

            conf = g("confidence", "sensor_confidence")
            if conf is not None:
                try:
                    confidence = {"sensor_confidence": int(conf)}
                except Exception:
                    pass

        if rtc_temp_c is not None:
            try:
                values["rtc_temp"] = float(rtc_temp_c)
            except Exception:
                pass

        return values, confidence

    def _dbg_values_sample(self, values, max_items=5):
        if not isinstance(values, dict):
            return
        n = 0
        try:
            for k in values:
                self._dbg_print("telemetry: val", k, "=", values.get(k))
                n += 1
                if n >= int(max_items):
                    break
        except Exception:
            pass

    def _read_battery(self):
        if self._ina is None or not self._ina.is_present:
            return None
        try:
            bus_v  = self._ina.bus_voltage_v()
            batt_v = self._ina.battery_v()
            cur_ma = self._ina.current_ma()
            pwr_mw = self._ina.power_mw()
            out = {}
            # ina_bus_v carries the battery's rest-voltage estimate (I*R swing
            # removed, see INA219.battery_v) because hopeturtles.org reads it
            # as "battery voltage" and derives SoC from it. The raw VIN- bus
            # reading, which jumps ~0.1 V when USB is plugged in, rides along
            # as ina_raw_bus_v for diagnostics.
            if bus_v is not None:
                out["ina_raw_bus_v"] = round(float(bus_v), 3)
            if batt_v is not None:
                out["ina_bus_v"] = round(float(batt_v), 2)
                from src.drivers.ina219 import pct_from_volts
                pct = pct_from_volts(batt_v)
                if pct is not None:
                    out["ina_batt_pct"] = pct
            if cur_ma is not None:
                out["ina_current_ma"] = round(float(cur_ma), 1)
            if pwr_mw is not None:
                out["ina_power_mw"] = round(float(pwr_mw), 1)
            return out if out else None
        except Exception:
            return None

    def _read_baro(self):
        """BMP180 fields from the shared GY87: pressure (hPa), temp (°C) and a
        derived ISA altitude (m). None when no barometer is attached."""
        if self._imu is None or getattr(self._imu, "baro", None) is None:
            return None
        try:
            r = self._imu.baro_read()
            if r is None:
                return None
            t, p, alt = r
            out = {}
            if p is not None and float(p) > 0.0:
                out["bmp_pressure"] = round(float(p), 1)
                if alt is not None:
                    out["bmp_alt_m"] = round(float(alt), 1)
            if t is not None:
                out["bmp_temp"] = round(float(t), 1)
            return out if out else None
        except Exception:
            return None

    def _read_imu(self):
        """Attitude fields: imu_heading (deg, offset applied), imu_pitch and
        imu_roll (deg, accelerometer-derived). A 0.0 is a real reading and is
        sent — the server only drops a packet when EVERY value is zero."""
        out = {}
        if self._heading_getter is not None:
            try:
                h = self._heading_getter()
                if h is not None:
                    out["imu_heading"] = round(float(h), 1)
            except Exception:
                pass
        if self._imu is not None:
            try:
                pr = self._imu.pitch_roll()
                if pr is not None:
                    # "+ 0.0" folds -0.0 into 0.0 so a level board doesn't serialize as "-0.0"
                    out["imu_pitch"] = round(float(pr[0]), 1) + 0.0
                    out["imu_roll"] = round(float(pr[1]), 1) + 0.0
            except Exception:
                pass
        return out if out else None

    def _build_full_payload(self, cfg, rtc_dict, do_print=False):
        """Build a complete telemetry payload dict from all local sensors.
        Returns the payload dict, or None if any blocking condition is unmet.
        WiFi-independent — safe to call whether or not the network is available."""
        if self._sampling_in_progress():
            if do_print:
                self._dbg_print("telemetry: skip (sampling in progress)")
            return None

        got_reading = False
        try:
            r = self.air.read_quick(source="telemetry")
            if r is not None:
                self._last_reading = r
                got_reading = True
        except Exception as e:
            if do_print:
                self._dbg_print("telemetry: read_quick err", repr(e))

        if not got_reading and not (cfg and cfg.get("turtle_mode", False)):
            # airOS: no air reading means nothing worth posting.
            # turtle_mode: sail turtle may carry no air sensor — battery/GPS/
            # machine_state still need to reach the server, so proceed anyway.
            if do_print:
                self._dbg_print("telemetry: skip (sensor not ready)")
            return None

        _rtc_dict = rtc_dict
        rtc_temp_c = None
        if _rtc_dict is None and self.get_rtc:
            try:
                _rtc_dict = self.get_rtc()
            except Exception:
                _rtc_dict = None
        if isinstance(_rtc_dict, dict):
            rtc_temp_c = _rtc_dict.get("temp_c")

        values, confidence = self._build_payload_parts(
            self._last_reading,
            rtc_temp_c=rtc_temp_c
        )

        batt = self._read_battery()
        if batt:
            values.update(batt)

        baro = self._read_baro()
        if baro:
            values.update(baro)
        att = self._read_imu()
        if att:
            values.update(att)

        # GPS is read before the "no values" gate: a hand-taken registry stamp
        # on a turtle with no air sensor carries a position and nothing else,
        # and that is a complete record — dropping it for having no sensor
        # values loses exactly the reading the user asked for.
        gps_lat, gps_lon = self._read_gps_fix()

        if not values and not (self._manual_flag and gps_lat is not None):
            if do_print:
                self._dbg_print("telemetry: skip (no values)")
            return None

        recorded_at = self._now_unix_seconds()
        if recorded_at < 1000000000:
            if do_print:
                self._dbg_print("telemetry: skip (rtc not epoch) t=", recorded_at)
            return None

        if do_print:
            self._dbg_print(
                "telemetry: payload ready",
                "reading=", "ok" if got_reading else "none",
                "recorded_at=", recorded_at,
                "values_len=", (len(values) if isinstance(values, dict) else 0)
            )
            self._dbg_values_sample(values, max_items=6)
            if gps_lat is not None:
                self._dbg_print("telemetry: gps lat=", gps_lat, "lon=", gps_lon)

        # One-shot manual flag: consumed here so a later automatic send is not
        # mislabelled as a hand-taken stamp.
        _manual = bool(self._manual_flag)
        self._manual_flag = False

        payload = {
            # Telemetry JSON v2 (docs/wrangler/03_v2_plan.md): field names per
            # the turtle data dictionary, plus the journey event flags below.
            # Additive: a v1 server ignores the extra keys.
            "schema": 2,
            "recorded_at": recorded_at,
            "values": values,
            "flags": {"auto_log": not _manual, "manual_registry": _manual},
        }

        # Journey tag: while an operator journey is open (src/app/journey.py,
        # driven by the Journey screen), stamp its id into flags so the
        # hopeturtles.org my-turtle map can highlight this leg's GPS points.
        # The server stores `flags` verbatim in raw_data, so no ingest change
        # is needed for this to land.
        #
        # Journey events: the oldest start/end the server hasn't been told
        # about rides on this reading as flags.journey_event ("start"/"end")
        # + flags.journey (the record: departure, destination, ended_at,
        # arrival), so the server learns when a journey ended and where.
        # flags.journey_id stays "the journey open now" (it tags the point);
        # the event's own journey is flags.journey.id.
        _event_taken = None
        _jid = None
        try:
            from src.app import journey as _journey
            _jid = _journey.active_id()
            if _jid:
                payload["flags"]["journey_id"] = _jid
            _ev = _journey.pending_event()
            if _ev:
                _rec = _ev.get("journey") or {}
                payload["flags"]["journey_event"] = _ev.get("event")
                payload["flags"]["journey"] = _rec
                _event_taken = _journey.event_taken
        except Exception:
            pass

        # Machine state (BOOT/ACQUIRE/SAIL_NAV/ARRIVAL/SAFE) — top-level so
        # the server maps it to a real column. Turtle mode only: airOS never
        # runs the nav state machine, so it would always say BOOT, and the
        # data dictionary leaves out a value there is no data for.
        if cfg and cfg.get("turtle_mode", False):
            try:
                from src.nav.state_machine import get_state as _nav_get_state
                from src.nav.state_machine import resume_stage as _nav_resume
                payload["machine_state"] = _nav_get_state()
                # Journey resume stage (BATTERY / GPS / WIND) while the turtle
                # works its way back to SAIL_NAV; flags is stored verbatim.
                _stage = _nav_resume()
                if _stage:
                    payload["flags"]["resume_stage"] = _stage
            except Exception:
                pass

        if gps_lat is not None and gps_lon is not None:
            payload["lat"] = gps_lat
            payload["lon"] = gps_lon
            # The open journey's track file (GATT JOURNEY_TRACK reads it).
            if _jid:
                try:
                    _journey.record_point(_jid, recorded_at, gps_lat, gps_lon,
                                          payload.get("machine_state"))
                except Exception:
                    pass

        if confidence:
            payload["confidence"] = confidence

        # Built: from here the reading is either handed to the sender or
        # queued to flash, so the event it carries is reported.
        if _event_taken is not None:
            try:
                _event_taken()
            except Exception:
                pass

        return payload

    def tick(self, cfg, rtc_dict=None):
        if not cfg:
            return

        # telemetry_mode: "auto" posts on the interval; "manual" posts only when
        # request_now()/send_manual() has armed _send_now; "off" never posts.
        try:
            mode = str(cfg.get("telemetry_mode", "auto") or "auto").strip().lower()
        except Exception:
            mode = "auto"
        if mode not in ("off", "auto", "manual"):
            mode = "auto"

        if mode == "off":
            self._send_now = False
            self._manual_flag = False
            return

        now = time.ticks_ms()
        # A journey start/end the server hasn't heard about gets a reading
        # right away, in manual mode too (it's an operator action). If the
        # reading can't be built yet, retry no faster than every 10 s.
        event_due = False
        if time.ticks_diff(now, self._next_event_try_ms) >= 0:
            try:
                from src.app import journey as _journey
                event_due = _journey.pending_event() is not None
            except Exception:
                event_due = False
            if event_due:
                self._next_event_try_ms = time.ticks_add(now, 10_000)

        if mode != "auto" and not self._send_now and not event_due:
            return

        if self._send_now:
            self._send_now = False
        elif not event_due and time.ticks_diff(now, self._next_send_ms) < 0:
            return

        try:
            interval_s = int(cfg.get("telemetry_post_every_s", 120) or 120)
        except Exception:
            interval_s = 120
        # While a journey is open, journey_log_every_s (if set) logs the
        # track faster, so a short field test isn't one point per 2 minutes.
        try:
            from src.app import journey as _journey
            if _journey.active():
                j_s = int(cfg.get("journey_log_every_s", 0) or 0)
                if 0 < j_s < interval_s:
                    interval_s = j_s
        except Exception:
            pass
        if interval_s < 10:
            interval_s = 10

        self._next_send_ms = time.ticks_add(now, interval_s * 1000)

        self._dbg_count += 1
        do_print = (self._dbg_count % int(self._dbg_every_n)) == 0
        if do_print:
            self._dbg_print("telemetry: DUE interval_s=", interval_s)

        # --- Hand off to the background process (non-blocking) ---
        # Never touch WiFi here: is_connected() / reconnect() block on the
        # WiFi driver mutex while WPA2 auth runs on the bg thread. Build the
        # payload and hand it off; the bg thread owns reconnect, HTTP POST
        # and queue flush. There is no inline send path any more (code review
        # section 2): with no background process, or its thread down, the
        # reading goes to the flash queue, which the thread drains later.
        payload = self._build_full_payload(cfg, rtc_dict, do_print=do_print)
        if payload is None:
            return

        bp = self._background_process
        if bp is not None:
            # Refresh api_state from bg result so Online screen stays current.
            try:
                bp_result = bp.result()
                if bp_result.get("last_ms") is not None:
                    self.api_state["sending"] = bool(bp_result.get("sending", False))
                    if bp_result.get("ok") is not None:
                        self.api_state["ok"] = bool(bp_result["ok"])
                        self.api_state["msg"] = str(bp_result.get("msg", ""))
                        self.api_state["last_ms"] = bp_result["last_ms"]
                        if bp_result.get("last_ok_ms") is not None:
                            self.api_state["last_ok_ms"] = bp_result["last_ok_ms"]
                        self.write_last_sent(payload["recorded_at"], ok=bool(bp_result["ok"]))
            except Exception:
                pass

            if do_print:
                self._dbg_print("telemetry: handing off to background_process")
            if bp.put_payload(payload, cfg):
                return None

        # No background process attached yet, or its thread is not running.
        # Without this the payload would be dropped in silence while the UI
        # reports a successful stamp.
        self._dbg_print("telemetry: no background sender — queueing to flash")
        try:
            self._ensure_client(cfg).enqueue(payload)
            self.api_state["ok"] = False
            self.api_state["msg"] = "queued (bg down)"
            self.api_state["last_ms"] = time.ticks_ms()
        except Exception as _qe:
            self._dbg_print("telemetry: queue err", repr(_qe))
        return False
