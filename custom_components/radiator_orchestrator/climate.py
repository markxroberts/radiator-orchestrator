"""Virtual thermostat per room."""
from .entity import HeatFlowEntity
from homeassistant.components.climate import ClimateEntity, HVACMode, HVACAction, ClimateEntityFeature
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from .const import DOMAIN

async def async_setup_entry(hass,entry,async_add_entities):
    ctrl=hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RoomClimate(ctrl,name) for name in ctrl.rooms])

class RoomClimate(HeatFlowEntity, ClimateEntity):
    _attr_should_poll=False
    _attr_temperature_unit=UnitOfTemperature.CELSIUS
    _attr_hvac_modes=[HVACMode.HEAT,HVACMode.OFF]
    _attr_supported_features=ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp=5
    _attr_max_temp=30
    _attr_target_temperature_step=0.5

    def __init__(self,ctrl,name):
        self.ctrl=ctrl
        self.room=ctrl.rooms[name]
        self._attr_name=f"{name} Heating"
        self._attr_unique_id=f"{ctrl.entry.entry_id}_{name}_climate"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.ctrl.entry.entry_id)}, "name": "HeatFlow Controller", "manufacturer": "HeatFlow", "model": "Central Heating & Hot Water Controller"}

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def hvac_mode(self):
        return HVACMode.HEAT if self.room.mode=="heat" else HVACMode.OFF

    @property
    def hvac_action(self):
        if self.room.mode=="off" or self.ctrl.summer and not self.ctrl.away:
            return HVACAction.OFF
        if self.room.demand and self.ctrl.boiler_on and not self.room.blocked_reason:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def current_temperature(self):
        return self.room.measured

    @property
    def target_temperature(self):
        # The climate card must not display a manual target which the
        # schedule is currently overriding. This reflects the actual control
        # temperature (read-only computed setpoint, recalculated each tick).
        if self.ctrl.settings.get("enable_schedule", False):
            return self.room.effective_target
        return self.room.conf["target"]

    @property
    def extra_state_attributes(self):
        return {"effective_target": self.room.effective_target,
                "configured_manual_target": self.room.conf["target"],
                "temporary_override_active": self.room.manual_override_target is not None,
                "temporary_override_target_c": self.room.manual_override_target,
                "scheduled_control_enabled": bool(self.ctrl.settings.get("enable_schedule", False)),
                "pid_boost":round(self.room.output,2),
                "trv_internal_temperature_c": self.room.trv_internal_temperature,
                "trv_compensation_status": self.room.trv_compensation_status,
                "trv_compensation_applied": self.room.trv_compensation_applied,
                "requested_trv_setpoint_c": self.room.trv_target,
                "demand":self.room.demand,"primary":self.room.conf["primary"],
                "blocked_reason":self.room.blocked_reason, "schedule_phase":self.room.schedule_phase,
                "calibration_paused": self.ctrl.calibration_paused,
                "target_reason": self.room.target_reason,
                "demand_reason": self.room.demand_reason,
                "can_start_boiler_now": self.room.effective_primary,
                "linked_bedrooms": list(self.room.conf.get("linked_rooms", []))}

    async def async_set_hvac_mode(self,hvac_mode):
        await self.ctrl.set_mode(self.room.conf["name"],hvac_mode.value)

    async def async_set_temperature(self,**kwargs):
        if ATTR_TEMPERATURE in kwargs:
            await self.ctrl.set_target(self.room.conf["name"],kwargs[ATTR_TEMPERATURE])
