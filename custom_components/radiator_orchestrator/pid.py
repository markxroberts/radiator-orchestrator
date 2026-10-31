"""Independent testable PID algorithm for thermostatic valve setpoint offsets."""
from dataclasses import dataclass


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class PID:
    kp: float = 1.5
    ki: float = 0.00015  # per second
    kd: float = 0.0
    integral: float = 0.0
    previous_measurement: float | None = None

    def reset(self) -> None:
        self.integral = 0.0
        self.previous_measurement = None

    def calculate(self, target: float, measurement: float, dt: float, max_offset: float = 8.0) -> float:
        """Return 0..max_offset degrees of positive TRV setpoint boost.

        P term reacts to setpoint error, I reduces residual error, D uses
        measurement derivative to avoid derivative kick on setpoint changes.
        Anti-windup stops the integral increasing while output is saturated.
        """
        dt = clamp(dt, 1.0, 600.0)
        error = target - measurement
        derivative = 0.0 if self.previous_measurement is None else -(measurement - self.previous_measurement) / dt
        self.previous_measurement = measurement
        proposed_integral = clamp(self.integral + error * dt, 0.0, 20000.0)
        candidate = self.kp * error + self.ki * proposed_integral + self.kd * derivative
        if 0.0 < candidate < max_offset or (candidate >= max_offset and error < 0) or (candidate <= 0 and error > 0):
            self.integral = proposed_integral
        return clamp(self.kp * error + self.ki * self.integral + self.kd * derivative, 0.0, max_offset)
