"""Read-only TRV sensor comparison for optional temperature compensation.

The external room sensor and occupancy/boiler arbitration decide if heat is
needed. This helper only alters the commanded climate setpoint when the
controller has *already* approved a room to participate in heating.
"""
from __future__ import annotations

import math


def read_trv_temperature(state) -> float | None:
    """Read the TRV's own climate.current_temperature, rejecting bad data."""
    if state is None or getattr(state, "state", None) in ("unknown", "unavailable"):
        return None
    attributes = getattr(state, "attributes", None) or {}
    raw = attributes.get("current_temperature")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and -10.0 <= number <= 65.0 else None


def trv_boosted_setpoint(
    pid_setpoint: float,
    internal_temperature: float | None,
    opening_margin_c: float = 1.5,
) -> tuple[float, str]:
    """Return safe 0.5°C step setpoint and reason, capped at 30°C.

    The result is only for an already-approved room with heating demand.
    Rounding *up*, not to nearest, guarantees the selected margin whenever
    the 30°C controller maximum permits it.
    """
    ordinary = max(5.0, min(30.0, float(pid_setpoint)))
    if internal_temperature is None or not math.isfinite(internal_temperature):
        return ordinary, "internal_temperature_unavailable"
    try:
        margin = float(opening_margin_c)
    except (TypeError, ValueError):
        margin = 1.5
    if not math.isfinite(margin):
        margin = 1.5
    margin = max(0.5, min(5.0, margin))
    required = internal_temperature + margin
    if required <= ordinary + 0.000001:
        return ordinary, "pid_setpoint_sufficient"
    boosted = max(ordinary, min(30.0, math.ceil(required * 2 - 1e-9) / 2))
    if boosted + 0.000001 < required:
        return boosted, "max_setpoint_limited"
    return boosted, "boosted"
