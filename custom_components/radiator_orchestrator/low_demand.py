"""Optional, bounded low-demand CH arbitration for HeatFlow.

Only an established primary room demand can enter this gate. The gate does
not adjust EMS/Hive settings or defeat room/window protections; it may defer
a low-load OFF request briefly while a verified CH flame is already burning.
All counters are process-local diagnostic observations, not estimated energy
savings or a count of starts that would otherwise have occurred.
"""
from __future__ import annotations

import math
import time

LOW_DEMAND_DEFAULTS = {
    "low_demand_cutoff_enabled": False,
    "low_demand_cutoff_c": 0.15,
    "low_demand_resume_c": 0.60,
    "low_demand_confirm_seconds": 120,
    "low_demand_max_calling_rooms": 2,
    "low_demand_max_burner_wait_seconds": 180,
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
        self.burner_wait_since = None
        self.burner_wait_remaining_seconds = None
        self.burner_deferral_events = 0
        self.suppression_events = 0
        self.suppressed_seconds_completed = 0.0
        self.suppressed_since = None

    def suppressed_seconds(self, now=None):
        now = time.monotonic() if now is None else now
        elapsed = (max(0.0, now - self.suppressed_since)
                   if self.suppressed and self.suppressed_since is not None else 0.0)
        return self.suppressed_seconds_completed + elapsed

    def _set_suppressed(self, value, now):
        if self.suppressed == value:
            return
        if value:
            self.suppression_events += 1
            self.suppressed_since = now
        else:
            if self.suppressed_since is not None:
                self.suppressed_seconds_completed += max(0.0, now - self.suppressed_since)
            self.suppressed_since = None
        self.suppressed = value

    def _clear(self, status, now):
        self._set_suppressed(False, now)
        self.low_since = None
        self.burner_wait_since = None
        self.burner_wait_remaining_seconds = None
        self.status = status
        self.remaining_seconds = None

    def evaluate(self, rooms, settings, now, *, raw_request: bool,
                 protected_comfort: bool = False, burner_firing: bool = False,
                 ch_active: bool = False, dhw_active: bool = False) -> bool:
        """True permits the normal boiler request; False suppresses only low load.

        CH flame deferral requires *all* of: positive CH flag, flame ON,
        no DHW evidence, continuous low room deficits, and a bounded timer.
        Missing/unavailable boiler evidence cannot extend the wait. Actual
        demand recovery, explicit room OFF and safety paths take precedence.
        """
        if not settings.get("low_demand_cutoff_enabled", False):
            self._clear("disabled", now)
            return raw_request
        if not raw_request:
            self._clear("no_primary_demand", now)
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
            self._clear("comfort_or_sensor_grace_protected", now)
            return True
        if any(room.measured is None or room.effective_target is None
               or not math.isfinite(room.measured)
               or not math.isfinite(room.effective_target)
               for room in calling):
            self._clear("invalid_sensor_data", now)
            return True
        deficits = [max(0.0, room.effective_target - room.measured)
                    for room in calling]
        self.max_deficit_c = max(deficits, default=0.0)
        self.total_deficit_c = round(sum(deficits), 2)
        cutoff = float(settings.get("low_demand_cutoff_c", 0.15))
        resume = float(settings.get("low_demand_resume_c", 0.60))
        confirm = int(settings.get("low_demand_confirm_seconds", 120))
        maximum_rooms = int(settings.get("low_demand_max_calling_rooms", 2))
        max_wait = int(settings.get("low_demand_max_burner_wait_seconds", 180))
        if not (0 < cutoff < resume <= 2 and 30 <= confirm <= 600
                and 1 <= maximum_rooms <= 4 and 0 <= max_wait <= 300):
            self._clear("invalid_configuration", now)
            return True
        if self.suppressed:
            if (self.max_deficit_c >= resume or self.total_deficit_c >= resume
                    or len(calling) > maximum_rooms):
                self._clear("resumed_high_demand", now)
                return True
            self.status = "suppressed_low_demand"
            self.remaining_seconds = None
            return False
        low = (len(calling) <= maximum_rooms
               and self.max_deficit_c <= cutoff
               and self.total_deficit_c <= cutoff * maximum_rooms)
        if not low:
            self._clear("normal_demand", now)
            return True
        if self.low_since is None:
            self.low_since = now
        elapsed = max(0.0, now - self.low_since)
        if elapsed < confirm:
            self.burner_wait_since = None
            self.burner_wait_remaining_seconds = None
            self.status = "confirming_low_demand"
            self.remaining_seconds = max(0, math.ceil(confirm - elapsed))
            return True
        # Prefer waiting until a genuine CH flame stops of its own accord.
        # This is bounded even if a sensor or valve is stuck; DHW combustion
        # and missing CH evidence never prolong the boiler request.
        if burner_firing and ch_active and not dhw_active and max_wait > 0:
            if self.burner_wait_since is None:
                self.burner_wait_since = now
                self.burner_deferral_events += 1
            remaining = max_wait - (now - self.burner_wait_since)
            if remaining > 0:
                self.status = "waiting_for_ch_burner_to_finish"
                self.burner_wait_remaining_seconds = math.ceil(remaining)
                self.remaining_seconds = None
                return True
        self._set_suppressed(True, now)
        self.burner_wait_since = None
        self.burner_wait_remaining_seconds = None
        self.status = "suppressed_low_demand"
        self.remaining_seconds = None
        return False
