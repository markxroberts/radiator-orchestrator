"""Living-room links use actual occupancy, preserving bedroom policy and outputs."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


def living_house(house):
    ha, settings, rooms, now, make = house
    rooms[0]["room_type"] = "living"
    rooms[-1].update(linked_rooms=["bed_a"], linked_morning_preheat=False)
    now[0] = datetime(2026, 10, 12, 18, tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("sensor.bed_a", "24")  # occupied but already warm
    ha.states.set("binary_sensor.bed_a", "on")
    return ha, settings, rooms, now, make


@pytest.mark.asyncio
async def test_warm_occupied_living_room_qualifies_without_starting_boiler(house):
    ha, _, _, _, make = living_house(house)
    ctrl = make()
    await ctrl.async_evaluate()
    bath = ctrl.rooms["bath"]
    assert bath.effective_target == 22
    assert bath.target_reason == "linked_living_room_occupied"
    assert bath.linked_occupied == ("bed_a",)
    assert not bath.linked_heating
    assert bath.demand and not bath.effective_primary
    assert not ctrl.boiler_requested
    assert bath.trv_target == 5
    ha.states.set("sensor.bed_b", "17")
    ha.states.set("binary_sensor.bed_b", "on")
    await ctrl.async_evaluate()
    assert ctrl.boiler_requested and bath.trv_target > 5
    assert not ctrl.hot_water_requested
    ha.states.set("binary_sensor.bed_a", "off")
    await ctrl.async_evaluate()
    assert bath.effective_target == 16
    assert not bath.linked_occupied


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["off", "unknown", "unavailable", None])
async def test_living_requires_actual_on_even_when_demanding(house, state):
    ha, _, rooms, _, make = living_house(house)
    ha.states.set("sensor.bed_a", "10")
    if state is None:
        ha.states.items.pop("binary_sensor.bed_a")
    else:
        ha.states.set("binary_sensor.bed_a", state)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].demand
    assert ctrl.rooms["bath"].effective_target == 16


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["night", "away", "summer", "absent", "window", "off", "disabled", "temperature"])
async def test_living_link_respects_gates(house, gate):
    ha, settings, rooms, now, make = living_house(house)
    rooms[-1]["primary"] = True
    if gate == "night": now[0] = now[0].replace(hour=23)
    if gate == "absent":
        settings["absence_delay_minutes"] = 0
        ha.states.set("binary_sensor.house", "off")
    if gate == "window": ha.states.set("binary_sensor.bath_window", "on")
    if gate == "temperature": ha.states.set("sensor.bath", "unavailable")
    if gate == "disabled": rooms[-1]["linked_opportunistic"] = False
    ctrl = make()
    if gate in ("away", "summer"): setattr(ctrl, gate, True)
    if gate == "off": ctrl.rooms["bath"].mode = "off"
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bath"].demand
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_multiple_mixed_links_and_room_order(house):
    ha, _, rooms, _, make = living_house(house)
    rooms[-1]["linked_rooms"] = ["missing", "bath", "bed_a", "bed_b", "bed_a"]
    rooms.reverse()
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].linked_occupied == ("bed_a",)
    assert ctrl.rooms["bath"].effective_target == 22
    ha.states.set("binary_sensor.bed_a", "off")
    ha.states.set("binary_sensor.bed_b", "on")
    ha.states.set("sensor.bed_b", "17")
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].target_reason == "opportunistic_linked_bedroom"


@pytest.mark.asyncio
@pytest.mark.parametrize("occupied, expected", [("on", True), ("off", False), ("unavailable", False)])
async def test_living_only_morning_needs_occupancy_not_house_fallback(house, occupied, expected):
    ha, _, rooms, now, make = living_house(house)
    rooms[-1]["linked_morning_preheat"] = True
    now[0] = now[0].replace(hour=6)
    ha.states.set("binary_sensor.bed_a", occupied)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].demand == expected
    assert ctrl.boiler_requested == expected


@pytest.mark.asyncio
async def test_living_link_does_not_change_guest_return_restrictions(house):
    _, settings, rooms, _, make = living_house(house)
    settings["holiday_guest_bedrooms"] = ["bed_b"]
    rooms[-1]["linked_rooms"] = ["bed_a", "bed_b"]
    ctrl = make()
    assert ctrl._return_preheat_room_authorisation(ctrl.rooms["bath"]) == (True, "linked_room_qualifies")
    ctrl.rooms["bath"].conf["linked_rooms"] = ["bed_b"]
    assert ctrl._return_preheat_room_authorisation(ctrl.rooms["bath"]) == (False, "linked_guest_bedrooms_unoccupied")


@pytest.mark.asyncio
async def test_living_link_respects_temporary_bathroom_target(house):
    _, _, _, _, make = living_house(house)
    ctrl = make()
    ctrl.rooms["bath"].manual_override_target = 18
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].effective_target == 18
    assert ctrl.rooms["bath"].target_reason == "temporary_thermostat_override"
