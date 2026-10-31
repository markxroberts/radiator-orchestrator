"""Adjustable holiday frost temperature."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from .const import DOMAIN,CONF_FROST
from .entity import HeatFlowEntity

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([FrostNumber(hass.data[DOMAIN][entry.entry_id])])

class FrostNumber(HeatFlowEntity, NumberEntity):
    _attr_should_poll=False
    _attr_native_min_value=4
    _attr_native_max_value=18
    _attr_native_step=0.5
    _attr_native_unit_of_measurement=UnitOfTemperature.CELSIUS
    _attr_mode=NumberMode.BOX
    _attr_icon="mdi:snowflake-thermometer"
    def __init__(self,ctrl):
        self.ctrl=ctrl
        self._attr_name="Holiday Minimum Temperature"
        self._attr_unique_id=f"{ctrl.entry.entry_id}_frost"
    async def async_added_to_hass(self):
        self.async_on_remove(self.ctrl.subscribe(self.async_write_ha_state))
    @property
    def native_value(self):
        return self.ctrl.settings[CONF_FROST]
    async def async_set_native_value(self, value):
        """Persist the setpoint in the config entry, not runtime-only storage.

        The options flow uses entry.data and entry.options as its source of
        truth. Previously the number entity only changed ctrl.settings, so
        reconfiguring or restarting reverted this number to its original 8 °C.
        Updating entry.options also triggers the integration's update listener.
        """
        value = float(value)
        self.ctrl.settings[CONF_FROST] = value
        await self.ctrl.async_evaluate()
        self.ctrl.hass.config_entries.async_update_entry(
            self.ctrl.entry,
            options={**self.ctrl.entry.options, CONF_FROST: value},
        )
