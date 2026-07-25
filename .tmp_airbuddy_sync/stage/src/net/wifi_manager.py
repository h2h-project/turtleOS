# src/net/wifi_manager.py
# Pico W / MicroPython Wi-Fi manager (STA mode)
# Robust connect with sane status handling across firmware variants.

import time
import sys as _sys
_IS_ESP32 = getattr(_sys, "platform", "") == "esp32"


def _build_hostname():
    """
    Build a human-readable DHCP hostname from config.json.
    Falls back to "AirBuddy" on any error (missing file, bad JSON, etc.).
    Format: AirBuddy_<device_id>_<board_type>  e.g. AirBuddy_AB_08_xiao_esp32s3
    """
    try:
        import json
        with open("config.json") as _f:
            _cfg = json.load(_f)
        _parts = ["AirBuddy"]
        _did = str(_cfg.get("device_id", "") or "").strip()
        _bt = str(_cfg.get("board_type", "") or "").strip()
        if _did:
            _parts.append(_did)
        if _bt:
            _parts.append(_bt)
        return "_".join(_parts)[:63]
    except Exception:
        return "AirBuddy"

# ESP32/esp-idf authmode codes that indicate WPA3.
# MicroPython wlan.scan() returns (ssid, bssid, channel, rssi, authmode, hidden).
# AUTH_WPA3_PSK=6, AUTH_WPA2_WPA3_PSK=7 — neither is supported by ESP32 MicroPython.
_WPA3_AUTHMODES = (6, 7)

# ESP32 WiFi TX power cap (dBm).  Default firmware value is 20 dBm, which
# causes a 300-500 mA current spike on association that can brownout a battery
# supply.  13 dBm is sufficient for indoor use and brings peak draw within
# typical LiPo/battery limits.
_ESP32_TX_POWER_DBM = 13

try:
    import network
except ImportError:
    network = None


