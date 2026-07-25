# src/nav/pid.py — heading-error PID controller for the sail servo
#
# Output is a sail-angle correction in degrees around the neutral trim.
# Gains are placeholders pending tethered water trials (see
# docs/pending_nav_dev.md).

class PID:
    def __init__(self, kp=1.0, ki=0.0, kd=0.2,
                 out_min=-60.0, out_max=60.0, i_limit=30.0):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.out_min = float(out_min)
        self.out_max = float(out_max)
        self.i_limit = float(i_limit)
        self._integral = 0.0
        self._prev_error = None

    def update(self, error, dt_s):
        """error in degrees (norm180); dt_s seconds since last update."""
        error = float(error)
        dt_s = float(dt_s)
        if dt_s <= 0.0:
            dt_s = 0.001

        self._integral += error * dt_s
        if self._integral > self.i_limit:
            self._integral = self.i_limit
        elif self._integral < -self.i_limit:
            self._integral = -self.i_limit

        d = 0.0
        if self._prev_error is not None:
            d = (error - self._prev_error) / dt_s
        self._prev_error = error

        out = self.kp * error + self.ki * self._integral + self.kd * d
        if out > self.out_max:
            return self.out_max
        if out < self.out_min:
            return self.out_min
        return out

    def reset(self):
        self._integral = 0.0
        self._prev_error = None
