# src/sensors/air.py  (MicroPython / Pico W) — Pico-safe
#
# Patched version:
# - Adds safer AHT21 init + busy/status handling
# - Keeps OPTIONAL AHT10 support
# - Adds SCD4X / SCD41 support on same primary I2C bus
# - Stores sensor readings side-by-side on AirReading:
#     r.aht10_temp_c, r.aht10_humidity
#     r.aht21_temp_c, r.aht21_humidity
#     r.scd41_co2_ppm, r.scd41_temp_c, r.scd41_humidity
# - Chooses a "primary" temp/rh for ENS160 compensation + UI:
#     Prefer AHT21, else AHT10, else last good reading, else 0/0 fallback
#
# LOGIC PATCHES:
# - Do NOT reject ENS160 reading just because tvoc_ppb == 0
# - Do NOT aggressively use 0/0 when AHT read fails
# - Use last good temp/rh if current env sensor fails
# - Only set ENS160 environment when temp/rh are actually available
# - Add AHT21 init + busy polling + sanity checks
# - Fix CSV log placeholder count bug
# - Add SCD4X periodic measurement support for true CO2 + diagnostic temp/RH

import time
from machine import Pin, I2C
from src.drivers.scd4x import SCD4X

# ---- CO2 confidence helper (your implemented file) ----
try:
    from src.sensors.co2_confidence import calculate_co2_confidence
except Exception:
    calculate_co2_confidence = None


class AirReading:
    """
    Lightweight reading container (Pico-safe).

    ready:
      - True means ENS160 data passed basic validity checks.
      - False means sensor wasn't ready / values unreliable.
    confidence:
      - 1..100 confidence percentage if available
      - None if not calculated / unavailable (UI should show XX%)

    Side-by-side comparison (optional):
      - aht10_temp_c / aht10_humidity
      - aht21_temp_c / aht21_humidity
      - scd41_co2_ppm / scd41_temp_c / scd41_humidity
    """

    def __init__(
            self,
            timestamp,
            temp_c,
            humidity,
            eco2_ppm,
            tvoc_ppb,
            aqi,
            rating,
            source,
            ready=True,
            confidence=None,
            reason="",
            aht10_temp_c=None,
            aht10_humidity=None,
            aht21_temp_c=None,
            aht21_humidity=None,
            scd41_co2_ppm=None,
            scd41_temp_c=None,
            scd41_humidity=None,
    ):
        self.timestamp = int(timestamp)

        # Primary values used by UI + telemetry
        self.temp_c = float(temp_c)
        self.humidity = float(humidity)

        # ENS160-derived values
        self.eco2_ppm = int(eco2_ppm)
        self.tvoc_ppb = int(tvoc_ppb)
        self.aqi = int(aqi)

        self.rating = str(rating)
        self.source = str(source)
        self.ready = bool(ready)
        self.confidence = None if confidence is None else int(confidence)
        self.reason = str(reason)

        # Optional comparison fields
        self.aht10_temp_c = aht10_temp_c
        self.aht10_humidity = aht10_humidity
        self.aht21_temp_c = aht21_temp_c
        self.aht21_humidity = aht21_humidity

        # Optional SCD41/SCD4X fields
        self.scd41_co2_ppm = scd41_co2_ppm
        self.scd41_temp_c = scd41_temp_c
        self.scd41_humidity = scd41_humidity


