# src/ui/screens/wind_finder.py
# Luff-sweep wind-direction experiment for TurtleOS.
#
# This module deliberately owns only the luff-test algorithm.
# ServoScreen passes callbacks for servo pulse output, AS5600 angle,
# INA219 current, and optional OLED progress updates.

import time
import math


# --------------------------------------------------------------------
# Sweep configuration
# --------------------------------------------------------------------

SWEEP_MIN_US = 500
SWEEP_MAX_US = 2500
SWEEP_STEP_US = 50

# First move fully to the beginning of the sweep.
START_SETTLE_MS = 900

# At every test position, let the servo settle before measuring flutter.
POSITION_SETTLE_MS = 150

# 16 samples x 25 ms ~= 400 ms observation window per position.
SAMPLE_COUNT = 16
SAMPLE_PERIOD_MS = 25
MIN_VALID_SAMPLES = 8

TOP_RESULTS = 5


class WindFinder:
    """
    Slow step-and-observe luff sweep.

    A continuous sweep would make the AS5600 angle change simply because
    TurtleOS is commanding the servo to move. That commanded movement could
    swamp the smaller luff vibration we want to detect.

    So each test point is:

        command position -> settle -> observe vibration -> next position

    At each position, circular AS5600 readings are unwrapped, a straight-line
    trend from first to last sample is removed, and the remaining residual
    motion is measured. Slow servo settling is therefore suppressed while
    rapid back-and-forth flutter is preserved.
    """

    def __init__(
        self,
        write_pulse_us,
        read_angle_deg,
        read_current_ma=None,
        progress_cb=None,
    ):
        self._write_pulse_us = write_pulse_us
        self._read_angle_deg = read_angle_deg
        self._read_current_ma = read_current_ma
        self._progress_cb = progress_cb


    # ----------------------------------------------------------------
    # Circular-angle helpers
    # ----------------------------------------------------------------

    def _unwrap(self, values):
        """Convert [359, 1, 3] to approximately [359, 361, 363]."""

        if not values:
            return []

        out = [float(values[0])]
        prev_raw = float(values[0])

        for value in values[1:]:
            raw = float(value)
            delta = ((raw - prev_raw + 180.0) % 360.0) - 180.0
            out.append(out[-1] + delta)
            prev_raw = raw

        return out


    def _analyse_angles(self, angle_samples):
        """
        Return:
            mean_angle_deg
            jitter_rms_deg
            jitter_p2p_deg
            raw_travel_deg

        The primary luff score is jitter_rms_deg.
        """

        unwrapped = self._unwrap(angle_samples)
        n = len(unwrapped)

        if n < MIN_VALID_SAMPLES:
            return None

        first = unwrapped[0]
        last = unwrapped[-1]
        residuals = []

        for i in range(n):
            frac = i / float(n - 1)
            trend = first + (last - first) * frac
            residuals.append(unwrapped[i] - trend)

        sq_sum = 0.0
        for r in residuals:
            sq_sum += r * r

        jitter_rms = math.sqrt(sq_sum / float(n))
        jitter_p2p = max(residuals) - min(residuals)
        mean_angle = (sum(unwrapped) / float(n)) % 360.0
        raw_travel = last - first

        return (
            mean_angle,
            jitter_rms,
            jitter_p2p,
            raw_travel,
        )


    # ----------------------------------------------------------------
    # Current helpers
    # ----------------------------------------------------------------

    def _current_stats(self, current_samples):
        valid = []

        for value in current_samples:
            if value is not None:
                valid.append(float(value))

        if not valid:
            return (None, None)

        avg_ma = sum(valid) / float(len(valid))
        peak_ma = max([abs(v) for v in valid])

        return (avg_ma, peak_ma)


    # ----------------------------------------------------------------
    # One observation position
    # ----------------------------------------------------------------

    def _observe_position(self, pulse_us, progress):
        self._write_pulse_us(pulse_us)
        time.sleep_ms(POSITION_SETTLE_MS)

        angles = []
        currents = []

        for _ in range(SAMPLE_COUNT):
            angle = self._read_angle_deg()

            if angle is not None:
                angles.append(float(angle))

            if self._read_current_ma is not None:
                try:
                    currents.append(self._read_current_ma())
                except Exception:
                    currents.append(None)

            time.sleep_ms(SAMPLE_PERIOD_MS)

        analysis = self._analyse_angles(angles)
        avg_ma, peak_ma = self._current_stats(currents)

        if analysis is None:
            print(
                "[WIND] pulse={} us  INVALID angle samples={}".format(
                    pulse_us,
                    len(angles),
                )
            )
            return None

        mean_angle, jitter_rms, jitter_p2p, raw_travel = analysis

        print(
            "[WIND] pulse={:4d} us  angle={:6.1f} deg  "
            "jitter_rms={:5.2f} deg  jitter_p2p={:5.2f} deg  "
            "drift={:+5.2f} deg  avg={} mA  peak={} mA".format(
                int(pulse_us),
                float(mean_angle),
                float(jitter_rms),
                float(jitter_p2p),
                float(raw_travel),
                "{:.0f}".format(avg_ma) if avg_ma is not None else "---",
                "{:.0f}".format(peak_ma) if peak_ma is not None else "---",
            )
        )

        if self._progress_cb is not None:
            try:
                self._progress_cb(
                    progress=progress,
                    pulse_us=int(pulse_us),
                    angle_deg=float(mean_angle),
                    current_ma=avg_ma,
                    jitter_deg=float(jitter_rms),
                )
            except Exception:
                pass

        # Lightweight tuple to reduce MicroPython heap use:
        # pulse, mean_angle, rms, p2p, drift, avg_current, peak_current
        return (
            int(pulse_us),
            float(mean_angle),
            float(jitter_rms),
            float(jitter_p2p),
            float(raw_travel),
            avg_ma,
            peak_ma,
        )


    # ----------------------------------------------------------------
    # Main sweep
    # ----------------------------------------------------------------

    def run(self):
        """
        Run one complete 500 -> 2500 us luff sweep.

        IMPORTANT:
        Luff alone identifies the sail's wind-alignment AXIS. Unless another
        directional clue exists, there is normally a 180-degree ambiguity:

            candidate angle
            candidate angle + 180 degrees

        Later compass/navigation logic can resolve this into a global wind
        bearing.
        """

        print("")
        print("[WIND] ========================================")
        print("[WIND] Luff sweep start")
        print(
            "[WIND] range={}..{} us  step={} us  "
            "settle={} ms  samples={} x {} ms".format(
                SWEEP_MIN_US,
                SWEEP_MAX_US,
                SWEEP_STEP_US,
                POSITION_SETTLE_MS,
                SAMPLE_COUNT,
                SAMPLE_PERIOD_MS,
            )
        )

        print(
            "[WIND] moving to sweep start: {} us".format(
                SWEEP_MIN_US
            )
        )

        self._write_pulse_us(SWEEP_MIN_US)
        time.sleep_ms(START_SETTLE_MS)

        results = []

        total_positions = (
            ((SWEEP_MAX_US - SWEEP_MIN_US) // SWEEP_STEP_US) + 1
        )

        index = 0
        pulse = SWEEP_MIN_US

        while pulse <= SWEEP_MAX_US:
            progress = index / float(max(1, total_positions - 1))

            result = self._observe_position(
                pulse,
                progress,
            )

            if result is not None:
                results.append(result)

            index += 1
            pulse += SWEEP_STEP_US

        if not results:
            print("[WIND] FAILED: no valid AS5600 observations")
            print("[WIND] ========================================")

            return {
                "ok": False,
                "error": "No AS5600 data",
            }

        ranked = sorted(
            results,
            key=lambda r: r[2],
            reverse=True,
        )

        best = ranked[0]

        scores = sorted([r[2] for r in results])
        mid = len(scores) // 2

        if len(scores) % 2:
            baseline = scores[mid]
        else:
            baseline = (scores[mid - 1] + scores[mid]) / 2.0

        if baseline > 0.0001:
            confidence = best[2] / baseline
        else:
            confidence = 0.0

        wind_angle = best[1] % 360.0
        opposite_angle = (wind_angle + 180.0) % 360.0

        print("")
        print("[WIND] -------- strongest luff positions --------")

        count = min(TOP_RESULTS, len(ranked))

        for i in range(count):
            r = ranked[i]

            print(
                "[WIND] #{:d} pulse={:4d} us  angle={:6.1f} deg  "
                "rms={:5.2f} deg  p2p={:5.2f} deg".format(
                    i + 1,
                    int(r[0]),
                    float(r[1]),
                    float(r[2]),
                    float(r[3]),
                )
            )

        print("")
        print("[WIND] BEST luff angle = {:.1f} deg".format(wind_angle))
        print("[WIND] opposite candidate = {:.1f} deg".format(opposite_angle))
        print("[WIND] best pulse = {} us".format(best[0]))
        print(
            "[WIND] jitter RMS = {:.2f} deg  p2p = {:.2f} deg".format(
                best[2],
                best[3],
            )
        )
        print(
            "[WIND] baseline RMS = {:.2f} deg  confidence ratio = {:.2f}x".format(
                baseline,
                confidence,
            )
        )
        print("[WIND] ========================================")
        print("")

        top = []
        for r in ranked[:TOP_RESULTS]:
            top.append((r[0], r[1], r[2], r[3]))

        return {
            "ok": True,
            "wind_angle_deg": wind_angle,
            "opposite_angle_deg": opposite_angle,
            "pulse_us": best[0],
            "jitter_deg": best[2],
            "jitter_p2p_deg": best[3],
            "baseline_jitter_deg": baseline,
            "confidence": confidence,
            "avg_current_ma": best[5],
            "peak_current_ma": best[6],
            "positions_tested": len(results),
            "top": top,
        }