"""HeatFlow: radiator-led central heating with independent DHW."""
from __future__ import annotations
import logging
from datetime import timedelta
from homeassistant.const import Platform
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from .const import DOMAIN, CONF_ROOMS, CONF_HEAT_SWITCH, CONF_HOT_WATER_SWITCH
from .controller import HeatingController
from .ems_monitor import EMS_ENABLED, EMS_FIELDS

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.NUMBER, Platform.BINARY_SENSOR, Platform.SENSOR]

async def async_setup_entry(hass, entry):
    settings = {**entry.data, **entry.options}
    controller = HeatingController(hass, entry, settings)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    await controller.async_initialize()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    watch = set()
    for room in settings.get(CONF_ROOMS, []):
        watch.update(v for v in (room.get("temperature"), room.get("occupancy"), room.get("window")) if v)
    for key in ("weekday_morning", "weekday_work", "weekday_evening", "weekday_bed", "weekend_rise", "weekend_bed", "early_start_time", "early_occupancy_from", "early_start_enable", "morning_target", "evening_target", "bedroom_target", "day_bedroom_target", "bathroom_morning_target", "bathroom_evening_target", "bathroom_day_target"):
        if settings.get(key):
            watch.add(settings[key])
    for key in ("house_occupancy", "guest_override", "holiday_enable", "holiday_return", "holiday_at_home", "outdoor_temperature", "workday"):
        if settings.get(key):
            watch.add(settings[key])
    watch.update(settings.get("people") or [])
    if settings.get(EMS_ENABLED):
        watch.update(settings[key] for key in EMS_FIELDS if settings.get(key))
    if settings.get(CONF_HEAT_SWITCH):
        watch.add(settings[CONF_HEAT_SWITCH])
    if settings.get(CONF_HOT_WATER_SWITCH):
        watch.add(settings[CONF_HOT_WATER_SWITCH])
    unsub = async_track_state_change_event(hass, list(watch), controller.async_event) if watch else lambda: None
    timer = async_track_time_interval(hass, controller.async_tick, timedelta(seconds=60))
    entry.async_on_unload(unsub)
    entry.async_on_unload(timer)
    entry.async_on_unload(entry.add_update_listener(async_reload))
    await controller.async_evaluate()
    return True

async def async_reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        controller = hass.data[DOMAIN].pop(entry.entry_id, None)
        if controller:
            await controller.async_flush_history()
        return True
    return False