# ----------------------------
# Minimal but safer AHT21 driver
# ----------------------------
class AHT21:
    _CMD_INIT = b"\xE1\x08\x00"   # AHT10/AHT20 init (0xE1); AHT21 uses 0xBE — using AHT10 command for hardware compatibility
    _CMD_TRIGGER = b"\xAC\x33\x00"
    _CMD_SOFTRESET = b"\xBA"

    def __init__(self, i2c, addr=0x38):
        self.i2c = i2c
        self.addr = addr
        self._inited = False
        self._init_sensor()

    def _read_status(self):
        try:
            return self.i2c.readfrom(self.addr, 1)[0]
        except Exception:
            return None

    def _wait_ready(self, timeout_ms=200):
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < int(timeout_ms):
            status = self._read_status()
            if status is not None:
                # bit7 busy = 1 while measuring/busy
                if (status & 0x80) == 0:
                    return True
            time.sleep_ms(10)
        return False

    def _init_sensor(self):
        # Soft reset (best-effort — no sensor present means both calls NACK)
        try:
            self.i2c.writeto(self.addr, self._CMD_SOFTRESET)
            time.sleep_ms(25)
        except Exception:
            pass

        # Init command — if this NACKs the device is not on the bus; raise so
        # the caller (AirSensor._ensure_hw) can set self._aht = None.
        self.i2c.writeto(self.addr, self._CMD_INIT)
        time.sleep_ms(10)

        self._wait_ready(200)
        self._inited = True

    def read(self):
        if not self._inited:
            self._init_sensor()

        # Trigger measurement
        self.i2c.writeto(self.addr, self._CMD_TRIGGER)
        time.sleep_ms(10)  # allow AHT21 to assert busy bit before polling

        # Wait until not busy
        if not self._wait_ready(200):
            raise OSError("AHT21 busy timeout")

        data = self.i2c.readfrom(self.addr, 6)
        if data is None or len(data) != 6:
            raise OSError("AHT21 short read")

        # If still busy, reject
        if data[0] & 0x80:
            raise OSError("AHT21 still busy")

        raw_h = ((data[1] << 12) | (data[2] << 4) | (data[3] >> 4)) & 0xFFFFF
        raw_t = (((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]) & 0xFFFFF

        humidity = raw_h * 100.0 / 1048576.0
        temp_c = raw_t * 200.0 / 1048576.0 - 50.0

        # Basic sanity checks
        if not (-40.0 <= temp_c <= 85.0):
            raise ValueError("AHT21 temp out of range")
        if not (0.0 <= humidity <= 100.0):
            raise ValueError("AHT21 humidity out of range")

        return temp_c, humidity


# ----------------------------
# Minimal ENS160 driver
# ----------------------------
class ENS160:
    _REG_PART_ID = 0x00
    _REG_OPMODE = 0x10
    _REG_TEMP_IN = 0x13
    _REG_RH_IN = 0x15
    _REG_DATA_STATUS = 0x20
    _REG_DATA_AQI = 0x21
    _REG_DATA_TVOC = 0x22
    _REG_DATA_ECO2 = 0x24

    _OPMODE_IDLE = 0x01
    _OPMODE_STD = 0x02

    def __init__(self, i2c, addr=0x53):
        self.i2c = i2c
        self.addr = addr
        self._init()

    def _read(self, reg, n):
        self.i2c.writeto(self.addr, bytes([reg]))
        return self.i2c.readfrom(self.addr, n)

    def _write8(self, reg, val):
        self.i2c.writeto(self.addr, bytes([reg, val & 0xFF]))

    def _write16(self, reg, val):
        self.i2c.writeto(self.addr, bytes([reg, val & 0xFF, (val >> 8) & 0xFF]))

    def _init(self):
        _ = self._read(self._REG_PART_ID, 2)
        self._write8(self._REG_OPMODE, self._OPMODE_STD)
        time.sleep_ms(50)

    def data_ready(self):
        """Return True if ENS160 has new measurement data (NEWDAT bit in DATA_STATUS)."""
        try:
            status = self._read(self._REG_DATA_STATUS, 1)[0]
            return bool(status & 0x02)  # bit 1 = NEWDAT
        except Exception:
            return False

    def reset_to_std(self):
        """Cycle through idle → standard mode to recover from a stuck/frozen state."""
        try:
            self._write8(self._REG_OPMODE, self._OPMODE_IDLE)
            time.sleep_ms(20)
            self._write8(self._REG_OPMODE, self._OPMODE_STD)
            time.sleep_ms(50)
        except Exception:
            pass

    def set_environment(self, temp_c, rh):
        temp_k = float(temp_c) + 273.15
        tval = int(temp_k * 64)
        hval = int(float(rh) * 512)
        self._write16(self._REG_TEMP_IN, tval)
        self._write16(self._REG_RH_IN, hval)

    def read_air_raw(self):
        aqi = self._read(self._REG_DATA_AQI, 1)[0]
        tvoc = self._read(self._REG_DATA_TVOC, 2)
        eco2 = self._read(self._REG_DATA_ECO2, 2)

        tvoc_ppb = tvoc[0] | (tvoc[1] << 8)
        eco2_ppm = eco2[0] | (eco2[1] << 8)
        return aqi, tvoc_ppb, eco2_ppm


class AirSensor:
    """
    AirSensor:
      - ENS160 + AHT21 (usual ENS stack)
      - OPTIONAL AHT10 (external module)
      - OPTIONAL SCD4X/SCD41 (true CO2 + diagnostic temp/RH)
      - begin_sampling()/finish_sampling() warmup timer
      - ENS160 retry loop
      - confidence score attached to AirReading.confidence

    ESP32/Pico portability:
      - If i2c is not provided, pins are taken from src.hal.board.i2c_pins()
      - Hardware init is lazy (done on first actual read)

    Optional second bus for AHT10:
      - If you want to compare AHT10 vs AHT21 safely (no 0x38 conflict),
        wire AHT10 to a different I2C bus and pass aht10_i2c_* params.
    """

    def __init__(
            self,
            i2c=None,
            i2c_id=None,
            pin_sda=None,
            pin_scl=None,
            freq=None,
            aht21_addr=0x38,
            ens160_addr=0x53,
            scd41_addr=0x62,
            # Optional: second I2C bus for AHT10 (recommended if also using AHT21 on 0x38)
            aht10_i2c=None,
            aht10_i2c_id=None,
            aht10_pin_sda=None,
            aht10_pin_scl=None,
            aht10_freq=None,
            log_path="air_records.csv",
            auto_init=False,
    ):
        self.log_path = log_path

        # Primary bus config (ENS160 + AHT21 + SCD41 typically)
        self._i2c = i2c
        self._i2c_id = i2c_id
        self._pin_sda = pin_sda
        self._pin_scl = pin_scl
        self._freq = freq

        # Optional AHT10 bus config
        self._aht10_i2c = aht10_i2c
        self._aht10_i2c_id = aht10_i2c_id
        self._aht10_pin_sda = aht10_pin_sda
        self._aht10_pin_scl = aht10_pin_scl
        self._aht10_freq = aht10_freq

        self._aht_addr = aht21_addr
        self._ens_addr = ens160_addr
        self._scd41_addr = scd41_addr

        self._aht = None        # AHT21
        self._ens = None        # ENS160
        self._aht10 = None      # AHT10 (optional)
        self._scd41 = None      # SCD4X/SCD41

        self._warmup_until = None
        self._warmup_source = None

        self._last = None
        self._last_eco2_ppm = None
        self._last_aqi = None
        self._last_scd41_co2_ppm = None

        # SCD41 staleness / freeze detection
        self._scd41_stale_count = 0
        self._scd41_prev_co2 = None
        self._scd41_prev_temp = None

        self._log_inited = False

        if auto_init:
            self._ensure_hw()

    # ----------------------------
    # Hardware init (lazy + HAL)
    # ----------------------------
    def _ensure_hw(self):
        # Primary I2C bus
        if self._i2c is None:
            # Prefer HAL-provided pins unless explicitly provided
            if self._pin_sda is None or self._pin_scl is None or self._i2c_id is None or self._freq is None:
                try:
                    from src.hal.board import i2c_pins
                    bus_id, scl, sda, hz = i2c_pins()
                    if self._pin_sda is None:
                        self._pin_sda = sda
                    if self._pin_scl is None:
                        self._pin_scl = scl
                    if self._i2c_id is None:
                        self._i2c_id = bus_id
                    if self._freq is None:
                        self._freq = hz
                except Exception:
                    if self._i2c_id is None:
                        self._i2c_id = 0
                    if self._pin_sda is None:
                        self._pin_sda = 0
                    if self._pin_scl is None:
                        self._pin_scl = 1
                    if self._freq is None:
                        self._freq = 100_000

            self._i2c = I2C(
                int(self._i2c_id),
                sda=Pin(int(self._pin_sda)),
                scl=Pin(int(self._pin_scl)),
                freq=int(self._freq),
            )

        # Instantiate primary drivers
        if self._aht is None:
            try:
                self._aht = AHT21(self._i2c, addr=self._aht_addr)
            except Exception:
                self._aht = None

        if self._ens is None:
            try:
                self._ens = ENS160(self._i2c, addr=self._ens_addr)
            except Exception:
                self._ens = None

        if self._scd41 is None:
            try:
                self._scd41 = SCD4X(self._i2c, addr=self._scd41_addr)
                try:
                    self._scd41.serial_number = self._scd41.get_serial_number()
                except Exception:
                    pass
                self._scd41.ensure_started()
            except Exception:
                self._scd41 = None

        # AHT10 optional bus: if not provided, we will try to use the primary bus
        if self._aht10_i2c is None and (
                self._aht10_i2c_id is not None
                or self._aht10_pin_sda is not None
                or self._aht10_pin_scl is not None
        ):
            # Build explicit AHT10 bus
            try:
                bus_id = 1 if self._aht10_i2c_id is None else int(self._aht10_i2c_id)
                sda = 2 if self._aht10_pin_sda is None else int(self._aht10_pin_sda)
                scl = 3 if self._aht10_pin_scl is None else int(self._aht10_pin_scl)
                hz = 100_000 if self._aht10_freq is None else int(self._aht10_freq)
                self._aht10_i2c = I2C(bus_id, sda=Pin(sda), scl=Pin(scl), freq=hz)
            except Exception:
                self._aht10_i2c = None

        # Only attempt AHT10 if a dedicated bus or explicit pin config was provided.
        _has_aht10_cfg = (
                self._aht10_i2c is not None
                or self._aht10_i2c_id is not None
                or self._aht10_pin_sda is not None
                or self._aht10_pin_scl is not None
        )
        if self._aht10 is None and _has_aht10_cfg:
            try:
                from src.drivers.aht10 import AHT10
                bus = self._aht10_i2c if self._aht10_i2c is not None else self._i2c
                self._aht10 = AHT10(bus)
            except Exception:
                self._aht10 = None

        # Lazy init log header
        if not self._log_inited:
            self._ensure_log_header()
            self._log_inited = True

    # ----------------------------
    # Rating
    # ----------------------------
    @staticmethod
    def _rating_from_aqi(aqi):
        if aqi <= 1:
            return "Very good"
        if aqi == 2:
            return "Good"
        if aqi == 3:
            return "Ok"
        return "Poor"

    # ----------------------------
    # Timestamp
    # ----------------------------
    @staticmethod
    def _now_timestamp():
        try:
            return int(time.time())
        except Exception:
            return 0

    # ----------------------------
    # Validity heuristic
    # ----------------------------
    @staticmethod
    def _ens_values_look_ready(aqi, tvoc_ppb, eco2_ppm, temp_c=None):
        # Do NOT reject just because TVOC is 0.
        # TVOC can legitimately be 0 in clean air / startup conditions.
        if eco2_ppm <= 0:
            return False
        if aqi is None or int(aqi) <= 0:
            return False
        if temp_c is not None and temp_c == 0:
            return False
        return True

    def _read_ens160_with_retry(self, temp_c, rh, timeout_ms=4000, step_ms=250):
        if self._ens is None:
            return 0, 0, 0, False, "ens160 unavailable"

        start = time.ticks_ms()
        last = None
        stale_count = 0

        while time.ticks_diff(time.ticks_ms(), start) < int(timeout_ms):
            # Always compensate — use real values when available, else safe defaults
            comp_temp = temp_c if temp_c is not None else 25.0
            comp_rh   = rh     if rh is not None     else 50.0
            try:
                self._ens.set_environment(comp_temp, comp_rh)
            except Exception:
                pass

            # Check DATA_STATUS register for fresh measurement (NEWDAT bit)
            new_data = False
            try:
                new_data = self._ens.data_ready()
            except Exception:
                pass

            if not new_data:
                stale_count += 1
                # After ~1 s of no new data, cycle opmode to recover a frozen sensor
                stale_thresh = max(4, int(1000 / max(1, int(step_ms))))
                if stale_count >= stale_thresh:
                    try:
                        self._ens.reset_to_std()
                    except Exception:
                        pass
                    stale_count = 0
                time.sleep_ms(int(step_ms))
                continue

            stale_count = 0
            try:
                aqi, tvoc_ppb, eco2_ppm = self._ens.read_air_raw()
                last = (aqi, tvoc_ppb, eco2_ppm)
                if self._ens_values_look_ready(aqi, tvoc_ppb, eco2_ppm, temp_c=temp_c):
                    return aqi, tvoc_ppb, eco2_ppm, True, ""
            except Exception:
                last = None

            time.sleep_ms(int(step_ms))

        if last is None:
            return 0, 0, 0, False, "ens160 read failed"
        return last[0], last[1], last[2], False, "ens160 not ready"

    def _read_scd41(self):
        """
        Best-effort SCD41 read.
        Returns:
          (co2_ppm, temp_c, rh, ready, reason)
        """
        if self._scd41 is None:
            return None, None, None, False, "scd41 unavailable"

        try:
            self._scd41.ensure_started()
        except Exception:
            pass

        try:
            if not self._scd41.data_ready():
                return None, None, None, False, "scd41 data not ready"
        except Exception as e:
            return None, None, None, False, "scd41 ready check failed: {}".format(e)

        try:
            co2, temp_c, rh = self._scd41.read_measurement()

            if co2 is None or int(co2) <= 0:
                return None, None, None, False, "scd41 co2 invalid"
            if not (-40.0 <= float(temp_c) <= 85.0):
                return None, None, None, False, "scd41 temp out of range"
            if not (0.0 <= float(rh) <= 100.0):
                return None, None, None, False, "scd41 humidity out of range"

            co2_int = int(co2)
            temp_f = float(temp_c)
            rh_f = float(rh)

            # Freeze detection: temp < 5 °C AND rh < 1 % is physically impossible
            # indoors and indicates the sensor is returning a frozen/zeroed frame.
            # Restart immediately and discard the reading.
            if temp_f < 5.0 and rh_f < 1.0:
                try:
                    self._scd41.stop_periodic_measurement()
                    self._scd41.start_periodic_measurement()
                except Exception:
                    pass
                self._scd41_stale_count = 0
                self._scd41_prev_co2 = None
                self._scd41_prev_temp = None
                return None, None, None, False, "scd41 restarted (frozen)"

            # Staleness detection: same CO2 and temp returned 3+ times in a row
            # means the sensor's data-ready flag is not clearing between reads.
            same = (
                self._scd41_prev_co2 is not None
                and co2_int == self._scd41_prev_co2
                and abs(temp_f - self._scd41_prev_temp) < 0.1
            )
            if same:
                self._scd41_stale_count += 1
            else:
                self._scd41_stale_count = 0
            self._scd41_prev_co2 = co2_int
            self._scd41_prev_temp = temp_f

            if self._scd41_stale_count >= 3:
                try:
                    self._scd41.stop_periodic_measurement()
                    self._scd41.start_periodic_measurement()
                except Exception:
                    pass
                self._scd41_stale_count = 0
                self._scd41_prev_co2 = None
                self._scd41_prev_temp = None
                return None, None, None, False, "scd41 restarted (stale)"

            return co2_int, temp_f, rh_f, True, ""
        except Exception as e:
            return None, None, None, False, "scd41 read failed: {}".format(e)

    # ----------------------------
    # Confidence inputs
    # ----------------------------
    @staticmethod
    def _temp_ok(temp_c):
        return temp_c is not None and (-10.0 <= float(temp_c) <= 60.0)

    @staticmethod
    def _rh_ok(rh):
        return rh is not None and (0.0 <= float(rh) <= 100.0)

    @staticmethod
    def _env_values_reasonable(temp_c, rh):
        if temp_c is None or rh is None:
            return False
        if not (-40.0 <= float(temp_c) <= 85.0):
            return False
        if not (0.0 <= float(rh) <= 100.0):
            return False
        return True

    def _select_env_values(self, aht21_temp, aht21_rh, aht10_temp, aht10_rh,
                           scd41_temp=None, scd41_rh=None):
        # Prefer AHT21
        if self._env_values_reasonable(aht21_temp, aht21_rh):
            return float(aht21_temp), float(aht21_rh), "aht21"

        # Then AHT10
        if self._env_values_reasonable(aht10_temp, aht10_rh):
            return float(aht10_temp), float(aht10_rh), "aht10"

        # Then last known good
        if self._last is not None and self._env_values_reasonable(self._last.temp_c, self._last.humidity):
            return float(self._last.temp_c), float(self._last.humidity), "last"

        # SCD41 provides its own temp/rh — use it before falling back to 0/0
        if self._env_values_reasonable(scd41_temp, scd41_rh):
            return float(scd41_temp), float(scd41_rh), "scd41"

        # Absolute fallback only if we have nothing else
        return 0.0, 0.0, "none"

    # ----------------------------
    # Core read
    # ----------------------------
    def _read_once(self, source, warmup_done):
        self._ensure_hw()

        # Read AHT21 (best effort)
        aht21_temp, aht21_rh = None, None
        if self._aht is not None:
            try:
                aht21_temp, aht21_rh = self._aht.read()
            except Exception:
                pass

        # Read AHT10 (best effort)
        aht10_temp, aht10_rh = None, None
        if self._aht10 is not None:
            try:
                aht10_temp, aht10_rh = self._aht10.read()
            except Exception:
                pass

        # Read SCD41 (best effort)
        scd41_co2_ppm, scd41_temp_c, scd41_rh = None, None, None
        scd41_ready, scd41_reason = False, "scd41 unavailable"
        try:
            scd41_co2_ppm, scd41_temp_c, scd41_rh, scd41_ready, scd41_reason = self._read_scd41()
        except Exception as e:
            scd41_ready, scd41_reason = False, "scd41 exception: {}".format(e)

        # Choose primary env for ENS160 + UI
        temp_c, rh, env_src = self._select_env_values(
            aht21_temp, aht21_rh, aht10_temp, aht10_rh,
            scd41_temp=scd41_temp_c, scd41_rh=scd41_rh,
        )

        # If env source is "none", do not feed 0/0 into ENS compensation
        ens_temp = temp_c if env_src != "none" else None
        ens_rh = rh if env_src != "none" else None

        aqi, tvoc_ppb, eco2_ppm, ready, reason = self._read_ens160_with_retry(
            ens_temp, ens_rh, timeout_ms=4500, step_ms=250
        )

        # If ENS160 is absent but SCD41 has valid CO2, promote it to the primary reading
        if eco2_ppm == 0 and scd41_co2_ppm is not None and scd41_co2_ppm > 0:
            eco2_ppm = int(scd41_co2_ppm)
            ready = True
            reason = ""
            source = "scd41"

        rating = self._rating_from_aqi(aqi) if ready else "Not ready"

        conf = None
        if calculate_co2_confidence is not None:
            try:
                conf = int(calculate_co2_confidence(
                    ens_valid=bool(ready),
                    warmup_done=bool(warmup_done),
                    temp_ok=bool(self._temp_ok(temp_c if env_src != "none" else None)),
                    rh_ok=bool(self._rh_ok(rh if env_src != "none" else None)),
                    eco2_ppm=int(eco2_ppm),
                    last_eco2_ppm=self._last_eco2_ppm,
                    aqi=int(aqi) if aqi is not None else None,
                    last_aqi=self._last_aqi,
                    source=str(source),
                ))
            except Exception:
                conf = None

        source_tag = "{}".format(source)
        if env_src != "none":
            source_tag = "{}+{}".format(source_tag, env_src)

        combined_reason = reason
        if not scd41_ready and scd41_reason:
            if combined_reason:
                combined_reason = "{} | {}".format(combined_reason, scd41_reason)
            else:
                combined_reason = scd41_reason

        r = AirReading(
            timestamp=self._now_timestamp(),
            temp_c=temp_c,
            humidity=rh,
            eco2_ppm=eco2_ppm,
            tvoc_ppb=tvoc_ppb,
            aqi=aqi,
            rating=rating,
            source=source_tag,
            ready=ready,
            confidence=conf,
            reason=combined_reason,
            aht10_temp_c=aht10_temp,
            aht10_humidity=aht10_rh,
            aht21_temp_c=aht21_temp,
            aht21_humidity=aht21_rh,
            scd41_co2_ppm=scd41_co2_ppm,
            scd41_temp_c=scd41_temp_c,
            scd41_humidity=scd41_rh,
        )

        if ready:
            self._last = r
            self._last_eco2_ppm = int(eco2_ppm)
            self._last_aqi = int(aqi)

        if scd41_ready and scd41_co2_ppm is not None:
            self._last_scd41_co2_ppm = int(scd41_co2_ppm)

        return r

    # ----------------------------
    # Warmup gating
    # ----------------------------
    def begin_sampling(self, warmup_seconds, source="button"):
        self._warmup_until = time.ticks_add(
            time.ticks_ms(), int(max(0, warmup_seconds) * 1000)
        )
        self._warmup_source = source

    def is_ready(self):
        if self._warmup_until is None:
            return True
        return time.ticks_diff(time.ticks_ms(), self._warmup_until) >= 0

    def finish_sampling(self, log=False):
        warmup_done = True
        if self._warmup_until is not None:
            warmup_done = self.is_ready()
            if not warmup_done:
                raise RuntimeError("Warmup not complete yet")

        source = self._warmup_source or "button"
        self._warmup_until = None
        self._warmup_source = None

        r = self._read_once(source=source, warmup_done=warmup_done)

        if log and r:
            self._append_log(r)

        return r

    # ----------------------------
    # Optional lightweight logging
    # ----------------------------
    def _ensure_log_header(self):
        try:
            with open(self.log_path, "r") as f:
                _ = f.readline()
        except Exception:
            try:
                with open(self.log_path, "w") as f:
                    f.write(
                        "timestamp,temp_c,humidity,eco2_ppm,tvoc_ppb,aqi,rating,source,ready,confidence,reason,"
                        "aht10_temp_c,aht10_humidity,aht21_temp_c,aht21_humidity,"
                        "scd41_co2_ppm,scd41_temp_c,scd41_humidity\n"
                    )
            except Exception:
                pass

    def _append_log(self, r):
        try:
            with open(self.log_path, "a") as f:
                f.write(
                    "{},{:.2f},{:.2f},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}\n".format(
                        r.timestamp,
                        r.temp_c,
                        r.humidity,
                        r.eco2_ppm,
                        r.tvoc_ppb,
                        r.aqi,
                        r.rating,
                        r.source,
                        int(1 if r.ready else 0),
                        (r.confidence if r.confidence is not None else ""),
                        r.reason,
                        ("" if r.aht10_temp_c is None else "{:.2f}".format(float(r.aht10_temp_c))),
                        ("" if r.aht10_humidity is None else "{:.2f}".format(float(r.aht10_humidity))),
                        ("" if r.aht21_temp_c is None else "{:.2f}".format(float(r.aht21_temp_c))),
                        ("" if r.aht21_humidity is None else "{:.2f}".format(float(r.aht21_humidity))),
                        ("" if r.scd41_co2_ppm is None else "{}".format(int(r.scd41_co2_ppm))),
                        ("" if r.scd41_temp_c is None else "{:.2f}".format(float(r.scd41_temp_c))),
                        ("" if r.scd41_humidity is None else "{:.2f}".format(float(r.scd41_humidity))),
                    )
                )
        except Exception:
            pass

    def get_last_logged(self):
        return self._last

    def get_log_count(self):
        try:
            n = 0
            with open(self.log_path, "r") as f:
                for _ in f:
                    n += 1
            return max(0, n - 1)
        except Exception:
            return 0

    def read_quick(self, source="summary"):
        """
        Faster read intended for live UI refresh.
        Uses shorter ENS retry window and returns last_good if not ready.
        """
        try:
            self._ensure_hw()

            # AHT21 best effort
            aht21_temp, aht21_rh = None, None
            if self._aht is not None:
                try:
                    aht21_temp, aht21_rh = self._aht.read()
                except Exception:
                    pass

            # AHT10 best effort
            aht10_temp, aht10_rh = None, None
            if self._aht10 is not None:
                try:
                    aht10_temp, aht10_rh = self._aht10.read()
                except Exception:
                    pass

            # SCD41 best effort
            scd41_co2_ppm, scd41_temp_c, scd41_rh = None, None, None
            scd41_ready, scd41_reason = False, "scd41 unavailable"
            try:
                scd41_co2_ppm, scd41_temp_c, scd41_rh, scd41_ready, scd41_reason = self._read_scd41()
            except Exception as e:
                scd41_ready, scd41_reason = False, "scd41 exception: {}".format(e)

            temp_c, rh, env_src = self._select_env_values(
                aht21_temp, aht21_rh, aht10_temp, aht10_rh
            )

            ens_temp = temp_c if env_src != "none" else None
            ens_rh = rh if env_src != "none" else None

            aqi, tvoc_ppb, eco2_ppm, ready, reason = self._read_ens160_with_retry(
                ens_temp, ens_rh, timeout_ms=1800, step_ms=200
            )
            rating = self._rating_from_aqi(aqi) if ready else "Not ready"

            conf = 0
            try:
                from src.sensors.co2_confidence import calculate_co2_confidence as _calc
                last = self._last
                conf = _calc(
                    ens_valid=ready,
                    warmup_done=True,
                    temp_ok=self._temp_ok(temp_c if env_src != "none" else None),
                    rh_ok=self._rh_ok(rh if env_src != "none" else None),
                    eco2_ppm=int(eco2_ppm),
                    last_eco2_ppm=int(last.eco2_ppm) if last else None,
                    aqi=int(aqi) if aqi is not None else None,
                    last_aqi=int(last.aqi) if last else None,
                    source=source,
                )
            except Exception:
                conf = 90 if ready else 0

            source_tag = "{}".format(source)
            if env_src != "none":
                source_tag = "{}+{}".format(source_tag, env_src)

            combined_reason = reason
            if not scd41_ready and scd41_reason:
                if combined_reason:
                    combined_reason = "{} | {}".format(combined_reason, scd41_reason)
                else:
                    combined_reason = scd41_reason

            r = AirReading(
                timestamp=self._now_timestamp(),
                temp_c=temp_c,
                humidity=rh,
                eco2_ppm=eco2_ppm,
                tvoc_ppb=tvoc_ppb,
                aqi=aqi,
                rating=rating,
                source=source_tag,
                ready=ready,
                confidence=conf,
                reason=combined_reason,
                aht10_temp_c=aht10_temp,
                aht10_humidity=aht10_rh,
                aht21_temp_c=aht21_temp,
                aht21_humidity=aht21_rh,
                scd41_co2_ppm=scd41_co2_ppm,
                scd41_temp_c=scd41_temp_c,
                scd41_humidity=scd41_rh,
            )

            if ready:
                self._last = r
                self._last_eco2_ppm = int(eco2_ppm)
                self._last_aqi = int(aqi)

            if scd41_ready and scd41_co2_ppm is not None:
                self._last_scd41_co2_ppm = int(scd41_co2_ppm)

            return self._last or r if not ready else r

        except Exception:
            return self._last