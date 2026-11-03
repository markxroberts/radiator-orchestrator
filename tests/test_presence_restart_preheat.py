"""Regression: already-away state must not start a fresh absence grace on reload."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


def workday(house, *, when="2026-11-03 16:00"):
    ha, settings, rooms, current, make = house
    current[0] = datetime.fromisoformat(when).replace(
        tzinfo=ZoneInfo("Europe/London"))
    ha.states.set("binary_sensor.workday", "on")
    settings["absence_delay_minutes"] = 10
    settings["preheat_base_minutes"] = 90
    settings["preheat_extra_minutes_per_degree"] = 8
    settings["preheat_reference_c"] = 15
    ha.states.set("sensor.bed_a", "12")
    ha.states.set("sensor.bed_b", "12")
    return ha, settings, rooms, current, make


@pytest.mark.asyncio
async def test_restart_during_work_preheat_resumes_immediately(house):
    ha, settings, rooms, current, make = workday(house)
    ha.states.set("binary_sensor.house", "off")
    settings["holiday_guest_bedrooms"] = ["bed_b"]
    ctrl = make()
    assert not ctrl._presence_observed
    await ctrl.async_evaluate()
    assert ctrl._presence_observed
    assert ctrl.raw_house_present is False
    assert ctrl.house_present is False
    assert ctrl.work_preheating is True
    assert ctrl.preheating is True
    assert ctrl.rooms["bed_a"].effective_target == 19
    assert ctrl.rooms["bed_a"].target_reason == "work_return_preheat"
    assert ctrl.rooms["bed_a"].demand
    assert ctrl.rooms["bed_b"].effective_target == 8
    assert ctrl.rooms["bed_b"].target_reason == "work_return_guest_unoccupied"
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_reload_during_preheat_does_not_add_a_new_delay(house):
    ha, settings, rooms, current, make = workday(house)
    ha.states.set("binary_sensor.house", "off")
    original = make()
    await original.async_evaluate()
    assert original.work_preheating
    # A config-entry reload constructs another controller.
    restarted = make()
    await restarted.async_evaluate()
    assert restarted.work_preheating
    assert not restarted.house_present


def test_departure_while_running_still_has_configured_grace(house):
    ha, settings, rooms, current, make = workday(house)
    ctrl = make()
    assert ctrl._occupied_with_delay(1000) is True
    ha.states.set("binary_sensor.house", "off")
    assert ctrl._occupied_with_delay(1010) is True
    assert ctrl.raw_house_present is False
    assert ctrl._occupied_with_delay(1609) is True
    assert ctrl._occupied_with_delay(1610) is False
    assert ctrl.house_absence_started == 1010


def test_return_resets_grace_and_subsequent_departure_restarts_it(house):
    ha, settings, rooms, current, make = workday(house)
    ctrl = make()
    ctrl._occupied_with_delay(1000)
    ha.states.set("binary_sensor.house", "off")
    assert ctrl._occupied_with_delay(1100)
    ha.states.set("binary_sensor.house", "on")
    assert ctrl._occupied_with_delay(1200)
    assert ctrl.house_absence_started is None
    ha.states.set("binary_sensor.house", "off")
    assert ctrl._occupied_with_delay(1250)
    assert ctrl._occupied_with_delay(1850) is False


@pytest.mark.asyncio
async def test_restart_outside_work_window_remains_at_absent_setback(house):
    ha, settings, rooms, current, make = workday(house, when="2026-11-03 12:00")
    ha.states.set("binary_sensor.house", "off")
    ctrl = make()
    await ctrl.async_evaluate()
    assert not ctrl.house_present
    assert not ctrl.work_preheating
    assert ctrl.rooms["bed_a"].target_reason == "house_absent_setback"
    assert ctrl.rooms["bed_a"].effective_target == 8


def test_startup_house_present_is_not_treated_as_away(house):
    ha, settings, rooms, current, make = workday(house)
    ctrl = make()
    assert ctrl._occupied_with_delay(1000)
    assert ctrl.raw_house_present
    assert ctrl.house_absence_started is None


def test_no_occupancy_configuration_preserves_present_default(house):
    ha, settings, rooms, current, make = workday(house)
    settings.pop("house_occupancy")
    ctrl = make()
    assert ctrl._occupied_with_delay(1000)
    assert ctrl.raw_house_present
    assert ctrl.house_absence_started is None
