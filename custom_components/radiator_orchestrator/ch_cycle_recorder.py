"""Read-only, event-driven CH burner cycle observations.

Unknown CH/DHW states are never silently classified as confirmed CH.
Only observed off->on transitions qualify as starts. No boiler writes.
"""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import math

from .cycle_monitor import cycle_source, binary_state

EXTRA_SOURCES = {
    "flow": "sensor.boiler_curflowtemp",
    "power": "sensor.boiler_curburnpow",
    "pump": "sensor.boiler_heatingpumpmod",
}


def number(state):
    try:
        value = float(state.state)
        return round(value, 2) if math.isfinite(value) else None
    except (AttributeError, TypeError, ValueError):
        return None


class CHCycleRecorder:
    """Tracks flame transitions; retains a bounded list of completed cycles."""

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.active = None
        self.previous_burner = None
        self.recent = deque(maxlen=20)
        self.last = None
        self.completed = 0
        self.listeners = []

    def subscribe(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback) if callback in self.listeners else None

    def _notify(self):
        for callback in tuple(self.listeners):
            callback()

    def _snapshot(self):
        ctrl = self.ctrl
        hass = ctrl.hass
        def state(key):
            entity = cycle_source(ctrl.settings, ctrl.ems.sources, key)
            return hass.states.get(entity) if entity else None
        def source(key):
            return hass.states.get(EXTRA_SOURCES[key])
        rooms = list(ctrl.rooms.values())
        deficits = [
            max(0, r.effective_target - r.measured)
            for r in rooms if r.demand and r.measured is not None
            and r.effective_target is not None
        ]
        outdoor_id = ctrl.settings.get("outdoor_temperature")
        return {
            "active_radiators": sum(bool(r.demand) for r in rooms),
            "primary_radiators": sum(bool(r.demand and r.effective_primary) for r in rooms),
            "demand_proxy": round(sum(deficits), 2),
            "outdoor_c": number(hass.states.get(outdoor_id)) if outdoor_id else None,
            "flow_c": number(source("flow")),
            "burner_power_pct": number(source("power")),
            "pump_modulation_pct": number(source("pump")),
            "heatflow_demand": bool(ctrl.boiler_requested),
            "ems_ch": binary_state(state("ems_ch")),
            "ems_dhw": binary_state(state("ems_dhw")),
        }

    def sample(self, when=None):
        now = when or datetime.now(timezone.utc)
        ctrl = self.ctrl
        eid = cycle_source(ctrl.settings, ctrl.ems.sources, "ems_burner")
        flame = binary_state(ctrl.hass.states.get(eid)) if eid else None
        snapshot = self._snapshot()
        if self.active is not None:
            self.active["peak_flow_c"] = max(
                (v for v in (self.active["peak_flow_c"], snapshot["flow_c"]) if v is not None),
                default=None,
            )
            if flame == 1 and (snapshot["ems_dhw"] != 0 or snapshot["ems_ch"] != 1):
                self.active["classification_uncertain"] = True
        if flame is None:
            # An unavailable burner signal invalidates the timing; do not
            # misinterpret the next observed 'off' as a completed cycle.
            self.active = None
            self.previous_burner = None
            return
        if flame == 1 and self.previous_burner == 0:
            self.active = {
                "started_at": now.isoformat(),
                "start_time": now,
                "start": snapshot,
                "peak_flow_c": snapshot["flow_c"],
                "classification_uncertain": snapshot["ems_ch"] != 1 or snapshot["ems_dhw"] != 0,
            }
        elif flame == 0 and self.previous_burner == 1 and self.active is not None:
            running = self.active
            duration = round(max(0, (now - running["start_time"]).total_seconds()))
            cycle = {
                "started_at": running["started_at"],
                "ended_at": now.isoformat(),
                "duration_seconds": duration,
                "classification": "CH" if not running["classification_uncertain"] else "uncertain_CH_DHW",
                "start": running["start"],
                "end": snapshot,
                "peak_flow_c": running["peak_flow_c"],
            }
            self.last = cycle
            self.recent.append(cycle)
            self.completed += 1
            self.active = None
            self._notify()
        self.previous_burner = flame
