from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


@pytest.mark.parametrize("at, rise, lead, hold, expected", [
    (360, 390, 45, 90, (True, False)),
    (300, 390, 45, 90, (False, False)),
    (390, 390, 45, 90, (False, True)),
    (470, 390, 45, 90, (False, True)),
    (480, 390, 45, 90, (False, False)),
    (1430, 10, 45, 90, (True, False)),
    (5, 10, 45, 90, (True, False)),
])
def test_window(controller_module, at, rise, lead, hold, expected):
    from radiator_orchestrator.linked_heating import morning_window
    v = morning_window(at, rise, lead, hold)
    assert (v.preheating, v.morning_hold) == expected


def test_outdoor_and_learning(controller_module):
    from radiator_orchestrator.linked_heating import preheat_allowance
    conf = {"linked_preheat_minutes": 45, "linked_preheat_max_minutes": 180,
            "linked_extra_minutes_per_degree": 3}
    assert preheat_allowance(conf, 17, 22, 5, {}) == (75, "outdoor_fallback")
    assert preheat_allowance(conf, 20, 22, 5, {"rate": 1, "outside": 6, "samples": 3}) == (130, "learned")
    assert preheat_allowance(conf, 17, 22, None, {}) == (45, "fixed_fallback")


@pytest.mark.asyncio
async def test_preheat_despite_undetected_sleepers(house):
    ha, settings, rooms, now, make = house
    ctrl = make()
    await ctrl.async_evaluate()
    bath = ctrl.rooms["bath"]
    assert bath.effective_target == 22
    assert bath.schedule_phase == "linked_morning_preheat"
    assert bath.demand
    assert bath.effective_primary
    assert bath.demand_reason == "linked_morning_preheat"
    assert ctrl.boiler_requested
    assert ha.states.get("switch.boiler").state == "on"
    assert not bath.linked_occupied


@pytest.mark.asyncio
async def test_multiple_bedrooms_any_one_qualifies(house):
    ha, settings, rooms, now, make = house
    rooms[-1]["linked_house_presence_fallback"] = False
    ha.states.set("binary_sensor.bed_b", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].linked_occupied == ("bed_b",)
    assert ctrl.rooms["bath"].demand


@pytest.mark.asyncio
async def test_no_bedroom_and_fallback_disabled(house):
    ha, settings, rooms, now, make = house
    rooms[-1]["linked_house_presence_fallback"] = False
    ctrl = make()
    await ctrl.async_evaluate()
    bath = ctrl.rooms["bath"]
    assert bath.effective_target == 8  # before schedule starts: night frost target
    assert not bath.demand
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_window_open_blocks_preheat(house):
    ha, settings, rooms, now, make = house
    ha.states.set("binary_sensor.bath_window", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].blocked_reason == "window_open"
    assert not ctrl.rooms["bath"].demand
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_workday_sensor_selects_weekend_rising(house):
    ha, settings, rooms, now, make = house
    ha.states.set("binary_sensor.workday", "off")
    now[0] = datetime(2026, 10, 18, 7, 15, tzinfo=ZoneInfo("Europe/London"))
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].demand_reason == "linked_morning_preheat"


@pytest.mark.asyncio
async def test_too_early_for_preheat(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 12, 4, 0, tzinfo=ZoneInfo("Europe/London"))
    ctrl = make()
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bath"].demand


@pytest.mark.asyncio
async def test_no_presence_no_preheat(house):
    ha, settings, rooms, now, make = house
    ha.states.set("binary_sensor.house", "off")
    settings["absence_delay_minutes"] = 0
    ctrl = make()
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bath"].demand


@pytest.mark.asyncio
async def test_summer_disables_morning_preheat(house):
    ha, settings, rooms, now, make = house
    ctrl = make()
    ctrl.summer = True
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bath"].demand


@pytest.mark.asyncio
async def test_away_disables_morning_preheat(house):
    ha, settings, rooms, now, make = house
    ctrl = make()
    ctrl.away = True
    await ctrl.async_evaluate()
    assert not ctrl.rooms["bath"].demand


@pytest.mark.asyncio
async def test_opportunistic_when_linked_bedroom_calls_evening(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 12, 18, 0, tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("sensor.bed_b", "17")  # linked bedroom now calls boiler
    ha.states.set("binary_sensor.bed_b", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    bath = ctrl.rooms["bath"]
    assert bath.linked_heating == ("bed_b",)
    assert bath.effective_target == 22
    assert bath.demand_reason == "opportunistic_linked_bedroom"
    assert bath.trv_target > 5
    assert not bath.effective_primary


@pytest.mark.asyncio
async def test_local_occupancy_explains_original_symptom(house):
    ha, settings, rooms, now, make = house
    rooms[-1]["linked_rooms"] = []
    now[0] = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    ctrl = make()
    await ctrl.async_evaluate()
    bath = ctrl.rooms["bath"]
    assert bath.effective_target == 16
    assert bath.target_reason == "room_unoccupied_setback"
    assert not bath.demand
    ha.states.set("binary_sensor.bath", "on")
    await ctrl.async_evaluate()
    assert bath.effective_target == 22
    assert bath.demand_reason == "room_occupied"
    # This room alone cannot start the boiler without a morning override.
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_linked_bedroom_cannot_bypass_bathroom_window(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 12, 18, 0, tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("sensor.bed_a", "17")
    ha.states.set("binary_sensor.bed_a", "on")
    ha.states.set("binary_sensor.bath_window", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].blocked_reason == "window_open"
    assert ctrl.rooms["bath"].trv_target == 5

@pytest.mark.asyncio
async def test_post_rise_hold_prevents_cold_bathroom(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    ctrl = make()
    await ctrl.async_evaluate()
    bath = ctrl.rooms["bath"]
    assert bath.effective_target == 22
    assert bath.demand_reason == "linked_morning_hold"
    assert bath.effective_primary


@pytest.mark.asyncio
async def test_bank_holiday_sensor_uses_weekend_morning(house):
    ha, settings, rooms, now, make = house
    ha.states.set("binary_sensor.workday", "off")
    now[0] = datetime(2026, 10, 12, 7, 15, tzinfo=ZoneInfo("Europe/London"))
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].schedule_phase == "linked_morning_preheat"


@pytest.mark.asyncio
async def test_bathroom_not_independent_without_permission(house):
    ha, settings, rooms, now, make = house
    rooms[-1]["linked_allow_morning_boiler"] = False
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].demand
    assert not ctrl.rooms["bath"].effective_primary
    assert ctrl.rooms["bath"].trv_target == 5
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_linked_morning_preheat_does_not_request_dhw(house):
    ha, settings, rooms, now, make = house
    settings["occupancy_hot_water"] = True
    rooms[-1]["occupancy_triggers_hot_water"] = True
    settings["hot_water_min_occupancy_seconds"] = 0
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].demand
    assert not ctrl.hot_water_requested
    assert ha.states.get("switch.dhw").state == "off"


@pytest.mark.asyncio
async def test_missing_temperature_blocks_bathroom(house):
    ha, settings, rooms, now, make = house
    ha.states.set("sensor.bath", "unavailable")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bath"].blocked_reason == "temperature_unavailable"
    assert not ctrl.rooms["bath"].demand
