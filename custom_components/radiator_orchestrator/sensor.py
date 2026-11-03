"""Heating status indicators for dashboards."""
from .entity import HeatFlowEntity
import time
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from .ems_monitor import EMS_NUMERIC, EMS_TEXT
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    ctrl = hass.data[DOMAIN][entry.entry_id]
    entities = [StatusSensor(ctrl, key, label) for key, label in (
        ("preheat_minutes", "Calculated Preheat Minutes"),
        ("preheat_source", "Preheat Calculation Method"),
        ("room_status", "Room Heating Summary"),
    )]
    entities.append(DiagnosticHistorySensor(ctrl))
    entities.append(HotWaterControlReasonSensor(ctrl))
    entities.append(HolidayReturnReasonSensor(ctrl))
    entities.extend(RoomReasonSensor(ctrl, name) for name in ctrl.rooms)
    entities.append(EMSStatusSensor(ctrl))
    if ctrl.ems.enabled:
        entities.extend(EMSNumericSensor(ctrl, key) for key in EMS_NUMERIC if key in ctrl.ems.sources)
        entities.extend(EMSTextSensor(ctrl, key) for key in EMS_TEXT if key in ctrl.ems.sources)
    async_add_entities(entities)

class StatusSensor(HeatFlowEntity, SensorEntity):
    _attr_should_poll = False
    def __init__(self, ctrl, key, label):
        self.ctrl, self.key = ctrl, key
        self._attr_name = label
        self._attr_unique_id = f"{ctrl.entry.entry_id}_{key}"
        self._attr_icon = {"preheat_minutes": "mdi:timer-outline", "preheat_source": "mdi:school-outline", "room_status": "mdi:radiator"}.get(key, "mdi:home-thermometer")

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        if self.key == "room_status":
            return f"{sum(r.demand for r in self.ctrl.rooms.values())} of {len(self.ctrl.rooms)} calling"
        return getattr(self.ctrl, self.key)

    @property
    def extra_state_attributes(self):
        if self.key != "room_status":
            return {}
        return {"rooms": {name: {
            "temperature": r.measured, "target": r.effective_target,
            "calling": r.demand, "primary": r.conf.get("primary"),
            "blocked": r.blocked_reason, "phase": r.schedule_phase,
            "learned_warming_c_per_hour": self.ctrl.learning.get(name, {}).get("rate"),
            "learning_samples": self.ctrl.learning.get(name, {}).get("samples", 0),
            "demand_reason": r.demand_reason,
            "target_reason": r.target_reason,
            "effective_primary": r.effective_primary,
            "linked_bedrooms_occupied": list(r.linked_occupied),
            "linked_bedrooms_heating": list(r.linked_heating),
            "holiday_return_authorisation": r.holiday_return_authorisation,
            "evening_preheat_status": r.evening_preheat_status,
            "evening_preheat_trigger": r.evening_preheat_trigger,
            "evening_preheat_lead_minutes": r.evening_preheat_minutes,
            "temporary_override_c": r.manual_override_target,
        } for name, r in self.ctrl.rooms.items()},
        "hot_water_only_rooms": [r["name"] for r in self.ctrl.hot_water_rooms]}


