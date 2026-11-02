"""Optional, strictly read-only EMS-ESP observations for HeatFlow.

Do not import HA or invoke services here. The heating and DHW controls remain
independent of EMS, including when the gateway or its entities are unavailable.
"""
from __future__ import annotations

import math

EMS_ENABLED = "ems_monitor_enabled"

# Settings keys, types, names, and sensible conservative limits.
# In particular, a 3-way-valve position is NOT proof of DHW charging.
EMS_NUMERIC = {
    "ems_flow_temperature": ("Flow temperature", "°C", "mdi:thermometer-water", -20, 120),
    "ems_return_temperature": ("Return temperature", "°C", "mdi:thermometer-chevron-down", -20, 120),
    "ems_selected_flow_temperature": ("Selected flow temperature", "°C", "mdi:thermometer-lines", 0, 120),
    "ems_heating_temperature": ("Heating temperature setting", "°C", "mdi:thermometer-high", 0, 120),
    "ems_burner_power": ("Burner modulation", "%", "mdi:fire", 0, 100),
    "ems_dhw_temperature": ("Hot-water temperature", "°C", "mdi:water-thermometer", -20, 100),
}
EMS_BOOLEAN = {
    "ems_gas": ("Burner firing (gas)", "mdi:fire"),
    "ems_fan": ("Fan running", "mdi:fan"),
    "ems_heating_pump": ("Heating pump running", "mdi:pump"),
    "ems_dhw_valve": ("DHW 3-way valve selected", "mdi:valve"),
    "ems_dhw_charging": ("DHW charging", "mdi:water-boiler"),
}
EMS_FIELDS = (*EMS_NUMERIC, *EMS_BOOLEAN)


def value_from_state(key: str, state) -> float | bool | None:
    """Decode an HA entity, returning None rather than guessing unavailable data."""
    if state is None:
        return None
    raw = str(getattr(state, "state", "")).strip().lower()
    if raw in ("", "unknown", "unavailable", "none"):
        return None
    if key in EMS_BOOLEAN:
        if raw in ("on", "true", "1"):
            return True
        if raw in ("off", "false", "0"):
            return False
        return None
    if key in EMS_NUMERIC:
        try:
            number = float(raw)
        except (ValueError, TypeError):
            return None
        _, _, _, minimum, maximum = EMS_NUMERIC[key]
        if math.isfinite(number) and minimum <= number <= maximum:
            return round(number, 2)
    return None


class EMSMonitor:
    """Snapshot and bounded discrete transitions, with no boiler commands."""

    def __init__(self, settings: dict):
        self.enabled = bool(settings.get(EMS_ENABLED, False))
        self.sources = {key: settings.get(key) for key in EMS_FIELDS
                        if self.enabled and settings.get(key)}
        self.values: dict[str, float | bool | None] = {}
        self._observed = False
        self.burner_starts = 0
        self._burner_since: float | None = None
        self.last_burner_duration_seconds: int | None = None
        self.burner_runtime_seconds = 0.0

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if not self.sources:
            return "no_entities_selected"
        valid = sum(v is not None for v in self.values.values())
        if not valid:
            return "sources_unavailable"
        if valid < len(self.sources):
            return "partial"
        return "monitoring"

    def sample(self, state_of, now_monotonic: float) -> list[dict]:
        """Return transition records. Do not log every temperature fluctuation."""
        if not self.sources:
            return []
        new_values = {key: value_from_state(key, state_of(eid))
                      for key, eid in self.sources.items()}
        old_values = self.values
        transitions = []
        if self._observed:
            for key in EMS_BOOLEAN:
                if key in new_values and new_values[key] != old_values.get(key):
                    transitions.append({"signal": key,
                                        "previous": old_values.get(key),
                                        "value": new_values[key]})
        # A burner start is only counted on an observed false->true change.
        # An initial 'on' sample establishes timing, but not a known start.
        if "ems_gas" in new_values:
            gas = new_values["ems_gas"]
            prior = old_values.get("ems_gas")
            if gas is True and self._burner_since is None:
                self._burner_since = now_monotonic
                if self._observed and prior is False:
                    self.burner_starts += 1
            elif gas is not True and self._burner_since is not None:
                elapsed = max(0, now_monotonic - self._burner_since)
                self.burner_runtime_seconds += elapsed
                if gas is False:
                    self.last_burner_duration_seconds = int(elapsed)
                    # Include duration only if an observed transition to off.
                    if transitions and transitions[0]["signal"] == "ems_gas":
                        transitions[0]["duration_seconds"] = int(elapsed)
                self._burner_since = None
        self.values = new_values
        self._observed = True
        return transitions

    def runtime_seconds(self, now_monotonic: float) -> int:
        return round(self.burner_runtime_seconds + (
            max(0, now_monotonic - self._burner_since)
            if self._burner_since is not None else 0))
