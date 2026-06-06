# src/app/telemetry_scheduler.py
# AirBuddy Telemetry Scheduler (Pico / MicroPython)

import time

LAST_SENT_FILE = "telemetry_last_sent.json"
QUEUE_FILE = "telemetry_queue.json"

# Morse code table (letters used in "BLESS THE AIR")
_MORSE = {
    'A': '.-',
    'B': '-...',
    'E': '.',
    'H': '....',
    'I': '..',
    'L': '.-..',
    'R': '.-.',
    'S': '...',
    'T': '-',
}
_MORSE_UNIT_MS = 50   # 1 unit = 50 ms  →  dot=50 ms, dash=150 ms


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


def _gc_free_kb():
    try:
        import gc
        return gc.mem_free() // 1024
    except Exception:
        return -1


class TelemetryScheduler:
    def __init__(self, air_sensor, rtc_info_getter=None, wifi_manager=None, gps=None, battery_sensor=None):
        self.air = air_sensor
        self.get_rtc = rtc_info_getter
        self.wifi = wifi_manager
        self.gps = gps
        self._ina = battery_sensor

        self._client = None
        self._next_send_ms = time.ticks_add(time.ticks_ms(), 30000)
        self._last_reading = None

        self._dbg_every_n = 1
        self._dbg_count = 0

        # Button LED for Morse signalling — acquired lazily on first use
        self._led = None
        self._led_ready = False
        self._led_on_val = 1   # logic level that turns the LED on
        self._led_off_val = 0  # logic level that turns it off
        self._consecutive_net_fails = 0
        self._last_gps_lat = None
        self._last_gps_lon = None
        self._gps_reject_streak = 0

        # Public state for UI (Online screen reads this instead of making its own HTTP)
        self.api_state = {"ok": None, "sending": False, "msg": "", "last_ms": None}
        self._send_now = False

    def request_now(self):
        """Ask the scheduler to send at the next tick (called by Online screen)."""
        self._send_now = True
        self._next_send_ms = time.ticks_ms()
        self._dbg_print("[ONLINE] request_now: send queued")

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
        try:
            n = 0
            with open(QUEUE_FILE, "r") as f:
                for line in f:
                    if line.strip():
                        n += 1
            return n
        except Exception:
            return 0
        finally:
            _gc_collect()

    def _dbg_print(self, *parts):
        try:
            print(*parts)
        except Exception:
            pass

    def _get_led(self):
        if not self._led_ready:
            self._led_ready = True
            try:
                from machine import Pin
                # Prefer the dedicated button LED; fall back to the onboard user LED
                # (XIAO ESP32-S3 has no button LED but does have a user LED on GPIO21,
                # active-low, exposed via user_led_pin() / user_led_active_value()).
                from src.hal.board import btn_led_pin
                pin_num = btn_led_pin()
                if pin_num is not None:
                    self._led = Pin(int(pin_num), Pin.OUT)
                    self._led_on_val = 1
                    self._led_off_val = 0
                else:
                    from src.hal.board import user_led_pin, user_led_active_value
                    pin_num = user_led_pin()
                    if pin_num is not None:
                        self._led = Pin(int(pin_num), Pin.OUT)
                        self._led_on_val = int(user_led_active_value())
                        self._led_off_val = 1 - self._led_on_val
            except Exception:
                self._led = None
        return self._led

    def _morse_blink(self, phrase):
        """Blink the button LED in Morse code for the given phrase (blocking).
        Spaces between words produce a 7-unit silence."""
        led = self._get_led()
        if led is None:
            return
        u = _MORSE_UNIT_MS
        try:
            prev_was_space = True   # suppress leading gap
            for char in phrase.upper():
                if char == ' ':
                    time.sleep_ms(u * 7)   # word gap
                    prev_was_space = True
                    continue
                if not prev_was_space:
                    time.sleep_ms(u * 3)   # inter-letter gap
                prev_was_space = False
                pattern = _MORSE.get(char, '')
                first_sym = True
                for sym in pattern:
                    if not first_sym:
                        time.sleep_ms(u)   # intra-symbol gap
                    first_sym = False
                    led.value(self._led_on_val)
                    time.sleep_ms(u * 3 if sym == '-' else u)
                    led.value(self._led_off_val)
        except Exception:
            pass
        finally:
            try:
                led.value(self._led_off_val)
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

    @staticmethod
    def _parse_rmc(line):
        """Parse a $GPRMC sentence → (lat_deg, lon_deg) or (None, None)."""
        try:
            parts = line.split(",")
            if len(parts) < 7 or parts[2] != "A":
                return None, None
            lat_raw, lat_dir = parts[3], parts[4]
            lon_raw, lon_dir = parts[5], parts[6]
            if not lat_raw or not lon_raw:
                return None, None
            lat_d = int(float(lat_raw) // 100)
            lat = lat_d + (float(lat_raw) - lat_d * 100) / 60.0
            if lat_dir == "S":
                lat = -lat
            lon_d = int(float(lon_raw) // 100)
            lon = lon_d + (float(lon_raw) - lon_d * 100) / 60.0
            if lon_dir == "W":
                lon = -lon
            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
                return None, None
            if lat == 0.0 and lon == 0.0:
                return None, None
            return round(lat, 6), round(lon, 6)
        except Exception:
            return None, None

    _GPS_MAX_DELTA = 1.0        # degrees (~111 km); jump larger than this is suspect
    _GPS_RELOC_THRESH = 5       # clear reference after this many consecutive all-rejected ticks

    def _read_gps_fix(self):
        """Non-blocking: drain UART buffer and return (lat, lon) from the first active RMC, or (None, None)."""
        if self.gps is None:
            return None, None
        delta_rejects = 0
        try:
            for _ in range(30):
                line = self.gps.read_nmea()
                if line is None:
                    break
                if "RMC" in line:
                    lat, lon = self._parse_rmc(line)
                    if lat is None:
                        continue
                    if self._last_gps_lat is not None:
                        if (abs(lat - self._last_gps_lat) > self._GPS_MAX_DELTA or
                                abs(lon - self._last_gps_lon) > self._GPS_MAX_DELTA):
                            delta_rejects += 1
                            continue
                    self._last_gps_lat = lat
                    self._last_gps_lon = lon
                    self._gps_reject_streak = 0
                    return lat, lon
        except Exception:
            pass
        if delta_rejects > 0:
            self._gps_reject_streak += 1
            if self._gps_reject_streak >= self._GPS_RELOC_THRESH:
                self._last_gps_lat = None
                self._last_gps_lon = None
                self._gps_reject_streak = 0
        return None, None

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

    @staticmethod
    def _estimate_batt_pct(bus_v):
        try:
            return max(0, min(100, int((float(bus_v) - 3.30) / (4.20 - 3.30) * 100)))
        except Exception:
            return None

    def _read_battery(self):
        if self._ina is None or not self._ina.is_present:
            return None
        try:
            bus_v  = self._ina.bus_voltage_v()
            cur_ma = self._ina.current_ma()
            pwr_mw = self._ina.power_mw()
            out = {}
            if bus_v is not None:
                out["ina_bus_v"] = round(float(bus_v), 2)
                pct = self._estimate_batt_pct(bus_v)
                if pct is not None:
                    out["ina_batt_pct"] = pct
            if cur_ma is not None:
                out["ina_current_ma"] = round(float(cur_ma), 1)
            if pwr_mw is not None:
                out["ina_power_mw"] = round(float(pwr_mw), 1)
            return out if out else None
        except Exception:
            return None

    def tick(self, cfg, rtc_dict=None):
        if not cfg or not cfg.get("telemetry_enabled", True):
            return

        now = time.ticks_ms()
        if self._send_now:
            self._send_now = False
        elif time.ticks_diff(now, self._next_send_ms) < 0:
            return

        try:
            interval_s = int(cfg.get("telemetry_post_every_s", 120) or 120)
        except Exception:
            interval_s = 120
        if interval_s < 10:
            interval_s = 10

        self._next_send_ms = time.ticks_add(now, interval_s * 1000)

        self._dbg_count += 1
        do_print = (self._dbg_count % int(self._dbg_every_n)) == 0
        if do_print:
            self._dbg_print("telemetry: DUE interval_s=", interval_s)

        if self.wifi:
            try:
                if not self.wifi.is_connected():
                    ssid = str(cfg.get("wifi_ssid") or "")
                    pw = str(cfg.get("wifi_password") or "")
                    if cfg.get("wifi_enabled", False) and ssid:
                        if do_print:
                            self._dbg_print("telemetry: wifi down, reconnecting")
                        try:
                            self.wifi.reconnect(ssid, pw)
                        except Exception as _re:
                            if do_print:
                                self._dbg_print("telemetry: reconnect err", repr(_re))
                    if not self.wifi.is_connected():
                        if do_print:
                            self._dbg_print("telemetry: skip (wifi not connected)")
                        return
            except Exception:
                if do_print:
                    self._dbg_print("telemetry: skip (wifi check error)")
                return

        if self._sampling_in_progress():
            if do_print:
                self._dbg_print("telemetry: skip (sampling in progress)")
            return

        got_reading = False
        try:
            r = self.air.read_quick(source="telemetry")
            if r is not None:
                self._last_reading = r
                got_reading = True
        except Exception as e:
            if do_print:
                self._dbg_print("telemetry: read_quick err", repr(e))

        if not got_reading:
            if do_print:
                self._dbg_print("telemetry: skip (sensor not ready)")
            return

        rtc_temp_c = None
        if rtc_dict is None and self.get_rtc:
            try:
                rtc_dict = self.get_rtc()
            except Exception:
                rtc_dict = None
        if isinstance(rtc_dict, dict):
            rtc_temp_c = rtc_dict.get("temp_c")

        values, confidence = self._build_payload_parts(
            self._last_reading,
            rtc_temp_c=rtc_temp_c
        )

        batt = self._read_battery()
        if batt:
            values.update(batt)

        recorded_at = self._now_unix_seconds()
        if recorded_at < 1000000000:
            if do_print:
                self._dbg_print("telemetry: skip (rtc not epoch) t=", recorded_at)
            return

        if do_print:
            self._dbg_print(
                "telemetry: sending",
                "reading=", "ok" if got_reading else "none",
                "recorded_at=", recorded_at,
                "values_len=", (len(values) if isinstance(values, dict) else 0)
            )
            self._dbg_values_sample(values, max_items=6)

        gps_lat, gps_lon = self._read_gps_fix()
        if do_print and gps_lat is not None:
            self._dbg_print("telemetry: gps lat=", gps_lat, "lon=", gps_lon)

        payload = {
            "recorded_at": recorded_at,
            "values": values,
            "flags": {"auto_log": True},
        }

        if gps_lat is not None and gps_lon is not None:
            payload["lat"] = gps_lat
            payload["lon"] = gps_lon

        if confidence:
            payload["confidence"] = confidence

        client = self._ensure_client(cfg)

        # Re-assert PM mode before TX — ESP32 can silently revert to PM_POWERSAVE
        # after idle periods, which causes OSError(116) on the first TCP connect.
        if self.wifi is not None:
            try:
                self.wifi._apply_pm_performance(quiet=True)
            except Exception:
                pass

        if cfg.get("morse_bless", False):
            self._morse_blink("BLESSTHEAIR")

        _gc_collect()
        if do_print:
            self._dbg_print("telemetry: heap", _gc_free_kb(), "KB free  sending...")

        self.api_state["sending"] = True
        ok = False
        msg = ""
        try:
            ok, msg = client.send(payload)
        except Exception as e:
            ok = False
            msg = "EXC " + repr(e)
        finally:
            self.api_state["sending"] = False
            self.api_state["ok"] = bool(ok)
            self.api_state["msg"] = str(msg)
            self.api_state["last_ms"] = time.ticks_ms()
            _gc_collect()

        if do_print:
            self._dbg_print("telemetry: heap", _gc_free_kb(), "KB free  done ok=%s" % ok)

        try:
            from src.ui.connection_header import set_api_ok
            set_api_ok(bool(ok))
        except Exception:
            pass

        self.write_last_sent(recorded_at, ok=bool(ok))

        if do_print:
            self._dbg_print("telemetry:", ok, msg)

        if ok:
            self._consecutive_net_fails = 0
        else:
            _is_net_err = ("OSError" in str(msg) or "TIMEOUT" in str(msg))
            if _is_net_err and self.wifi is not None and hasattr(self.wifi, "reconnect"):
                self._consecutive_net_fails += 1
                ssid = str(cfg.get("wifi_ssid") or "")
                pw = str(cfg.get("wifi_password") or "")
                # Only force-reconnect if WiFi actually dropped, or after 3+
                # consecutive failures. Don't disconnect a live radio just because
                # one TCP request failed (that makes things worse, not better).
                _wifi_down = not self.wifi.is_connected()
                if ssid and (_wifi_down or self._consecutive_net_fails >= 3):
                    if do_print:
                        self._dbg_print(
                            "telemetry: net fail #", self._consecutive_net_fails,
                            "- reconnecting wifi"
                        )
                    try:
                        self.wifi.reconnect(ssid, pw)
                    except Exception as _e:
                        if do_print:
                            self._dbg_print("telemetry: reconnect err", repr(_e))
                elif do_print:
                    self._dbg_print(
                        "telemetry: net fail #", self._consecutive_net_fails,
                        "- tcp retry (wifi up)"
                    )
            else:
                self._consecutive_net_fails = 0

        return bool(ok)