"""Event-driven, read-only burner cycle observations for HeatFlow.

EMS CH and DHW flags can lag flame transitions. Classify from evidence seen
*while the flame is burning*, not from an off-state at the ignition instant or
at shutdown. Never invoke a service or feed these readings into boiler control.
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


def classify_cycle(observed: dict) -> tuple[str, str]:
    """Use positive evidence during firing; return explicit uncertainty."""
    ch = observed["ch_on"]
    dhw = observed["dhw_on"]
    if ch and dhw:
        return "mixed_CH_DHW", "both_CH_and_DHW_observed_while_firing"
    if observed["dhw_unknown"]:
        return "uncertain_CH_DHW", "DHW_sensor_unavailable_during_firing"
    if ch and not dhw:
        return "CH", "CH_observed_DHW_never_active_while_firing"
    if dhw and not ch:
        if observed["ch_unknown"]:
            return "uncertain_CH_DHW", "CH_sensor_unavailable_during_DHW_cycle"
        return "DHW", "DHW_observed_no_CH_evidence"
    if observed["ch_unknown"]:
        return "uncertain_CH_DHW", "CH_sensor_unavailable_during_firing"
    return "uncertain_CH_DHW", "neither_CH_nor_DHW_active_confirmed_during_firing"


class CHCycleRecorder:
    """Tracks flame edges and a bounded in-memory list of completed cycles."""

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.active = None
        self.previous_burner = None
        self.recent = deque(maxlen=20)
        self.last = None
        self.last_confirmed_ch = None
        self.completed = 0
        self.confirmed_ch_cycles = 0
        self.uncertain_cycles = 0
        self.dhw_cycles = 0
        self.listeners = []

    def subscribe(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback) if callback in self.listeners else None

    def _notify(self):
        # Called from async_handle_state_change on Home Assistant's event loop.
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

        rooms = [room for room in ctrl.rooms.values() if room.demand]
        deficit = [
            max(0.0, room.effective_target - room.measured)
            for room in rooms if room.measured is not None
            and room.effective_target is not None
        ]
        # A requested TRV setpoint is NOT a measurement of the valve opening.
        # The number of heads requesting more than their own sensed temperature
        # is a useful qualitative heat-acceptance proxy, not thermal kW.
        with_trv_reading = [
            room for room in rooms
            if room.trv_internal_temperature is not None
        ]
        requested_open = sum(
            room.trv_target > room.trv_internal_temperature + 0.5
            for room in with_trv_reading
        )
        outdoor_id = ctrl.settings.get("outdoor_temperature")
        return {
            "active_radiators": len(rooms),
            "primary_radiators": sum(bool(room.effective_primary) for room in rooms),
            "demand_proxy": round(sum(deficit), 2),
            "requested_trv_open_count": requested_open,
            "trv_temperature_known_count": len(with_trv_reading),
            "trv_demand_note": "requested_setpoint_vs_TRV_temperature_not_valve_position",
            "outdoor_c": number(hass.states.get(outdoor_id)) if outdoor_id else None,
            "flow_c": number(source("flow")),
            "burner_power_pct": number(source("power")),
            "pump_modulation_pct": number(source("pump")),
            "heatflow_demand": bool(ctrl.boiler_requested),
            "ems_ch": binary_state(state("ems_ch")),
            "ems_dhw": binary_state(state("ems_dhw")),
        }

    async def async_handle_state_change(self, event):
        """Observe transitions on HA's event loop, preserving thread safety."""
        self.sample()

    def _observe_firing(self, sample):
        active = self.active
        flow = sample["flow_c"]
        if flow is not None:
            active["peak_flow_c"] = max(
                (v for v in (active["peak_flow_c"], flow) if v is not None),
                default=None,
            )
        power = sample["burner_power_pct"]
        if power is not None and power > 0:
            active["min_observed_burner_power_pct"] = min(
                (v for v in (active["min_observed_burner_power_pct"], power)
                 if v is not None), default=None,
            )
            active["max_observed_burner_power_pct"] = max(
                (v for v in (active["max_observed_burner_power_pct"], power)
                 if v is not None), default=None,
            )
        ch, dhw = sample["ems_ch"], sample["ems_dhw"]
        active["observed"]["ch_on"] |= ch == 1
        active["observed"]["ch_unknown"] |= ch is None
        active["observed"]["dhw_on"] |= dhw == 1
        active["observed"]["dhw_unknown"] |= dhw is None
        active["observed"]["samples"] += 1

    def sample(self, when=None):
        now = when or datetime.now(timezone.utc)
        ctrl = self.ctrl
        eid = cycle_source(ctrl.settings, ctrl.ems.sources, "ems_burner")
        flame = binary_state(ctrl.hass.states.get(eid)) if eid else None
        sample = self._snapshot()
        if flame is None:
            # Don't bridge a missing flame signal or invent a transition.
            self.active = None
            self.previous_burner = None
            return
        if flame == 1 and self.previous_burner == 0:
            self.active = {
                "started_at": now.isoformat(),
                "start_time": now,
                "start": sample,
                "peak_flow_c": None,
                "min_observed_burner_power_pct": None,
                "max_observed_burner_power_pct": None,
                "observed": {
                    "ch_on": False,
                    "ch_unknown": False,
                    "dhw_on": False,
                    "dhw_unknown": False,
                    "samples": 0,
                },
            }
        if flame == 1 and self.active is not None:
            self._observe_firing(sample)
        elif flame == 0 and self.previous_burner == 1 and self.active is not None:
            active = self.active
            classification, reason = classify_cycle(active["observed"])
            duration = round(max(0, (now - active["start_time"]).total_seconds()))
            initial_flow = active["start"]["flow_c"]
            peak_flow = active["peak_flow_c"]
            cycle = {
                "started_at": active["started_at"],
                "ended_at": now.isoformat(),
                "duration_seconds": duration,
                "classification": classification,
                "classification_reason": reason,
                "firing_samples": active["observed"]["samples"],
                "start": active["start"],
                "end": sample,
                "peak_flow_c": peak_flow,
                # Flow rise is not flow-return Delta T (return is unavailable).
                "observed_flow_rise_c": (
                    round(peak_flow - initial_flow, 2)
                    if peak_flow is not None and initial_flow is not None else None
                ),
                "min_observed_burner_power_pct": active["min_observed_burner_power_pct"],
                "max_observed_burner_power_pct": active["max_observed_burner_power_pct"],
            }
            self.last = cycle
            self.recent.append(cycle)
            self.completed += 1
            if classification == "CH":
                self.last_confirmed_ch = cycle
                self.confirmed_ch_cycles += 1
            elif classification == "uncertain_CH_DHW":
                self.uncertain_cycles += 1
            else:
                self.dhw_cycles += 1
            self.active = None
            self._notify()
        self.previous_burner = flame
