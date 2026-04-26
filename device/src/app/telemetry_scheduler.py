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
        j = _json()
        try:
            with open(QUEUE_FILE, "r") as f:
                q = j.load(f)
            return len(q) if isinstance(q, list) else 0
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
                from src.hal.board import btn_led_pin
                self._led = Pin(int(btn_led_pin()), Pin.OUT)
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
                    led.value(1)
                    time.sleep_ms(u * 3 if sym == '-' else u)
                    led.value(0)
        except Exception:
            pass
        finally:
            try:
                led.value(0)
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

    def _now_unix_seconds(self):
        # RP2040 (Pico): time.mktime() epoch is 2000-01-01 — must add 30 years to reach Unix epoch.
        # ESP32 / ESP32-S3: time.mktime() epoch is already 1970-01-01 — no offset needed.
        try:
            from src.hal.platform import platform_tag
            _MP_EPOCH_OFFSET = 946_684_800 if platform_tag() == "pico" else 0
        except Exception:
            _MP_EPOCH_OFFSET = 0
        try:
            from machine import RTC
            y, mo, d, wd, hh, mm, ss, sub = RTC().datetime()
            if y < 2020:          # RTC not synced yet — return 0 so caller skips
                return 0
            mp_s = int(time.mktime((y, mo, d, hh, mm, ss, wd, 0)))
            return mp_s + _MP_EPOCH_OFFSET
        except Exception:
            try:
                return int(time.time()) + _MP_EPOCH_OFFSET
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
            return round(lat, 6), round(lon, 6)
        except Exception:
            return None, None

    def _read_gps_fix(self):
        """Non-blocking: drain UART buffer and return (lat, lon) from the first active RMC, or (None, None)."""
        if self.gps is None:
            return None, None
        try:
            for _ in range(30):
                line = self.gps.read_nmea()
                if line is None:
                    break
                if "RMC" in line:
                    lat, lon = self._parse_rmc(line)
                    if lat is not None:
                        return lat, lon
        except Exception:
            pass
        return None, None

    def _build_payload_parts(self, reading, rtc_temp_c=None):
        values = {}
        confidence = None

        if reading is not None and (not isinstance(reading, dict)):
            try:
                # ENS160 fields — only emit when ENS160 was active (tvoc > 0 confirms it)
                tvoc = getattr(reading, "tvoc_ppb", None)
                if tvoc is not None and int(tvoc) > 0:
                    values["ens_tvoc"] = int(tvoc)
                    eco2 = getattr(reading, "eco2_ppm", None)
                    if eco2 is not None and int(eco2) != 0:
                        values["ens_eco2"] = int(eco2)
                    aqi = getattr(reading, "aqi", None)
                    if aqi is not None:
                        try:
                            values["ens_aqi"] = int(aqi)
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
        if time.ticks_diff(now, self._next_send_ms) < 0:
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

        # Signal "BLESSTHEAIR" in Morse on the button LED before transmitting
        self._morse_blink("BLESSTHEAIR")

        ok = False
        msg = ""
        try:
            ok, msg = client.send(payload)
        except Exception as e:
            ok = False
            msg = "EXC " + repr(e)
        finally:
            _gc_collect()

        try:
            from src.ui.connection_header import set_api_ok
            set_api_ok(bool(ok))
        except Exception:
            pass

        self.write_last_sent(recorded_at, ok=bool(ok))

        if do_print:
            self._dbg_print("telemetry:", ok, msg)

        return bool(ok)