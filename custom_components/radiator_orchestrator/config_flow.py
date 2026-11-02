"""UI configuration: central settings and room management through options."""
from __future__ import annotations
from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .const import *


def central_schema(existing: dict | None = None):
    old = existing or {}
    return vol.Schema({
        vol.Required(CONF_HEAT_SWITCH, default=old.get(CONF_HEAT_SWITCH, "")): selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "input_boolean"])),
        vol.Optional(CONF_HOT_WATER_SWITCH, default=old.get(CONF_HOT_WATER_SWITCH, "")): selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "input_boolean"])),
        vol.Required(CONF_FROST, default=old.get(CONF_FROST, DEFAULT_FROST)): vol.All(vol.Coerce(float), vol.Range(min=4,max=18)),
        vol.Required(CONF_MIN_ON, default=old.get(CONF_MIN_ON, DEFAULT_MIN_ON)): vol.All(vol.Coerce(int),vol.Range(min=0,max=3600)),
        vol.Required(CONF_MIN_OFF, default=old.get(CONF_MIN_OFF, DEFAULT_MIN_OFF)): vol.All(vol.Coerce(int),vol.Range(min=0,max=3600)),
    })


def room_schema(existing: dict | None = None, hot_water_only: bool = False):
    old = existing or {}
    opt_entity = lambda domains: selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))
    if hot_water_only:
        return vol.Schema({
            vol.Required("name", default=old.get("name", "")): str,
            vol.Required("room_type", default="hot_water_only"): vol.In(["hot_water_only"]),
            vol.Required("occupancy", default=old.get("occupancy", "")): opt_entity(["binary_sensor", "input_boolean"]),
            vol.Required("occupancy_triggers_hot_water", default=old.get("occupancy_triggers_hot_water", True)): bool,
        })
    return vol.Schema({
        vol.Required("name", default=old.get("name", "")): str,
        vol.Required("trv", default=old.get("trv", "")): opt_entity(["climate"]),
        vol.Required("temperature", default=old.get("temperature", "")): opt_entity(["sensor"]),
        vol.Optional("window", description={"suggested_value": old.get("window")}) : opt_entity(["binary_sensor","input_boolean"]),
        vol.Optional("occupancy", description={"suggested_value": old.get("occupancy")}) : opt_entity(["binary_sensor","input_boolean"]),
        vol.Required("primary",default=old.get("primary",True)): bool,
        vol.Required("occupancy_triggers_hot_water",default=old.get("occupancy_triggers_hot_water",False)): bool,
        vol.Required("early_occupancy_heating", default=old.get(
            "early_occupancy_heating", old.get("room_type", "living") in ("living", "bathroom"))): bool,
        vol.Required("room_type", default=old.get("room_type", "living")): selector.SelectSelector(selector.SelectSelectorConfig(options=["living", "bedroom", "bathroom"])),
        vol.Required("target",default=old.get("target",20.0)): vol.All(vol.Coerce(float),vol.Range(min=5,max=30)),
        vol.Required("unoccupied_target",default=old.get("unoccupied_target",16.0)): vol.All(vol.Coerce(float),vol.Range(min=5,max=25)),
        vol.Required("kp",default=old.get("kp",1.5)): vol.All(vol.Coerce(float),vol.Range(min=0,max=20)),
        vol.Required("ki",default=old.get("ki",0.00015)): vol.All(vol.Coerce(float),vol.Range(min=0,max=0.1)),
        vol.Required("kd",default=old.get("kd",0.0)): vol.All(vol.Coerce(float),vol.Range(min=0,max=100)),
    })


SCHEDULE_TIMES = {
    "weekday_morning": "06:30", "weekday_work": "08:30",
    "weekday_evening": "17:00", "weekday_bed": "22:30",
    "weekend_rise": "08:00", "weekend_bed": "23:00",
    "early_start_time": "05:45",
    "early_occupancy_from": "04:00",
}
SCHEDULE_TARGETS = {
    "morning_target": 20.0, "evening_target": 21.0,
    "day_bedroom_target": 16.0, "bedroom_target": 19.0,
    "bathroom_morning_target": 22.0, "bathroom_evening_target": 22.0,
    "bathroom_day_target": 17.0,
}

def schedule_schema(existing=None):
    """Use existing helper entities; fall back to editable constants."""
    old = existing or {}
    tm = selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_datetime"]))
    num = selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_number"]))
    boolean = selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_boolean"]))
    schema = {}
    for key in SCHEDULE_TIMES:
        schema[vol.Optional(key, description={"suggested_value": old.get(key)})] = tm
    for key in SCHEDULE_TARGETS:
        schema[vol.Optional(key, description={"suggested_value": old.get(key)})] = num
    schema[vol.Optional("early_start_enable", description={"suggested_value":old.get("early_start_enable")})] = boolean
    schema[vol.Required("enable_schedule", default=old.get("enable_schedule", False))] = bool
    schema[vol.Required("night_frost_target", default=old.get("night_frost_target", 8.0))] = vol.All(vol.Coerce(float),vol.Range(min=4,max=18))
    return vol.Schema(schema)


