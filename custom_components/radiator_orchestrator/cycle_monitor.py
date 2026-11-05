"""Pure read-only Home Assistant state readers for heating cycle diagnosis.

These readings must NEVER influence heating control or call services.
0/1 numeric measurements are stored by Home Assistant Recorder and can be
compared with existing EMS burner starts, flow and modulation statistics.
"""
from __future__ import annotations

import math

CYCLE_DEFAULTS = {
    "hive_climate": "climate.boiler_controller_heat",
    "ems_gas": "binary_sensor.boiler_burngas",
    "ems_ch": "binary_sensor.boiler_heatingactive",
    "ems_dhw": "binary_sensor.boiler_dhw_active",
}

CYCLE_FIELDS = {
    "heatflow_demand": {
        "name": "HeatFlow Heating Demand (0/1)",
        "icon": "mdi:thermostat",
        "meaning": "Controller requests CH; 1=requested, 0=not requested",
    },
    "heating_switch": {
        "name": "Heating Switch State (0/1)",
        "icon": "mdi:electric-switch",
        "meaning": "Actual Home Assistant state of HeatFlow's configured heat switch",
    },
    "hive_setpoint": {
        "name": "Hive Target Temperature",
        "icon": "mdi:thermometer",
        "meaning": "Hive climate target temperature, not actual boiler flame demand",
    },
    "hive_action": {
        "name": "Hive Heating Action (0/1)",
        "icon": "mdi:home-thermometer",
        "meaning": "Hive hvac_action heating=1, idle/off=0, otherwise unavailable",
    },
    "ems_burner": {
        "name": "EMS Burner Firing (0/1)",
        "icon": "mdi:fire",
        "meaning": "EMS burner firing status; may include domestic hot water",
    },
    "ems_ch": {
        "name": "EMS Central Heating Active (0/1)",
        "icon": "mdi:radiator",
        "meaning": "EMS boiler central-heating-active status (not the same as burner flame)",
    },
    "ems_dhw": {
        "name": "EMS Hot Water Active (0/1)",
        "icon": "mdi:water-boiler",
        "meaning": "EMS boiler DHW active status; helps exclude DHW-only burner starts",
    },
}


def cycle_source(settings: dict, ems_sources: dict, key: str) -> str | None:
    if key == "heating_switch":
        return settings.get("heat_switch")
    if key in ("hive_setpoint", "hive_action"):
        return settings.get("cycle_hive_climate") or CYCLE_DEFAULTS["hive_climate"]
    if key == "ems_burner":
        return ems_sources.get("ems_gas") or CYCLE_DEFAULTS["ems_gas"]
    if key == "ems_ch":
        return settings.get("cycle_ems_ch_entity") or CYCLE_DEFAULTS["ems_ch"]
    if key == "ems_dhw":
        return settings.get("cycle_ems_dhw_entity") or CYCLE_DEFAULTS["ems_dhw"]
    return None


def cycle_source_entities(settings: dict, ems_sources: dict) -> set[str]:
    """Watch source changes in addition to scheduled 60-second evaluations."""
    return {
        entity_id for key in CYCLE_FIELDS
        if (entity_id := cycle_source(settings, ems_sources, key))
    }


def binary_state(state) -> int | None:
    raw = getattr(state, "state", None)
    if raw == "on":
        return 1
    if raw == "off":
        return 0
    return None


def hive_action(state) -> int | None:
    """Read hvac_action; do NOT assume the thermostat setpoint proves a call."""
    if state is None or getattr(state, "state", None) in (
        "unknown", "unavailable"
    ):
        return None
    if state.state == "off":
        return 0
    action = (getattr(state, "attributes", None) or {}).get("hvac_action")
    if action == "heating":
        return 1
    if action in ("idle", "off"):
        return 0
    return None


def hive_setpoint(state) -> float | None:
    if state is None or getattr(state, "state", None) in (
        "unknown", "unavailable"
    ):
        return None
    raw = (getattr(state, "attributes", None) or {}).get("temperature")
    try:
        value = float(raw)
    except (ValueError, TypeError):
        return None
    return round(value, 2) if math.isfinite(value) and 0 <= value <= 40 else None


def cycle_value(ctrl, key: str) -> int | float | None:
    if key == "heatflow_demand":
        return int(bool(ctrl.boiler_requested))
    eid = cycle_source(ctrl.settings, ctrl.ems.sources, key)
    state = ctrl.hass.states.get(eid) if eid else None
    if key == "hive_action":
        return hive_action(state)
    if key == "hive_setpoint":
        return hive_setpoint(state)
    return binary_state(state)
