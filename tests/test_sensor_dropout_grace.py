"""Regressions for 60s Zigbee2MQTT room input dropout protection."""
import importlib.util
from pathlib import Path
import pytest


def prepare(house):
    ha, settings, rooms, clock, make = house
    settings["enable_schedule"] = False
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "13")
    ctrl = make()
    return ha, settings, clock, ctrl


def make_unavailable(ha):
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "unavailable")


def is_on(ha):
    return ha.states.get("switch.boiler").state == "on"


@pytest.mark.asyncio
async def test_short_simultaneous_zigbee_loss_preserves_active_primary_calls(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    assert is_on(ha)
    assert all(ctrl.rooms[n].demand for n in ("bed_a", "bed_b"))
    make_unavailable(ha)
    actions_before = len(ha.services.actions)
    await ctrl.async_evaluate()
    assert is_on(ha)
    assert not any(action == "turn_off" and domain == "switch"
                   for domain, action, _ in ha.services.actions[actions_before:])
    assert all(ctrl.rooms[n].sensor_grace_reason == "temperature_unavailable"
               for n in ("bed_a", "bed_b"))
    assert all(ctrl.rooms[n].sensor_grace_remaining_seconds > 0
               for n in ("bed_a", "bed_b"))
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_grace_expires_then_both_rooms_stop_requesting(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    make_unavailable(ha)
    for name in ("bed_a", "bed_b"):
        ctrl.rooms[name].last_valid_sensor_at -= 61
    await ctrl.async_evaluate()
    assert not is_on(ha)
    assert not ctrl.boiler_requested
    assert all(ctrl.rooms[n].blocked_reason == "temperature_unavailable"
               for n in ("bed_a", "bed_b"))


@pytest.mark.asyncio
async def test_recovery_and_satisfaction_end_call_immediately(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    make_unavailable(ha)
    await ctrl.async_evaluate()
    assert is_on(ha)
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "21")
    await ctrl.async_evaluate()
    assert not is_on(ha)
    assert all(not ctrl.rooms[n].demand and ctrl.rooms[n].sensor_grace_reason is None
               for n in ("bed_a", "bed_b"))


@pytest.mark.asyncio
async def test_confirmed_window_open_stops_call_even_if_temperature_missing(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    make_unavailable(ha)
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"binary_sensor.{name}_window", "on")
    await ctrl.async_evaluate()
    assert not is_on(ha)
    assert all(ctrl.rooms[n].blocked_reason == "window_open"
               for n in ("bed_a", "bed_b"))


@pytest.mark.asyncio
async def test_unknown_window_uses_grace_but_confirmed_open_does_not(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"binary_sensor.{name}_window", "unavailable")
    await ctrl.async_evaluate()
    assert is_on(ha)
    assert all(ctrl.rooms[n].sensor_grace_reason == "window_unavailable"
               for n in ("bed_a", "bed_b"))
    ha.states.set("binary_sensor.bed_a_window", "on")
    ha.states.set("binary_sensor.bed_b_window", "on")
    await ctrl.async_evaluate()
    assert not is_on(ha)


@pytest.mark.asyncio
async def test_explicit_room_off_beats_grace(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    make_unavailable(ha)
    for name in ("bed_a", "bed_b"):
        ctrl.rooms[name].mode = "off"
    await ctrl.async_evaluate()
    assert not is_on(ha)
    assert all(ctrl.rooms[n].blocked_reason == "off"
               for n in ("bed_a", "bed_b"))


@pytest.mark.asyncio
async def test_target_setback_beats_grace(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    make_unavailable(ha)
    for name in ("bed_a", "bed_b"):
        ctrl.rooms[name].conf["unoccupied_target"] = 8.0
    await ctrl.async_evaluate()
    assert not is_on(ha)
    assert all(ctrl.rooms[n].sensor_grace_reason is None
               for n in ("bed_a", "bed_b"))


@pytest.mark.asyncio
async def test_noncalling_room_never_starts_heat_from_unavailable_sensor(house):
    ha, settings, clock, ctrl = prepare(house)
    make_unavailable(ha)
    await ctrl.async_evaluate()
    assert not is_on(ha)
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_grace_is_not_persisted_across_restart(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    assert is_on(ha)
    make_unavailable(ha)
    # A fresh controller has no prior valid input; even if relay was on,
    # it must not invent demand from a persisted grace timer.
    new_ctrl = house[-1]()
    await new_ctrl.async_evaluate()
    assert not new_ctrl.boiler_requested
    assert all(new_ctrl.rooms[n].last_valid_sensor_at is None
               for n in ("bed_a", "bed_b"))


@pytest.mark.asyncio
async def test_single_primary_drops_out_other_primary_continues(house):
    ha, settings, clock, ctrl = prepare(house)
    await ctrl.async_evaluate()
    ha.states.set("sensor.bed_a", "unavailable")
    ctrl.rooms["bed_a"].last_valid_sensor_at -= 61
    await ctrl.async_evaluate()
    assert is_on(ha)
    assert ctrl.rooms["bed_b"].demand
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_summer_off_beats_grace_during_holiday(house):
    ha, settings, clock, ctrl = prepare(house)
    settings["holiday_enable"] = "input_boolean.holiday"
    settings["holiday_return"] = "input_datetime.return"
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-12 08:30:00")
    assert clock[0].hour == 6
    await ctrl.async_evaluate()
    assert ctrl.holiday_return_preheating
    assert is_on(ha)
    make_unavailable(ha)
    ctrl.summer = True
    await ctrl.async_evaluate()
    assert not ctrl.holiday_return_preheating
    assert not ctrl.boiler_requested
    assert not is_on(ha)


def test_helper_deadline_and_fail_closed_conditions():
    path = (Path(__file__).resolve().parents[1] / "custom_components"
            / "radiator_orchestrator" / "demand_grace.py")
    spec = importlib.util.spec_from_file_location("heatflow_demand_grace_isolated", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    retained_call_seconds = module.retained_call_seconds
    good = dict(
        was_calling=True,
        last_valid_at=100.0,
        last_valid_target=21.0,
        current_target=21.0,
        hard_blocked=False,
    )
    assert retained_call_seconds(now=159.5, **good) == 1
    assert retained_call_seconds(now=160.0, **good) == 0
    assert retained_call_seconds(now=161.0, **good) == 0
    assert retained_call_seconds(now=101, **{**good, "was_calling":False}) == 0
    assert retained_call_seconds(now=101, **{**good, "hard_blocked":True}) == 0
    assert retained_call_seconds(now=101, **{**good, "current_target":8}) == 0
    assert retained_call_seconds(now=101, **{**good, "last_valid_at":None}) == 0
    assert retained_call_seconds(now=99, **good) == 0
