"""Regression tests: evening bedrooms warm before use; guest bedrooms require occupancy."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


def evening(house, at="2026-11-02 18:30", guest=False, *, primary=False):
    ha, settings, rooms, current, make = house
    current[0] = datetime.fromisoformat(at).replace(tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("sensor.bed_a", "16")
    rooms[0].update(
        primary=primary, evening_preheat=True, evening_allow_boiler=True,
        evening_ready_time="20:00:00",
        evening_preheat_minutes=90,
        evening_preheat_max_minutes=240,
        evening_extra_minutes_per_degree=4,
    )
    if guest:
        settings["holiday_guest_bedrooms"] = ["bed_a"]
        rooms[0]["occupancy"] = "input_boolean.bed_a"
        ha.states.set("input_boolean.bed_a", "off")
    return ha, settings, rooms, current, make


@pytest.mark.asyncio
async def test_normal_unoccupied_bedroom_warms_before_evening(house):
    ha, settings, rooms, now, make = evening(house)
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.effective_target == 19
    assert r.target_reason == "evening_bedroom_preheat"
    assert r.evening_preheat_status == "active_preheat"
    assert r.evening_preheat_trigger == "house_present_non_guest"
    assert r.evening_preheat_minutes == 130
    assert r.demand
    assert r.effective_primary
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_preheat_starts_only_in_lead_window_and_holds_until_bedtime(house):
    ha, settings, rooms, now, make = evening(house, "2026-11-02 16:00")
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.evening_preheat_status == "outside_evening_window"
    assert r.target_reason == "room_unoccupied_setback"
    assert r.effective_target == 16
    now[0] = datetime(2026, 11, 2, 20, 30, tzinfo=ZoneInfo("Europe/London"))
    await ctrl.async_evaluate()
    assert r.evening_preheat_status == "active_comfort_hold"
    assert r.effective_target == 19
    assert r.target_reason == "evening_bedroom_comfort_hold"
    now[0] = datetime(2026, 11, 2, 22, 31, tzinfo=ZoneInfo("Europe/London"))
    await ctrl.async_evaluate()
    assert r.effective_target == 8
    assert r.evening_preheat_status == "outside_evening_window"


@pytest.mark.asyncio
async def test_guest_bedroom_off_and_unavailable_never_preheats(house):
    ha, settings, rooms, now, make = evening(house, guest=True)
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.evening_preheat_status == "guest_unoccupied"
    assert r.effective_target == 16
    assert not r.demand
    ha.states.set("input_boolean.bed_a", "unavailable")
    await ctrl.async_evaluate()
    assert r.evening_preheat_status == "guest_occupancy_unavailable"
    assert r.effective_target == 16
    ha.states.set("input_boolean.bed_a", "on")
    await ctrl.async_evaluate()
    assert r.evening_preheat_trigger == "guest_room_occupied"
    assert r.effective_target == 19
    assert r.demand


@pytest.mark.asyncio
async def test_guest_needs_occupancy_helper_unless_explicitly_disabled(house):
    ha, settings, rooms, now, make = evening(house, guest=True)
    rooms[0].pop("occupancy")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].evening_preheat_status == "guest_occupancy_not_configured"
    rooms[0]["evening_guest_requires_occupancy"] = False
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].target_reason == "evening_bedroom_preheat"


@pytest.mark.asyncio
async def test_non_guest_motion_sensor_off_does_not_block_evening(house):
    ha, settings, rooms, now, make = evening(house)
    ha.states.set("binary_sensor.bed_a", "off")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].effective_target == 19
    assert ctrl.rooms["bed_a"].demand


@pytest.mark.asyncio
async def test_away_and_summer_block_evening_preheat(house):
    ha, settings, rooms, now, make = evening(house)
    ctrl = make()
    ctrl.away = True
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].effective_target == 8
    assert ctrl.rooms["bed_a"].evening_preheat_status == "heating_inhibited"
    ctrl.away = False
    ctrl.summer = True
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].evening_preheat_status == "heating_inhibited"
    assert not ctrl.rooms["bed_a"].demand


@pytest.mark.asyncio
async def test_house_absent_does_not_preheat(house):
    ha, settings, rooms, now, make = evening(house)
    settings["absence_delay_minutes"] = 0
    ha.states.set("binary_sensor.house", "off")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].evening_preheat_status == "house_absent"
    assert ctrl.rooms["bed_a"].effective_target == 8


@pytest.mark.asyncio
async def test_window_open_protection(house):
    ha, settings, rooms, now, make = evening(house)
    ha.states.set("binary_sensor.bed_a_window", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].evening_preheat_status == "window_open"
    assert ctrl.rooms["bed_a"].blocked_reason == "window_open"
    assert not ctrl.rooms["bed_a"].demand


@pytest.mark.asyncio
async def test_opted_out_guest_uses_original_setback(house):
    ha, settings, rooms, now, make = evening(house, guest=True)
    rooms[0]["evening_preheat"] = False
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].effective_target == 16
    assert ctrl.rooms["bed_a"].evening_preheat_status == "not_configured"


@pytest.mark.asyncio
async def test_secondary_bedroom_without_boiler_permission_does_not_start(house):
    ha, settings, rooms, now, make = evening(house)
    rooms[0]["evening_allow_boiler"] = False
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.demand
    assert not r.effective_primary
    assert not ctrl.boiler_requested
    assert r.trv_target == 5


@pytest.mark.asyncio
async def test_weekend_bedtime_and_ready_time(house):
    ha, settings, rooms, now, make = evening(house, "2026-11-07 22:45")
    ha.states.set("binary_sensor.workday", "off")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].evening_preheat_status == "active_comfort_hold"
    now[0] = datetime(2026, 11, 7, 23, 1, tzinfo=ZoneInfo("Europe/London"))
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].effective_target == 8


@pytest.mark.asyncio
async def test_manual_thermostat_override_still_wins_evening(house):
    ha, settings, rooms, now, make = evening(house, "2026-11-02 20:30")
    ctrl = make()
    await ctrl.async_evaluate()
    await ctrl.set_target("bed_a", 22)
    assert ctrl.rooms["bed_a"].target_reason == "temporary_thermostat_override"
    assert ctrl.rooms["bed_a"].effective_target == 22


def test_evening_window_is_not_recurring_morning():
    from pathlib import Path
    import importlib.util
    module_path = (Path(__file__).resolve().parents[1] /
                   "custom_components/radiator_orchestrator/linked_heating.py")
    spec = importlib.util.spec_from_file_location("evening_window_isolated", module_path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    evening_window = mod.evening_window
    assert not evening_window(7*60, 20*60, 23*60, 150).preheating
    assert evening_window(18*60, 20*60, 23*60, 150).preheating
    assert evening_window(20*60+10, 20*60, 23*60, 150).holding
    assert not evening_window(23*60+10, 20*60, 23*60, 150).holding
    assert evening_window(23*60+40, 20*60, 30, 150).holding
    assert evening_window(20, 20*60, 30, 150).holding
    assert not evening_window(40, 20*60, 30, 150).holding