def presence_schema(existing=None):
    old = existing or {}
    entity = lambda domains, multiple=False: selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domains, multiple=multiple))
    fields = {
        "house_occupancy": entity(["binary_sensor", "input_boolean"]),
        "people": entity(["person"], True),
        "guest_override": entity(["input_boolean", "binary_sensor"]),
        "holiday_enable": entity(["input_boolean"]),
        "holiday_return": entity(["input_datetime"]),
        "holiday_at_home": entity(["input_boolean"]),
        "outdoor_temperature": entity(["sensor"]),
        "workday": entity(["binary_sensor"]),
    }
    schema = {vol.Optional(k, description={"suggested_value": old.get(k)}): v
              for k, v in fields.items()}
    schema[vol.Required("preheat_base_minutes", default=old.get("preheat_base_minutes", 90))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=720))
    schema[vol.Required("preheat_extra_minutes_per_degree", default=old.get("preheat_extra_minutes_per_degree", 8))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=60))
    schema[vol.Required("preheat_reference_c", default=old.get("preheat_reference_c", 15))] = vol.All(vol.Coerce(float), vol.Range(min=-10,max=30))
    schema[vol.Required("max_preheat_minutes", default=old.get("max_preheat_minutes", 360))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=1440))
    schema[vol.Required("occupancy_hot_water", default=old.get("occupancy_hot_water", False))] = bool
    schema[vol.Required("absence_delay_minutes", default=old.get("absence_delay_minutes", 10))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=180))
    schema[vol.Required("hot_water_min_occupancy_seconds", default=old.get("hot_water_min_occupancy_seconds", 60))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=1800))
    schema[vol.Required("hot_water_max_run_minutes", default=old.get("hot_water_max_run_minutes", 0))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=1440))
    schema[vol.Required("adaptive_preheat", default=old.get("adaptive_preheat", False))] = bool
    schema[vol.Required("preheat_target_margin_minutes", default=old.get("preheat_target_margin_minutes", 15))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=120))
    schema[vol.Required("holiday_return_hold_minutes", default=old.get("holiday_return_hold_minutes", 120))] = vol.All(vol.Coerce(int), vol.Range(min=0,max=720))
    return vol.Schema(schema)



def ems_schema(existing=None):
    """Select actual EMS-ESP HA entities; never infer writable capabilities."""
    from .ems_monitor import EMS_ENABLED, EMS_FIELDS
    old = existing or {}
    entity = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "binary_sensor", "switch"]))
    schema = {vol.Required(EMS_ENABLED, default=old.get(EMS_ENABLED, False)): bool}
    for field in EMS_FIELDS:
        schema[vol.Optional(field, description={"suggested_value": old.get(field)})] = entity
    return vol.Schema(schema)

class RadiatorOrchestratorFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id("house")
            self._abort_if_unique_id_configured()
            user_input[CONF_ROOMS] = []
            return self.async_create_entry(title="HeatFlow",data=user_input)
        return self.async_show_form(step_id="user",data_schema=central_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return RadiatorOptionsFlow()


class RadiatorOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "add_room": "Add radiator room",
                "add_hot_water_room": "Add hot-water-only room",
                "edit_room": "Edit room",
                "remove_room": "Remove room",
                "central": "Boiler settings",
                "schedule": "Heating schedule and targets",
                "presence": "House occupancy, holidays and preheating",
                "linked_bathroom": "Link bathrooms to bedrooms",
                "morning_room": "Morning preheating by room",
                "holiday_guest_bedrooms": "Holiday return: guest bedrooms",
                "ems": "EMS-ESP monitoring (read-only)",
            },
        )

    @property
    def _settings(self):
        return {**self.config_entry.data, **self.config_entry.options}

    @property
    def _rooms(self):
        return list(self._settings.get(CONF_ROOMS, []))

    def _save(self, changes):
        return self.async_create_entry(data={**self.config_entry.options,**changes})

    async def async_step_add_room(self, user_input=None):
        if user_input is not None:
            rooms = self._rooms
            if any(x["name"].lower()==user_input["name"].lower() for x in rooms):
                return self.async_show_form(step_id="add_room",data_schema=room_schema(),errors={"base":"duplicate_room"})
            rooms.append(user_input)
            return self._save({CONF_ROOMS: rooms})
        return self.async_show_form(step_id="add_room",data_schema=room_schema())

    async def async_step_add_hot_water_room(self, user_input=None):
        if user_input is not None:
            rooms = self._rooms
            if any(x["name"].lower() == user_input["name"].lower() for x in rooms):
                return self.async_show_form(step_id="add_hot_water_room", data_schema=room_schema(hot_water_only=True), errors={"base": "duplicate_room"})
            rooms.append(user_input)
            return self._save({CONF_ROOMS: rooms})
        return self.async_show_form(step_id="add_hot_water_room", data_schema=room_schema(hot_water_only=True))

    async def async_step_edit_room(self,user_input=None):
        if user_input is not None:
            self._selected = int(user_input["index"])
            return await self.async_step_edit_details()
        rooms=self._rooms
        if not rooms:
            return self.async_abort(reason="no_rooms")
        return self.async_show_form(step_id="edit_room",data_schema=vol.Schema({vol.Required("index"): selector.SelectSelector(selector.SelectSelectorConfig(options=[selector.SelectOptionDict(value=str(i),label=r["name"]) for i,r in enumerate(rooms)]))}))

    async def async_step_edit_details(self,user_input=None):
        rooms=self._rooms
        if user_input is not None:
            preserved = {k: v for k, v in rooms[self._selected].items() if k.startswith("linked_") or k.startswith("morning_")}
            old_name = rooms[self._selected]["name"]
            rooms[self._selected] = {**preserved, **user_input}
            # Linked-bedroom references use room names; update references on rename.
            if old_name != user_input["name"]:
                for other in rooms:
                    if "linked_rooms" in other:
                        other["linked_rooms"] = [user_input["name"] if n == old_name else n for n in other["linked_rooms"]]
                guests = [user_input["name"] if n == old_name else n for n in
                          self._settings.get("holiday_guest_bedrooms", [])]
                return self._save({CONF_ROOMS: rooms, "holiday_guest_bedrooms": guests})
            return self._save({CONF_ROOMS:rooms})
        return self.async_show_form(step_id="edit_details",data_schema=room_schema(rooms[self._selected], rooms[self._selected].get("room_type") == "hot_water_only"))

    async def async_step_remove_room(self,user_input=None):
        rooms=self._rooms
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            removed = rooms.pop(int(user_input["index"]))["name"]
            for other in rooms:
                if "linked_rooms" in other:
                    other["linked_rooms"] = [n for n in other["linked_rooms"] if n != removed]
            guests = [n for n in self._settings.get("holiday_guest_bedrooms", []) if n != removed]
            return self._save({CONF_ROOMS: rooms, "holiday_guest_bedrooms": guests})
        return self.async_show_form(step_id="remove_room",data_schema=vol.Schema({vol.Required("index"):selector.SelectSelector(selector.SelectSelectorConfig(options=[selector.SelectOptionDict(value=str(i),label=r["name"]) for i,r in enumerate(rooms)]))}))

    async def async_step_linked_bathroom(self, user_input=None):
        """Select an existing bathroom to configure linked-bedroom behaviour."""
        bathrooms = [r for r in self._rooms if r.get("room_type") == "bathroom"]
        if not bathrooms:
            return self.async_abort(reason="no_bathrooms")
        if user_input is not None:
            self._selected_bathroom = user_input["name"]
            return await self.async_step_linked_bathroom_details()
        return self.async_show_form(step_id="linked_bathroom", data_schema=vol.Schema({
            vol.Required("name"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    selector.SelectOptionDict(value=r["name"], label=r["name"])
                    for r in bathrooms
                ]))
        }))

    async def async_step_linked_bathroom_details(self, user_input=None):
        rooms = self._rooms
        bathroom = next((r for r in rooms if r["name"] == self._selected_bathroom), None)
        if bathroom is None:
            return self.async_abort(reason="no_bathrooms")
        bedrooms = [r["name"] for r in rooms
                    if r.get("room_type") == "bedroom" and r["name"] != bathroom["name"]]
        if user_input is not None:
            names = user_input.get("linked_rooms", [])
            if not isinstance(names, list) or any(n not in bedrooms for n in names):
                return self.async_show_form(step_id="linked_bathroom_details",
                    data_schema=self._linked_bathroom_schema(bathroom, bedrooms),
                    errors={"base": "invalid_link"})
            # Keep unrelated room properties and existing room IDs unchanged.
            bathroom.update(user_input)
            bathroom["linked_rooms"] = names
            return self._save({CONF_ROOMS: rooms})
        return self.async_show_form(step_id="linked_bathroom_details",
            data_schema=self._linked_bathroom_schema(bathroom, bedrooms))

    @staticmethod
    def _linked_bathroom_schema(room, bedrooms):
        return vol.Schema({
            vol.Optional("linked_rooms", description={"suggested_value": room.get("linked_rooms", [])}):
                selector.SelectSelector(selector.SelectSelectorConfig(options=bedrooms, multiple=True)),
            vol.Required("linked_morning_preheat", default=room.get("linked_morning_preheat", False)): bool,
            vol.Required("linked_allow_morning_boiler", default=room.get("linked_allow_morning_boiler", False)): bool,
            vol.Required("linked_opportunistic", default=room.get("linked_opportunistic", True)): bool,
            vol.Required("linked_house_presence_fallback", default=room.get("linked_house_presence_fallback", True)): bool,
            vol.Required("linked_preheat_minutes", default=room.get("linked_preheat_minutes", 45)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
            vol.Required("linked_preheat_max_minutes", default=room.get("linked_preheat_max_minutes", 180)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
            vol.Required("linked_extra_minutes_per_degree", default=room.get("linked_extra_minutes_per_degree", 3)):
                vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
            vol.Required("linked_morning_hold_minutes", default=room.get("linked_morning_hold_minutes", 90)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
        })

    async def async_step_morning_room(self, user_input=None):
        """Choose any heated room for individually controlled morning warming."""
        rooms = [r for r in self._rooms if r.get("room_type") != "hot_water_only"]
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._morning_room_name = user_input["name"]
            return await self.async_step_morning_room_details()
        return self.async_show_form(step_id="morning_room", data_schema=vol.Schema({
            vol.Required("name"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    selector.SelectOptionDict(value=r["name"], label=r["name"])
                    for r in rooms
                ]))
        }))

    async def async_step_morning_room_details(self, user_input=None):
        rooms = self._rooms
        room = next((r for r in rooms if r.get("name") == self._morning_room_name), None)
        if room is None or room.get("room_type") == "hot_water_only":
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            room.update(user_input)
            return self._save({CONF_ROOMS: rooms})
        return self.async_show_form(
            step_id="morning_room_details",
            data_schema=self._morning_room_schema(room),
        )

    @staticmethod
    def _morning_room_schema(room):
        return vol.Schema({
            vol.Required("morning_preheat", default=room.get("morning_preheat", False)): bool,
            vol.Required("morning_allow_boiler", default=room.get("morning_allow_boiler", False)): bool,
            vol.Required("morning_house_presence_fallback", default=room.get("morning_house_presence_fallback", True)): bool,
            vol.Required("morning_preheat_minutes", default=room.get("morning_preheat_minutes", 45)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
            vol.Required("morning_preheat_max_minutes", default=room.get("morning_preheat_max_minutes", 180)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
            vol.Required("morning_extra_minutes_per_degree", default=room.get("morning_extra_minutes_per_degree", 3)):
                vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
            vol.Required("morning_hold_minutes", default=room.get("morning_hold_minutes", 90)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
        })

    async def async_step_holiday_guest_bedrooms(self, user_input=None):
        """Mark guest rooms that must prove occupancy before return warming."""
        bedrooms = [room["name"] for room in self._rooms
                    if room.get("room_type") == "bedroom"]
        if not bedrooms:
            return self.async_abort(reason="no_bedrooms")
        if user_input is not None:
            selected = user_input.get("holiday_guest_bedrooms", [])
            if not isinstance(selected, list) or any(name not in bedrooms for name in selected):
                return self.async_show_form(step_id="holiday_guest_bedrooms",
                                            data_schema=self._holiday_guest_schema(bedrooms),
                                            errors={"base": "invalid_guest_bedrooms"})
            return self._save({"holiday_guest_bedrooms": selected})
        return self.async_show_form(step_id="holiday_guest_bedrooms",
                                    data_schema=self._holiday_guest_schema(bedrooms))

    def _holiday_guest_schema(self, bedrooms):
        selected = [n for n in self._settings.get("holiday_guest_bedrooms", []) if n in bedrooms]
        return vol.Schema({
            vol.Optional("holiday_guest_bedrooms", description={"suggested_value": selected}):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=n, label=n) for n in bedrooms],
                    multiple=True)),
        })

    async def async_step_central(self,user_input=None):
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(step_id="central",data_schema=central_schema(self._settings))

    async def async_step_schedule(self,user_input=None):
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(step_id="schedule", data_schema=schedule_schema(self._settings))

    async def async_step_presence(self, user_input=None):
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(step_id="presence", data_schema=presence_schema(self._settings))

    async def async_step_ems(self, user_input=None):
        from .ems_monitor import EMS_FIELDS
        if user_input is not None:
            # Empty fields explicitly clear old mappings, instead of persisting
            # previously selected sensors through the options merge.
            changes = {field: user_input.get(field) for field in EMS_FIELDS}
            changes["ems_monitor_enabled"] = user_input.get("ems_monitor_enabled", False)
            return self._save(changes)
        return self.async_show_form(step_id="ems", data_schema=ems_schema(self._settings))
