"""Bounded protection against short Zigbee sensor availability interruptions.

This pure helper never creates heating demand. It may retain only an *existing*
room call backed by recent valid inputs, while no explicit protection or target
reduction requires the call to stop. Timestamps are process-local monotonic
seconds; they are deliberately not restored after a HA restart.
"""
from __future__ import annotations
import math

SENSOR_DROPOUT_GRACE_SECONDS = 60


def retained_call_seconds(
    *,
    was_calling: bool,
    last_valid_at: float | None,
    last_valid_target: float | None,
    current_target: float,
    now: float,
    hard_blocked: bool = False,
    grace_seconds: int = SENSOR_DROPOUT_GRACE_SECONDS,
) -> int:
    """Return bounded seconds of existing demand that may be held, else zero.

    A target reduction invalidates retention so schedule, holiday, occupancy,
    manual setback and other control decisions are never defeated by stale data.
    This function must only be called for invalid/missing sensor observations.
    """
    if (hard_blocked or not was_calling or last_valid_at is None
            or last_valid_target is None or grace_seconds <= 0):
        return 0
    if not (math.isfinite(now) and math.isfinite(last_valid_at)
            and math.isfinite(current_target) and math.isfinite(last_valid_target)):
        return 0
    if current_target < last_valid_target - 0.01:
        return 0
    elapsed = now - last_valid_at
    if elapsed < 0 or elapsed >= grace_seconds:
        return 0
    return min(grace_seconds, math.ceil(grace_seconds - elapsed))
