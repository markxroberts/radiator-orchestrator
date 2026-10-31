"""Demand and relay indication."""
from .entity import HeatFlowEntity
from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN

async def async_setup_entry(hass,entry,async_add_entities):
    ctrl=hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HeatSensor(ctrl,key,label) for key,label in (("boiler_requested","Heating Demand"),("boiler_on","Boiler Heating Relay"),("house_present","House Occupied"),("preheating","Preheating"),("morning_preheating","Morning Room Preheating"),("holiday_active","Away Holiday Active"),("holiday_return_preheating","Holiday Return Preheating"),("holiday_return_holding","Holiday Return Comfort Hold"),("calibration_paused","Calibration Paused"),("hot_water_occupancy_requested","Occupancy Hot Water Demand"),("hot_water_requested","Hot Water Requested"),("hot_water_relay_on","Hot Water Relay"))])

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
