"""Regression: work-return comfort never indiscriminately warms guest rooms."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


def prepare(house, *, guests=("bed_b",), at="2026-11-03 16:00"):
    ha, settings, rooms, current, make = house
    current[0] = datetime.fromisoformat(at).replace(
        tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("binary_sensor.workday", "on")
    ha.states.set("binary_sensor.house", "off")
    settings["absence_delay_minutes"] = 0
    settings["holiday_guest_bedrooms"] = list(guests)
    settings["preheat_base_minutes"] = 90
    settings["preheat_extra_minutes_per_degree"] = 8
    settings["preheat_reference_c"] = 15
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "12")
        ha.states.set(f"binary_sensor.{name}", "off")
    ha.states.set("sensor.bath", "12")
    return ha, settings, rooms, current, make


@pytest.mark.asyncio
async def test_work_preheat_excludes_unoccupied_guest_but_not_normal_bedroom(house):
    ha, settings, rooms, current, make = prepare(house)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.work_preheating and ctrl.preheating
    assert not ctrl.house_present
    assert ctrl.rooms["bed_a"].effective_target == 19
    assert ctrl.rooms["bed_a"].target_reason == "work_return_preheat"
    guest = ctrl.rooms["bed_b"]
    assert guest.effective_target == 8
    assert guest.target_reason == "work_return_guest_unoccupied"
    assert guest.work_return_authorisation == "guest_unoccupied"
    assert not guest.demand
    assert guest.trv_target == 5
    assert ctrl.rooms["bath"].effective_target == 22
    assert ctrl.rooms["bath"].target_reason == "work_return_preheat"
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_work_return_guest_occupancy_on_allows_preheat(house):
    ha, settings, rooms, current, make = prepare(house)
    ha.states.set("binary_sensor.bed_b", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    guest = ctrl.rooms["bed_b"]
    assert guest.work_return_authorisation == "guest_occupied"
    assert guest.effective_target == 19
    assert guest.target_reason == "work_return_preheat"
    assert guest.demand


@pytest.mark.asyncio
async def test_work_return_guest_missing_and_unknown_state_fail_closed(house):
    ha, settings, rooms, current, make = prepare(house)
    rooms[1]["occupancy"] = "input_boolean.guest_room"
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_b"].work_return_authorisation == "guest_occupancy_unavailable"
    assert ctrl.rooms["bed_b"].effective_target == 8
    ha.states.set("input_boolean.guest_room", "unavailable")
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_b"].target_reason == "work_return_guest_occupancy_unavailable"
    rooms[1].pop("occupancy")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_b"].target_reason == "work_return_guest_occupancy_not_configured"


@pytest.mark.asyncio
async def test_exclusively_guest_linked_bathroom_excluded(house):
    ha, settings, rooms, current, make = prepare(house, guests=("bed_a", "bed_b"))
    ctrl = make()
    await ctrl.async_evaluate()
    bathroom = ctrl.rooms["bath"]
    assert bathroom.effective_target == 8
    assert bathroom.work_return_authorisation == "linked_guest_bedrooms_unoccupied"
    assert bathroom.target_reason == "work_return_linked_guest_bedrooms_unoccupied"
    assert not bathroom.demand
    ha.states.set("binary_sensor.bed_a", "on")
    await ctrl.async_evaluate()
    assert bathroom.effective_target == 22
    assert bathroom.target_reason == "work_return_preheat"
    assert ctrl.rooms["bed_b"].effective_target == 8


@pytest.mark.asyncio
async def test_normal_living_room_preheats_despite_pir_off(house):
    ha, settings, rooms, current, make = prepare(house)
    room = dict(
        name="kitchen", room_type="living", trv="climate.kitchen",
        temperature="sensor.kitchen", occupancy="binary_sensor.kitchen",
        window="binary_sensor.kitchen_window", primary=True,
        target=21.0, unoccupied_target=16.0, kp=1.5, ki=0., kd=0.,
    )
    rooms.append(room)
    ha.states.set("sensor.kitchen", "14")
    ha.states.set("binary_sensor.kitchen", "off")
    ha.states.set("binary_sensor.kitchen_window", "off")
    ha.states.set("climate.kitchen", "heat", temperature=5)
    ctrl = make()
    await ctrl.async_evaluate()
    k = ctrl.rooms["kitchen"]
    assert k.effective_target == 21
    assert k.target_reason == "work_return_preheat"
    assert k.work_return_authorisation == "unrestricted"
    assert k.demand


@pytest.mark.asyncio
async def test_after_return_ordinary_guest_setback_applies(house):
    ha, settings, rooms, current, make = prepare(house)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_b"].effective_target == 8
    ha.states.set("binary_sensor.house", "on")
    current[0] = datetime(2026, 11, 3, 17, 5, tzinfo=ZoneInfo("Europe/London"))
    await ctrl.async_evaluate()
    guest = ctrl.rooms["bed_b"]
    assert not ctrl.work_preheating
    assert guest.work_return_authorisation == "not_in_work_return_window"
    assert guest.target_reason == "room_unoccupied_setback"
    assert guest.effective_target == 16


@pytest.mark.asyncio
async def test_adaptive_return_lead_ignores_unused_guest_learning(house):
    ha, settings, rooms, current, make = prepare(house)
    settings["adaptive_preheat"] = True
    ctrl = make()
    # A slow-heating empty guest room should not push return preheat to the max.
    ctrl.learning["bed_a"] = {
        "samples": 3, "outside": 5, "rate": 3.0
    }
    ctrl.learning["bed_b"] = {
        "samples": 5, "outside": 5, "rate": 0.1
    }
    assert ctrl._preheat_minutes() == 155  # (19 - 12) / 3 * 60 + 15
    ha.states.set("binary_sensor.bed_b", "on")
    # With an occupied guest, both bedrooms can contribute.
    assert ctrl._preheat_minutes() == 360


@pytest.mark.asyncio
async def test_holiday_guest_behaviour_unchanged(house):
    ha, settings, rooms, current, make = prepare(house)
    settings.update(
        holiday_enable="input_boolean.holiday",
        holiday_return="input_datetime.holiday_return",
    )
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.holiday_return", "2026-11-03 17:00:00")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.holiday_return_preheating
    assert ctrl.rooms["bed_a"].effective_target == 19
    assert ctrl.rooms["bed_b"].effective_target == 8
    assert ctrl.rooms["bed_b"].target_reason == "holiday_return_guest_unoccupied"
