"""Regression: remote room overrides work while away, and failed preheat is explained."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


def configure(house, at="2026-11-04 16:40"):
    ha, settings, rooms, now, make = house
    now[0] = datetime.fromisoformat(at).replace(tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("binary_sensor.house", "off")
    ha.states.set("binary_sensor.workday", "on")
    settings["absence_delay_minutes"] = 0
    settings["preheat_base_minutes"] = 90
    return ha, settings, rooms, now, make


@pytest.mark.asyncio
async def test_remote_override_works_even_when_house_empty(house):
    ha, settings, rooms, current, make = configure(house, "2026-11-04 12:00")
    ha.states.set("sensor.bed_a", "15")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.work_return_status == "before_preheat_window"
    assert ctrl.rooms["bed_a"].effective_target == 8
    assert not ctrl.house_present
    await ctrl.set_target("bed_a", 21)
    r = ctrl.rooms["bed_a"]
    assert r.target_reason == "temporary_thermostat_override"
    assert r.effective_target == 21
    assert r.demand
    assert ctrl.boiler_requested
    assert r.trv_target > 5


@pytest.mark.asyncio
async def test_manual_override_is_cleared_at_bedtime(house):
    ha, settings, rooms, now, make = configure(house, "2026-11-04 19:00")
    ctrl = make()
    await ctrl.set_target("bed_a", 21)
    assert ctrl.rooms["bed_a"].effective_target == 21
    now[0] = datetime(2026, 11, 4, 22, 31, tzinfo=ZoneInfo("Europe/London"))
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].manual_override_target is None
    assert ctrl.rooms["bed_a"].effective_target == 8


@pytest.mark.asyncio
async def test_override_still_respects_away_and_summer(house):
    ha, settings, rooms, now, make = configure(house, "2026-11-04 12:00")
    ctrl = make()
    ctrl.away = True
    await ctrl.set_target("bed_a", 21)
    assert ctrl.rooms["bed_a"].effective_target == 8
    assert ctrl.rooms["bed_a"].target_reason != "temporary_thermostat_override"
    ctrl.away = False
    ctrl.summer = True
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].target_reason != "temporary_thermostat_override"


@pytest.mark.asyncio
async def test_window_open_and_room_off_still_block(house):
    ha, settings, rooms, now, make = configure(house, "2026-11-04 12:00")
    ha.states.set("binary_sensor.bed_a_window", "on")
    ctrl = make()
    await ctrl.set_target("bed_a", 21)
    assert ctrl.rooms["bed_a"].blocked_reason == "window_open"
    assert not ctrl.rooms["bed_a"].demand
    await ctrl.set_mode("bed_a", "off")
    assert not ctrl.rooms["bed_a"].demand


@pytest.mark.asyncio
async def test_secondary_room_override_needs_primary_demand(house):
    ha, settings, rooms, now, make = configure(house, "2026-11-04 12:00")
    rooms[0]["primary"] = False
    ctrl = make()
    await ctrl.set_target("bed_a", 21)
    assert ctrl.rooms["bed_a"].demand
    assert not ctrl.boiler_requested
    assert ctrl.rooms["bed_a"].trv_target == 5


@pytest.mark.asyncio
async def test_work_return_active_in_window(house):
    ha, settings, rooms, now, make = configure(house)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.work_return_status == "active"
    assert ctrl.work_preheating
    assert ctrl.preheating


@pytest.mark.asyncio
async def test_work_return_reports_house_present(house):
    ha, settings, rooms, now, make = configure(house)
    ha.states.set("binary_sensor.house", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.work_return_status == "house_present"
    assert not ctrl.work_preheating


@pytest.mark.asyncio
async def test_work_return_reports_away_mode(house):
    ha, settings, rooms, now, make = configure(house)
    ctrl = make()
    ctrl.away = True
    await ctrl.async_evaluate()
    assert ctrl.work_return_status == "away_mode_enabled"
    assert not ctrl.work_preheating


@pytest.mark.asyncio
async def test_work_return_reports_workday_unavailable(house):
    ha, settings, rooms, now, make = configure(house)
    ha.states.set("binary_sensor.workday", "unavailable")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.work_return_status == "workday_unavailable"


@pytest.mark.asyncio
async def test_work_return_reports_after_return_time(house):
    ha, settings, rooms, now, make = configure(house, "2026-11-04 17:05")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.work_return_status == "after_return_time"
    assert not ctrl.work_preheating