class RoomReasonSensor(HeatFlowEntity, SensorEntity):
    """Explain why a configured radiator room is or is not heating."""
    _attr_should_poll = False
    def __init__(self, ctrl, room_name):
        self.ctrl = ctrl
        self.room_name = room_name
        self._attr_name = f"{room_name} heating reason"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_reason_{room_name}"
        kind = ctrl.rooms[room_name].conf.get("room_type")
        self._attr_icon = {"bathroom": "mdi:shower", "bedroom": "mdi:bed-king-outline"}.get(kind, "mdi:radiator")

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        room = self.ctrl.rooms[self.room_name]
        if room.blocked_reason:
            return room.blocked_reason.replace("_", " ")
        if room.demand and not room.effective_primary and not self.ctrl.boiler_requested:
            return "waiting for primary boiler demand"
        if room.demand:
            return room.demand_reason.replace("_", " ")
        if room.measured is None:
            return "temperature unavailable"
        if room.measured < room.effective_target - 0.25:
            # This is not a satisfied target. Surface arbitration and state
            # mismatches rather than hiding them behind an optimistic label.
            return "below target / no heat demand (" + room.target_reason.replace("_", " ") + ")"
        if room.target_reason == "room_unoccupied_setback":
            return "unoccupied setback satisfied"
        if room.target_reason == "house_absent_setback":
            return "house absent / frost target satisfied"
        if room.effective_target < float(room.conf.get("target", room.effective_target)) - 0.25:
            return "scheduled setback satisfied (" + room.target_reason.replace("_", " ") + ")"
        return "target satisfied (" + room.target_reason.replace("_", " ") + ")"

    @property
    def extra_state_attributes(self):
        room = self.ctrl.rooms[self.room_name]
        occupancy_entity = room.conf.get("occupancy")
        occupancy = self.ctrl.hass.states.get(occupancy_entity) if occupancy_entity else None
        local_now = dt_util.now()
        return {
            "room_occupancy_sensor_state": occupancy.state if occupancy else None,
            "early_occupancy_window_active": self.ctrl._early_occupancy_window(
                local_now.hour * 60 + local_now.minute),
            "measured_c": room.measured,
            "effective_target_c": room.effective_target,
            "configured_manual_target_c": room.conf.get("target"),
            "temporary_override_active": room.manual_override_target is not None,
            "temporary_override_target_c": room.manual_override_target,
            "holiday_return_guest_bedroom": room.conf["name"] in self.ctrl.settings.get("holiday_guest_bedrooms", []),
            "holiday_return_authorisation": room.holiday_return_authorisation,
            "error_to_effective_target_c": (
                round(room.effective_target - room.measured, 2) if room.measured is not None else None
            ),
            "scheduled_phase": room.schedule_phase,
            "early_occupancy_heating_enabled": bool(room.conf.get(
                "early_occupancy_heating", room.conf.get("room_type", "living") in ("living", "bathroom"))),
            "target_reason": room.target_reason,
            "demand_reason": room.demand_reason,
            "demand": room.demand,
            "primary_configured": room.conf.get("primary", True),
            "can_start_boiler_now": room.effective_primary,
            "boiler_requested": self.ctrl.boiler_requested,
            "boiler_relay_on": self.ctrl.boiler_on,
            "requested_trv_setpoint_c": room.trv_target,
            "pid_output": room.output,
            "linked_bedrooms": list(room.conf.get("linked_rooms", [])),
            "linked_bedrooms_occupied": list(room.linked_occupied),
            "linked_bedrooms_calling_heat": list(room.linked_heating),
            "morning_preheat_enabled": bool(room.conf.get("morning_preheat", False)),
            "morning_preheat_status": room.morning_preheat_status,
            "morning_preheat_trigger": room.morning_preheat_trigger,
            "morning_preheat_lead_minutes": room.morning_preheat_minutes,
            "morning_preheat_calculation": room.morning_preheat_method,
            "evening_preheat_enabled": bool(room.conf.get("evening_preheat", False)),
            "evening_ready_time": room.evening_ready_time,
            "evening_preheat_status": room.evening_preheat_status,
            "evening_preheat_trigger": room.evening_preheat_trigger,
            "evening_preheat_lead_minutes": room.evening_preheat_minutes,
            "evening_preheat_calculation": room.evening_preheat_method,
            "evening_can_start_boiler": bool(room.conf.get("evening_allow_boiler", True)),
            "evening_guest_occupancy_required": (
                room.conf.get("name") in self.ctrl.settings.get("holiday_guest_bedrooms", [])
                and bool(room.conf.get("evening_guest_requires_occupancy", True))
            ),
            "linked_morning_preheat_minutes": room.linked_preheat_minutes,
            "linked_preheat_calculation": room.linked_preheat_method,
        }


class DiagnosticHistorySensor(HeatFlowEntity, SensorEntity):
    """Recent diagnostic events; state is a small count, not JSON history."""
    _attr_should_poll = False
    _attr_icon = "mdi:history"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self._attr_name = "Diagnostic History"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_diagnostic_history"

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        return len(self.ctrl.history)

    @property
    def extra_state_attributes(self):
        # Limit Recorder attribute size; full 300 events live in persistent HA storage.
        return {"recent_events": list(self.ctrl.history)[-10:],
                "max_retained": 300,
                "oldest_event": self.ctrl.history[0]["time"] if self.ctrl.history else None}


class HotWaterControlReasonSensor(HeatFlowEntity, SensorEntity):
    """Explain automatic and manual DHW demand, without guessing relay state."""
    _attr_should_poll = False
    _attr_icon = "mdi:water-boiler"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self._attr_name = "Hot Water Control Reason"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_hot_water_reason"

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.ctrl.hot_water_reason.replace("_", " ")

    @property
    def extra_state_attributes(self):
        decision = self.ctrl.hot_water_decision
        return {
            "house_occupancy_hot_water_enabled": bool(self.ctrl.settings.get("occupancy_hot_water", False)),
            "configured_hot_water_switch": self.ctrl.settings.get("hot_water_switch"),
            "manual_demand": self.ctrl.hot_water,
            "occupancy_demand": self.ctrl.hot_water_occupancy_requested,
            "combined_demand": self.ctrl.hot_water_requested,
            "relay_on": self.ctrl.hot_water_relay_on,
            "occupancy_reason": decision.reason if decision else None,
            "rooms_configured": list(decision.configured_rooms) if decision else [],
            "rooms_occupied": list(decision.active_rooms) if decision else [],
            "rooms_qualified": list(decision.qualified_rooms) if decision else [],
            "rooms_requesting_hot_water": list(decision.requesting_rooms) if decision else [],
            "rooms_timed_out": list(decision.timed_out_rooms) if decision else [],
            "rooms_with_unavailable_sensors": list(decision.unavailable_rooms) if decision else [],
            "seconds_until_occupancy_qualified": decision.wait_seconds if decision else None,
            "minimum_occupancy_seconds": int(self.ctrl.settings.get("hot_water_min_occupancy_seconds", 60)),
            "maximum_occupancy_run_minutes": int(self.ctrl.settings.get("hot_water_max_run_minutes", 0)),
        }


