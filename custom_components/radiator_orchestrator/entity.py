"""Common HeatFlow device registration; stable IDs across the rename."""
from homeassistant.helpers.entity import Entity

class HeatFlowEntity(Entity):
    _attr_has_entity_name = True
    @property
    def device_info(self):
        return {
            "identifiers": {("radiator_orchestrator", self.ctrl.entry.entry_id)},
            "name": "HeatFlow Controller",
            "manufacturer": "HeatFlow",
            "model": "Central Heating & Hot Water Controller",
            "sw_version": "0.7.12",
        }
