"""Regressions for daytime occupancy and holiday return preheating."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest


def add_kitchen(ha, settings, rooms, *, temp="19.2", occupancy="on", primary=True):
    room = dict(name="kitchen", room_type="living", trv="climate.kitchen",
                temperature="sensor.kitchen", occupancy="binary_sensor.kitchen",
                window="binary_sensor.kitchen_window", primary=primary,
                target=21.0, unoccupied_target=16.0,
                kp=1.5, ki=0.00015, kd=0.0)
    rooms.append(room)
    ha.states.set("sensor.kitchen", temp)
    ha.states.set("binary_sensor.kitchen", occupancy)
    ha.states.set("binary_sensor.kitchen_window", "off")
    ha.states.set("climate.kitchen", "heat", temperature=5)
    return room


@pytest.mark.asyncio
async def test_weekday_daytime_occupied_living_room_not_set_back(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 13, 10, tzinfo=ZoneInfo("Europe/London"))
    add_kitchen(ha, settings, rooms)
    ctrl = make()
    await ctrl.async_evaluate()
    kitchen = ctrl.rooms["kitchen"]
    assert kitchen.measured == 19.2
    assert kitchen.effective_target == 21
    assert kitchen.target_reason == "room_occupied"
    assert kitchen.demand
    assert ctrl.boiler_requested
    assert ctrl.boiler_on
    assert kitchen.trv_target > 21


@pytest.mark.asyncio
async def test_weekday_daytime_unoccupied_living_room_is_set_back(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 13, 10, tzinfo=ZoneInfo("Europe/London"))
    add_kitchen(ha, settings, rooms, occupancy="off")
    ctrl = make()
    await ctrl.async_evaluate()
    kitchen = ctrl.rooms["kitchen"]
    assert kitchen.effective_target == 16
    assert not kitchen.demand
    assert kitchen.target_reason == "room_unoccupied_setback"


@pytest.mark.asyncio
async def test_holiday_return_sunday_noon_warms_house_while_absent_and_away_on(house):
    ha, settings, rooms, current, make = house
    # UK clocks went back on Sunday 25 October 2026. The same local timezone
    # must be used for the input_datetime and the current moment.
    tz = ZoneInfo("Europe/London")
    current[0] = datetime(2026, 10, 25, 10, 0, tzinfo=tz)
    settings.update(holiday_enable="input_boolean.holiday", holiday_return="input_datetime.return",
                    preheat_base_minutes=90, preheat_extra_minutes_per_degree=8,
                    preheat_reference_c=15, max_preheat_minutes=360)
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-25 12:00:00")
    ha.states.set("binary_sensor.house", "off")
    settings["absence_delay_minutes"] = 0
    ha.states.set("binary_sensor.workday", "off")
    add_kitchen(ha, settings, rooms, temp="16.0", occupancy="off")
    ctrl = make()
    ctrl.away = True  # A common configuration throughout a holiday.
    await ctrl.async_evaluate()
    kitchen = ctrl.rooms["kitchen"]
    assert ctrl.preheat_minutes == 170
    assert ctrl.holiday_active
    assert ctrl.preheating
    assert ctrl.holiday_return_preheating
    assert ctrl.holiday_return_status == "preheating_before_return"
    assert not ctrl.house_present
    assert kitchen.effective_target == 21
    assert kitchen.target_reason == "holiday_return_preheat"
    assert kitchen.demand
    assert ctrl.boiler_requested
    assert ctrl.boiler_on


@pytest.mark.asyncio
async def test_monday_lunchtime_return_uses_comfort_not_daytime_bedroom_setback(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 10, 0, tzinfo=ZoneInfo("Europe/London"))
    settings.update(holiday_enable="input_boolean.holiday", holiday_return="input_datetime.return")
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-26 12:00:00")
    ha.states.set("binary_sensor.house", "off")
    ha.states.set("sensor.bed_a", "16")
    ha.states.set("sensor.bath", "17")
    ha.states.set("binary_sensor.bed_a", "off")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.holiday_return_preheating
    assert ctrl.rooms["bed_a"].effective_target == 19
    assert ctrl.rooms["bath"].effective_target == 22
    assert ctrl.rooms["bed_a"].demand
    assert ctrl.rooms["bath"].demand


@pytest.mark.asyncio
async def test_holiday_return_hold_after_due_time(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 13, 10, tzinfo=ZoneInfo("Europe/London"))
    settings.update(holiday_enable="input_boolean.holiday", holiday_return="input_datetime.return",
                    holiday_return_hold_minutes=120)
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-26 12:00:00")
    ha.states.set("binary_sensor.house", "off")
    add_kitchen(ha, settings, rooms, temp="18", occupancy="off")
    ctrl = make()
    ctrl.away = True
    await ctrl.async_evaluate()
    assert not ctrl.holiday_return_preheating
    assert ctrl.holiday_return_holding
    assert ctrl.rooms["kitchen"].effective_target == 21
    assert ctrl.rooms["kitchen"].target_reason == "holiday_return_hold"
    assert ctrl.boiler_requested
    current[0] = datetime(2026, 10, 26, 14, 1, tzinfo=ZoneInfo("Europe/London"))
    await ctrl.async_evaluate()
    assert not ctrl.holiday_return_holding
    assert ctrl.rooms["kitchen"].effective_target == 8
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_summer_off_and_window_open_still_protect_on_holiday_return(house):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 10, 26, 10, 0, tzinfo=ZoneInfo("Europe/London"))
    settings.update(holiday_enable="input_boolean.holiday", holiday_return="input_datetime.return")
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-26 12:00:00")
    ha.states.set("binary_sensor.house", "off")
    add_kitchen(ha, settings, rooms, temp="16", occupancy="off")
    ctrl = make()
    ctrl.summer = True
    await ctrl.async_evaluate()
    assert not ctrl.holiday_return_preheating
    assert ctrl.holiday_return_status == "summer_mode_inhibits_return_heating"
    assert not ctrl.rooms["kitchen"].demand
    ctrl.summer = False
    ha.states.set("binary_sensor.kitchen_window", "on")
    await ctrl.async_evaluate()
    assert ctrl.holiday_return_preheating
    assert ctrl.rooms["kitchen"].blocked_reason == "window_open"
    assert not ctrl.rooms["kitchen"].demand


@pytest.mark.asyncio
async def test_missing_holiday_return_date_is_reported_not_guessed(house):
    ha, settings, rooms, current, make = house
    settings.update(holiday_enable="input_boolean.holiday", holiday_return="input_datetime.return")
    ha.states.set("input_boolean.holiday", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.holiday_active
    assert ctrl.holiday_return_status == "return_datetime_missing"
    assert not ctrl.holiday_return_preheating


@pytest.mark.asyncio
async def test_return_preheat_disabled_when_holiday_boolean_off(house):
    ha, settings, rooms, current, make = house
    settings.update(holiday_enable="input_boolean.holiday", holiday_return="input_datetime.return")
    ha.states.set("input_boolean.holiday", "off")
    ha.states.set("input_datetime.return", "2026-10-26 12:00:00")
    ctrl = make()
    await ctrl.async_evaluate()
    assert not ctrl.holiday_active
    assert ctrl.holiday_return_status == "holiday_switch_off"
    assert not ctrl.holiday_return_preheating


def test_holiday_return_uses_elapsed_time_across_dstu_end(house):
    ha, settings, rooms, current, make = house
    tz = ZoneInfo("Europe/London")
    settings.update(holiday_enable="input_boolean.holiday", holiday_return="input_datetime.return",
                    preheat_base_minutes=120, preheat_extra_minutes_per_degree=0)
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-25 03:30:00")
    current[0] = datetime(2026, 10, 25, 1, 30, tzinfo=tz, fold=0)  # BST 00:30 UTC
    ctrl = make()
    active, preheat, holding = ctrl._holiday_window()
    assert active and not preheat and not holding  # 3 actual hours until return
    current[0] = datetime(2026, 10, 25, 2, 30, tzinfo=tz)  # GMT 02:30 UTC
    active, preheat, holding = ctrl._holiday_window()
    assert active and preheat and not holding
