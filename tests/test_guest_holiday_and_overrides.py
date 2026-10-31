"""Holiday return deliberately warms empty ordinary rooms, but not unused guest rooms."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


def holiday(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 10, 0, tzinfo=ZoneInfo("Europe/London"))
    settings.update(holiday_enable="input_boolean.holiday",
                    holiday_return="input_datetime.return",
                    holiday_return_hold_minutes=120,
                    preheat_base_minutes=150)
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-26 12:00:00")
    ha.states.set("binary_sensor.house", "off")
    settings["absence_delay_minutes"] = 0
    for name in ("bed_a", "bed_b", "bath"):
        ha.states.set(f"sensor.{name}", "12")
    return ha, settings, rooms, current, make


@pytest.mark.asyncio
async def test_only_guest_bedrooms_are_occupancy_gated(house):
    ha, settings, rooms, _, make = holiday(house)
    settings["holiday_guest_bedrooms"] = ["bed_b"]
    rooms.append(dict(name="kitchen", room_type="living", trv="climate.kitchen",
                      temperature="sensor.kitchen", occupancy="input_boolean.kitchen",
                      primary=True, target=21., unoccupied_target=16.,
                      kp=1.5, ki=0., kd=0.))
    ha.states.set("sensor.kitchen", "15")
    ha.states.set("climate.kitchen", "heat", temperature=5)
    ha.states.set("input_boolean.kitchen", "off")
    ctrl = make()
    ctrl.away = True
    await ctrl.async_evaluate()
    assert ctrl.holiday_return_preheating
    assert ctrl.rooms["bed_a"].effective_target == 19 # normal bedroom, despite unoccupied
    assert ctrl.rooms["bed_b"].effective_target == 8 # guest bedroom unavailable for return
    assert ctrl.rooms["bed_b"].target_reason == "holiday_return_guest_unoccupied"
    assert ctrl.rooms["kitchen"].effective_target == 21 # empty kitchen still warmed
    assert ctrl.rooms["kitchen"].demand
    assert ctrl.rooms["bath"].effective_target == 22 # linked normal bedroom qualifies
    ha.states.set("binary_sensor.bed_b", "on")
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_b"].effective_target == 19
    assert ctrl.rooms["bed_b"].holiday_return_authorisation == "guest_occupied"


@pytest.mark.asyncio
async def test_shared_bathroom_only_guest_bedrooms_restrict_it(house):
    ha, settings, rooms, _, make = holiday(house)
    settings["holiday_guest_bedrooms"] = ["bed_a", "bed_b"]
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].effective_target == 8
    assert ctrl.rooms["bath"].target_reason == "holiday_return_linked_guest_bedrooms_unoccupied"
    assert not ctrl.rooms["bath"].demand
    ha.states.set("binary_sensor.bed_b", "on")
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].effective_target == 22
    assert ctrl.rooms["bath"].demand
    assert ctrl.rooms["bed_a"].effective_target == 8
    ha.states.set("binary_sensor.bed_b", "unknown")
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_b"].holiday_return_authorisation == "guest_occupancy_unavailable"
    assert ctrl.rooms["bath"].effective_target == 8


@pytest.mark.asyncio
async def test_ordinary_day_manual_override_until_occupancy_change(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 18, 0, tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("sensor.bed_a", "18")
    ha.states.set("binary_sensor.bed_a", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].effective_target == 19
    await ctrl.set_target("bed_a", 22)
    assert ctrl.rooms["bed_a"].effective_target == 22
    assert ctrl.rooms["bed_a"].conf["target"] == 20
    assert ctrl.rooms["bed_a"].target_reason == "temporary_thermostat_override"
    ha.states.set("binary_sensor.bed_a", "off")
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].manual_override_target is None
    assert ctrl.rooms["bed_a"].effective_target < 22


@pytest.mark.asyncio
async def test_thermostat_override_expires_at_bedtime_and_not_saved(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 20, 0, tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("binary_sensor.bed_a", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    await ctrl.set_target("bed_a", 23)
    assert ctrl.rooms["bed_a"].effective_target == 23
    current[0] = datetime(2026, 10, 26, 22, 31, tzinfo=ZoneInfo("Europe/London"))
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].manual_override_target is None
    assert ctrl.rooms["bed_a"].effective_target == 8


@pytest.mark.asyncio
async def test_manual_mode_temperature_change_is_still_persistent(house):
    ha, settings, rooms, current, make = house
    settings["enable_schedule"] = False
    ctrl = make()
    await ctrl.set_target("bed_a", 21)
    assert ctrl.rooms["bed_a"].manual_override_target is None
    assert ctrl.rooms["bed_a"].conf["target"] == 21