class HolidayReturnReasonSensor(HeatFlowEntity, SensorEntity):
    """Expose precise holiday return readiness and missing prerequisites."""
    _attr_should_poll = False
    _attr_icon = "mdi:home-clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self._attr_name = "Holiday Return Heating Reason"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_holiday_return_reason"

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.ctrl.holiday_return_status.replace("_", " ")

    @property
    def extra_state_attributes(self):
        return {
            "return_at": self.ctrl.holiday_return_at,
            "holiday_enabled": self.ctrl._enabled(self.ctrl.settings.get("holiday_enable")),
            "manual_away": self.ctrl.away,
            "summer_off": self.ctrl.summer,
            "holiday_preheating": self.ctrl.holiday_return_preheating,
            "holiday_holding_for_arrival": self.ctrl.holiday_return_holding,
            "estimated_preheat_minutes": self.ctrl.preheat_minutes,
            "estimate_method": self.ctrl.preheat_source,
            "return_hold_minutes": int(self.ctrl.settings.get("holiday_return_hold_minutes", 120)),
            "guest_bedrooms_requiring_occupancy": list(self.ctrl.settings.get("holiday_guest_bedrooms", [])),
            "rooms_not_preheating_for_guest_occupancy": [
                n for n, r in self.ctrl.rooms.items()
                if (self.ctrl.holiday_return_preheating or self.ctrl.holiday_return_holding)
                and (r.holiday_return_authorisation.startswith("guest_")
                     or r.holiday_return_authorisation == "linked_guest_bedrooms_unoccupied")
                and r.holiday_return_authorisation not in ("guest_occupied",)
            ],
            "rooms_requesting_heat": [
                n for n, r in self.ctrl.rooms.items() if r.demand
            ],
            "primary_rooms_requesting_heat": [
                n for n, r in self.ctrl.rooms.items() if r.demand and r.effective_primary
            ],
        }

class EMSStatusSensor(HeatFlowEntity, SensorEntity):
    """EMS availability, source mappings and observed values (diagnostic only)."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:heating-coil"

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self._attr_name = "EMS Monitoring Status"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_ems_monitor_status"

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.ctrl.ems.status

    @property
    def extra_state_attributes(self):
        monitor = self.ctrl.ems
        return {
            "mode": "read_only",
            "configured_sources": dict(monitor.sources),
            "current_values": dict(monitor.values),
            "raw_source_states": dict(monitor.raw_values),
            "burner_starts_observed": monitor.burner_starts,
            "burner_runtime_observed_seconds": monitor.runtime_seconds(time.monotonic()),
            "last_burner_duration_seconds": monitor.last_burner_duration_seconds,
            "notes": "DHW valve selection is not proof of active DHW charging",
        }


class EMSNumericSensor(HeatFlowEntity, SensorEntity):
    """Optional mirror of a selected EMS-ESP sensor; no polling or writes."""
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl, key):
        self.ctrl, self.key = ctrl, key
        label, unit, icon, _, _ = EMS_NUMERIC[key]
        self._attr_name = f"EMS {label}"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        if unit == "°C":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.ctrl.ems.values.get(self.key)

    @property
    def available(self):
        return self.native_value is not None

    @property
    def extra_state_attributes(self):
        return {"source_entity": self.ctrl.ems.sources.get(self.key),
                "ems_monitoring_mode": "read_only"}

class EMSTextSensor(HeatFlowEntity, SensorEntity):
    """Mirror categorical select/sensor states without assuming boiler control."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl, key):
        self.ctrl, self.key = ctrl, key
        label, icon = EMS_TEXT[key]
        self._attr_name = f"EMS {label}"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_{key}"
        self._attr_icon = icon

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.ctrl.ems.values.get(self.key)

    @property
    def available(self):
        return self.native_value is not None

    @property
    def extra_state_attributes(self):
        return {"source_entity": self.ctrl.ems.sources.get(self.key),
                "ems_monitoring_mode": "read_only",
                "note": "Reported state only; not a controllable setting"}
