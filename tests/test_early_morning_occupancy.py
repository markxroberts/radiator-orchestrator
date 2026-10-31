"""Regression: a weekend early riser can heat an occupied room before 08:00."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest


def living_room(house, *, primary=True, early=True, name='kitchen'):
    ha, settings, rooms, now, make = house
    conf = dict(rooms[0], name=name, room_type='living', primary=primary,
                trv=f'climate.{name}', temperature=f'sensor.{name}',
                occupancy=f'binary_sensor.{name}', window=f'binary_sensor.{name}_window',
                early_occupancy_heating=early, morning_preheat=False)
    rooms.append(conf)
    ha.states.set(f'sensor.{name}', '18')
    ha.states.set(f'binary_sensor.{name}', 'on')
    ha.states.set(f'binary_sensor.{name}_window', 'off')
    ha.states.set(f'climate.{name}', 'heat', temperature=5)
    return conf


@pytest.mark.asyncio
async def test_weekend_occupied_kitchen_heats_before_rising(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house)
    ctrl = make()
    await ctrl.async_evaluate()
    kitchen = ctrl.rooms['kitchen']
    assert kitchen.effective_target == 20
    assert kitchen.target_reason == 'early_occupied_morning'
    assert kitchen.schedule_phase == 'early_occupied_morning'
    assert kitchen.demand
    assert kitchen.effective_primary
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_early_unoccupied_kitchen_does_not_heat(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house)
    ha.states.set('binary_sensor.kitchen', 'off')
    ctrl = make()
    await ctrl.async_evaluate()
    kitchen = ctrl.rooms['kitchen']
    assert kitchen.effective_target == 8
    assert not kitchen.demand


@pytest.mark.asyncio
async def test_late_night_occupancy_respects_bedtime(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 23, 30, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].effective_target == 8
    assert not ctrl.rooms['kitchen'].demand


@pytest.mark.asyncio
async def test_too_early_occupancy_respects_night_setback(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 3, 45, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].effective_target == 8
    assert not ctrl.rooms['kitchen'].demand


@pytest.mark.asyncio
async def test_early_occupancy_can_be_disabled_per_room(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house, early=False)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].effective_target == 8
    assert not ctrl.rooms['kitchen'].demand


@pytest.mark.asyncio
async def test_secondary_kitchen_demand_only_when_boiler_called(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house, primary=False)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].demand
    assert not ctrl.rooms['kitchen'].effective_primary
    assert not ctrl.boiler_requested
    assert ctrl.rooms['kitchen'].trv_target == 5.0
    # Another room's preheat can activate the boiler; then the same
    # occupied kitchen participates without itself becoming primary.
    rooms[0].update(morning_preheat=True, morning_allow_boiler=True,
                    morning_preheat_minutes=90,
                    occupancy='input_boolean.bed_a')
    ha.states.set('input_boolean.bed_a', 'on')
    ha.states.set('sensor.bed_a', '16')
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.boiler_requested
    assert ctrl.rooms['kitchen'].demand
    assert ctrl.rooms['kitchen'].trv_target > 5


@pytest.mark.asyncio
async def test_holiday_and_window_restrictions_remain(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house)
    ha.states.set('binary_sensor.kitchen_window', 'on')
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].blocked_reason == 'window_open'
    assert not ctrl.rooms['kitchen'].demand
    ha.states.set('binary_sensor.kitchen_window', 'off')
    ctrl.away = True
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].effective_target == 8
    assert not ctrl.rooms['kitchen'].demand


@pytest.mark.asyncio
async def test_weekday_occupied_living_room_before_rising(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 11, 2, 5, 20, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'on')
    living_room(house)
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].target_reason == 'early_occupied_morning'
    assert ctrl.rooms['kitchen'].demand


@pytest.mark.asyncio
async def test_helper_moves_earliest_occupancy_time(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house)
    settings['early_occupancy_from'] = 'input_datetime.early_occupancy_from'
    ha.states.set('input_datetime.early_occupancy_from', '07:00:00')
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.rooms['kitchen'].effective_target == 8
    assert not ctrl.rooms['kitchen'].demand


@pytest.mark.asyncio
async def test_existing_input_number_morning_target_is_used(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    living_room(house)
    settings['morning_target'] = 'input_number.morning_target'
    ha.states.set('input_number.morning_target', '21')
    ha.states.set('sensor.kitchen', '19.2')
    ctrl = make()
    await ctrl.async_evaluate()
    kitchen = ctrl.rooms['kitchen']
    assert kitchen.effective_target == 21
    assert kitchen.demand
    assert kitchen.demand_reason == 'early_occupied_morning'


@pytest.mark.asyncio
async def test_bedroom_boolean_does_not_disable_night_setback_by_default(house):
    ha, settings, rooms, now, make = house
    now[0] = datetime(2026, 10, 31, 6, 35, tzinfo=ZoneInfo('Europe/London'))
    ha.states.set('binary_sensor.workday', 'off')
    rooms[0].update(occupancy='input_boolean.bed_a')
    ha.states.set('input_boolean.bed_a', 'on')
    ha.states.set('sensor.bed_a', '16')
    ctrl = make()
    await ctrl.async_evaluate()
    bedroom = ctrl.rooms['bed_a']
    assert bedroom.effective_target == 8
    assert bedroom.demand is False
