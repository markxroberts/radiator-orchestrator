"""Demand and relay indication."""
from .entity import HeatFlowEntity
from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN
from homeassistant.helpers.entity import EntityCategory
from .ems_monitor import EMS_BOOLEAN

async def async_setup_entry(hass,entry,async_add_entities):
    ctrl=hass.data[DOMAIN][entry.entry_id]
    entities = [HeatSensor(ctrl,key,label) for key,label in (("boiler_requested","Heating Demand"),("boiler_on","Boiler Heating Relay"),("house_present","House Occupied"),("preheating","Preheating"),("morning_preheating","Morning Room Preheating"),("holiday_active","Away Holiday Active"),("holiday_return_preheating","Holiday Return Preheating"),("holiday_return_holding","Holiday Return Comfort Hold"),("calibration_paused","Calibration Paused"),("hot_water_occupancy_requested","Occupancy Hot Water Demand"),("hot_water_requested","Hot Water Requested"),("hot_water_relay_on","Hot Water Relay"))]
    if ctrl.ems.enabled:
        entities.extend(EMSBinarySensor(ctrl, key) for key in EMS_BOOLEAN if key in ctrl.ems.sources)
    async_add_entities(entities)

class HeatSensor(HeatFlowEntity, BinarySensorEntity):
    _attr_should_poll=False
    def __init__(self,ctrl,attr,name):
        self.ctrl=ctrl
        self.attr=attr
        self._attr_name=name
        self._attr_unique_id=f"{ctrl.entry.entry_id}_{attr}"
        self._attr_icon={
            "boiler_requested": "mdi:thermostat",
            "boiler_on": "mdi:fire",
            "house_present": "mdi:home-account",
            "preheating": "mdi:clock-fast",
            "morning_preheating": "mdi:weather-sunset-up",
            "holiday_active": "mdi:bag-suitcase",
            "holiday_return_preheating": "mdi:home-clock-outline",
            "holiday_return_holding": "mdi:home-thermometer",
            "calibration_paused": "mdi:pause-circle-outline",
            "hot_water_occupancy_requested": "mdi:water-boiler",
            "hot_water_requested": "mdi:water-check",
            "hot_water_relay_on": "mdi:water-boiler",
        }.get(attr, "mdi:home-thermometer")
    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))
    @property
    def is_on(self):
        return getattr(self.ctrl,self.attr)

class EMSBinarySensor(HeatFlowEntity, BinarySensorEntity):
    """Expose observed EMS states; never assume unavailable means off."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl, key):
        self.ctrl, self.key = ctrl, key
        label, icon = EMS_BOOLEAN[key]
        self._attr_name = f"EMS {label}"
        self._attr_unique_id = f"{ctrl.entry.entry_id}_{key}"
        self._attr_icon = icon

    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))

    @property
    def is_on(self):
        return self.ctrl.ems.values.get(self.key)

    @property
    def available(self):
        return self.is_on is not None

    @property
    def extra_state_attributes(self):
        return {"source_entity": self.ctrl.ems.sources.get(self.key),
                "ems_monitoring_mode": "read_only"}