class WiFiManager:
    supported = True

    def __init__(self):
        if network is None:
            raise RuntimeError("network module not available (are you on Pico W firmware?)")
        self.wlan = network.WLAN(network.STA_IF)
        self._last_error = ""
        self._last_status = None
        self._hostname = _build_hostname()

        # Optional: disable power-save (often improves stability on Pico W)
        # 0xA11140 is CYW43-specific (Pico W); calling it on ESP32 causes abort()
        if not _IS_ESP32:
            try:
                self.wlan.config(pm=0xA11140)
            except Exception:
                pass

        # Apply hostname now in case the radio is already active (ESP32 pre-activates
        # the PHY before WiFiManager is created to avoid heap fragmentation).
        self._apply_hostname()

    def _apply_hostname(self):
        if not self._hostname:
            return
        try:
            self.wlan.config(dhcp_hostname=self._hostname)
        except Exception:
            pass

    def _apply_pm_performance(self, quiet=False):
        # Prevent TCP timeouts from deep radio sleep on ESP32.
        # PM_PERFORMANCE = light sleep between beacons (~20 mA idle).
        # PM_POWERSAVE (ESP32 default) causes OSError(116) after long idles.
        # Re-call this before each HTTP request — the setting can revert after idle.
        if not _IS_ESP32:
            return
        try:
            pm = getattr(network.WLAN, "PM_PERFORMANCE", None)
            if pm is not None:
                self.wlan.config(pm=pm)
                if not quiet:
                    print("[WIFI] PM_PERFORMANCE set")
            else:
                if not quiet:
                    print("[WIFI] PM_PERFORMANCE unavailable — trying PM_NONE")
                pm0 = getattr(network.WLAN, "PM_NONE", None)
                if pm0 is not None:
                    self.wlan.config(pm=pm0)
                    if not quiet:
                        print("[WIFI] PM_NONE set")
                elif not quiet:
                    print("[WIFI] no PM constants — power-save active")
        except Exception as _e:
            if not quiet:
                print("[WIFI] PM set err:", repr(_e))

    # -------------------------
    # Basic state
    # -------------------------
    def enabled(self):
        try:
            return bool(self.wlan.active())
        except Exception:
            return False

    def active(self, on=True):
        try:
            self.wlan.active(bool(on))
        except Exception:
            return
        if not on:
            try:
                self.wlan.disconnect()
            except Exception:
                pass

    def is_connected(self):
        try:
            return bool(self.wlan.isconnected())
        except Exception:
            return False

    def ip(self):
        if not self.is_connected():
            return ""
        try:
            return self.wlan.ifconfig()[0]
        except Exception:
            return ""

    def status_code(self):
        try:
            return self.wlan.status()
        except Exception:
            return None

    def status_text(self):
        if not self.enabled():
            return "RADIO OFF"
        if self.is_connected():
            return "CONNECTED"

        code = self.status_code()

        # rp2 / CYW43 codes (0..5):
        # 0 IDLE, 1 CONNECTING, 2 WRONG_PASSWORD, 3 NO_AP_FOUND, 4 CONNECT_FAIL, 5 GOT_IP
        if code == 0:
            return "IDLE"
        if code == 1:
            return "CONNECTING"
        if code == 2:
            return "WRONG PASSWORD"
        if code == 3:
            return "NO AP FOUND"
        if code == 4:
            return "CONNECT FAIL"
        if code == 5:
            return "GOT IP"

        # Some firmwares/ports return negative codes:
        # -2 NO AP FOUND, -3 WRONG PASSWORD, -4 CONNECT FAIL
        if code == -2:
            return "NO AP FOUND"
        if code == -3:
            return "WRONG PASSWORD"
        if code == -4:
            return "CONNECT FAIL"

        # IMPORTANT:
        # Some ports use -1 for CONNECT_FAIL or transient states.
        # Don't label it "IDLE" because that hides failures.
        if code == -1:
            return "UNKNOWN (-1)"

        # ESP32 / esp-idf codes:
        if code == 1000:
            return "IDLE"
        if code == 1001:
            return "CONNECTING"
        if code == 201:
            return "NO AP FOUND"
        if code == 202:
            return "WRONG PASSWORD"
        if code == 203:
            return "CONNECT FAIL"
        if code == 204:
            return "HANDSHAKE TIMEOUT"
        if code == 1010:
            return "GOT IP"

        return "DISCONNECTED"

    def last_error(self):
        return self._last_error

    # -------------------------
    # Connect / disconnect
    # -------------------------
    def disconnect(self):
        self._last_error = ""
        try:
            self.wlan.disconnect()
        except Exception:
            self._last_error = "disconnect err"
            return False
        return True

    def reconnect(self, ssid, password, timeout_s=15):
        # Soft reconnect — does NOT cycle active(False)/active(True).
        # Cycling radio power releases DMA RX buffers; on a fragmented
        # post-boot heap this fails with "only N of 10 buffers allocated".
        try:
            self.wlan.disconnect()
        except Exception:
            pass
        time.sleep_ms(500)
        return self.connect(ssid, password, timeout_s=timeout_s, retry=0)

    def _is_wpa3(self, ssid):
        """
        Scan visible networks and return True if ssid advertises WPA3-only or
        WPA2/WPA3 transition auth.  ESP32 MicroPython (esp-idf) does not
        support SAE/WPA3 and hangs for the full timeout when connecting to one.
        Fails open (returns False) on any scan error so connect() can proceed.
        """
        if not ssid:
            return False
        try:
            nets = self.wlan.scan()
            for net in nets:
                try:
                    net_ssid = net[0]
                    if isinstance(net_ssid, bytes):
                        net_ssid = net_ssid.decode("utf-8", "ignore")
                    if net_ssid == ssid and int(net[4]) in _WPA3_AUTHMODES:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def _hard_reset_sta(self):
        """
        Hard re-init of STA interface to clear sticky states.
        """
        try:
            self.wlan.disconnect()
        except Exception:
            pass
        try:
            self.wlan.active(False)
        except Exception:
            pass
        time.sleep_ms(200)
        try:
            self.wlan.active(True)
        except Exception:
            pass
        time.sleep_ms(250)

        # Re-apply pm config if supported (Pico W / CYW43 only — skip on ESP32)
        if not _IS_ESP32:
            try:
                self.wlan.config(pm=0xA11140)
            except Exception:
                pass
        else:
            # Cap TX power after radio init so the association handshake doesn't
            # spike current high enough to brownout a battery supply.
            # Extra settle delay gives the power supply time to stabilize after
            # the wlan.active(True) PHY init spike before connect() runs.
            try:
                self.wlan.config(txpower=_ESP32_TX_POWER_DBM)
            except Exception:
                pass
            time.sleep_ms(300)

        self._apply_hostname()

    def connect(self, ssid, password, timeout_s=12, retry=2, tick_cb=None):
        """
        Blocking connect attempt with timeout.
        Returns (ok:bool, ip:str, status_text:str)
        """

        self._last_error = ""

        ssid = "" if ssid is None else str(ssid)
        password = "" if password is None else str(password)

        if not ssid:
            self._last_error = "No SSID"
            return (False, "", "NO SSID")

        # If already connected, keep it.
        if self.is_connected():
            self._apply_pm_performance()
            return (True, self.ip(), "CONNECTED")

        # If the radio is already active (pre-activated during boot before heap
        # fragmentation), skip the active(False)→active(True) cycle.
        # That cycle releases and re-allocates the WiFi rx buffers — on a
        # fragmented heap it will fail with "only 3 of 10 buffers allocated".
        if self.wlan.active():
            if _IS_ESP32:
                # Cap TX power before connecting to reduce peak current on battery.
                try:
                    self.wlan.config(txpower=_ESP32_TX_POWER_DBM)
                except Exception:
                    pass
                # WPA3 detection: scan before connecting.
                # ESP32 MicroPython does not support SAE/WPA3 and hangs for the
                # full timeout_s when connecting to a WPA3 network.  Scan first
                # and bail immediately if the target SSID advertises WPA3.
                if self._is_wpa3(ssid):
                    self._last_error = "WPA3 not supported on ESP32"
                    print("ESP32 cannot use WPA3. Switch to a 2GHz WPA2 network.")
                    return (False, "", "WPA3 NOT SUPPORTED")

                # On ESP32 the radio is often pre-activated (IDLE / status=1000)
                # before connect() is called.  Calling disconnect() on an IDLE
                # interface can silently prevent connect() from initiating a
                # connection (status stays 1000, WiFi task never starts).
                # Skip disconnect() when already IDLE; call connect() directly.
                # If the driver is in a post-failure state the connect() call
                # itself resets it on all supported ESP-IDF / MicroPython builds.
                #
                # NOTE: status() is NOT called here — it can deadlock when the
                # WiFi task is actively associating.  We infer IDLE from the fact
                # that is_connected() returned False above (handled at the top).
                try:
                    self.wlan.connect(ssid, password)
                except Exception:
                    self._last_error = "connect() threw"
                    return (False, "", "CONNECT EXC")
            else:
                # Pico / CYW43: status() is safe.
                st = self.status_code()
                _in_progress = (st in (1, 5, 1001, 1010))
                if not _in_progress:
                    try:
                        self.wlan.disconnect()
                    except Exception:
                        pass
                    time.sleep_ms(200)
                    try:
                        self.wlan.connect(ssid, password)
                    except Exception:
                        self._last_error = "connect() threw"
                        return (False, "", "CONNECT EXC")
                # else: association already in progress — poll loop handles the rest.
        else:
            # Radio is off — full hard reset to bring it up cleanly, then connect.
            self._hard_reset_sta()
            try:
                self.wlan.connect(ssid, password)
            except Exception:
                self._last_error = "connect() threw"
                return (False, "", "CONNECT EXC")

        start = time.ticks_ms()
        last_print_ms = 0
        last_st = None
        neg1_start = None
        cyw43_fail_start = None  # debounce timer for CYW43 transient fail codes

        if _IS_ESP32:
            # ESP32/esp-idf: all these codes are definitive — exit immediately.
            early_fail = (-2, -3, -4, 2, 3, 4, 201, 202, 203, 204)
        else:
            # CYW43/rp2: positive fail codes (2=WRONG_PASSWORD, 3=NO_AP_FOUND,
            # 4=CONNECT_FAIL) are often spurious on the first cold-boot association
            # attempt — CYW43 reports WRONG_PASSWORD before the 4-way handshake
            # fully completes, then recovers on a clean disconnect + reconnect.
            # Only the negative firmware-level variants are reliable immediate exits.
            # Positive codes are debounced below; after 2 s they break to retry.
            early_fail = (-2, -3, -4)

        while time.ticks_diff(time.ticks_ms(), start) < int(timeout_s * 1000):
            # Connection-check strategy differs by platform:
            #
            # ESP32: isconnected() calls esp_wifi_sta_get_ap_info() which acquires
            # the WiFi driver mutex.  The WiFi task holds that mutex during WPA2
            # authentication → calling isconnected() at that moment blocks forever
            # (RTCWDT eventually fires).  ifconfig() reads the LWIP TCP/IP stack's
            # netif IP info via a *separate* LWIP core lock — safe at any time.
            # We treat a non-zero, non-0.0.0.0 IP as "connected".
            #
            # Pico/CYW43: isconnected() is safe; no mutex contention.
            if _IS_ESP32:
                try:
                    _ip = self.wlan.ifconfig()[0]
                    if _ip and _ip != "0.0.0.0":
                        self._apply_pm_performance()
                        return (True, _ip, "CONNECTED")
                except Exception:
                    pass
            else:
                if self.is_connected():
                    return (True, self.ip(), "CONNECTED")

            now = time.ticks_ms()

            if _IS_ESP32:
                # status() also deadlocks on ESP32 during association — skip it.
                if time.ticks_diff(now, last_print_ms) >= 1000:
                    print("WIFI: connecting...")
                    if tick_cb is not None:
                        try:
                            tick_cb()
                        except Exception:
                            pass
                    last_print_ms = now
                time.sleep_ms(200)
                continue

            st = self.status_code()
            self._last_status = st

            # Print on state transitions only — CYW43 holds WRONG_PASSWORD for
            # several seconds while completing the handshake; periodic repeats add
            # noise without information.
            if st != last_st:
                print("WIFI: connect st=", st, self.status_text())
                last_st = st
                last_print_ms = now

            # Treat GOT_IP as success even if isconnected lags
            # 5 = Pico W/CYW43 STAT_GOT_IP, 1010 = ESP32/esp-idf STAT_GOT_IP
            if st == 5 or st == 1010:
                ip = self.ip()
                if ip:
                    return (True, ip, "CONNECTED")

            # Early failures
            if st in early_fail:
                return (False, "", self.status_text())

            # If firmware uses -1, fail if it persists (often means connect fail)
            if st == -1:
                if neg1_start is None:
                    neg1_start = now
                elif time.ticks_diff(now, neg1_start) > 1500:
                    return (False, "", self.status_text())
            else:
                neg1_start = None

            # CYW43 positive fail codes (2=WRONG_PASSWORD, 3=NO_AP_FOUND,
            # 4=CONNECT_FAIL) are often spurious: the firmware holds WRONG_PASSWORD
            # for several seconds while the WPA2 four-way handshake completes, then
            # isconnected() flips True.  Wait 5 s before breaking so the first
            # attempt usually succeeds rather than forcing a hard-reset + retry.
            # (3 s covers CONNECTING + ~2 s of handshake time in WRONG_PASSWORD state.)
            if not _IS_ESP32 and st in (2, 3, 4):
                if cyw43_fail_start is None:
                    cyw43_fail_start = now
                elif time.ticks_diff(now, cyw43_fail_start) > 5000:
                    break
            else:
                cyw43_fail_start = None

            time.sleep_ms(200)

        # Timed out (or broke out of loop after CYW43 persistent fail code).
        # On ESP32: the WiFi task may still hold its internal mutex after a
        # failed association.  Calling disconnect() or connect() at this point
        # deadlocks → RTCWDT reset.  Return immediately; the caller decides
        # whether to retry (which will do a fresh connect() call safely).
        if _IS_ESP32:
            return (False, "", "TIMEOUT")

        if retry and retry > 0:
            if not _IS_ESP32:
                # CYW43: hard-reset the radio to clear stuck fail state.
                # A simple disconnect() leaves the status code (e.g. 2=WRONG_PASSWORD)
                # cached in the driver — the next connect() immediately re-reads it.
                # Cycling active(False)→active(True) clears the CYW43 state machine.
                self._hard_reset_sta()
            else:
                try:
                    self.wlan.disconnect()
                except Exception:
                    pass
                backoff_ms = 800 + (2 - retry) * 500
                time.sleep_ms(backoff_ms)
            return self.connect(ssid, password, timeout_s=timeout_s, retry=retry - 1, tick_cb=tick_cb)

        return (False, "", "TIMEOUT")
