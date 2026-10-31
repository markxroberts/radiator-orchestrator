"""House operating modes and independent domestic hot water command."""
from .entity import HeatFlowEntity
from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN,CONF_HOT_WATER_SWITCH

async def async_setup_entry(hass,entry,async_add_entities):
    ctrl=hass.data[DOMAIN][entry.entry_id]
    values=[HouseSwitch(ctrl,"away","Holiday / Away"),HouseSwitch(ctrl,"summer","Summer Heating Off")]
    if ctrl.settings.get(CONF_HOT_WATER_SWITCH):
        values.append(HouseSwitch(ctrl,"hot_water","Hot Water Demand"))
    async_add_entities(values)

class HouseSwitch(HeatFlowEntity, SwitchEntity):
    _attr_should_poll=False
    def __init__(self,ctrl,attr,name):
        self.ctrl=ctrl
        self.attr=attr
        self._attr_name=name
        self._attr_unique_id=f"{ctrl.entry.entry_id}_{attr}"
        self._attr_icon={"away":"mdi:airplane", "summer":"mdi:weather-sunny", "hot_water":"mdi:water-boiler"}.get(attr, "mdi:radiator")
    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))
    @property
    def is_on(self):
        return getattr(self.ctrl,self.attr)
    async def async_turn_on(self,**kwargs):
        await self.ctrl.set_global(self.attr,True)
    async def async_turn_off(self,**kwargs):
        await self.ctrl.set_global(self.attr,False)
