"""Optional low-demand boiler-call gate for HeatFlow.

Uses only verified room temperature deficits; never controls EMS or bypasses
HeatFlow's existing relay min-on/min-off timers. Disabled by default.
"""
from __future__ import annotations

import math

LOW_DEMAND_DEFAULTS = {
    "low_demand_cutoff_enabled": False,
    "low_demand_cutoff_c": 0.15,
    "low_demand_resume_c": 0.60,
    "low_demand_confirm_seconds": 120,
    "low_demand_max_calling_rooms": 2,
}


class LowDemandGate:
    def __init__(self):
        self.suppressed = False
        self.low_since = None
        self.status = "disabled"
        self.remaining_seconds = None
        self.max_deficit_c = None
        self.total_deficit_c = None
        self.calling_rooms = 0

    def _clear(self, status):
        self.suppressed = False
        self.low_since = None
        self.status = status
        self.remaining_seconds = None

    def evaluate(self, rooms, settings, now, *,
                 raw_request: bool, protected_comfort: bool = False) -> bool:
        """Return the allowed CH request; leave individual room demands intact.

        With a cut-off already active, restart only on a definite >resume
        deficit or increased number of calling radiators. Invalid inputs fail
        open (preserve heat), never infer low demand from missing readings.
        """
        if not settings.get("low_demand_cutoff_enabled", False):
            self._clear("disabled")
            return raw_request
        if not raw_request:
            self._clear("no_primary_demand")
            return False
        calling = [room for room in rooms if room.demand]
        self.calling_rooms = len(calling)
        if protected_comfort or any(
            room.sensor_grace_reason
            or room.manual_override_target is not None
            or room.target_reason in (
                "holiday_return_preheat", "holiday_return_hold",
                "work_return_preheat", "morning_room_preheat",
                "morning_room_hold", "linked_morning_preheat",
                "linked_morning_hold", "evening_bedroom_preheat",
                "evening_bedroom_comfort_hold", "early_occupied_morning"
            ) for room in calling
        ):
            self._clear("comfort_or_sensor_grace_protected")
            return True
        if any(room.measured is None or room.effective_target is None
               or not math.isfinite(room.measured)
               or not math.isfinite(room.effective_target)
               for room in calling):
            self._clear("invalid_sensor_data")
            return True
        deficits = [max(0.0, room.effective_target - room.measured)
                    for room in calling]
        self.max_deficit_c = max(deficits, default=0.0)
        self.total_deficit_c = round(sum(deficits), 2)
        cutoff = float(settings.get("low_demand_cutoff_c", 0.15))
        resume = float(settings.get("low_demand_resume_c", 0.60))
        confirm = int(settings.get("low_demand_confirm_seconds", 120))
        maximum_rooms = int(settings.get("low_demand_max_calling_rooms", 2))
        # Ignore inconsistent manually edited config rather than risk
        # unexpected control behaviour.
        if not (0 < cutoff < resume <= 2 and 30 <= confirm <= 600
                and 1 <= maximum_rooms <= 4):
            self._clear("invalid_configuration")
            return True
        if self.suppressed:
            if (self.max_deficit_c >= resume
                    or self.total_deficit_c >= resume
                    or len(calling) > maximum_rooms):
                self._clear("resumed_high_demand")
                return True
            self.status = "suppressed_low_demand"
            self.remaining_seconds = None
            return False
        low = (len(calling) <= maximum_rooms
               and self.max_deficit_c <= cutoff
               and self.total_deficit_c <= cutoff * maximum_rooms)
        if not low:
            self._clear("normal_demand")
            return True
        if self.low_since is None:
            self.low_since = now
        elapsed = max(0.0, now - self.low_since)
        if elapsed >= confirm:
            self.suppressed = True
            self.status = "suppressed_low_demand"
            self.remaining_seconds = None
            return False
        self.status = "confirming_low_demand"
        self.remaining_seconds = max(0, math.ceil(confirm - elapsed))
        return True
