"""Regressions for morning preheat room selection and boolean-trigger semantics."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


@pytest.mark.parametrize("state,expected,reason", [
    ("on", True, "input_boolean_on"),
    ("off", False, "input_boolean_off"),
    ("unavailable", False, "input_boolean_unavailable"),
    (None, False, "input_boolean_unavailable"),
])
def test_input_boolean_no_house_fallback(controller_module, state, expected, reason):
    from radiator_orchestrator.linked_heating import preheat_authorisation
    assert preheat_authorisation("input_boolean.bedroom_occupied", state, True, True) == (expected, reason)


def test_no_house_presence_always_blocks(controller_module):
    from radiator_orchestrator.linked_heating import preheat_authorisation
    assert preheat_authorisation("input_boolean.bedroom_occupied", "on", False, True) == (False, "house_absent")


def test_pir_allowed_house_fallback(controller_module):
    from radiator_orchestrator.linked_heating import preheat_authorisation
    assert preheat_authorisation("binary_sensor.bedroom_pir", "off", True, True) == (True, "house_presence_fallback")
    assert preheat_authorisation("binary_sensor.bedroom_pir", "off", True, False) == (False, "no_room_presence")


@pytest.mark.asyncio
async def test_bedroom_boolean_on_preheats_before_rise(house):
    ha, settings, rooms, now, make = house
    rooms[0].update(occupancy="input_boolean.bed_a", morning_preheat=True,
                    morning_allow_boiler=True)
    ha.states.set("input_boolean.bed_a", "on")
    ha.states.set("sensor.bed_a", "16")
    ctrl = make()
    await ctrl.async_evaluate()
    room = ctrl.rooms["bed_a"]
    assert room.demand and room.effective_primary
    assert room.effective_target == 19
    assert room.target_reason == "morning_room_preheat"
    assert room.morning_preheat_trigger == "input_boolean_on"
    assert room.morning_preheat_status == "active_preheat"
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_bedroom_boolean_off_overrides_house_presence(house):
    ha, settings, rooms, now, make = house
    rooms[0].update(occupancy="input_boolean.bed_a", morning_preheat=True,
                    morning_allow_boiler=True, morning_house_presence_fallback=True)
    ha.states.set("input_boolean.bed_a", "off")
    ha.states.set("sensor.bed_a", "16")
    ctrl = make()
    await ctrl.async_evaluate()
    room = ctrl.rooms["bed_a"]
    assert not room.demand
    assert room.effective_target == 8
    assert room.morning_preheat_trigger == "input_boolean_off"
    assert room.morning_preheat_status == "not_authorised"


@pytest.mark.asyncio
async def test_boolean_turning_off_stops_morning_preheat(house):
    ha, settings, rooms, now, make = house
    rooms[0].update(occupancy="input_boolean.bed_a", morning_preheat=True,
                    morning_allow_boiler=True)
    ha.states.set("input_boolean.bed_a", "on")
    ha.states.set("sensor.bed_a", "16")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].demand
    ha.states.set("input_boolean.bed_a", "off")
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bed_a"].demand
    assert ctrl.rooms["bed_a"].morning_preheat_trigger == "input_boolean_off"


@pytest.mark.asyncio
async def test_living_room_pir_off_can_preheat_with_house_presence(house):
    ha, settings, rooms, now, make = house
    living = dict(rooms[0], name="lounge", room_type="living",
                  trv="climate.lounge", temperature="sensor.lounge",
                  occupancy="binary_sensor.lounge", window="binary_sensor.lounge_window",
                  morning_preheat=True, morning_allow_boiler=True)
    rooms.append(living)
    ha.states.set("sensor.lounge", "16")
    ha.states.set("binary_sensor.lounge", "off")
    ha.states.set("binary_sensor.lounge_window", "off")
    ha.states.set("climate.lounge", "heat", temperature=5)
    ctrl = make()
    await ctrl.async_evaluate()
    room = ctrl.rooms["lounge"]
    assert room.demand
    assert room.effective_target == 20
    assert room.target_reason == "morning_room_preheat"
    assert room.morning_preheat_trigger == "house_presence_fallback"


@pytest.mark.asyncio
async def test_pir_fallback_can_be_disabled(house):
    ha, settings, rooms, now, make = house
    rooms[0].update(morning_preheat=True, morning_allow_boiler=True,
                    morning_house_presence_fallback=False)
    ha.states.set("sensor.bed_a", "16")
    ctrl = make()
    await ctrl.async_evaluate()
    room = ctrl.rooms["bed_a"]
    assert not room.demand
    assert room.morning_preheat_trigger == "no_room_presence"


@pytest.mark.asyncio
async def test_shared_bathroom_linked_boolean_cannot_be_bypassed(house):
    ha, settings, rooms, now, make = house
    rooms[0]["occupancy"] = "input_boolean.bed_a"
    rooms[1]["occupancy"] = "input_boolean.bed_b"
    ha.states.set("input_boolean.bed_a", "off")
    ha.states.set("input_boolean.bed_b", "off")
    ctrl = make()
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bath"].demand
    ha.states.set("input_boolean.bed_b", "on")
    await ctrl.async_evaluate()
    bath = ctrl.rooms["bath"]
    assert bath.demand
    assert bath.linked_occupied == ("bed_b",)
    assert bath.target_reason == "linked_morning_preheat"


@pytest.mark.asyncio
async def test_room_preheat_disallowed_while_away_or_summer(house):
    ha, settings, rooms, now, make = house
    rooms[0].update(occupancy="input_boolean.bed_a", morning_preheat=True,
                    morning_allow_boiler=True)
    ha.states.set("input_boolean.bed_a", "on")
    ha.states.set("sensor.bed_a", "16")
    ctrl = make()
    ctrl.away = True
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bed_a"].demand
    assert ctrl.rooms["bed_a"].morning_preheat_status == "heating_inhibited"
    ctrl.away = False
    ctrl.summer = True
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bed_a"].demand


@pytest.mark.asyncio
async def test_workday_weekend_rise_time_applies_to_selected_living_room(house):
    ha, settings, rooms, now, make = house
    rooms[0].update(room_type="living", occupancy="input_boolean.bed_a",
                    morning_preheat=True, morning_allow_boiler=True)
    ha.states.set("input_boolean.bed_a", "on")
    ha.states.set("sensor.bed_a", "16")
    ha.states.set("binary_sensor.workday", "off")
    now[0] = datetime(2026, 10, 18, 7, 15, tzinfo=ZoneInfo("Europe/London"))
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].target_reason == "morning_room_preheat"
    assert ctrl.rooms["bed_a"].effective_target == 20
