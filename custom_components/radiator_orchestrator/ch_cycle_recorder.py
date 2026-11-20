"""Read-only burner-cycle and demand-normalised performance monitoring.

DHW status flags can remain OFF despite DHW start-counter increments; compare
charging/valve signals and monotonic start counters as independent evidence.
Conflicts are labelled uncertain rather than counted as confirmed CH.
No EMS writes, boiler control calls or heating overrides.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import math
import statistics
import time

from .cycle_monitor import cycle_source, binary_state

EXTRA_SOURCES = {
    "flow": "sensor.boiler_curflowtemp",
    "power": "sensor.boiler_curburnpow",
    "pump": "sensor.boiler_heatingpumpmod",
    "dhw_charging": "binary_sensor.boiler_dhw_charging",
    "dhw_valve": "binary_sensor.boiler_dhw_3wayvalve",
    "ch_starts": "sensor.boiler_heatstarts",
    "dhw_starts": "sensor.boiler_dhw_starts",
    "burner_starts": "sensor.boiler_burnstarts",
}
COUNTERS = ("ch_starts", "dhw_starts", "burner_starts")


def number(state):
    try:
        value = float(state.state)
        return round(value, 2) if math.isfinite(value) else None
    except (AttributeError, TypeError, ValueError):
        return None


def counter_delta(baseline, current):
    """Return a trustworthy monotonic counter increment, or None."""
    if baseline is None or current is None:
        return None
    try:
        if not math.isfinite(baseline) or not math.isfinite(current):
            return None
    except TypeError:
        return None
    if current < baseline:
        return None  # reset or stale/out-of-order data
    return int(current - baseline)


def classify_cycle(observed: dict, counter_changes: dict | None = None) -> tuple[str, str]:
    """Corroborate CH/DHW flags with start counters; fail closed on conflicts."""
    changes = counter_changes or {}
    ch_count = changes.get("ch_starts")
    dhw_count = changes.get("dhw_starts")
    ch = observed.get("ch_on", False)
    dhw_primary = observed.get("dhw_on", False)
    dhw_charging = observed.get("dhw_charging_on", False)
    dhw_valve = observed.get("dhw_valve_on", False)
    dhw = dhw_primary or dhw_charging or dhw_valve
    ch_counter = ch_count is not None and ch_count > 0
    dhw_counter = dhw_count is not None and dhw_count > 0
    if ch and dhw:
        return "mixed_CH_DHW", "CH_and_DHW_flags_observed_while_firing"
    if dhw_counter and (ch or ch_counter):
        return "uncertain_CH_DHW", "DHW_counter_conflicts_with_CH_evidence"
    if dhw and ch_counter:
        return "uncertain_CH_DHW", "CH_counter_conflicts_with_DHW_evidence"
    if dhw:
        if observed.get("ch_unknown", False) and ch_count is None:
            return "uncertain_CH_DHW", "CH_signal_missing_during_DHW"
        return "DHW", "DHW_charging_or_valve_signal_observed"
    if dhw_counter:
        return "DHW", "DHW_start_counter_increment_despite_inactive_DHW_flag"
    if observed.get("dhw_unknown", False) and dhw_count is None:
        return "uncertain_CH_DHW", "DHW_signals_and_counter_unavailable"
    if ch:
        return "CH", "CH_active_observed_without_DHW_evidence"
    if ch_counter:
        return "CH", "CH_start_counter_increment_without_DHW_evidence"
    if observed.get("ch_unknown", False):
        return "uncertain_CH_DHW", "CH_signal_unavailable"
    return "uncertain_CH_DHW", "neither_CH_nor_DHW_evidence_observed"


class CHCycleRecorder:
    """A bounded event-driven cycle history with process-local counters."""

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.active = None
        self.previous_burner = None
        self.previous_snapshot = None
        self.recent = deque(maxlen=20)
        self.last = None
        self.last_confirmed_ch = None
        self.completed = 0
        self.confirmed_ch_cycles = 0
        self.uncertain_cycles = 0
        self.dhw_cycles = 0
        self.listeners = []
        self._counter_first = None
        self._counter_latest = None
        self._controller_sample_at = None
        self._last_controller_request = False
        self._last_controller_relay = False
        self.demand_elapsed_seconds = 0.0
        self.relay_elapsed_seconds = 0.0
        self.controller_demand_samples = 0
        self.ch_fires_while_suppressed = 0

    def subscribe(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback) if callback in self.listeners else None

    def _notify(self):
        # Invoked only from HA event-loop callbacks (never an executor).
        for callback in tuple(self.listeners):
            callback()

    def observe_controller(self, when=None):
        """Integrate actual controller request/relay time between evaluations."""
        now = time.monotonic() if when is None else when
        if self._controller_sample_at is not None:
            elapsed = max(0.0, now - self._controller_sample_at)
            # Discard anomalously large gaps (e.g. a frozen HA process).
            if elapsed <= 300:
                if self._last_controller_request:
                    self.demand_elapsed_seconds += elapsed
                if self._last_controller_relay:
                    self.relay_elapsed_seconds += elapsed
        self._controller_sample_at = now
        self._last_controller_request = bool(self.ctrl.boiler_requested)
        self._last_controller_relay = bool(self.ctrl.boiler_on)
        self.controller_demand_samples += 1

    def _snapshot(self):
        ctrl, hass = self.ctrl, self.ctrl.hass

        def state(key):
            entity_id = cycle_source(ctrl.settings, ctrl.ems.sources, key)
            return hass.states.get(entity_id) if entity_id else None

        def extra(key):
            return hass.states.get(EXTRA_SOURCES[key])

        rooms = [r for r in ctrl.rooms.values() if r.demand]
        deficits = [max(0.0, r.effective_target - r.measured)
                    for r in rooms if r.measured is not None and r.effective_target is not None]
        with_trv = [r for r in rooms if r.trv_internal_temperature is not None]
        requested_open = sum(r.trv_target > r.trv_internal_temperature + 0.5 for r in with_trv)
        outside_id = ctrl.settings.get("outdoor_temperature")
        return {
            "active_radiators": len(rooms),
            "primary_radiators": sum(bool(r.effective_primary) for r in rooms),
            "demand_proxy": round(sum(deficits), 2),
            "requested_trv_open_count": requested_open,
            "trv_temperature_known_count": len(with_trv),
            "trv_demand_note": "requested_setpoint_vs_TRV_temperature_not_valve_position",
            "outdoor_c": number(hass.states.get(outside_id)) if outside_id else None,
            "flow_c": number(extra("flow")),
            "burner_power_pct": number(extra("power")),
            "pump_modulation_pct": number(extra("pump")),
            "heatflow_demand": bool(ctrl.boiler_requested),
            "ems_ch": binary_state(state("ems_ch")),
            "ems_dhw": binary_state(state("ems_dhw")),
            "ems_dhw_charging": binary_state(extra("dhw_charging")),
            "ems_dhw_valve": binary_state(extra("dhw_valve")),
            "ch_starts": number(extra("ch_starts")),
            "dhw_starts": number(extra("dhw_starts")),
            "burner_starts": number(extra("burner_starts")),
        }

    async def async_handle_state_change(self, event):
        """Sample on HA event loop; sensor listeners write only from this loop."""
        self.sample()

    def _observe_firing(self, sample):
        active = self.active
        if sample["flow_c"] is not None:
            active["peak_flow_c"] = max(
                (v for v in (active["peak_flow_c"], sample["flow_c"]) if v is not None),
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
        observed = active["observed"]
        observed["ch_on"] |= sample["ems_ch"] == 1
        observed["ch_unknown"] |= sample["ems_ch"] is None
        observed["dhw_on"] |= sample["ems_dhw"] == 1
        observed["dhw_charging_on"] |= sample["ems_dhw_charging"] == 1
        observed["dhw_valve_on"] |= sample["ems_dhw_valve"] == 1
        observed["dhw_unknown"] |= all(
            sample[key] is None for key in
            ("ems_dhw", "ems_dhw_charging", "ems_dhw_valve")
        )
        observed["samples"] += 1

    def _counter_deltas(self, sample):
        start = self.active["baseline"] if self.active is not None else {}
        return {key: counter_delta(start.get(key), sample.get(key)) for key in COUNTERS}

    def sample(self, when=None):
        now = when or datetime.now(timezone.utc)
        eid = cycle_source(self.ctrl.settings, self.ctrl.ems.sources, "ems_burner")
        flame = binary_state(self.ctrl.hass.states.get(eid)) if eid else None
        sample = self._snapshot()
        # Track counter increments since recorder startup as a separate,
        # independently measured indicator of CH/DHW activity.
        current_counters = {key: sample[key] for key in COUNTERS}
        if self._counter_first is None and all(v is not None for v in current_counters.values()):
            self._counter_first = current_counters
        if all(v is not None for v in current_counters.values()):
            self._counter_latest = current_counters
        if flame is None:
            self.active = None
            self.previous_burner = None
            self.previous_snapshot = None
            return
        if flame == 1 and self.previous_burner == 0:
            before_ignition = self.previous_snapshot or sample
            self.active = {
                "started_at": now.isoformat(),
                "start_time": now,
                "start": sample,
                "baseline": {key: before_ignition.get(key) for key in COUNTERS},
                "peak_flow_c": None,
                "min_observed_burner_power_pct": None,
                "max_observed_burner_power_pct": None,
                "observed": {
                    "ch_on": False, "ch_unknown": False,
                    "dhw_on": False, "dhw_charging_on": False,
                    "dhw_valve_on": False, "dhw_unknown": False,
                    "samples": 0,
                },
            }
            if getattr(self.ctrl.low_demand, "suppressed", False):
                self.ch_fires_while_suppressed += 1
        if flame == 1 and self.active is not None:
            self._observe_firing(sample)
        elif flame == 0 and self.previous_burner == 1 and self.active is not None:
            active = self.active
            deltas = self._counter_deltas(sample)
            classification, reason = classify_cycle(active["observed"], deltas)
            duration = round(max(0.0, (now - active["start_time"]).total_seconds()))
            start_flow, peak_flow = active["start"]["flow_c"], active["peak_flow_c"]
            cycle = {
                "started_at": active["started_at"],
                "ended_at": now.isoformat(),
                "duration_seconds": duration,
                "classification": classification,
                "classification_reason": reason,
                "counter_deltas_during_cycle": deltas,
                "firing_evidence": dict(active["observed"]),
                "firing_samples": active["observed"]["samples"],
                "start": active["start"],
                "end": sample,
                "peak_flow_c": peak_flow,
                "observed_flow_rise_c": (
                    round(peak_flow - start_flow, 2)
                    if peak_flow is not None and start_flow is not None else None
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
        self.previous_snapshot = sample

    def performance(self):
        """Small, bounded, recorder-friendly statistical summary."""
        ch_cycles = [c for c in self.recent if c["classification"] == "CH"]
        durations = sorted(c["duration_seconds"] for c in ch_cycles)
        peaks = [c["peak_flow_c"] for c in ch_cycles if c["peak_flow_c"] is not None]
        deficits = [c["start"]["demand_proxy"] for c in ch_cycles
                    if c["start"]["demand_proxy"] is not None]
        count_dhw = 0
        count_ch = None
        if self._counter_first is not None and self._counter_latest is not None:
            count_dhw = counter_delta(
                self._counter_first["dhw_starts"], self._counter_latest["dhw_starts"]
            )
            count_ch = counter_delta(
                self._counter_first["ch_starts"], self._counter_latest["ch_starts"]
            )
        hours = self.demand_elapsed_seconds / 3600.0
        rate = round(count_ch / hours, 2) if count_ch is not None and hours >= 1/6 else None
        return {
            "observed_CH_starts_per_demand_hour": rate,
            "CH_counter_starts_since_reload": count_ch,
            "DHW_counter_starts_since_reload": count_dhw,
            "heatflow_request_hours_since_reload": round(hours, 3),
            "heating_relay_hours_since_reload": round(self.relay_elapsed_seconds / 3600, 3),
            "completed_burner_cycles_since_reload": self.completed,
            "confirmed_CH_cycles_since_reload": self.confirmed_ch_cycles,
            "uncertain_cycles_since_reload": self.uncertain_cycles,
            "DHW_or_mixed_cycles_since_reload": self.dhw_cycles,
            "recent_CH_duration_median_s": statistics.median(durations) if durations else None,
            "recent_CH_duration_p90_s": (
                durations[min(len(durations) - 1, math.ceil(0.9 * len(durations)) - 1)]
                if durations else None
            ),
            "recent_short_CH_under_120s": sum(d < 120 for d in durations),
            "recent_CH_peak_flow_median_c": (
                round(statistics.median(peaks), 1) if peaks else None
            ),
            "recent_CH_initial_deficit_median_c": (
                round(statistics.median(deficits), 2) if deficits else None
            ),
            "last_20_cycle_classifications": [c["classification"] for c in self.recent],
            "low_demand_cutoff_suppression_events": self.ctrl.low_demand.suppression_events,
            "low_demand_burner_deferral_events": self.ctrl.low_demand.burner_deferral_events,
            "low_demand_suppressed_minutes_since_reload": round(
                self.ctrl.low_demand.suppressed_seconds() / 60, 1
            ),
            "burner_starts_observed_while_cutoff_suppressed": self.ch_fires_while_suppressed,
            "basis": "Counters and HeatFlow demand hours since integration reload; not saved between restarts",
            "avoided_starts_note": "Prevented starts cannot be directly measured; count suppression events instead",
        }
